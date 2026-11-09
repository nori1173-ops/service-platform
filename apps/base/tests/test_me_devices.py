"""`GET /me/devices` と、`GET /me/assignments` のグループ宛 (ADR D13 / D14)。

一覧は表示だけで、認可には使わない (通信機ログインは強い整合の条件 (1)〜(3) で確かめ直す)。everyone の通信機は
稼働中の利用期間の指し (GSI1 の DEVICE。結果整合) から出す。同じ通信機は 1 行 (割り当てを優先)。製造番号は、人宛は
割り当ての行、グループ宛と everyone は本人の控え。ほかの人の控えは読まない。
"""

from datetime import timedelta

import pytest
from device_data import (
    NOW,
    OTHER_USER_ID,
    iso,
    put_assignment,
    put_grant,
    put_group,
    put_group_assignment,
    put_membership,
    put_period,
)

from service_platform import ledger, services

ROOT = "01JBZ0000000000000GROUPRT"
CHILD = "01JBZ0000000000000GROUPCH"
M_CHILD = "01JBZ000000000000MEMBERCH"
EVERYONE_ACCOUNT = "10000001"
USER_ACCOUNT = "10000002"
GROUP_ACCOUNT = "10000003"
ASSIGNED_ACCOUNT = "10000004"
BOTH_ACCOUNT = "10000005"


def period_id(account):
    return f"01JBZ00000000000000P{account[-6:]}"


@pytest.fixture
def listed(aws, clock):
    aws.put_user()
    put_period(aws, period_id=period_id(EVERYONE_ACCOUNT), account=EVERYONE_ACCOUNT, access=None, org_name="組織E")
    put_period(aws, period_id=period_id(USER_ACCOUNT), account=USER_ACCOUNT, access="assigned", org_name="組織U")
    put_assignment(
        aws,
        period_id=period_id(USER_ACCOUNT),
        account=USER_ACCOUNT,
        assignment_id="01JBZ00000000000000ASSIGNU",
        serial="87654321",
        org_name="組織U",
    )
    put_period(aws, period_id=period_id(GROUP_ACCOUNT), account=GROUP_ACCOUNT, access="assigned", org_name="組織G")
    put_group(aws, ROOT, "会社A", [ROOT])
    put_group(aws, CHILD, "部署B", [ROOT, CHILD])
    put_membership(aws, CHILD, M_CHILD, [ROOT, CHILD])
    put_group_assignment(
        aws, ROOT, assignment_id="01JBZ00000000000000ASSIGNG", period_id=period_id(GROUP_ACCOUNT), account=GROUP_ACCOUNT
    )
    put_period(aws, period_id=period_id(ASSIGNED_ACCOUNT), account=ASSIGNED_ACCOUNT, access="assigned")
    put_period(aws, period_id=period_id(BOTH_ACCOUNT), account=BOTH_ACCOUNT, access="everyone")
    put_assignment(
        aws, period_id=period_id(BOTH_ACCOUNT), account=BOTH_ACCOUNT, assignment_id="01JBZ00000000000000ASSIGNB"
    )
    return aws


def save_serial(aws, account, serial, user_id=None):
    aws.ledger.put_item(
        Item={
            "PK": ledger.user_pk(user_id or aws.ids.user_id),
            "SK": ledger.serial_sk(period_id(account)),
            "account": account,
            "device_period_id": period_id(account),
            "serial": serial,
            "saved_at": iso(NOW),
        }
    )


