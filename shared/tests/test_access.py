"""`service_platform.access` のテスト（計画 2.1 / 2.4、ADR-001 D4 の条件 (1)〜(4)）。

条件 (1) 通信機アカウントに稼働中の利用期間 P がある、(2) 自分の割り当てが P に属し今が有効期間内、
(3) ログイン記録の assignment_id が (2) と同じで、ログインの時刻が valid_from 以後かつ期限内、
(4) サービスの許可が P に属し今が期間内。前の利用期間に属する割り当て・ログイン記録・許可は、
それぞれ (2)・(3)・(4) で落ちる (ADR D4 の 3 通り)。

 (ADR D13 / D14) の前からの人宛の割り当てのテスト。利用期間の既定を access=assigned にして、
割り当てが条件だった今までの振る舞いがそのまま通ることを確かめる (回帰)。根拠 (b)(c) と世代の照合は
test_access_basis.py。
"""

from datetime import UTC, datetime

import pytest

from service_platform import access, ledger
from service_platform.auth import ForbiddenError

ACCOUNT = "12345678"
OTHER_ACCOUNT = "23456789"
USER_ID = "01JBZ0000000000000000USER1"
OTHER_USER_ID = "01JBZ0000000000000000USER2"
PERIOD_ID = "01JBZ00000000000000PERIOD"
OLD_PERIOD_ID = "01JBZ0000000000000OLDPERD"
ASSIGNMENT_ID = "01JBZ0000000000000ASSIGN1"
NEW_ASSIGNMENT_ID = "01JBZ0000000000000ASSIGN2"
OLD_ASSIGNMENT_ID = "01JBZ000000000000OLDASSGN"
GRANT_ID = "01JBZ00000000000000GRANT1"

NOW = datetime(2026, 11, 4, 3, 0, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
STARTED = "2026-11-01T00:00:00Z"
EARLIER = "2026-11-02T00:00:00Z"
LATER = "2026-11-05T00:00:00Z"
NOW_ISO = "2026-11-04T03:00:00Z"


@pytest.fixture
def table(ledger_table):
    return ledger_table


def put_period(
    table, *, period_id=PERIOD_ID, account=ACCOUNT, status="active", current=True, access="assigned", **attributes
):
    item = {
        "PK": ledger.device_pk(account),
        "SK": ledger.dperiod_sk(period_id),
        "device_period_id": period_id,
        "account": account,
        "org_name": "組織A",
        "status": status,
        "started_at": STARTED,
        "version": 1,
        **attributes,
    }
    if access is not None:
        item["access"] = access
    table.put_item(Item=item)
    if current:
        table.put_item(
            Item={
                "PK": ledger.device_pk(account),
                "SK": ledger.CURRENT_SK,
                "device_period_id": period_id,
                "org_name": "組織A",
                "started_at": STARTED,
            }
        )


def put_assignment(
    table,
    *,
    period_id=PERIOD_ID,
    user_id=USER_ID,
    assignment_id=ASSIGNMENT_ID,
    account=ACCOUNT,
    valid_from=STARTED,
    valid_to=None,
    **attributes,
):
    table.put_item(
        Item={
            "PK": ledger.dperiod_pk(period_id),
            "SK": ledger.assignment_sk(user_id, assignment_id),
            "assignment_id": assignment_id,
            "device_period_id": period_id,
            "account": account,
            "user_id": user_id,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "role": "user",
            **attributes,
        }
    )


def put_grant(
    table, *, period_id=PERIOD_ID, service="camera", grant_id=GRANT_ID, starts_at=STARTED, ends_at=None, status="active"
):
    table.put_item(
        Item={
            "PK": ledger.dperiod_pk(period_id),
            "SK": ledger.grant_sk(service, grant_id),
            "grant_id": grant_id,
            "service": service,
            "account": ACCOUNT,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "status": status,
        }
    )


def put_login(
    table,
    *,
    user_id=USER_ID,
    account=ACCOUNT,
    assignment_id=ASSIGNMENT_ID,
    period_id=PERIOD_ID,
    logged_in_at=NOW_ISO,
    expires_at=NOW_EPOCH + 3600,
):
    table.put_item(
        Item={
            "PK": ledger.user_pk(user_id),
            "SK": ledger.login_sk(account),
            "assignment_id": assignment_id,
            "device_period_id": period_id,
            "logged_in_at": logged_in_at,
            "expires_at": expires_at,
            "ttl": expires_at + 86400,
            "method": "previous",
            "serial": "87654321",
            "summary": {"location_name": "現場の名前", "observation": {}, "devices": []},
        }
    )


def assert_denied(error_type, code, func, *args, **kwargs):
    with pytest.raises(error_type) as info:
        func(*args, **kwargs)
    assert isinstance(info.value, ForbiddenError)
    assert info.value.code == code
    return info.value


# =============================================================================
# 条件 (1)(2): 稼働中の利用期間と自分の割り当て
# =============================================================================


def test_require_basis_returns_the_access(table):
    put_period(table)
    put_assignment(table, serial="87654321")

    result = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert result.account == ACCOUNT
    assert result.device_period_id == PERIOD_ID
    assert result.assignment_id == ASSIGNMENT_ID
    assert result.valid_from == STARTED
    assert result.valid_to is None
    assert result.org_name == "組織A"
    assert result.serial == "87654321"
    assert result.login is None
    # 書き手が「読んだ version」を条件にするため ( の追加の裁定)
    assert result.period_version == 1


def test_without_a_current_period_it_is_not_assigned(table):
    put_period(table, current=False)
    put_assignment(table)

    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)


