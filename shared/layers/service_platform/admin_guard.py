"""admin の API の守り（計画 2.4、ADR-001 D9、判断 12 / 13）。

admin の API は、次の全部を満たす要求だけを通す (どれかが欠ければ 403。fail-closed)。

1. ID トークンのクレームが admin グループ
2. 台帳の利用者が有効 (`status=active`)
3. 台帳の admin の状態が有効 (`admin_state=active`) で、`mfa_enabled_at` がある
4. クレームの `auth_time` (サインインした時刻) が `mfa_enabled_at` より後 (同じ秒は拒む)。
   `auth_time` は更新では変わらないので、TOTP を登録する前のセッションは更新しても通らない
5. Cognito の現在の状態: 利用者が有効、admin グループ、`UserMFASettingList` に TOTP、sub が台帳と同じ。
   TOTP は `ConfiguredUserAuthFactors` では見ない (外した後も残るため。判断 13)

台帳の確認 (1〜4) を先に行い、通ったときだけ Cognito を呼ぶ。Cognito の Username は台帳の
`cognito_username` から引く (sub の Filter で探さない)。
"""

import os
from dataclasses import dataclass

from botocore.exceptions import ClientError

from service_platform import auth, ledger
from service_platform.auth import Claims, ForbiddenError
from service_platform.ledger import UserContext, UserSuspendedError

TOTP = "SOFTWARE_TOKEN_MFA"
PAGE_SIZE = 60
USER_POOL_ID_ENV = "USER_POOL_ID"


class AdminNotActiveError(ForbiddenError):
    """admin として有効ではない（403）。"""

    code = "ADMIN_NOT_ACTIVE"

    def __init__(self, message: str = "管理者として有効ではありません") -> None:
        super().__init__(message)


class AdminSessionStaleError(ForbiddenError):
    """TOTP の登録より前のサインインのセッション（403）。"""

    code = "ADMIN_SESSION_STALE"

    def __init__(self, message: str = "パスワードと TOTP でサインインし直してください") -> None:
        super().__init__(message)


class AdminMfaMissingError(ForbiddenError):
    """TOTP が設定されていない（403）。"""

    code = "ADMIN_MFA_MISSING"

    def __init__(self, message: str = "TOTP が登録されていません") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class CognitoState:
    """Cognito の利用者の現在の状態。"""

    exists: bool
    enabled: bool
    in_admin_group: bool
    totp: bool
    sub: str | None
    status: str | None


MISSING = CognitoState(exists=False, enabled=False, in_admin_group=False, totp=False, sub=None, status=None)


def _is_user_not_found(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "UserNotFoundException"


def _groups(client, user_pool_id: str, username: str) -> set[str]:
    names: set[str] = set()
    token = None
    while True:
        page = client.admin_list_groups_for_user(
            UserPoolId=user_pool_id,
            Username=username,
            Limit=PAGE_SIZE,
            **({"NextToken": token} if token else {}),
        )
        names.update(group.get("GroupName") for group in page.get("Groups") or [])
        token = page.get("NextToken")
        if not token:
            return names


def cognito_state(client, user_pool_id: str, username: str) -> CognitoState:
    """AdminGetUser と AdminListGroupsForUser で現在の状態を引く。利用者がいなければ `MISSING`。"""
    try:
        user = client.admin_get_user(UserPoolId=user_pool_id, Username=username)
        groups = _groups(client, user_pool_id, username)
    except ClientError as exc:
        if _is_user_not_found(exc):
            return MISSING
        raise
    attributes = {item.get("Name"): item.get("Value") for item in user.get("UserAttributes") or []}
    return CognitoState(
        exists=True,
        enabled=bool(user.get("Enabled")),
        in_admin_group=auth.ADMIN_GROUP in groups,
        totp=TOTP in (user.get("UserMFASettingList") or []),
        sub=attributes.get("sub"),
        status=user.get("UserStatus"),
    )


def auth_time(claims: Claims) -> int | None:
    """クレームの `auth_time` (epoch 秒)。無い・数でなければ `None`。"""
    value = claims.raw.get("auth_time")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def require_admin(claims: Claims, user: UserContext, cognito_client, *, user_pool_id: str | None = None) -> None:
    """admin の API の前に呼ぶ。満たさなければ `ForbiddenError` の子を投げる。"""
    if not auth.is_admin(claims):
        raise AdminNotActiveError("管理者権限がありません")
    if user.status != ledger.STATUS_ACTIVE:
        raise UserSuspendedError()
    if user.admin_state != ledger.ADMIN_ACTIVE:
        raise AdminNotActiveError()
    if user.mfa_enabled_at is None:
        raise AdminMfaMissingError()
    signed_in_at = auth_time(claims)
    if signed_in_at is None or signed_in_at <= user.mfa_enabled_at:
        raise AdminSessionStaleError()
    if not user.cognito_username:
        raise AdminNotActiveError()

    pool = user_pool_id or os.environ[USER_POOL_ID_ENV]
    state = cognito_state(cognito_client, pool, user.cognito_username)
    if not (state.exists and state.enabled and state.in_admin_group and state.sub == user.sub):
        raise AdminNotActiveError()
    if not state.totp:
        raise AdminMfaMissingError()
