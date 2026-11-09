"""通信機ログインと最新値と、サービスの一覧のテスト（計画 2.6 / 4.5、ADR-001 D4 / D5、判断 4・9）。

device-backend は `_opener.open` を差し替えた偽物 (通信しない)。暗証番号は明らかな架空の値で、
応答・ログ・監査・台帳のどこにも出ないことを、その文字列が無いことで確かめる。
"""

import logging
from datetime import timedelta

import pytest
from device_data import (
    ACCOUNT,
    ASSIGNMENT_ID,
    GRANT_ID,
    NOW,
    OLD_PERIOD_ID,
    OTHER_USER_ID,
    PERIOD_ID,
    PIN,
    SERIAL,
    SETTINGS_PATH,
    SETTINGS_PAYLOAD,
    STATUS_PATH,
    iso,
    ledger_row,
    put_assignment,
    put_camera,
    put_grant,
    put_period,
)

from service_platform import ledger

LOGIN_BODY = {"account": ACCOUNT, "serial": SERIAL, "pin": PIN, "mode": "previous"}
TWELVE_HOURS = 12 * 60 * 60


@pytest.fixture
def device(aws, clock, device_backend, caplog):
    """有効な利用者に、稼働中の利用期間・割り当て・camera の許可がある状態。"""
    caplog.set_level(logging.DEBUG)
    aws.put_user()
    put_period(aws)
    put_assignment(aws)
    put_grant(aws)
    aws.clock = clock
    aws.device_backend = device_backend
    aws.caplog = caplog
    return aws


def login_row(aws):
    return ledger_row(aws, ledger.user_pk(aws.ids.user_id), ledger.login_sk(ACCOUNT))


def pinfail_row(aws):
    return ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.PINFAIL_SK)


def refresh_row(aws):
    return ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.REFRESH_SK)


def assignment_row(aws):
    return ledger_row(aws, ledger.dperiod_pk(PERIOD_ID), ledger.assignment_sk(aws.ids.user_id, ASSIGNMENT_ID))


def actions(aws) -> list[tuple[str, str, str | None]]:
    rows = sorted(aws.audit_items(), key=lambda item: item["SK"])
    return [(row["action"], row["result"], row.get("code")) for row in rows]


def assert_pin_is_nowhere(aws, *bodies) -> None:
    """暗証番号を応答・ログ・監査・台帳に出さない (ADR D5、計画 7 章)。"""
    texts = [str(body) for body in bodies]
    texts.append(aws.caplog.text)
    texts.append(str(aws.audit_items()))
    texts.append(str(aws.ledger.scan()["Items"]))
    for text in texts:
        assert PIN not in text


# =============================================================================
# POST /devices/login: 成功
# =============================================================================


