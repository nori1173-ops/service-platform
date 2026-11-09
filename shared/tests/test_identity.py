"""`service_platform.identity` のテスト（計画 2.4 / 4.2、ADR-001 D3 / D9）。"""

import pytest

from service_platform import identity, ledger
from service_platform.auth import ForbiddenError, UnauthorizedError
from service_platform.ledger import UserNotRegisteredError, UserSuspendedError

USER_ID = "01JBZ0000000000000000USER1"
SUB = "11111111-2222-3333-4444-210000000001"
OLD_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
EMAIL = "user@example.com"


def event(sub: str | None = SUB, groups: str = "[]", **claims) -> dict:
    raw = {"email": EMAIL, "cognito:groups": groups, **claims}
    if sub is not None:
        raw["sub"] = sub
    return {"requestContext": {"authorizer": {"jwt": {"claims": raw}}}}


def put_user(table, *, status="active", sub=SUB, profile_sub=None, revoked=False, **attributes):
    table.put_item(
        Item={
            "PK": ledger.user_pk(USER_ID),
            "SK": ledger.PROFILE_SK,
            "user_id": USER_ID,
            "cognito_username": "cognito-user-1",
            "sub": profile_sub or sub,
            "email": EMAIL,
            "status": status,
            "kind": "customer",
            "role": "user",
            **attributes,
        }
    )
    mapping = {"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK, "user_id": USER_ID}
    if revoked:
        mapping["revoked_at"] = "2026-11-04T01:00:00Z"
    table.put_item(Item=mapping)


def test_require_user_returns_claims_and_the_ledger_user(ledger_table):
    put_user(ledger_table)

    claims, user = identity.require_user(event())

    assert claims.sub == SUB
    assert user.user_id == USER_ID
    assert user.status == "active"
    assert user.cognito_username == "cognito-user-1"


def test_require_user_without_claims_is_401(ledger_table):
    with pytest.raises(UnauthorizedError):
        identity.require_user({"requestContext": {}})


def test_unknown_sub_is_403_user_not_registered(ledger_table):
    with pytest.raises(UserNotRegisteredError) as excinfo:
        identity.require_user(event())
    assert excinfo.value.code == "USER_NOT_REGISTERED"


def test_revoked_sub_is_403_user_not_registered_even_for_the_exceptions(ledger_table):
    """再招待の前のトークンは例外 1・2 でも使えない（ADR D9）。"""
    put_user(ledger_table, sub=OLD_SUB, profile_sub=SUB, revoked=True, status="suspended")

    with pytest.raises(UserNotRegisteredError):
        identity.require_user(event(sub=OLD_SUB), allow_suspended=True)


def test_suspended_user_is_403_user_suspended(ledger_table):
    put_user(ledger_table, status="suspended")

    with pytest.raises(UserSuspendedError) as excinfo:
        identity.require_user(event())
    assert excinfo.value.code == "USER_SUSPENDED"
    assert isinstance(excinfo.value, ForbiddenError)


def test_suspended_user_passes_when_suspension_is_allowed(ledger_table):
    put_user(ledger_table, status="suspended")

    _claims, user = identity.require_user(event(), allow_suspended=True)

    assert user.is_suspended


# =============================================================================
# 準備中・確認待ちの admin (ADR D9: 例外 1・2 の API のほかは 403)
# =============================================================================


@pytest.mark.parametrize(
    ("groups", "role", "admin_state"),
    [
        ("[admin]", "admin", "pending"),
        ("[admin]", "admin", "awaiting"),
        ("[admin]", "admin", None),
        ("[admin]", "user", None),
        ("[]", "admin", "pending"),
        ("[]", "admin", "awaiting"),
    ],
)
def test_an_admin_who_is_not_active_is_403_by_default(ledger_table, groups, role, admin_state):
    """クレームの admin か台帳の role=admin で、admin の状態が有効でなければ断る。"""
    attributes = {"role": role, "kind": "employee"}
    if admin_state is not None:
        attributes["admin_state"] = admin_state
    put_user(ledger_table, **attributes)

    with pytest.raises(ForbiddenError) as excinfo:
        identity.require_user(event(groups=groups))
    assert excinfo.value.code == "ADMIN_NOT_ACTIVE"

    _claims, user = identity.require_user(event(groups=groups), allow_inactive_admin=True)
    assert user.user_id == USER_ID


def test_an_active_admin_and_a_customer_pass(ledger_table):
    put_user(ledger_table, role="admin", kind="employee", admin_state="active", mfa_enabled_at=1_762_000_000)
    assert identity.require_user(event(groups="[admin]"))[1].admin_state == "active"

    put_user(ledger_table)
    assert identity.require_user(event())[1].role == "user"


def test_the_exceptions_pass_a_suspended_inactive_admin(ledger_table):
    """例外 1・2 (`/me`、`/me/totp-registered`) は両方を許す。"""
    put_user(ledger_table, status="suspended", role="admin", kind="employee", admin_state="pending")

    _claims, user = identity.require_user(event(groups="[admin]"), allow_suspended=True, allow_inactive_admin=True)

    assert user.admin_state == "pending"