def test_devices_lists_assignments_then_everyone_one_row_per_device(listed, call):
    status, body = call("GET", "/me/devices")

    assert status == 200, body
    rows = body["data"]["devices"]
    assert [(row["account"], row["basis"]) for row in rows] == [
        (USER_ACCOUNT, "user"),
        (GROUP_ACCOUNT, "group"),
        (BOTH_ACCOUNT, "user"),
        (EVERYONE_ACCOUNT, "everyone"),
    ]
    user_row, group_row, _both, everyone_row = rows
    assert user_row["serial"] == "87654321" and user_row["org_name"] == "組織U"
    assert user_row["assignment_id"] == "01JBZ00000000000000ASSIGNU"
    assert group_row["group_id"] == ROOT and group_row["group_label"] == "会社A"
    assert group_row["assignment_id"] == "01JBZ00000000000000ASSIGNG" and group_row["serial"] is None
    assert everyone_row == {
        "account": EVERYONE_ACCOUNT,
        "device_period_id": period_id(EVERYONE_ACCOUNT),
        "org_name": "組織E",
        "basis": "everyone",
        "assignment_id": None,
        "group_id": None,
        "group_label": None,
        "valid_from": None,
        "valid_to": None,
        "serial": None,
        "service_count": 0,
    }


def test_devices_shows_only_the_callers_own_saved_serial(listed, call):
    save_serial(listed, EVERYONE_ACCOUNT, "11112222")
    save_serial(listed, GROUP_ACCOUNT, "33334444", user_id=OTHER_USER_ID)

    rows = {row["account"]: row for row in call("GET", "/me/devices")[1]["data"]["devices"]}

    assert rows[EVERYONE_ACCOUNT]["serial"] == "11112222"
    assert rows[GROUP_ACCOUNT]["serial"] is None


@pytest.fixture
def ledger_reads(monkeypatch):
    """台帳の GetItem の SK と、query_prefix の接頭辞を記録する。"""
    reads = {"get": [], "prefix": []}
    real_get, real_query = ledger._get, ledger.query_prefix

    def get(pk, sk):
        reads["get"].append(sk)
        return real_get(pk, sk)

    def query(pk, prefix):
        reads["prefix"].append(prefix)
        return real_query(pk, prefix)

    monkeypatch.setattr(ledger, "_get", get)
    monkeypatch.setattr(ledger, "query_prefix", query)
    return reads


def test_devices_reads_the_saved_serials_with_one_query(listed, call, ledger_reads):
    """控えは本人の `SERIAL#` を 1 回の Query で読んで引く (通信機ごとに GetItem しない。グループ宛も同じ辞書)。"""
    save_serial(listed, EVERYONE_ACCOUNT, "11112222")
    save_serial(listed, GROUP_ACCOUNT, "33334444")

    rows = {row["account"]: row for row in call("GET", "/me/devices")[1]["data"]["devices"]}

    assert rows[EVERYONE_ACCOUNT]["serial"] == "11112222"
    assert rows[GROUP_ACCOUNT]["serial"] == "33334444"
    assert not [sk for sk in ledger_reads["get"] if sk.startswith(ledger.SERIAL_PREFIX)]
    assert ledger_reads["prefix"].count(ledger.SERIAL_PREFIX) == 1


def test_assignments_read_the_group_serials_with_one_query(listed, call, ledger_reads):
    save_serial(listed, GROUP_ACCOUNT, "33334444")

    rows = {row["account"]: row for row in call("GET", "/me/assignments")[1]["data"]["assignments"]}

    assert rows[GROUP_ACCOUNT]["serial"] == "33334444"
    assert not [sk for sk in ledger_reads["get"] if sk.startswith(ledger.SERIAL_PREFIX)]
    assert ledger_reads["prefix"].count(ledger.SERIAL_PREFIX) == 1


def test_devices_hides_ended_periods_and_ended_or_future_assignments(aws, clock, call):
    aws.put_user()
    put_period(aws, period_id=period_id(EVERYONE_ACCOUNT), account=EVERYONE_ACCOUNT, status="ended", current=False)
    put_period(aws, period_id=period_id(USER_ACCOUNT), account=USER_ACCOUNT, access="assigned")
    put_assignment(
        aws,
        period_id=period_id(USER_ACCOUNT),
        account=USER_ACCOUNT,
        valid_from=iso(NOW + timedelta(days=1)),
    )

    assert call("GET", "/me/devices")[1]["data"]["devices"] == []


