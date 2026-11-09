"""要求ごとの利用者の確定（計画 2.4、ADR-001 D3 / D9）。

`auth.get_claims` でクレームを取り、`ledger.resolve_user` で sub から台帳の利用者を引く。
クレームが無ければ 401、sub が台帳に無い・無効の印・今の sub でなければ 403 `USER_NOT_REGISTERED`、
停止中なら 403 `USER_SUSPENDED`。停止中でも通すのは ADR D9 の例外 1・2 の API だけで、
呼び出し側が `allow_suspended=True` を渡す。

admin (クレームの admin グループか、台帳の role=admin) で admin の状態が有効でない利用者 (準備中・確認待ち) も、
既定で 403 `ADMIN_NOT_ACTIVE` にする。通すのは例外 1・2 の API だけで、呼び出し側が `allow_inactive_admin=True`
を渡す。admin の API は、これに加えて `admin_guard.require_admin` を通す。
"""

from service_platform import auth, ledger
from service_platform.admin_guard import AdminNotActiveError
from service_platform.auth import Claims
from service_platform.ledger import UserContext


def require_user(
    event: dict, *, allow_suspended: bool = False, allow_inactive_admin: bool = False
) -> tuple[Claims, UserContext]:
    """イベントのクレームと台帳の利用者を返す。"""
    claims = auth.get_claims(event)
    user = ledger.resolve_user(claims.sub, allow_suspended=allow_suspended)
    is_admin = auth.is_admin(claims) or user.role == ledger.ROLE_ADMIN
    if is_admin and user.admin_state != ledger.ADMIN_ACTIVE and not allow_inactive_admin:
        raise AdminNotActiveError()
    return claims, user