def test_login_with_the_previous_settings_succeeds(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 200
    data = body["data"]
    assert data["account"] == ACCOUNT
    assert data["method"] == "previous"
    assert data["location_name"] == "見本の現場"
    assert data["observation"] == {
        "enabled": True,
        "interval": "10M",
        "interval_short": "5M",
        "interval_min": 10,
        "interval_short_min": 5,
    }
    assert data["devices"] == [
        {"address": 5, "address_text": "05", "model": "CAM-UNIT-1", "site": "ｶﾒﾗ", "action": "L"},
        {"address": 12, "address_text": "12", "model": "LOGGER-UNIT-A", "site": "ｽｲｲ", "action": "S"},
    ]
    assert data["services"] == [{"id": "camera", "name": "カメラ画像判定", "url": f"/camera/?account={ACCOUNT}"}]
    assert_pin_is_nowhere(device, body)


def test_login_asks_the_device_backend_backend_with_the_pin_header_and_the_serial(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    call("POST", "/devices/login", body=LOGIN_BODY)

    (sent,) = device.device_backend.calls
    assert sent["url"].startswith("https://device_backend.example.test/")
    assert sent["method"] == "GET"
    assert sent["path"] == SETTINGS_PATH
    assert sent["query"] == {"serial": SERIAL}
    assert sent["headers"]["x-example-pin"] == PIN
    assert sent["timeout"] == 10


def test_login_writes_the_login_record_for_12_hours(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    call("POST", "/devices/login", body=LOGIN_BODY)

    row = login_row(device)
    assert row["assignment_id"] == ASSIGNMENT_ID
    assert row["device_period_id"] == PERIOD_ID
    assert row["logged_in_at"] == iso(NOW)
    assert int(row["expires_at"]) == device.clock.epoch + TWELVE_HOURS
    assert int(row["ttl"]) == device.clock.epoch + TWELVE_HOURS + 86400
    assert row["method"] == "previous"
    assert row["serial"] == SERIAL
    assert row["summary"]["location_name"] == "見本の現場"
    assert [int(item["address"]) for item in row["summary"]["devices"]] == [5, 12]
    assert "pin" not in row
    assert "pin" not in row["summary"]


def test_login_makes_the_services_api_pass(device, call):
    """ログインの後は条件 (1)〜(3) を満たし、サービスの一覧が読める。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    assert call("GET", f"/devices/{ACCOUNT}/services")[0] == 403

    call("POST", "/devices/login", body=LOGIN_BODY)

    status, body = call("GET", f"/devices/{ACCOUNT}/services")
    assert status == 200
    assert [service["id"] for service in body["data"]["services"]] == ["camera"]


def test_login_saves_the_serial_on_the_assignment_only_when_empty(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    call("POST", "/devices/login", body=LOGIN_BODY)
    assert assignment_row(device)["serial"] == SERIAL

    device.ledger.update_item(
        Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.assignment_sk(device.ids.user_id, ASSIGNMENT_ID)},
        UpdateExpression="SET serial = :s",
        ExpressionAttributeValues={":s": "11112222"},
    )
    call("POST", "/devices/login", body=LOGIN_BODY)
    assert assignment_row(device)["serial"] == "11112222"


def test_login_records_one_audit_row_without_secrets(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    call("POST", "/devices/login", body=LOGIN_BODY)

    (item,) = device.audit_items()
    assert item["action"] == "device.login"
    assert item["result"] == "ok"
    assert item["service"] == "base"
    assert item["PK"] == f"ACTOR#{device.ids.user_id}"
    assert item["account"] == ACCOUNT
    assert item["device_period_id"] == PERIOD_ID
    assert item["assignment_id"] == ASSIGNMENT_ID
    assert item["billing"] is False
    assert item["detail"] == {"method": "previous", "device_count": 2, "basis": "user"}
    text = str(item)
    assert SERIAL not in text
    assert "見本の現場" not in text


def test_login_lists_only_the_services_granted_now(device, call):
    put_grant(device, grant_id="01JBZ00000000000000GRANT2", service="other")
    device.ledger.delete_item(Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.grant_sk("camera", GRANT_ID)})
    put_grant(device, starts_at=iso(NOW + timedelta(days=1)))
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 200
    assert body["data"]["services"] == []


def period_row(aws):
    return ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.dperiod_sk(PERIOD_ID))


def end_period(aws, at=NOW):
    """admin-users の一括終了を写す: 利用期間・割り当てを終え、親の version を上げる。"""
    aws.ledger.update_item(
        Key={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.dperiod_sk(PERIOD_ID)},
        UpdateExpression="SET #s = :ended, ended_at = :at, version = version + :one",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":ended": "ended", ":at": iso(at), ":one": 1},
    )
    aws.ledger.delete_item(Key={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.CURRENT_SK})
    end_assignment(aws, at)


def end_assignment(aws, at=NOW):
    aws.ledger.update_item(
        Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.assignment_sk(aws.ids.user_id, ASSIGNMENT_ID)},
        UpdateExpression="SET valid_to = :t",
        ExpressionAttributeValues={":t": iso(at)},
    )


def test_saving_the_serial_bumps_the_parent_period_version(device, call):
    """製造番号の保存は、親の利用期間の version の更新と同じトランザクション ( の追加の裁定)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    call("POST", "/devices/login", body=LOGIN_BODY)

    assert assignment_row(device)["serial"] == SERIAL
    period = period_row(device)
    assert int(period["version"]) == 2
    assert period["last_op_id"] == assignment_row(device)["last_op_id"]

    call("POST", "/devices/login", body=LOGIN_BODY)
    assert int(period_row(device)["version"]) == 2


def test_the_serial_is_not_saved_when_the_period_ends_in_between(device, call):
    """照会の後、書く前に一括終了が挟まったら、ログイン記録も製造番号も書かない (1 つのトランザクション)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device.dynamodb.plan = [lambda: end_period(device)]

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert login_row(device) is None
    assert "serial" not in assignment_row(device)
    assert period_row(device)["status"] == "ended"
    assert int(period_row(device)["version"]) == 2


def bump_period_version(aws):
    """許可の追加・終了など、利用期間の子の変更 (admin-users は親の version を上げる)。"""
    aws.ledger.update_item(
        Key={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.dperiod_sk(PERIOD_ID)},
        UpdateExpression="SET version = version + :one",
        ExpressionAttributeValues={":one": 1},
    )


def during_the_query(device, monkeypatch, action):
    """device-backend の照会の間 (照会の開始の後、書き込みの前) に、ほかの操作と時刻の経過を挟む。"""
    from base_api import device_backend_client

    original = device_backend_client.get_settings

    def slow(*args):
        result = original(*args)
        action()
        return result

    monkeypatch.setattr(device_backend_client, "get_settings", slow)


@pytest.mark.parametrize("serial_saved", [False, True])
def test_a_parent_version_change_during_the_query_is_409(device, call, monkeypatch, serial_saved):
    """ログイン記録も、読んだ親の version を条件にする ( の追加の裁定)。照会の間に変われば書かない。"""
    if serial_saved:
        device.ledger.update_item(
            Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.assignment_sk(device.ids.user_id, ASSIGNMENT_ID)},
            UpdateExpression="SET serial = :s",
            ExpressionAttributeValues={":s": SERIAL},
        )
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    during_the_query(device, monkeypatch, lambda: bump_period_version(device))

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 409
    assert body["error"]["code"] == "CONFLICT"
    assert login_row(device) is None
    assert int(period_row(device)["version"]) == 2
    assert ("device.login", "ok", None) not in actions(device)


def test_an_assignment_ended_during_the_query_is_not_saved(device, call, monkeypatch):
    """照会の開始 t0 → 割り当ての終了 t1 (t0 < t1) → 書き込み、の順なら書かない。

    有効性は照会の前の時刻ではなく、書く直前に取り直した時刻で確かめる。親の version を上げない終了でも落ちる。
    """
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    def end_after_t0():
        end_assignment(device, NOW + timedelta(seconds=2))
        device.clock.advance(5)

    during_the_query(device, monkeypatch, end_after_t0)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert login_row(device) is None
    assert "serial" not in assignment_row(device)
    assert int(period_row(device)["version"]) == 1


@pytest.mark.parametrize("change", ["assignment_ended", "period_ended"])
def test_the_login_record_is_not_written_when_the_assignment_ends_in_between(device, call, change):
    """照会の間に割り当てか利用期間が終わったら、ログイン記録を書かない (遅れた照会)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device.dynamodb.plan = [lambda: end_assignment(device) if change == "assignment_ended" else end_period(device)]

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert login_row(device) is None
    assert ("device.login", "ok", None) not in actions(device)


def test_a_late_login_does_not_overwrite_the_login_of_a_newer_assignment(device, call):
    """付け直した後の新しい割り当てのログイン記録を、古い割り当ての遅れた照会で上書きしない。

    : 置き換えは、初めに読んだ記録の版 (`last_op_id`。無ければ行が無いこと) を条件にする。
    """
    newer = "01JBZ0000000000000ASSIGN0"
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    def newer_login():
        device.ledger.put_item(
            Item={
                "PK": ledger.user_pk(device.ids.user_id),
                "SK": ledger.login_sk(ACCOUNT),
                "assignment_id": newer,
                "assignment_valid_from": iso(NOW - timedelta(hours=1)),
                "device_period_id": PERIOD_ID,
                "logged_in_at": iso(NOW),
                "expires_at": device.clock.epoch + 3600,
            }
        )

    device.dynamodb.plan = [newer_login]

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 409
    assert body["error"]["code"] == "CONFLICT"
    assert login_row(device)["assignment_id"] == newer


@pytest.mark.parametrize(
    "existing",
    [
        {"assignment_valid_from": iso(NOW - timedelta(days=10)), "expires_at": int(NOW.timestamp()) + 3600},
        {"assignment_valid_from": iso(NOW + timedelta(days=1)), "expires_at": int(NOW.timestamp())},
        {"expires_at": int(NOW.timestamp()) + 3600},
    ],
)
def test_a_login_overwrites_an_older_or_expired_record(device, call, existing):
    """前の割り当てか、期限の切れた記録なら上書きする (割り当ての作成の順ではなく、有効期間の始まりで比べる)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device.ledger.put_item(
        Item={
            "PK": ledger.user_pk(device.ids.user_id),
            "SK": ledger.login_sk(ACCOUNT),
            "assignment_id": "01JBZ0000000000000000OLDER",
            "device_period_id": OLD_PERIOD_ID,
            "logged_in_at": iso(NOW - timedelta(days=5)),
            **existing,
        }
    )

    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    row = login_row(device)
    assert row["assignment_id"] == ASSIGNMENT_ID
    # : 根拠の始まり (assignment_valid_from) は書かず、置き換えは読んだ記録の版を条件にする
    assert "assignment_valid_from" not in row and row["basis"] == "user"
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200


# =============================================================================
# POST /devices/login: 割り当て (条件 (1)(2)) は device_backend より先
# =============================================================================


def test_login_without_an_assignment_does_not_ask_the_device_backend_backend(aws, clock, device_backend, call):
    """割り当ての無い人は暗証番号を試せない (総当たりできない。判断 9)。"""
    aws.put_user()
    put_period(aws)
    put_assignment(aws, user_id=OTHER_USER_ID)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert device_backend.calls == []
    assert [(row["action"], row["result"], row.get("code")) for row in aws.audit_items()] == [
        ("device.login", "denied", "DEVICE_NOT_ASSIGNED")
    ]


def test_login_without_an_active_period_is_not_assigned(aws, clock, device_backend, call):
    aws.put_user()
    put_period(aws, status="ended", current=False)
    put_assignment(aws)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert device_backend.calls == []


def test_an_ended_assignment_cannot_log_in_even_with_the_right_pin(device, call):
    """割り当てを終えると、同じ暗証番号でも入れない (計画 4.5 の確認)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device.ledger.update_item(
        Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.assignment_sk(device.ids.user_id, ASSIGNMENT_ID)},
        UpdateExpression="SET valid_to = :t",
        ExpressionAttributeValues={":t": iso(NOW - timedelta(hours=1))},
    )

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert device.device_backend.calls == []


def test_an_assignment_of_the_previous_period_cannot_log_in(aws, clock, device_backend, call):
    aws.put_user()
    put_period(aws, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_assignment(aws, period_id=OLD_PERIOD_ID)
    put_period(aws)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert device_backend.calls == []


def test_a_suspended_user_cannot_log_in(aws, clock, device_backend, call):
    aws.put_user(status="suspended")
    put_period(aws)
    put_assignment(aws)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "USER_SUSPENDED"
    assert device_backend.calls == []


@pytest.mark.parametrize("admin_state", ["pending", "awaiting"])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/me/assignments", None),
        ("POST", "/devices/login", LOGIN_BODY),
        ("POST", "/devices/refresh", {"account": ACCOUNT, "serial": SERIAL, "pin": PIN}),
        ("GET", f"/devices/{ACCOUNT}/refresh-availability", None),
        ("GET", f"/devices/{ACCOUNT}/services", None),
    ],
)
def test_an_admin_who_is_not_active_cannot_use_the_device_apis(
    aws, clock, device_backend, call, admin_state, method, path, body
):
    """準備中・確認待ちの admin は、例外 1・2 のほかは 403 (ADR D9)。割り当てがあっても通信機を扱えない。"""
    aws.put_admin(admin_state=admin_state)
    put_period(aws)
    put_assignment(aws)
    put_grant(aws)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, response = call(method, path, groups=("admin",), body=body)

    assert status == 403
    assert response["error"]["code"] == "ADMIN_NOT_ACTIVE"
    assert device_backend.calls == []


def test_an_active_admin_can_log_in_to_an_assigned_device(aws, clock, device_backend, call):
    """admin も自分を割り当ててから通信機ログインする (判断 9)。"""
    aws.put_admin(admin_state="active", mfa_enabled_at=1_762_000_000)
    put_period(aws)
    put_assignment(aws)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, _body = call("POST", "/devices/login", groups=("admin",), body=LOGIN_BODY)

    assert status == 200


# =============================================================================
# POST /devices/login: 入力の検査
# =============================================================================


@pytest.mark.parametrize(
    "changes",
    [
        {"account": "1234567"},
        {"account": "123456789"},
        {"account": "１２３４５６７８"},
        {"serial": "8765432"},
        {"serial": None},
        {"pin": "999"},
        {"pin": "99999"},
        {"pin": "９９９９"},
        {"pin": 9999},
        {"mode": "other"},
    ],
)
def test_login_rejects_malformed_input_without_echoing_it(device, call, changes):
    body = {**LOGIN_BODY, **changes}

    status, response = call("POST", "/devices/login", body=body)

    assert status == 400
    assert response["error"]["code"] == "VALIDATION_ERROR"
    assert device.device_backend.calls == []
    assert_pin_is_nowhere(device, response)


def test_login_rejects_a_body_that_is_not_an_object(device, call):
    status, response = call("POST", "/devices/login", body=[1, 2])

    assert status == 400
    assert response["error"]["code"] == "VALIDATION_ERROR"


def test_login_mode_defaults_to_previous(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    body = {key: value for key, value in LOGIN_BODY.items() if key != "mode"}

    status, response = call("POST", "/devices/login", body=body)

    assert status == 200
    assert response["data"]["method"] == "previous"


# =============================================================================
# POST /devices/login: 照会の失敗と、失敗回数 (10 分に 5 回で 10 分止める)
# =============================================================================


def test_a_wrong_pin_is_403_pin_mismatch_and_counted(device, call):
    """401 にすると SPA がサインアウトするので 403 にそろえる (計画 2.6)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 401, {"message": "x"})

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "PIN_MISMATCH"
    row = pinfail_row(device)
    assert int(row["count"]) == 1
    assert int(row["window_started_at"]) == device.clock.epoch
    assert "locked_until" not in row
    assert int(row["ttl"]) > device.clock.epoch + 1200
    assert login_row(device) is None
    assert actions(device) == [("device.login", "denied", "PIN_MISMATCH")]
    assert_pin_is_nowhere(device, body)


def test_a_wrong_serial_is_403_serial_mismatch_and_counted(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 403, {"message": "x"})

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 403
    assert body["error"]["code"] == "SERIAL_MISMATCH"
    assert int(pinfail_row(device)["count"]) == 1
    assert actions(device) == [("device.login", "denied", "SERIAL_MISMATCH")]


def test_five_failures_in_ten_minutes_lock_the_device_for_ten_minutes(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(4):
        assert call("POST", "/devices/login", body=LOGIN_BODY)[1]["error"]["code"] == "PIN_MISMATCH"
        device.clock.advance(60)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)
    assert status == 403
    assert body["error"]["code"] == "PIN_MISMATCH"
    assert int(pinfail_row(device)["locked_until"]) == device.clock.epoch + 600

    device.device_backend.calls.clear()
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    status, body = call("POST", "/devices/login", body=LOGIN_BODY)
    assert status == 429
    assert body["error"]["code"] == "PIN_LOCKED"
    assert device.device_backend.calls == []
    assert login_row(device) is None
    assert actions(device).count(("device.pin_locked", "ok", None)) == 1
    assert actions(device).count(("device.login", "denied", "PIN_LOCKED")) == 1
    assert_pin_is_nowhere(device, body)


def test_the_lock_ends_after_ten_minutes(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(5):
        call("POST", "/devices/login", body=LOGIN_BODY)
    device.clock.advance(599)
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 429

    device.clock.advance(1)
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    status, _body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 200
    assert pinfail_row(device) is None


def test_failures_outside_the_window_start_a_new_count(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(4):
        call("POST", "/devices/login", body=LOGIN_BODY)
    device.clock.advance(600)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert body["error"]["code"] == "PIN_MISMATCH"
    row = pinfail_row(device)
    assert int(row["count"]) == 1
    assert int(row["window_started_at"]) == device.clock.epoch
    assert "locked_until" not in row


def test_a_success_clears_the_failures(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(4):
        call("POST", "/devices/login", body=LOGIN_BODY)
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200
    assert pinfail_row(device) is None

    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(4):
        assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 403
    assert "locked_until" not in pinfail_row(device)


def test_the_lock_is_per_device_account(device, call):
    """失敗回数は通信機アカウントごと。ほかの通信機の照会は止めない。"""
    other = "23456789"
    put_period(device, period_id="01JBZ0000000000000PERIOD2", account=other)
    put_assignment(
        device, period_id="01JBZ0000000000000PERIOD2", assignment_id="01JBZ0000000000000ASSIGN9", account=other
    )
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(5):
        call("POST", "/devices/login", body=LOGIN_BODY)
    device.device_backend.on("GET", f"/api/v2/accounts/{other}/settings", 200, SETTINGS_PAYLOAD)

    status, _body = call("POST", "/devices/login", body={**LOGIN_BODY, "account": other})

    assert status == 200


@pytest.mark.parametrize("device_backend_status", [404, 500, 503])
def test_other_device_backend_failures_are_502_and_not_counted(device, call, device_backend_status):
    device.device_backend.on("GET", SETTINGS_PATH, device_backend_status)

    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 502
    assert body["error"]["code"] == "DEVICE_BACKEND_UNAVAILABLE"
    assert pinfail_row(device) is None
    assert actions(device) == [("device.login", "failed", "DEVICE_BACKEND_UNAVAILABLE")]
    assert_pin_is_nowhere(device, body)


def test_an_unreachable_device_backend_backend_is_502(device, call):
    status, body = call("POST", "/devices/login", body=LOGIN_BODY)

    assert status == 502
    assert body["error"]["code"] == "DEVICE_BACKEND_UNAVAILABLE"
    assert_pin_is_nowhere(device, body)


# =============================================================================
# 最新値: POST /devices/refresh と、待機中の照会 (mode=latest)
# =============================================================================


REFRESH_BODY = {"account": ACCOUNT, "serial": SERIAL, "pin": PIN}
OTHER_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
POLL_GAP = 8


def start_refresh(device, call, *, sub=None, body=None):
    """前回値で暗証番号を確かめて受け付ける。応答の data (poll_token を含む) を返す。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device.device_backend.on("POST", STATUS_PATH, 202)
    device.device_backend.on("POST", SETTINGS_PATH, 202)
    kwargs = {"sub": sub} if sub else {}
    return call("POST", "/devices/refresh", body=body or REFRESH_BODY, **kwargs)


def poll(device, call, started, *, pin=PIN, advance=POLL_GAP, **overrides):
    """待機中の照会 (mode=latest)。受付の応答の acceptance_id と poll_token を付ける。"""
    device.clock.advance(advance)
    body = {**LOGIN_BODY, "pin": pin, "mode": "latest"}
    if started is not None:
        body.update(acceptance_id=started["acceptance_id"], poll_token=started["poll_token"])
    body.update(overrides)
    return call("POST", "/devices/login", body={key: value for key, value in body.items() if value is not None})


def assert_secrets_are_nowhere(device, started, *bodies) -> None:
    assert_pin_is_nowhere(device, *bodies)
    texts = [device.caplog.text, str(device.audit_items()), str(device.ledger.scan()["Items"])]
    for text in texts:
        assert started["poll_token"] not in text


def test_refresh_start_checks_the_pin_with_the_previous_settings_first(device, call):
    status, body = start_refresh(device, call)

    assert status == 202
    data = body["data"]
    assert data["account"] == ACCOUNT
    assert data["accepted_at"] == iso(NOW)
    assert data["expires_at"] == iso(NOW + timedelta(minutes=10))
    assert data["timeout_seconds"] == 600
    assert data["resumed"] is False
    assert len(data["poll_token"]) >= 43
    assert data["acceptance_id"]
    calls = [(sent["method"], sent["path"]) for sent in device.device_backend.calls]
    assert calls == [("GET", SETTINGS_PATH), ("POST", STATUS_PATH), ("POST", SETTINGS_PATH)]
    assert device.device_backend.calls[0]["headers"]["x-example-pin"] == PIN
    for sent in device.device_backend.calls[1:]:
        assert sent["query"] == {"serial": SERIAL}
        assert "x-example-pin" not in sent["headers"]
        assert sent["headers"]["x-example-user"] == "service-platform"
    row = refresh_row(device)
    assert row["accepted_by"] == device.ids.user_id
    assert row["acceptance_id"] == data["acceptance_id"]
    assert int(row["expires_at"]) == device.clock.epoch + 600
    assert len(row["pin_mac"]) == 64
    assert login_row(device) is None
    assert actions(device) == [("device.refresh_start", "ok", None)]
    assert_secrets_are_nowhere(device, data, body)


def test_refresh_start_requires_the_pin(device, call):
    status, body = call("POST", "/devices/refresh", body={"account": ACCOUNT, "serial": SERIAL})

    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert device.device_backend.calls == []


def test_refresh_start_with_a_wrong_pin_is_counted_and_does_not_ask_the_device(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)

    status, body = call("POST", "/devices/refresh", body=REFRESH_BODY)

    assert status == 403
    assert body["error"]["code"] == "PIN_MISMATCH"
    assert [sent["method"] for sent in device.device_backend.calls] == ["GET"]
    assert int(pinfail_row(device)["count"]) == 1
    assert refresh_row(device) is None
    assert actions(device) == [("device.refresh_start", "denied", "PIN_MISMATCH")]
    assert_pin_is_nowhere(device, body)


def test_refresh_start_without_previous_settings_is_409(device, call):
    """前回値の無い通信機は、通信機が一度報告した後に最新値を使える。"""
    device.device_backend.on("GET", SETTINGS_PATH, 404)

    status, body = call("POST", "/devices/refresh", body=REFRESH_BODY)

    assert status == 409
    assert body["error"]["code"] == "NO_PREVIOUS_SETTINGS"
    assert pinfail_row(device) is None
    assert refresh_row(device) is None


def test_refresh_start_again_by_the_same_user_resumes_with_a_new_token(device, call):
    """本人の受付が期限内なら、読取り要求を送り直さずに、新しい poll_token で待機を再開する。"""
    first = start_refresh(device, call)[1]["data"]
    device.device_backend.calls.clear()
    device.clock.advance(30)

    status, body = start_refresh(device, call)

    assert status == 202
    second = body["data"]
    assert second["resumed"] is True
    assert second["accepted_at"] == first["accepted_at"]
    assert second["acceptance_id"] != first["acceptance_id"]
    assert second["poll_token"] != first["poll_token"]
    assert [sent["method"] for sent in device.device_backend.calls] == ["GET"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    poll(device, call, first)
    assert int(pinfail_row(device)["count"]) == 1
    poll(device, call, second)
    assert int(pinfail_row(device)["count"]) == 1


def test_refresh_start_by_another_user_is_409_in_progress(device, call):
    put_other_user(device)
    start_refresh(device, call)
    device.device_backend.calls.clear()

    status, body = start_refresh(device, call, sub=OTHER_SUB)

    assert status == 409
    assert body["error"]["code"] == "IN_PROGRESS"
    assert device.device_backend.calls == []


def test_refresh_start_after_the_record_expires_is_accepted_again(device, call):
    start_refresh(device, call)
    device.clock.advance(600)

    status, body = start_refresh(device, call)

    assert status == 202
    assert body["data"]["resumed"] is False


def test_refresh_start_is_refused_while_in_operation(device, call):
    """固定の 48 時間以内に画像が届いていれば運用中 (カメラ §5.5)。通信機と通信しない。"""
    put_camera(device, hours_ago=47)

    status, body = start_refresh(device, call)

    assert status == 409
    assert body["error"]["code"] == "IN_OPERATION"
    assert device.device_backend.calls == []
    assert refresh_row(device) is None


def test_refresh_start_is_allowed_when_the_last_image_is_older_than_48_hours(device, call):
    put_camera(device, hours_ago=49)
    put_camera(device, address="12", hours_ago=100)

    assert start_refresh(device, call)[0] == 202


@pytest.mark.parametrize("break_guard", ["empty_parameter", "missing_parameter", "unreadable_row", "missing_table"])
def test_refresh_start_fails_closed_when_the_guard_cannot_read(device, call, monkeypatch, break_guard):
    """外部のデバイス活動台帳を読めなければ不可 (判断 4、fail-closed)。"""
    import boto3

    if break_guard == "empty_parameter":
        monkeypatch.setenv("DEVICE_ACTIVITY_TABLE_PARAMETER", "")
    elif break_guard == "missing_parameter":
        boto3.client("ssm", region_name="ap-northeast-1").delete_parameter(
            Name="/service-platform/test/devices/activity-table-name"
        )
    elif break_guard == "unreadable_row":
        put_camera(device, last_seen="")
    else:
        device.cameras.delete()

    status, body = start_refresh(device, call)

    assert status == 503
    assert body["error"]["code"] == "GUARD_UNAVAILABLE"
    assert device.device_backend.calls == []


def test_refresh_start_requires_an_assignment(aws, clock, device_backend, call):
    aws.put_user()
    put_period(aws)

    status, body = call("POST", "/devices/refresh", body=REFRESH_BODY)

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert device_backend.calls == []


@pytest.mark.parametrize(
    ("device_backend_status", "code", "status", "counted"),
    [(403, "SERIAL_MISMATCH", 403, 1), (404, "NO_RESPONSE", 404, None)],
)
def test_refresh_start_failures_release_the_record(device, call, device_backend_status, code, status, counted):
    """製造番号の違い (403) は照会と同じく失敗回数に数える (製造番号を総当たりで確かめる道を残さない)。"""
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device.device_backend.on("POST", STATUS_PATH, device_backend_status)

    result, body = call("POST", "/devices/refresh", body=REFRESH_BODY)

    assert result == status
    assert body["error"]["code"] == code
    assert refresh_row(device) is None
    row = pinfail_row(device)
    assert (int(row["count"]) if row else None) == counted


def test_refresh_start_is_refused_while_the_pin_is_locked(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    for _ in range(5):
        call("POST", "/devices/login", body=LOGIN_BODY)
    device.device_backend.calls.clear()

    status, body = start_refresh(device, call)

    assert status == 429
    assert body["error"]["code"] == "PIN_LOCKED"
    assert device.device_backend.calls == []


def test_waiting_polls_with_the_same_pin_are_not_counted_up_to_the_limit(device, call):
    """待機中の照会の 401 は「報告がまだ」と「暗証番号の誤り」を区別できない (カメラ §5.5)。

    受付の時に確かめた暗証番号と同じ照会 (HMAC が一致) だけを、受付ごとに 65 回まで数えない。
    """
    from base_api import refresh_guard

    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)

    for _ in range(refresh_guard.MAX_POLLS):
        status, body = poll(device, call, started)
        assert status == 403
        assert body["error"]["code"] == "PIN_MISMATCH"
    assert pinfail_row(device) is None
    assert int(refresh_row(device)["polls"]) == refresh_guard.MAX_POLLS

    poll(device, call, started)
    assert int(pinfail_row(device)["count"]) == 1
    assert_secrets_are_nowhere(device, started)


def test_waiting_polls_with_other_pins_are_counted_and_lock_at_the_fifth(device, call):
    """暗証番号を変えた照会は免除しない (受付の後に候補を変えて試させない)。"""
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)

    for candidate in ("1111", "2222", "3333", "4444"):
        assert poll(device, call, started, pin=candidate)[0] == 403
    assert int(pinfail_row(device)["count"]) == 4
    assert poll(device, call, started, pin="5555")[0] == 403
    assert "locked_until" in pinfail_row(device)

    status, body = poll(device, call, started, pin="6666")
    assert status == 429
    assert body["error"]["code"] == "PIN_LOCKED"
    assert_secrets_are_nowhere(device, started)


@pytest.mark.parametrize(
    "overrides",
    [
        {"poll_token": None, "acceptance_id": None},
        {"poll_token": None},
        {"acceptance_id": "01JBZ000000000000000OTHER0"},
        {"poll_token": "x" * 43},
    ],
)
def test_waiting_polls_without_the_matching_token_are_counted(device, call, overrides):
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)

    poll(device, call, started, **overrides)

    assert int(pinfail_row(device)["count"]) == 1


def test_waiting_polls_of_another_user_are_counted(device, call):
    """受付の本人のほかは、トークンを持っていても数える。"""
    put_other_user(device)
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    device.clock.advance(POLL_GAP)
    body = {**LOGIN_BODY, "mode": "latest", **{k: started[k] for k in ("acceptance_id", "poll_token")}}

    status, _body = call("POST", "/devices/login", sub=OTHER_SUB, body=body)

    assert status == 403
    assert int(pinfail_row(device)["count"]) == 1


def test_waiting_polls_closer_than_eight_seconds_are_429(device, call):
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)
    poll(device, call, started)
    device.device_backend.calls.clear()

    status, body = poll(device, call, started, advance=POLL_GAP - 1)

    assert status == 429
    assert body["error"]["code"] == "POLL_TOO_SOON"
    assert device.device_backend.calls == []
    assert pinfail_row(device) is None