def test_devices_shows_a_nested_group_label(aws, clock, call):
    aws.put_user()
    put_period(aws, period_id=period_id(GROUP_ACCOUNT), account=GROUP_ACCOUNT, access="assigned")
    put_group(aws, ROOT, "会社A", [ROOT])
    put_group(aws, CHILD, "部署B", [ROOT, CHILD])
    put_membership(aws, CHILD, M_CHILD, [ROOT, CHILD])
    put_group_assignment(
        aws,
        CHILD,
        assignment_id="01JBZ00000000000000ASSIGNG",
        period_id=period_id(GROUP_ACCOUNT),
        account=GROUP_ACCOUNT,
    )

    (row,) = call("GET", "/me/devices")[1]["data"]["devices"]

    assert row["group_label"] == "会社A > 部署B"


def grant(aws, account, grant_id, *, service="camera", **fields):
    put_grant(aws, period_id=period_id(account), service=service, grant_id=grant_id, **fields)


def test_devices_count_the_services_granted_now_on_each_device(listed, call):
    """の近道の判定に使う。今有効な許可のサービスの数 (同じサービスは 1 つ、定義に無いサービスは数えない)。"""
    grant(listed, USER_ACCOUNT, "01JBZ0000000000000GRANTU1")
    grant(listed, GROUP_ACCOUNT, "01JBZ0000000000000GRANTG1")
    grant(listed, GROUP_ACCOUNT, "01JBZ0000000000000GRANTG2")
    grant(listed, EVERYONE_ACCOUNT, "01JBZ0000000000000GRANTE1")
    grant(listed, BOTH_ACCOUNT, "01JBZ0000000000000GRANTB1", starts_at=iso(NOW + timedelta(days=1)))
    grant(listed, BOTH_ACCOUNT, "01JBZ0000000000000GRANTB2", ends_at=iso(NOW - timedelta(days=1)))
    grant(listed, BOTH_ACCOUNT, "01JBZ0000000000000GRANTB3", status="ended")
    grant(listed, BOTH_ACCOUNT, "01JBZ0000000000000GRANTB4", service="unknown")

    rows = {row["account"]: row for row in call("GET", "/me/devices")[1]["data"]["devices"]}

    assert rows[USER_ACCOUNT]["service_count"] == 1
    assert rows[GROUP_ACCOUNT]["service_count"] == 1
    assert rows[EVERYONE_ACCOUNT]["service_count"] == 1
    assert rows[BOTH_ACCOUNT]["service_count"] == 0


def test_devices_count_each_defined_service_once(listed, call, monkeypatch):
    monkeypatch.setitem(services.SERVICES, "other", services.Service(id="other", name="別のサービス", path="/other/"))
    grant(listed, USER_ACCOUNT, "01JBZ0000000000000GRANTU1")
    grant(listed, USER_ACCOUNT, "01JBZ0000000000000GRANTU2", service="other")
    grant(listed, EVERYONE_ACCOUNT, "01JBZ0000000000000GRANTE1")
    grant(listed, EVERYONE_ACCOUNT, "01JBZ0000000000000GRANTE2", service="other")

    rows = {row["account"]: row for row in call("GET", "/me/devices")[1]["data"]["devices"]}

    assert rows[USER_ACCOUNT]["service_count"] == 2
    assert rows[EVERYONE_ACCOUNT]["service_count"] == 2


def test_devices_requires_an_active_user(aws, clock, call):
    aws.put_user(status="suspended")

    assert call("GET", "/me/devices")[1]["error"]["code"] == "USER_SUSPENDED"


def test_assignments_include_group_assignments(listed, call):
    rows = call("GET", "/me/assignments")[1]["data"]["assignments"]

    by_account = {row["account"]: row for row in rows}
    assert by_account[GROUP_ACCOUNT]["target_type"] == "group"
    assert by_account[GROUP_ACCOUNT]["group_id"] == ROOT
    assert by_account[GROUP_ACCOUNT]["group_label"] == "会社A"
    assert by_account[GROUP_ACCOUNT]["state"] == "current"
    assert by_account[USER_ACCOUNT]["target_type"] == "user"
    assert by_account[USER_ACCOUNT].get("group_id") is None
    assert EVERYONE_ACCOUNT not in by_account