def test_a_current_pointer_to_an_ended_or_missing_period_is_not_assigned(table):
    put_period(table, status="ended")
    put_assignment(table)
    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)

    table.delete_item(Key={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.dperiod_sk(PERIOD_ID)})
    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)


def test_a_period_of_another_account_is_not_used(table):
    """CURRENT の指す利用期間の行の通信機アカウントが違えば使わない。"""
    put_period(table, account=OTHER_ACCOUNT)
    table.put_item(Item={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.CURRENT_SK, "device_period_id": PERIOD_ID})
    put_assignment(table)

    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)


def test_without_an_assignment_it_is_not_assigned(table):
    put_period(table)
    put_assignment(table, user_id=OTHER_USER_ID)

    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "ok"),
    [
        (STARTED, None, True),
        (STARTED, LATER, True),
        (NOW_ISO, None, True),
        (LATER, None, False),
        (STARTED, EARLIER, False),
        (STARTED, NOW_ISO, False),
    ],
)
def test_the_assignment_must_be_valid_now(table, valid_from, valid_to, ok):
    """有効期間は開始を含み、終了を含まない。"""
    put_period(table)
    put_assignment(table, valid_from=valid_from, valid_to=valid_to)

    if ok:
        assert access.require_basis(USER_ID, ACCOUNT, now=NOW).assignment_id == ASSIGNMENT_ID
    else:
        assert_denied(
            access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW
        )


def test_the_current_assignment_is_chosen_among_ended_ones(table):
    """付け直した割り当て (新しい assignment_id) があれば、終えた前の行ではなくそれを使う。"""
    put_period(table)
    put_assignment(table, valid_from=STARTED, valid_to=EARLIER)
    put_assignment(table, assignment_id=NEW_ASSIGNMENT_ID, valid_from=EARLIER)

    assert access.require_basis(USER_ID, ACCOUNT, now=NOW).assignment_id == NEW_ASSIGNMENT_ID


def test_an_assignment_of_the_previous_period_falls_at_condition_2(table):
    """ADR D4: 前の利用期間の割り当ては、終え損ねて開いたままでも (2) で落ちる。"""
    put_period(table, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_assignment(table, period_id=OLD_PERIOD_ID, assignment_id=OLD_ASSIGNMENT_ID)
    put_period(table)

    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)


