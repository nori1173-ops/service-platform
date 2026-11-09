"""email の変更（計画 2.6、ADR-001 運用の「メールアドレスを変える」）。

台帳で直列化する: 変更の途中の印 (`email_pending`: 新しい email と操作 ID) を version を条件に書いてから
Cognito を変え、印の操作 ID が自分のものであることを条件に確定する。印がある間のほかの宛先への変更は 409。
"""

from botocore.exceptions import ClientError

from admin_users import cognito, errors, validation
from admin_users.ledger_writes import audit_item
from admin_users.tx import Tx
from admin_users.tx import write as _write
from admin_users.users import EXISTS_CODES, _email_taken, _log, load
from service_platform import ledger
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

logger = get_logger()

PENDING = "email_pending"


def _email_step(caller: UserContext, profile: dict, op_id: str, request: dict, *, email: str | None) -> Tx:
    """変更の途中の印 (`email_pending.op_id`) が自分の操作のものであることを条件に、確定するか印を消す。

    `email` があれば確定 (email と GSI1SK を書いて印を消す)、無ければ Cognito の失敗で印だけを消す。
    """
    changes = {"email": email, "GSI1SK": ledger.user_gsi1sk(email, profile["user_id"])} if email else {}
    tx = Tx()
    return tx.update_profile(
        profile,
        by=caller.user_id,
        set_=changes,
        remove=(PENDING,),
        condition="#c_pending.#c_op = :c_op",
        names={"#c_pending": PENDING, "#c_op": "op_id"},
        values={":c_op": op_id},
        check_version=False,
    ).add(
        audit_item(
            "user.email_change",
            actor=caller,
            target_user_id=profile["user_id"],
            request=request,
            result="ok" if email else "failed",
            code=None if email else "COGNITO_UPDATE_FAILED",
        )
    )


def change_email(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    """email の変更を台帳で直列化する: 途中の印 → Cognito → 確定。

    1. version を条件に `email_pending` (新しい email と操作 ID) を書く。印が既にあれば 409 `CONFLICT`
       (同じ宛先なら、前の回の続きとして 2 と 3 を進める)
    2. AdminUpdateUserAttributes (email と email_verified)
    3. 印の操作 ID が自分のものであることを条件に email を確定し、印を消す。Cognito が失敗したら印だけを消す
    """
    profile = load(user_id)
    email = validation.email(payload.get("email"))
    pending = profile.get(PENDING) or None
    if pending is None and email == profile.get("email"):
        return {"user_id": user_id, "email": email}
    if pending is not None and pending.get("email") != email:
        raise errors.conflict(errors.CONFLICT, "email の変更が途中です。前と同じ宛先でやり直すと続きから進みます")
    if pending is None and _email_taken(email, user_id):
        raise errors.conflict(errors.USER_EXISTS, "同じメールアドレスの利用者がいます")
    username = profile.get("cognito_username")
    if not username or cognito.get_user(username) is None:
        raise errors.conflict(errors.COGNITO_USER_MISSING, "Cognito に利用者がいません。再招待してください")

    if pending is None:
        start = Tx()
        op_id = start.op_id
        start.update_profile(
            profile,
            by=caller.user_id,
            set_={PENDING: {"email": email, "op_id": op_id}},
            condition="attribute_not_exists(#c_pending)",
            names={"#c_pending": PENDING},
        )
        _write(start, applied=start.applied_to(user_id))
    else:
        op_id = str(pending.get("op_id") or "")
    try:
        cognito.update_email(username, email)
    except Exception as exc:
        try:
            undo = _email_step(caller, profile, op_id, request, email=None)
            _write(undo, applied=undo.applied_to(user_id))
        except errors.ApiError as revert:
            logger.error("email change could not be reverted", {"target_user_id": user_id})
            raise errors.outcome_unknown() from revert
        if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in EXISTS_CODES:
            raise errors.conflict(errors.USER_EXISTS, "同じメールアドレスの利用者がいます") from exc
        raise
    finish = _email_step(caller, profile, op_id, request, email=email)
    _write(finish, applied=finish.applied_to(user_id))
    _log("user.email_change", caller, user_id)
    return {"user_id": user_id, "email": email}