def test_waiting_polls_are_counted_after_the_record_expires(device, call):
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 401)

    poll(device, call, started, advance=600)

    assert int(pinfail_row(device)["count"]) == 1


def test_the_poll_budget_covers_ten_minutes_of_ten_second_polls_only():
    """待機の上限 10 分を 10 秒ごとに照会する分 (60 回) と少しの余裕だけ。"""
    from base_api import refresh_guard

    assert 60 <= refresh_guard.MAX_POLLS <= 66
    assert refresh_guard.MIN_POLL_INTERVAL_SECONDS == 8


def test_latest_mode_without_a_refresh_record_is_counted(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 401)

    call("POST", "/devices/login", body={**LOGIN_BODY, "mode": "latest"})

    assert int(pinfail_row(device)["count"]) == 1


def test_a_serial_mismatch_while_waiting_is_counted(device, call):
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 403)

    poll(device, call, started)

    assert int(pinfail_row(device)["count"]) == 1


def test_the_latest_login_clears_its_own_refresh_record(device, call):
    started = start_refresh(device, call)[1]["data"]
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, body = poll(device, call, started)

    assert status == 200
    assert body["data"]["method"] == "latest"
    assert login_row(device)["method"] == "latest"
    assert refresh_row(device) is None
    assert_secrets_are_nowhere(device, started, body)


