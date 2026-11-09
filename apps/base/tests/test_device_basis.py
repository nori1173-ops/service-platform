"""通信機ログインの根拠 (ADR D13 / D14、計画 4.12 の base)。

everyone の通信機は割り当ての無い有効な利用者も通信機ログインに進め、暗証番号が門になる。グループ宛の割り当ては
本人の所属の経路にそのグループがあれば効く。ログイン記録には根拠の種類と ID と世代を書き、置き換えは読んだ記録の
版 (last_op_id) を条件にする (根拠の始まりの大小では縛らない)。製造番号は人宛なら割り当ての行、グループ宛と
everyone は本人の控え (`SERIAL#`) に残す。利用者ごとの失敗回数は全通信機を合わせて 60 分に 10 回で 60 分止める。
"""

import logging
from datetime import timedelta

import pytest
from device_data import (
    ACCOUNT,
    NOW,
    PERIOD_ID,
    PIN,
    SERIAL,
    SETTINGS_PATH,
    SETTINGS_PAYLOAD,
    ledger_row,
    put_assignment,
    put_grant,
    put_group,
    put_group_assignment,
    put_membership,
    put_period,
)

from service_platform import ledger

LOGIN_BODY = {"account": ACCOUNT, "serial": SERIAL, "pin": PIN, "mode": "previous"}
ROOT = "01JBZ0000000000000GROUPRT"
CHILD = "01JBZ0000000000000GROUPCH"
OTHER = "01JBZ0000000000000GROUPOT"
M_CHILD = "01JBZ000000000000MEMBERCH"
M_OTHER = "01JBZ000000000000MEMBEROT"
M_AGAIN = "01JBZ000000000000MEMBERAG"
GA_EARLY = "01JBZ000000000000GASSIGN1"
GA_LATE = "01JBZ000000000000GASSIGN2"
GEN = "01JBZ000000000000000GEN01"


@pytest.fixture
def base(aws, clock, device_backend, caplog):
    caplog.set_level(logging.DEBUG)
    aws.put_user()
    aws.clock = clock
    aws.device_backend = device_backend
    aws.caplog = caplog
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    return aws


def everyone_device(aws, **kwargs):
    put_period(aws, access="everyone", access_gen=GEN, **kwargs)
    put_grant(aws)


def group_device(aws, *, access="assigned"):
    put_period(aws, access=access)
    put_grant(aws)
    put_group(aws, ROOT, "会社A", [ROOT])
    put_group(aws, CHILD, "部署B", [ROOT, CHILD])
    put_membership(aws, CHILD, M_CHILD, [ROOT, CHILD])
    put_group_assignment(aws, ROOT, assignment_id=GA_EARLY)


def login_row(aws):
    return ledger_row(aws, ledger.user_pk(aws.ids.user_id), ledger.login_sk(ACCOUNT))


def serial_row(aws, user_id=None):
    return ledger_row(aws, ledger.user_pk(user_id or aws.ids.user_id), ledger.serial_sk(PERIOD_ID))


def period_row(aws):
    return ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.dperiod_sk(PERIOD_ID))


def actions(aws):
    rows = sorted(aws.audit_items(), key=lambda item: item["SK"])
    return [(row["action"], row["result"], row.get("code")) for row in rows]


def audit_of(aws, action, result="ok"):
    return [row for row in aws.audit_items() if row["action"] == action and row["result"] == result]


def assert_pin_is_nowhere(aws, *bodies):
    for text in [*(str(body) for body in bodies), aws.caplog.text, str(aws.audit_items())]:
        assert PIN not in text
    assert PIN not in str(aws.ledger.scan()["Items"])


def during_the_query(monkeypatch, action):
    from base_api import device_backend_client

    original = device_backend_client.get_settings

    def slow(*args):
        result = original(*args)
        action()
        return result

    monkeypatch.setattr(device_backend_client, "get_settings", slow)


