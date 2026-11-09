"""`service_platform.admin_guard` のテスト（計画 2.4 / 4.2、ADR-001 D9、判断 12 / 13）。

Cognito は薄い偽物で包む（moto は AdminGetUser の UserMFASettingList を TOTP の登録で埋めない）。
"""

from dataclasses import replace

import pytest
from botocore.exceptions import ClientError

from service_platform import admin_guard
from service_platform.admin_guard import AdminMfaMissingError, AdminNotActiveError, AdminSessionStaleError
from service_platform.auth import Claims, ForbiddenError
from service_platform.ledger import UserContext, UserSuspendedError

POOL = "ap-northeast-1_EXMPLpool1"
SUB = "11111111-2222-3333-4444-210000000001"
USERNAME = "cognito-admin-1"
MFA_ENABLED_AT = 1_762_000_000


class FakeCognito:
    """AdminGetUser と AdminListGroupsForUser だけを持つ偽物。呼び出しを記録する。"""

    def __init__(self, *, exists=True, enabled=True, mfa=("SOFTWARE_TOKEN_MFA",), groups=("admin",), sub=SUB):
        self.exists = exists
        self.enabled = enabled
        self.mfa = list(mfa)
        self.groups = list(groups)
        self.sub = sub
        self.calls = []

    def _missing(self, operation):
        return ClientError({"Error": {"Code": "UserNotFoundException", "Message": "User does not exist."}}, operation)

    def admin_get_user(self, **kwargs):
        self.calls.append(("admin_get_user", kwargs))
        if not self.exists:
            raise self._missing("AdminGetUser")
        return {
            "Username": kwargs["Username"],
            "Enabled": self.enabled,
            "UserStatus": "CONFIRMED",
            "UserMFASettingList": self.mfa,
            "UserAttributes": [{"Name": "sub", "Value": self.sub}, {"Name": "email", "Value": "a@example.com"}],
        }

    def admin_list_groups_for_user(self, **kwargs):
        self.calls.append(("admin_list_groups_for_user", kwargs))
        if not self.exists:
            raise self._missing("AdminListGroupsForUser")
        if kwargs.get("NextToken") is None and len(self.groups) > 1:
            return {"Groups": [{"GroupName": self.groups[0]}], "NextToken": "page-2"}
        start = 1 if kwargs.get("NextToken") else 0
        return {"Groups": [{"GroupName": name} for name in self.groups[start:]]}


def claims(*, groups=("admin",), auth_time=MFA_ENABLED_AT + 60) -> Claims:
    raw = {"sub": SUB, "email": "a@example.com"}
    if auth_time is not None:
        raw["auth_time"] = auth_time
    return Claims(sub=SUB, email="a@example.com", groups=list(groups), raw=raw)


def admin(**changes) -> UserContext:
    user = UserContext(
        user_id="01JBZ0000000000000000ADMIN",
        sub=SUB,
        email="a@example.com",
        status="active",
        kind="employee",
        role="admin",
        admin_state="active",
        mfa_enabled_at=MFA_ENABLED_AT,
        cognito_username=USERNAME,
    )
    return replace(user, **changes)


@pytest.fixture(autouse=True)
def _pool(monkeypatch):
    monkeypatch.setenv("USER_POOL_ID", POOL)


def test_an_active_admin_with_a_fresh_totp_session_passes():
    cognito = FakeCognito(groups=("dev", "admin"))

    admin_guard.require_admin(claims(), admin(), cognito)

    assert ("admin_get_user", {"UserPoolId": POOL, "Username": USERNAME}) in cognito.calls
    group_calls = [kwargs for name, kwargs in cognito.calls if name == "admin_list_groups_for_user"]
    assert group_calls[0]["Username"] == USERNAME
    assert len(group_calls) == 2


def test_auth_time_may_arrive_as_a_string():
    """HTTP API の JWT authorizer はクレームの値を文字列で渡す。"""
    admin_guard.require_admin(claims(auth_time=str(MFA_ENABLED_AT + 1)), admin(), FakeCognito())


def test_user_pool_id_can_be_passed_explicitly(monkeypatch):
    monkeypatch.delenv("USER_POOL_ID")
    cognito = FakeCognito()

    admin_guard.require_admin(claims(), admin(), cognito, user_pool_id="other-pool")

    assert cognito.calls[0][1]["UserPoolId"] == "other-pool"


def test_only_the_claim_is_not_enough():
    """クレームだけ admin で、台帳が準備中なら通さない（ADR D9）。"""
    with pytest.raises(AdminNotActiveError) as excinfo:
        admin_guard.require_admin(claims(), admin(admin_state="pending", mfa_enabled_at=None), FakeCognito())
    assert excinfo.value.code == "ADMIN_NOT_ACTIVE"