@pytest.mark.parametrize("overrides", [{"poll_token": None, "acceptance_id": None}, {}])
def test_a_latest_login_without_its_acceptance_keeps_the_refresh_record(device, call, overrides):
    """受付に対応しない (トークンの合わない) 照会の成功では、受付の記録を消さない。"""
    start_refresh(device, call)
    other = {"acceptance_id": refresh_row(device)["acceptance_id"], "poll_token": "y" * 43}
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    status, _body = poll(device, call, other, **overrides)

    assert status == 200
    assert refresh_row(device) is not None


def put_other_user(aws):
    """同じ利用期間に割り当てられた、もう 1 人の有効な利用者。"""
    aws.ledger.put_item(
        Item={
            "PK": ledger.user_pk(OTHER_USER_ID),
            "SK": ledger.PROFILE_SK,
            "user_id": OTHER_USER_ID,
            "sub": OTHER_SUB,
            "email": "other@example.com",
            "status": "active",
            "kind": "customer",
            "role": "user",
        }
    )
    aws.ledger.put_item(Item={"PK": ledger.sub_pk(OTHER_SUB), "SK": ledger.SUB_SK, "user_id": OTHER_USER_ID})
    put_assignment(aws, user_id=OTHER_USER_ID, assignment_id="01JBZ0000000000000ASSIGN8")