def set_access(aws, value, generation=None):
    names = {"#a": "access", "#v": "version"}
    values = {":a": value, ":one": 1}
    expression = "SET #a = :a, #v = #v + :one"
    if generation:
        names["#g"] = "access_gen"
        values[":g"] = generation
        expression += ", #g = :g"
    aws.ledger.update_item(
        Key={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.dperiod_sk(PERIOD_ID)},
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def remove_membership(aws, group_id):
    aws.ledger.delete_item(Key={"PK": ledger.user_pk(aws.ids.user_id), "SK": ledger.member_sk(group_id)})


# =============================================================================
# everyone
# =============================================================================


def test_everyone_lets_a_user_without_an_assignment_log_in(base, call):
    everyone_device(base)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 200, body
    row = login_row(base)
    assert row["basis"] == "everyone" and row["access_gen"] == GEN
    assert "assignment_id" not in row and "assignment_valid_from" not in row
    assert row["device_period_id"] == PERIOD_ID and row["last_op_id"]
    saved = serial_row(base)
    assert saved["serial"] == SERIAL and saved["account"] == ACCOUNT and saved["device_period_id"] == PERIOD_ID
    assert int(period_row(base)["version"]) == 1
    (audit,) = audit_of(base, "device.login")
    assert audit["detail"]["basis"] == "everyone"
    assert "assignment_id" not in audit or audit["assignment_id"] is None
    assert call("GET", f"/devices/{ACCOUNT}/services")[0] == 200
    assert_pin_is_nowhere(base, body)


def test_a_period_without_access_attributes_is_everyone_with_the_start_generation(base, call):
    put_period(base, access=None)

    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    assert login_row(base)["access_gen"] == f"START#{period_row(base)['started_at']}"


def test_the_second_login_keeps_the_saved_serial(base, call):
    everyone_device(base)
    call("POST", "/devices/login", body=LOGIN_BODY)
    first = serial_row(base)
    base.clock.advance(60)

    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    assert serial_row(base) == first


def test_assigned_without_an_assignment_does_not_ask_the_device_backend_backend(base, call):
    put_period(base, access="assigned")

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403 and body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert base.device_backend.calls == []


def test_a_user_assignment_on_an_everyone_device_is_the_recorded_basis(base, call):
    everyone_device(base)
    put_assignment(base)

    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    row = login_row(base)
    assert row["basis"] == "user" and "access_gen" not in row
    assert serial_row(base) is None


# =============================================================================
# グループ宛
# =============================================================================


def test_a_group_login_records_the_membership_ids(base, call):
    group_device(base)
    base.dynamodb.calls.clear()

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 200, body
    row = login_row(base)
    assert row["basis"] == "group" and row["group_id"] == ROOT
    assert row["assignment_id"] == GA_EARLY and row["membership_ids"] == [M_CHILD]
    assert serial_row(base)["serial"] == SERIAL
    (sent,) = base.dynamodb.calls
    checked = [item["ConditionCheck"]["Key"]["SK"]["S"] for item in sent["TransactItems"] if "ConditionCheck" in item]
    assert ledger.member_sk(CHILD) in checked
    assert ledger.group_assignment_sk(ROOT, GA_EARLY) in checked
    (audit,) = audit_of(base, "device.login")
    assert audit["detail"]["basis"] == "group" and audit["detail"]["group_id"] == ROOT
    assert audit["assignment_id"] == GA_EARLY
    assert_pin_is_nowhere(base, body)


@pytest.mark.parametrize("fallback", ["group", "everyone"])
def test_a_user_can_log_in_again_on_another_basis_with_an_old_record_in_force(base, call, fallback):
    """始まりの遅いグループ宛でログイン → その所属を外す → 別の根拠で、期限内の古い記録があっても入り直せる。"""
    group_device(base, access="assigned" if fallback == "group" else "everyone")
    put_group(base, OTHER, "会社B", [OTHER])
    put_membership(base, OTHER, M_OTHER, [OTHER])
    put_group_assignment(base, OTHER, assignment_id=GA_LATE, valid_from="2026-11-04T00:00:00Z")
    if fallback == "everyone":
        remove_membership(base, CHILD)
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    assert login_row(base)["assignment_id"] == GA_LATE
    remove_membership(base, OTHER)
    base.clock.advance(60)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 200, body
    assert login_row(base)["basis"] == fallback


def test_concurrent_logins_of_the_same_user_let_only_the_first_writer_through(base, call, monkeypatch):
    everyone_device(base)
    inner = []

    def other_login():
        if not inner:
            inner.append(None)
            inner[0] = call("POST", "/devices/login", body=LOGIN_BODY)

    during_the_query(monkeypatch, other_login)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert inner[0][0] == 200
    assert status == 409 and body["error"]["code"] == "CONFLICT"
    assert len(audit_of(base, "device.login")) == 1


def test_a_login_record_deleted_by_an_admin_after_it_was_read_is_409(base, call, monkeypatch):
    everyone_device(base)
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    base.clock.advance(60)
    during_the_query(
        monkeypatch,
        lambda: base.ledger.delete_item(Key={"PK": ledger.user_pk(base.ids.user_id), "SK": ledger.login_sk(ACCOUNT)}),
    )

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 409 and body["error"]["code"] == "CONFLICT"
    assert login_row(base) is None


@pytest.mark.parametrize("change", ["membership_replaced", "access_round_trip"])
def test_a_basis_replaced_during_the_query_is_not_written_and_is_409(base, call, monkeypatch, change):
    if change == "membership_replaced":
        group_device(base)

        def replace():
            remove_membership(base, CHILD)
            put_membership(base, CHILD, M_AGAIN, [ROOT, CHILD])
    else:
        everyone_device(base)

        def replace():
            set_access(base, "assigned")
            set_access(base, "everyone", "01JBZ000000000000000GEN02")

    during_the_query(monkeypatch, replace)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 409 and body["error"]["code"] == "CONFLICT"
    assert login_row(base) is None
    assert serial_row(base) is None


@pytest.mark.parametrize("change", ["membership_removed", "access_assigned", "group_assignment_ended"])
def test_a_basis_lost_during_the_query_is_403(base, call, monkeypatch, change):
    if change == "access_assigned":
        everyone_device(base)
        action = lambda: set_access(base, "assigned")  # noqa: E731
    else:
        group_device(base)
        if change == "membership_removed":
            action = lambda: remove_membership(base, CHILD)  # noqa: E731
        else:

            def action():
                base.ledger.update_item(
                    Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.group_assignment_sk(ROOT, GA_EARLY)},
                    UpdateExpression="SET valid_to = :t",
                    ExpressionAttributeValues={":t": "2026-11-04T03:00:00Z"},
                )

    during_the_query(monkeypatch, action)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403 and body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert login_row(base) is None