@pytest.mark.parametrize("account", ["", "12#45678", None])
def test_an_unusable_account_is_not_assigned(table, account):
    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, account, now=NOW)


# =============================================================================
# 条件 (3): ログイン記録
# =============================================================================


def test_require_device_returns_the_login_record(table):
    put_period(table)
    put_assignment(table)
    put_login(table)

    result = access.require_device(USER_ID, ACCOUNT, now=NOW)

    assert result.assignment_id == ASSIGNMENT_ID
    assert result.login is not None
    assert result.login["method"] == "previous"
    assert result.login["summary"]["location_name"] == "現場の名前"


def test_without_a_login_record_login_is_required(table):
    put_period(table)
    put_assignment(table)

    assert_denied(
        access.DeviceLoginRequiredError, "DEVICE_LOGIN_REQUIRED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


@pytest.mark.parametrize("expires_at", [NOW_EPOCH, NOW_EPOCH - 1])
def test_an_expired_login_record_is_not_used_even_before_the_ttl_deletes_it(table, expires_at):
    """DynamoDB の TTL は削除が遅れるので、読むたびに期限を確かめる (ADR D5)。"""
    put_period(table)
    put_assignment(table)
    put_login(table, expires_at=expires_at)

    assert_denied(
        access.DeviceLoginRequiredError, "DEVICE_LOGIN_REQUIRED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


def test_a_login_record_of_the_previous_period_falls_at_condition_3(table):
    """ADR D4: 前の利用期間の割り当てでのログイン記録は、新しい割り当てがあっても (3) で落ちる。"""
    put_period(table, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_period(table)
    put_assignment(table, assignment_id=NEW_ASSIGNMENT_ID)
    put_login(table, assignment_id=OLD_ASSIGNMENT_ID, period_id=OLD_PERIOD_ID)

    assert_denied(
        access.DeviceLoginRequiredError, "DEVICE_LOGIN_REQUIRED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


def test_a_login_record_of_a_reassigned_user_falls_by_the_assignment_id(table):
    """同じ利用期間で付け直したら、前の割り当てのログイン記録は assignment_id で落ちる。"""
    put_period(table)
    put_assignment(table, valid_from=STARTED, valid_to=EARLIER)
    put_assignment(table, assignment_id=NEW_ASSIGNMENT_ID, valid_from=EARLIER)
    put_login(table, assignment_id=ASSIGNMENT_ID)

    assert_denied(
        access.DeviceLoginRequiredError, "DEVICE_LOGIN_REQUIRED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


def test_the_login_time_is_not_compared_with_the_valid_from(table):
    """(ADR D14): 時刻 (秒) は比べず、使い回さない assignment_id で照合する。

    記録は書き込みの条件でその割り当ての有効期間の中にしか書けないので、ID が同じなら時刻の比較は要らない。
    """
    put_period(table)
    put_assignment(table, valid_from=EARLIER)
    put_login(table, logged_in_at="2026-11-01T12:00:00Z")

    assert access.require_device(USER_ID, ACCOUNT, now=NOW).assignment_id == ASSIGNMENT_ID


def test_a_login_record_of_another_account_is_not_used(table):
    put_period(table)
    put_assignment(table)
    put_login(table, account=OTHER_ACCOUNT)

    assert_denied(
        access.DeviceLoginRequiredError, "DEVICE_LOGIN_REQUIRED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


def test_a_login_record_does_not_help_without_the_assignment(table):
    """割り当てを終えたら、期限内のログイン記録があっても (2) で落ちる (同じ暗証番号でも入れない)。"""
    put_period(table)
    put_assignment(table, valid_to=EARLIER)
    put_login(table)

    assert_denied(
        access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


# =============================================================================
# 条件 (4): サービスの許可
# =============================================================================


def test_require_grant_returns_the_grant(table):
    put_period(table)
    put_assignment(table)
    put_login(table)
    put_grant(table)
    device = access.require_device(USER_ID, ACCOUNT, now=NOW)

    grant = access.require_grant(device, "camera", now=NOW)

    assert grant == access.Grant(grant_id=GRANT_ID, service="camera", starts_at=STARTED, ends_at=None)


@pytest.mark.parametrize(
    ("starts_at", "ends_at", "status", "ok"),
    [
        (STARTED, None, "active", True),
        (STARTED, LATER, "active", True),
        (NOW_ISO, None, "active", True),
        (LATER, None, "active", False),
        (STARTED, EARLIER, "active", False),
        (STARTED, NOW_ISO, "active", False),
        (STARTED, None, "ended", False),
    ],
)
def test_the_grant_must_be_active_now(table, starts_at, ends_at, status, ok):
    put_period(table)
    put_assignment(table)
    put_grant(table, starts_at=starts_at, ends_at=ends_at, status=status)
    device = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    if ok:
        assert access.require_grant(device, "camera", now=NOW).grant_id == GRANT_ID
    else:
        assert_denied(
            access.ServiceNotGrantedError, "SERVICE_NOT_GRANTED", access.require_grant, device, "camera", now=NOW
        )


def test_a_grant_of_another_service_does_not_count(table):
    put_period(table)
    put_assignment(table)
    put_grant(table, service="other")
    device = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert_denied(access.ServiceNotGrantedError, "SERVICE_NOT_GRANTED", access.require_grant, device, "camera", now=NOW)


def test_a_grant_of_the_previous_period_falls_at_condition_4(table):
    """ADR D4: 前の利用期間の許可は、終え損ねて active のままでも (4) で落ちる。"""
    put_period(table, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_grant(table, period_id=OLD_PERIOD_ID)
    put_period(table)
    put_assignment(table)
    device = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert_denied(access.ServiceNotGrantedError, "SERVICE_NOT_GRANTED", access.require_grant, device, "camera", now=NOW)


def test_active_grants_lists_only_the_grants_valid_now(table):
    put_period(table)
    put_grant(table, grant_id="01JBZ00000000000000GRANT1")
    put_grant(table, grant_id="01JBZ00000000000000GRANT2", starts_at=LATER)
    put_grant(table, service="other", grant_id="01JBZ00000000000000GRANT3")

    grants = access.active_grants(PERIOD_ID, now=NOW)

    assert [(grant.service, grant.grant_id) for grant in grants] == [
        ("camera", "01JBZ00000000000000GRANT1"),
        ("other", "01JBZ00000000000000GRANT3"),
    ]


# =============================================================================
# 読み方 (計画 2.1: 認可の読み取りは強い整合で、GSI を使わない)
# =============================================================================


class _Recorder:
    def __init__(self, table):
        self._table = table
        self.calls: list[tuple[str, dict]] = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self._table.get_item(**kwargs)

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self._table.query(**kwargs)


def test_reads_are_strongly_consistent_and_do_not_use_the_index(table, monkeypatch):
    put_period(table)
    put_assignment(table)
    put_login(table)
    put_grant(table)
    recorder = _Recorder(table)
    monkeypatch.setattr(ledger, "_table", lambda: recorder)

    device = access.require_device(USER_ID, ACCOUNT, now=NOW)
    access.require_grant(device, "camera", now=NOW)

    assert {name for name, _ in recorder.calls} == {"get_item", "query"}
    for _name, kwargs in recorder.calls:
        assert kwargs.get("ConsistentRead") is True
        assert "IndexName" not in kwargs


def test_the_codes_follow_the_common_error_table():
    """`NO_ACTIVE_DEVICE_PERIOD` は外に出さず、`DEVICE_NOT_ASSIGNED` にまとめる (計画 2.6)。"""
    assert access.DeviceNotAssignedError().code == "DEVICE_NOT_ASSIGNED"
    assert access.DeviceLoginRequiredError().code == "DEVICE_LOGIN_REQUIRED"
    assert access.ServiceNotGrantedError().code == "SERVICE_NOT_GRANTED"
    assert not hasattr(access, "NoActiveDevicePeriodError")