# =============================================================================
# GET /devices/{account}/refresh-availability
# =============================================================================


def test_refresh_availability_when_available(device, call):
    status, body = call("GET", f"/devices/{ACCOUNT}/refresh-availability")

    assert status == 200
    assert body["data"] == {"account": ACCOUNT, "available": True, "reason": None, "in_progress": False}


def test_refresh_availability_in_operation_and_in_progress(device, call):
    start_refresh(device, call)
    put_camera(device, hours_ago=1)

    _status, body = call("GET", f"/devices/{ACCOUNT}/refresh-availability")

    assert body["data"] == {"account": ACCOUNT, "available": False, "reason": "in_operation", "in_progress": True}


def test_refresh_availability_fails_closed(device, call, monkeypatch):
    monkeypatch.setenv("DEVICE_ACTIVITY_TABLE_PARAMETER", "")

    _status, body = call("GET", f"/devices/{ACCOUNT}/refresh-availability")

    assert body["data"] == {"account": ACCOUNT, "available": False, "reason": "guard_unavailable", "in_progress": False}


def test_refresh_availability_does_not_answer_without_an_assignment(aws, clock, call):
    """運用中かを第三者に知らせない (カメラ §16 31)。"""
    aws.put_user()
    put_period(aws)
    put_camera(aws, hours_ago=1)

    status, body = call("GET", f"/devices/{ACCOUNT}/refresh-availability")

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"
    assert "in_operation" not in str(body)