def test_an_awaiting_admin_is_not_active():
    with pytest.raises(AdminNotActiveError):
        admin_guard.require_admin(claims(), admin(admin_state="awaiting"), FakeCognito())


def test_a_ledger_admin_without_the_claim_is_not_active():
    with pytest.raises(AdminNotActiveError):
        admin_guard.require_admin(claims(groups=()), admin(), FakeCognito())


def test_a_user_without_admin_state_is_not_active():
    with pytest.raises(AdminNotActiveError):
        admin_guard.require_admin(claims(), admin(role="user", admin_state=None, mfa_enabled_at=None), FakeCognito())


def test_a_suspended_admin_is_rejected():
    with pytest.raises(UserSuspendedError):
        admin_guard.require_admin(claims(), admin(status="suspended"), FakeCognito())


def test_an_active_state_without_mfa_enabled_at_is_mfa_missing():
    with pytest.raises(AdminMfaMissingError) as excinfo:
        admin_guard.require_admin(claims(), admin(mfa_enabled_at=None), FakeCognito())
    assert excinfo.value.code == "ADMIN_MFA_MISSING"


@pytest.mark.parametrize(
    "auth_time",
    [MFA_ENABLED_AT, MFA_ENABLED_AT - 1, None, "", "abc", True],
    ids=["same-second", "before", "missing", "empty", "not-a-number", "bool"],
)
def test_a_session_from_before_the_totp_registration_is_stale(auth_time):
    """`auth_time` が `mfa_enabled_at` より後でなければ拒む。同じ秒も拒む（判断 12）。"""
    with pytest.raises(AdminSessionStaleError) as excinfo:
        admin_guard.require_admin(claims(auth_time=auth_time), admin(), FakeCognito())
    assert excinfo.value.code == "ADMIN_SESSION_STALE"


def test_missing_totp_in_cognito_is_mfa_missing():
    """TOTP の現在状態は UserMFASettingList で見る（判断 13）。"""
    with pytest.raises(AdminMfaMissingError):
        admin_guard.require_admin(claims(), admin(), FakeCognito(mfa=()))


@pytest.mark.parametrize(
    "cognito",
    [
        FakeCognito(enabled=False),
        FakeCognito(groups=("dev",)),
        FakeCognito(groups=()),
        FakeCognito(exists=False),
        FakeCognito(sub="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ],
    ids=["disabled", "other-group", "no-group", "not-found", "other-sub"],
)
def test_cognito_state_must_still_be_an_enabled_admin(cognito):
    with pytest.raises(AdminNotActiveError):
        admin_guard.require_admin(claims(), admin(), cognito)


def test_without_a_cognito_username_the_guard_fails_closed():
    cognito = FakeCognito()

    with pytest.raises(AdminNotActiveError):
        admin_guard.require_admin(claims(), admin(cognito_username=None), cognito)
    assert cognito.calls == []


def test_ledger_checks_run_before_calling_cognito():
    cognito = FakeCognito()

    with pytest.raises(AdminNotActiveError):
        admin_guard.require_admin(claims(), admin(admin_state="pending"), cognito)
    assert cognito.calls == []


def test_other_cognito_errors_are_not_swallowed():
    class Broken(FakeCognito):
        def admin_get_user(self, **kwargs):
            raise ClientError({"Error": {"Code": "InternalErrorException", "Message": "x"}}, "AdminGetUser")

    with pytest.raises(ClientError):
        admin_guard.require_admin(claims(), admin(), Broken())


def test_guard_errors_are_403():
    for error in (AdminNotActiveError, AdminSessionStaleError, AdminMfaMissingError):
        assert issubclass(error, ForbiddenError)


def test_cognito_state_reports_the_facts():
    state = admin_guard.cognito_state(FakeCognito(groups=("dev", "admin")), POOL, USERNAME)

    assert state == admin_guard.CognitoState(
        exists=True, enabled=True, in_admin_group=True, totp=True, sub=SUB, status="CONFIRMED"
    )


def test_cognito_state_of_a_missing_user():
    state = admin_guard.cognito_state(FakeCognito(exists=False), POOL, USERNAME)

    assert state.exists is False
    assert state.enabled is False
    assert state.in_admin_group is False
    assert state.totp is False


@pytest.mark.parametrize(
    "raw,expected",
    [({"auth_time": 10}, 10), ({"auth_time": "11"}, 11), ({}, None), ({"auth_time": "x"}, None)],
)
def test_auth_time(raw, expected):
    assert admin_guard.auth_time(Claims(sub=SUB, email="", raw=raw)) == expected