# =============================================================================
# 利用者ごとの失敗回数 (ADR D14 の総当たり)
# =============================================================================


ACCOUNTS = ["11111111", "22222222", "33333333", "44444444"]


def failing_devices(aws):
    for index, account in enumerate(ACCOUNTS):
        put_period(aws, period_id=f"01JBZ0000000000000PERIOD{index}", account=account, access=None)
        aws.device_backend.on("GET", f"/api/v2/accounts/{account}/settings", 401)


def test_ten_failures_across_devices_lock_the_user_for_an_hour(base, call):
    failing_devices(base)
    attempts = [ACCOUNTS[0]] * 4 + [ACCOUNTS[1]] * 4 + [ACCOUNTS[2]] * 2
    for account in attempts:
        status, body = call("POST", "/devices/login", body={**LOGIN_BODY, "account": account})
        assert status == 403 and body["error"]["code"] == "PIN_MISMATCH"
    row = ledger_row(base, ledger.user_pk(base.ids.user_id), ledger.USER_PINFAIL_SK)
    assert int(row["locked_until"]) == base.clock.epoch + 3600
    (locked,) = audit_of(base, "device.pin_locked")
    assert locked["detail"]["scope"] == "user"
    calls = len(base.device_backend.calls)

    status, body = call("POST", "/devices/login", body={**LOGIN_BODY, "account": ACCOUNTS[3]})

    assert status == 429 and body["error"]["code"] == "USER_PIN_LOCKED"
    assert ACCOUNTS[3] not in body["error"]["message"]
    assert len(base.device_backend.calls) == calls
    base.clock.advance(3601)
    assert call("POST", "/devices/login", body={**LOGIN_BODY, "account": ACCOUNTS[3]})[1]["error"]["code"] == (
        "PIN_MISMATCH"
    )