def test_refresh_availability_rejects_a_malformed_account(device, call):
    status, body = call("GET", "/devices/1234/refresh-availability")

    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"


# =============================================================================
# GET /devices/{account}/services (条件 (1)(2)(3))
# =============================================================================


def login(device, call):
    device.device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    assert call("POST", "/devices/login", body=LOGIN_BODY)[0] == 200


def test_services_lists_the_granted_services_with_their_launch_urls(device, call):
    login(device, call)

    status, body = call("GET", f"/devices/{ACCOUNT}/services")

    assert status == 200
    data = body["data"]
    assert data["account"] == ACCOUNT
    assert data["org_name"] == "組織A"
    assert data["location_name"] == "見本の現場"
    assert data["method"] == "previous"
    assert data["logged_in_at"] == iso(NOW)
    assert data["expires_at"] == iso(NOW + timedelta(hours=12))
    assert data["services"] == [
        {
            "id": "camera",
            "name": "カメラ画像判定",
            "url": f"/camera/?account={ACCOUNT}",
            "starts_at": iso(NOW - timedelta(days=2)),
            "ends_at": None,
        }
    ]
    assert SERIAL not in str(body)


def test_services_requires_a_login_record(device, call):
    status, body = call("GET", f"/devices/{ACCOUNT}/services")

    assert status == 403
    assert body["error"]["code"] == "DEVICE_LOGIN_REQUIRED"


def test_services_after_the_login_expires_requires_a_new_login(device, call):
    login(device, call)
    device.clock.advance(TWELVE_HOURS)

    status, body = call("GET", f"/devices/{ACCOUNT}/services")

    assert status == 403
    assert body["error"]["code"] == "DEVICE_LOGIN_REQUIRED"


def test_services_without_a_grant_is_an_empty_list(device, call):
    login(device, call)
    device.ledger.delete_item(Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.grant_sk("camera", GRANT_ID)})

    status, body = call("GET", f"/devices/{ACCOUNT}/services")

    assert status == 200
    assert body["data"]["services"] == []


def test_services_after_the_assignment_ends_is_not_assigned(device, call):
    login(device, call)
    device.ledger.update_item(
        Key={"PK": ledger.dperiod_pk(PERIOD_ID), "SK": ledger.assignment_sk(device.ids.user_id, ASSIGNMENT_ID)},
        UpdateExpression="SET valid_to = :t",
        ExpressionAttributeValues={":t": iso(NOW)},
    )

    status, body = call("GET", f"/devices/{ACCOUNT}/services")

    assert status == 403
    assert body["error"]["code"] == "DEVICE_NOT_ASSIGNED"


def test_services_rejects_a_malformed_account(device, call):
    status, body = call("GET", "/devices/abc/services")

    assert status == 400
    assert body["error"]["code"] == "VALIDATION_ERROR"