def test_the_user_lock_comes_before_the_assignment_check(base, call):
    """止めている間は、通信機のこと (割り当て・稼働中かどうか) を何も知らせない。"""
    base.ledger.put_item(
        Item={
            "PK": ledger.user_pk(base.ids.user_id),
            "SK": ledger.USER_PINFAIL_SK,
            "count": 10,
            "window_started_at": base.clock.epoch,
            "locked_until": base.clock.epoch + 600,
        }
    )

    status, body = call("POST", "/devices/login", body={**LOGIN_BODY, "account": "99999999"})

    assert status == 429 and body["error"]["code"] == "USER_PIN_LOCKED"


def test_a_device_failure_also_counts_for_the_user_and_a_success_keeps_the_user_count(base, call):
    everyone_device(base)
    base.device_backend.on("GET", SETTINGS_PATH, 403)
    call("POST", "/devices/login", body=LOGIN_BODY)
    base.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200

    assert int(ledger_row(base, ledger.user_pk(base.ids.user_id), ledger.USER_PINFAIL_SK)["count"]) == 1
    assert ledger_row(base, ledger.device_pk(ACCOUNT), ledger.PINFAIL_SK) is None


def test_the_waiting_poll_exemption_is_not_counted_for_the_user_either(base, call):
    everyone_device(base)
    base.device_backend.on("POST", f"/api/v2/accounts/{ACCOUNT}/status", 202)
    base.device_backend.on("POST", SETTINGS_PATH, 202)
    status, started = call("POST", "/devices/refresh", body={"account": ACCOUNT, "serial": SERIAL, "pin": PIN})
    assert status == 202, started
    base.device_backend.on("GET", SETTINGS_PATH, 401)
    base.clock.advance(8)
    data = started["data"]

    status, body = call(
        "POST",
        "/devices/login",
        body={**LOGIN_BODY, "mode": "latest", "acceptance_id": data["acceptance_id"], "poll_token": data["poll_token"]},
    )

    assert status == 403 and body["error"]["code"] == "PIN_MISMATCH"
    assert ledger_row(base, ledger.user_pk(base.ids.user_id), ledger.USER_PINFAIL_SK) is None


def test_refresh_availability_answers_anyone_on_an_everyone_device(base, call):
    everyone_device(base)

    status, body = call("GET", f"/devices/{ACCOUNT}/refresh-availability")

    assert status == 200, body


def test_refresh_availability_does_not_answer_without_a_basis(base, call):
    put_period(base, access="assigned")

    status, body = call("GET", f"/devices/{ACCOUNT}/refresh-availability")

    assert status == 403 and body["error"]["code"] == "DEVICE_NOT_ASSIGNED"


def test_an_old_assignment_login_is_replaced_without_comparing_valid_from(base, call):
    """古い形の記録 (`assignment_valid_from` あり、`last_op_id` なし) も置き換えられる。"""
    put_period(base)
    put_assignment(base)
    base.ledger.put_item(
        Item={
            "PK": ledger.user_pk(base.ids.user_id),
            "SK": ledger.login_sk(ACCOUNT),
            "assignment_id": "01JBZ0000000000000ASSIGN9",
            "assignment_valid_from": "2027-01-01T00:00:00Z",
            "device_period_id": PERIOD_ID,
            "expires_at": base.clock.epoch + 3600,
        }
    )

    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    assert login_row(base)["basis"] == "user"


def test_the_login_expiry_is_twelve_hours_for_every_basis(base, call):
    everyone_device(base)
    call("POST", "/devices/login", body=LOGIN_BODY)

    assert int(login_row(base)["expires_at"]) == int((NOW + timedelta(hours=12)).timestamp())
