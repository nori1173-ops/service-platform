"""利用者の管理の操作（計画 2.6 / 4.2、ADR-001 D3 / D8 / D9 / D12）。

- 利用者の識別子は台帳の user_id。Cognito の Admin 系 API には台帳の `cognito_username` を渡す。
- 自分自身への停止・再招待・admin を外す・有効化・一時パスワードは 403。最後の有効な admin を
  停止・外す・再招待するのは 409 (台帳の `ADMIN#SET` の条件付きの更新で、書き込みと一体に守る)。
- 利用者の行の更新は、最初に読んだ version を条件にする (楽観ロック)。重なったら 409 `CONFLICT`。
- Cognito と台帳をまたぐ操作は、どこで失敗しても安全側に倒れる順にする (台帳の停止を先に、
  Cognito の有効化を先に)。招待と再招待で台帳が確定して失敗したら、作った Cognito の利用者を消す。
- 再招待は、新しい sub への付け替えまで台帳に `reinvite_in_progress` を立てる。立っている間と、古い sub の
  無効の印を確かめられない間は、有効に戻さない (古いパスキーで戻らないように。ADR D8 手順 2)。
- 一時パスワードは応答で 1 回だけ返し、ログ・監査・台帳・例外のメッセージに入れない。
  連絡先 (`contact`) は台帳と詳細の応答だけに置き、ログ・監査に入れない。
"""

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from admin_users import cognito, errors, ledger_writes, passwords, validation
from admin_users.ledger_writes import audit_item, put, update
from admin_users.tx import Tx
from admin_users.tx import write as _write
from service_platform import admin_guard, audit, ledger
from service_platform.ids import ULID_PATTERN, new_ulid
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

SERVICE = ledger_writes.SERVICE
ADMIN_VERIFIED_FIELDS = ("admin_verified_by", "admin_verified_at", "admin_verified_method", "admin_verified_note")
EXISTS_CODES = ("UsernameExistsException", "AliasExistsException")
IN_PROGRESS = "reinvite_in_progress"

logger = get_logger()


# =============================================================================
# 読み取りと共通の確認
# =============================================================================


def load(user_id: str) -> dict:
    if not isinstance(user_id, str) or not ULID_PATTERN.fullmatch(user_id):
        raise errors.not_found()
    profile = ledger.get_profile(user_id)
    if not profile:
        raise errors.not_found()
    return profile


def _query_gsi1(condition) -> list[dict]:
    """台帳の GSI1 (結果整合。一覧と重複の確認のためだけ。認可の判断には使わない)。"""
    table = ledger._table()
    query = {"IndexName": ledger.GSI1, "KeyConditionExpression": condition}
    rows: list[dict] = []
    while True:
        page = table.query(**query)
        rows.extend(page.get("Items") or [])
        if not page.get("LastEvaluatedKey"):
            return rows
        query = {**query, "ExclusiveStartKey": page["LastEvaluatedKey"]}


def list_profiles() -> list[dict]:
    return _query_gsi1(Key("GSI1PK").eq(ledger.USER_GSI1PK))


def profiles_with_email(email: str) -> list[dict]:
    return _query_gsi1(
        Key("GSI1PK").eq(ledger.USER_GSI1PK) & Key("GSI1SK").begins_with(f"{ledger.normalize_email(email)}#")
    )


def _sub_row(sub: str) -> dict | None:
    return (
        ledger._table().get_item(Key={"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK}, ConsistentRead=True).get("Item")
    )


def maps(sub: str, user_id: str) -> bool:
    """sub が台帳の利用者 user_id の今の sub として引けるか（強い整合）。"""
    try:
        return ledger.resolve_user(sub, allow_suspended=True).user_id == user_id
    except ledger.ForbiddenError:
        return False


def _linked_to_other(cognito_user: dict, user_id: str) -> bool:
    """Cognito の利用者が、台帳のほかの利用者の sub として引き当てられているか。"""
    row = _sub_row(cognito.attribute(cognito_user, cognito.SUB_ATTRIBUTE))
    return row is not None and row.get("user_id") != user_id


def _email_taken(email: str, user_id: str | None = None) -> bool:
    if any(profile.get("user_id") != user_id for profile in profiles_with_email(email)):
        return True
    existing = cognito.find_by_email(email)
    return existing is not None and (user_id is None or _linked_to_other(existing, user_id))


def _in_reinvite(profile: dict) -> bool:
    return profile.get("status") == ledger.STATUS_SUSPENDED and profile.get("restore_requires") == (
        ledger.RESTORE_REINVITE
    )


def _is_admin_profile(profile: dict) -> bool:
    return profile.get("role") == ledger.ROLE_ADMIN or "admin_state" in profile


def _is_active_admin(profile: dict) -> bool:
    return profile.get("admin_state") == ledger.ADMIN_ACTIVE


def reject_self(caller: UserContext, profile: dict) -> None:
    if profile["user_id"] == caller.user_id:
        raise errors.ApiError(403, errors.SELF_OPERATION_FORBIDDEN, "自分自身には操作できません")


def _require_reinvite_complete(profile: dict, cognito_user: dict | None) -> None:
    """再招待が新しい sub への付け替えまで終わっていること (古い sub とパスキーで戻さない)。"""
    previous = profile.get("previous_sub")
    previous_row = _sub_row(previous) if previous else None
    complete = (
        not profile.get(IN_PROGRESS)
        and previous_row is not None
        and previous_row.get("revoked_at")
        and cognito_user is not None
        and cognito.attribute(cognito_user, cognito.SUB_ATTRIBUTE) == profile["sub"]
        and maps(profile["sub"], profile["user_id"])
    )
    if not complete:
        raise errors.reinvite_incomplete()


def _log(operation: str, caller: UserContext, target_user_id: str | None, result: str = "ok", **extra) -> None:
    """操作の構造化ログ (保持 365 日)。秘密 (一時パスワード・連絡先) を載せない。"""
    logger.info(
        operation,
        {"operation": operation, "actor_user_id": caller.user_id, "target_user_id": target_user_id, "result": result}
        | extra,
    )


# =============================================================================
# 招待
# =============================================================================


def _create(email: str, password: str | None) -> dict:
    try:
        return cognito.create_user(email, temporary_password=password)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in EXISTS_CODES:
            raise errors.conflict(errors.USER_EXISTS, "同じメールアドレスの利用者がいます") from exc
        raise


def _grant_group_or_undo(created: dict) -> None:
    try:
        cognito.add_to_admin(created["username"])
    except Exception:
        cognito.delete_user(created["username"])
        raise


def invite(caller: UserContext, payload: dict, request: dict) -> dict:
    email = validation.email(payload.get("email"))
    kind = validation.choice(payload.get("kind"), "kind", validation.KINDS)
    role = validation.choice(payload.get("role"), "role", validation.ROLES)
    customer = kind == ledger.KIND_CUSTOMER
    org_name = validation.text(payload.get("org_name"), "org_name", validation.ORG_NAME_MAX, required=customer)
    contact = validation.text(payload.get("contact"), "contact", validation.CONTACT_MAX)
    note = validation.text(payload.get("note"), "note", validation.NOTE_MAX)
    is_admin = role == ledger.ROLE_ADMIN
    if is_admin and customer:
        raise errors.validation("admin は社員 (kind=employee) で招待してください")
    if _email_taken(email):
        raise errors.conflict(errors.USER_EXISTS, "同じメールアドレスの利用者がいます")

    user_id = new_ulid()
    password = passwords.generate() if is_admin else None
    created = _create(email, password)
    if is_admin:
        _grant_group_or_undo(created)

    at = ledger_writes.now_iso()
    profile = {
        "PK": ledger.user_pk(user_id),
        "SK": ledger.PROFILE_SK,
        "GSI1PK": ledger.USER_GSI1PK,
        "GSI1SK": ledger.user_gsi1sk(email, user_id),
        "user_id": user_id,
        "cognito_username": created["username"],
        "sub": created["sub"],
        "email": email,
        "status": ledger.STATUS_ACTIVE,
        "kind": kind,
        "role": role,
        "created_by": caller.user_id,
        "created_at": at,
        "updated_by": caller.user_id,
        "updated_at": at,
        "version": 1,
    }
    profile.update({k: v for k, v in {"org_name": org_name, "contact": contact, "note": note}.items() if v})
    if is_admin:
        profile["admin_state"] = ledger.ADMIN_PENDING
    mapping = {"PK": ledger.sub_pk(created["sub"]), "SK": ledger.SUB_SK, "user_id": user_id, "created_at": at}
    tx = Tx()
    profile["last_op_id"] = tx.op_id
    (
        tx.add(put(profile))
        .add(put(mapping))
        .caller(caller)
        .add(
            audit_item(
                "user.invite",
                actor=caller,
                target_user_id=user_id,
                request=request,
                detail={"kind": kind, "role": role},
            )
        )
    )
    _write(
        tx,
        applied=lambda: maps(created["sub"], user_id),
        compensate=lambda: cognito.delete_user(created["username"]),
    )
    _log("user.invite", caller, user_id, kind=kind, role=role)
    result = {
        "user_id": user_id,
        "email": email,
        "kind": kind,
        "role": role,
        "status": ledger.STATUS_ACTIVE,
        "admin_state": profile.get("admin_state"),
    }
    if password:
        result["temporary_password"] = password
    return result


# =============================================================================
# 台帳の項目と email
# =============================================================================

PROFILE_FIELDS = {"org_name": validation.ORG_NAME_MAX, "contact": validation.CONTACT_MAX, "note": validation.NOTE_MAX}


def update_profile(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    profile = load(user_id)
    fields = sorted(name for name in PROFILE_FIELDS if name in payload)
    if not fields:
        raise errors.validation(f"{' / '.join(PROFILE_FIELDS)} のどれかを指定してください")
    values = {name: validation.text(payload[name], name, PROFILE_FIELDS[name]) for name in fields}
    if profile.get("kind") == ledger.KIND_CUSTOMER and "org_name" in values and not values["org_name"]:
        raise errors.validation("お客様の org_name は消せません")
    set_ = {name: value for name, value in values.items() if value}
    remove = tuple(name for name, value in values.items() if not value)
    tx = Tx()
    (
        tx.update_profile(profile, by=caller.user_id, set_=set_, remove=remove).add(
            audit_item(
                "user.profile_update",
                actor=caller,
                target_user_id=user_id,
                request=request,
                detail={"fields": fields},
            )
        )
    )
    _write(tx, applied=tx.applied_to(user_id))
    _log("user.profile_update", caller, user_id, fields=fields)
    return {"user_id": user_id, "updated": fields}


# =============================================================================
# 停止と有効に戻す
# =============================================================================


def _retire_sessions(username: str | None, *, delete: bool = False) -> None:
    if not username or cognito.get_user(username) is None:
        return
    cognito.disable(username)
    cognito.global_sign_out(username)
    if delete:
        cognito.delete_user(username)


def suspend(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    """台帳で停止 → AdminDisableUser → AdminUserGlobalSignOut (ADR D8 手順 3)。

    台帳が既に停止なら、呼び出し元の確認と監査を書いてから Cognito の段だけを流し直す (遅れた無効化を済ませる)。
    """
    profile = load(user_id)
    reject_self(caller, profile)
    reason = validation.text(payload.get("reason"), "reason", validation.REASON_MAX)
    tx = Tx()
    if profile.get("status") != ledger.STATUS_SUSPENDED:
        set_ = {
            "status": ledger.STATUS_SUSPENDED,
            "suspended_by": caller.user_id,
            "suspended_at": ledger_writes.now_iso(),
        }
        if reason:
            set_["suspend_reason"] = reason
        tx.update_profile(profile, by=caller.user_id, set_=set_, remove=() if reason else ("suspend_reason",))
        if _is_active_admin(profile):
            tx.remove_admin(user_id)
        step = "ledger"
    else:
        tx.update_profile(
            profile,
            by=caller.user_id,
            condition="#c_status = :c_suspended",
            names={"#c_status": "status"},
            values={":c_suspended": ledger.STATUS_SUSPENDED},
        )
        step = "cognito"
    tx.caller(caller).add(
        audit_item(
            "user.suspend",
            actor=caller,
            target_user_id=user_id,
            request=request,
            detail={"reason": reason, "step": step},
        )
    )
    _write(tx, applied=tx.applied_to(user_id))
    _retire_sessions(profile.get("cognito_username"))
    _log("user.suspend", caller, user_id, step=step)
    return {"user_id": user_id, "status": ledger.STATUS_SUSPENDED}


def _require_cognito_admin(profile: dict) -> None:
    username = profile.get("cognito_username")
    state = (
        admin_guard.cognito_state(cognito.client(), cognito.user_pool_id(), username)
        if username
        else admin_guard.MISSING
    )
    if not (state.exists and state.in_admin_group and state.totp and state.sub == profile["sub"]):
        raise errors.conflict(
            errors.ADMIN_NOT_READY, "Cognito で admin グループにいて、TOTP が登録されていることを確かめられません"
        )


def _enable_after_late_disable(caller: UserContext, profile: dict, username: str, request: dict) -> dict:
    """台帳は有効なのに Cognito が無効 (遅れた無効化) のときの後始末。AdminEnableUser だけを行う。"""
    user_id = profile["user_id"]
    tx = Tx()
    tx.update_profile(
        profile,
        by=caller.user_id,
        condition="#c_status = :c_active",
        names={"#c_status": "status"},
        values={":c_active": ledger.STATUS_ACTIVE},
    ).caller(caller).add(
        audit_item("user.restore", actor=caller, target_user_id=user_id, request=request, detail={"step": "cognito"})
    )
    _write(tx, applied=tx.applied_to(user_id))
    cognito.enable(username)
    _log("user.restore", caller, user_id, step="cognito")
    return {"user_id": user_id, "status": ledger.STATUS_ACTIVE}


def _enable_or_unavailable(username: str) -> None:
    try:
        cognito.enable(username)
    except (ClientError, BotoCoreError) as exc:
        logger.error("restore: AdminEnableUser failed", {"error": type(exc).__name__})
        raise errors.temporarily_unavailable(
            "Cognito で有効にできませんでした。台帳は停止のままです。しばらくしてからもう一度呼んでください"
        ) from exc


def restore(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    """有効に戻す (別経路の確認の記録つき)。Cognito の有効化 → 台帳の順。

    台帳を先に戻すと、Cognito の有効化が失敗しても、待っていた要求の呼び出し元の条件 (台帳の有効な admin) が
    また成り立ってしまう。Cognito の有効化が失敗したら台帳を触らず 503。有効化の後で台帳が失敗したら、
    Cognito は有効・台帳は停止のままで、認可は台帳で閉じる (API は 403)。戻すには停止を流し直してから restore。

    - 自分自身は 403。再招待の途中、または付け替えが確かめられなければ 409 `REINVITE_INCOMPLETE`
    - 再招待の後の admin は admin-activation を使う (409 `USE_ADMIN_ACTIVATION`)
    - 通常の停止は、Cognito の無効化まで済んでいること (済んでいなければ 409 `SUSPEND_INCOMPLETE`。停止を流し直す)
    - 通常の停止の admin は、TOTP が残っていることを確かめて戻す (admin の状態と mfa_enabled_at は保つ)
    - 再招待の後のお客様・社員は、新しい利用者のパスキーの登録が要る
    - 台帳が有効で Cognito が無効なら、AdminEnableUser だけを行う (遅れた無効化の後始末)
    """
    profile = load(user_id)
    reject_self(caller, profile)
    if profile.get(IN_PROGRESS):
        raise errors.reinvite_incomplete()
    username = profile.get("cognito_username")
    user = cognito.get_user(username) if username else None
    admin_target = _is_admin_profile(profile) or bool(user and cognito.ADMIN_GROUP in cognito.groups_of(username))
    after_reinvite = profile.get("restore_requires") == ledger.RESTORE_REINVITE
    if admin_target and after_reinvite:
        raise errors.conflict(
            errors.USE_ADMIN_ACTIVATION, "再招待の後の admin は「admin を有効にする」で戻してください"
        )
    method = validation.verify_method(payload.get("verified_method"), "verified_method")
    note = validation.text(payload.get("note"), "note", validation.NOTE_MAX)
    if profile.get("status") == ledger.STATUS_ACTIVE and user is not None and not user.get("Enabled"):
        return _enable_after_late_disable(caller, profile, username, request)
    if profile.get("status") != ledger.STATUS_SUSPENDED:
        raise errors.conflict(errors.USER_NOT_SUSPENDED, "停止中の利用者ではありません")
    if user is None:
        raise errors.conflict(errors.COGNITO_USER_MISSING, "Cognito に利用者がいません。再招待してください")
    if after_reinvite:
        _require_reinvite_complete(profile, user)
        if not (cognito.auth_factors(username) or {}).get("web_authn"):
            raise errors.conflict(errors.PASSKEY_NOT_REGISTERED, "再招待の後のパスキーの登録がまだです")
    if admin_target:
        _require_cognito_admin(profile)
    if not after_reinvite and user.get("Enabled"):
        raise errors.conflict(
            errors.SUSPEND_INCOMPLETE, "停止が Cognito まで済んでいません。停止を流し直してから戻してください"
        )

    set_ = {
        "status": ledger.STATUS_ACTIVE,
        "restored_by": caller.user_id,
        "restored_at": ledger_writes.now_iso(),
        "restore_verified_method": method,
    }
    if note:
        set_["restore_note"] = note
    remove = ("restore_requires",) + (() if note else ("restore_note",))
    if not user.get("Enabled"):
        _enable_or_unavailable(username)
    tx = Tx()
    tx.update_profile(
        profile,
        by=caller.user_id,
        set_=set_,
        remove=remove,
        condition="#c_status = :c_suspended",
        names={"#c_status": "status"},
        values={":c_suspended": ledger.STATUS_SUSPENDED},
    )
    if admin_target and _is_active_admin(profile):
        tx.add(ledger_writes.admin_set_add(user_id))
    tx.caller(caller).add(
        audit_item(
            "user.restore",
            actor=caller,
            target_user_id=user_id,
            request=request,
            detail={"verified_method": method, "after_reinvite": after_reinvite, "admin": admin_target},
        )
    )
    _write(tx, applied=tx.applied_to(user_id))
    _log("user.restore", caller, user_id, verified_method=method, admin=admin_target)
    return {"user_id": user_id, "status": ledger.STATUS_ACTIVE}


# =============================================================================
# 再招待 (ADR D8 手順 2 / 4)
# =============================================================================


def _verified_before(value: object) -> tuple[str, str | None] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise errors.validation("verified_before は {method, note} で指定してください")
    return (
        validation.verify_method(value.get("method"), "verified_before.method"),
        validation.text(value.get("note"), "verified_before.note", validation.NOTE_MAX),
    )


def _start_reinvite(caller: UserContext, profile: dict, reason: str, request: dict) -> dict:
    """手順の最初: 台帳で停止し、付け替えが終わるまで再招待の途中の印を立てる。admin なら有効な集合から外す。"""
    user_id = profile["user_id"]
    set_ = {
        "status": ledger.STATUS_SUSPENDED,
        "restore_requires": ledger.RESTORE_REINVITE,
        IN_PROGRESS: True,
        "suspended_by": caller.user_id,
        "suspended_at": ledger_writes.now_iso(),
        "suspend_reason": reason,
    }
    tx = Tx()
    tx.update_profile(profile, by=caller.user_id, set_=set_)
    if _is_active_admin(profile):
        tx.remove_admin(user_id)
    tx.caller(caller).add(
        audit_item(
            "user.reinvite",
            actor=caller,
            target_user_id=user_id,
            request=request,
            detail={"step": "suspend", "reason": reason},
        )
    )
    _write(tx, applied=tx.applied_to(user_id))
    started = load(user_id)
    if started.get("sub") != profile["sub"] or not started.get(IN_PROGRESS):
        raise errors.stale()
    return started


def _clear_address(email: str, user_id: str) -> bool:
    """宛先のメールアドレスを使う Cognito の利用者を片付ける。台帳に無い利用者 (前の回の残り) だけを消す。"""
    existing = cognito.find_by_email(email)
    if existing is None:
        return False
    if _linked_to_other(existing, user_id):
        raise errors.conflict(errors.USER_EXISTS, "同じメールアドレスの利用者がいます")
    cognito.delete_user(existing["Username"])
    return True


def _link_tx(
    caller: UserContext,
    profile: dict,
    created: dict,
    *,
    email: str,
    admin_target: bool,
    verified: tuple[str, str | None] | None,
    reason: str,
    orphan_deleted: bool,
    request: dict,
) -> Tx:
    user_id = profile["user_id"]
    at = ledger_writes.now_iso()
    set_ = {
        "sub": created["sub"],
        "cognito_username": created["username"],
        "previous_sub": profile["sub"],
        "email": email,
        "GSI1SK": ledger.user_gsi1sk(email, user_id),
        "status": ledger.STATUS_SUSPENDED,
        "restore_requires": ledger.RESTORE_REINVITE,
        "role": ledger.ROLE_ADMIN if admin_target else ledger.ROLE_USER,
    }
    remove: tuple[str, ...] = (IN_PROGRESS, "mfa_enabled_at", *ADMIN_VERIFIED_FIELDS)
    if admin_target:
        set_["admin_state"] = ledger.ADMIN_PENDING
    else:
        remove = (*remove, "admin_state")
    if verified:
        set_.update(
            {
                "identity_verified_by": caller.user_id,
                "identity_verified_at": at,
                "identity_verified_method": verified[0],
            }
        )
        if verified[1]:
            set_["identity_verified_note"] = verified[1]
    tx = Tx()
    (
        tx.update_profile(profile, by=caller.user_id, set_=set_, remove=remove, increment=("reinvite_count",))
        .add(put({"PK": ledger.sub_pk(created["sub"]), "SK": ledger.SUB_SK, "user_id": user_id, "created_at": at}))
        .add(
            update(
                ledger.sub_pk(profile["sub"]),
                ledger.SUB_SK,
                set_={"revoked_at": at},
                increment=(),
                condition="attribute_exists(PK) AND attribute_not_exists(revoked_at)",
            )
        )
        .caller(caller)
        .add(
            audit_item(
                "user.reinvite",
                actor=caller,
                target_user_id=user_id,
                request=request,
                detail={
                    "step": "reinvite",
                    "reason": reason,
                    "email_changed": email != profile.get("email"),
                    "orphan_deleted": orphan_deleted,
                    "role": ledger.ROLE_ADMIN if admin_target else ledger.ROLE_USER,
                },
            )
        )
    )
    if not admin_target:
        tx.remove_admin(user_id)
    if verified:
        tx.add(
            audit_item(
                "user.identity_verified",
                actor=caller,
                target_user_id=user_id,
                request=request,
                detail={"method": verified[0], "before": "reinvite"},
            )
        )
    return tx


def reinvite(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    """停止 → 無効化 → 全セッション失効 → 削除 → 作成 → 新しい sub を同じ user_id に結ぶ (古い sub に無効の印)。

    台帳は有効に戻すまで停止のまま。admin は admin グループに付け直し、`mfa_enabled_at` と確認の記録を消し、
    admin の状態を準備中に戻す。途中で止まっても、もう一度呼ぶと続きから進む。
    """
    profile = load(user_id)
    reject_self(caller, profile)
    reason = validation.text(payload.get("reason"), "reason", validation.REASON_MAX, required=True)
    old_email = profile.get("email")
    email = validation.email(payload["email"]) if payload.get("email") is not None else old_email
    verified = _verified_before(payload.get("verified_before"))
    if email != old_email and _email_taken(email, user_id):
        raise errors.conflict(errors.USER_EXISTS, "同じメールアドレスの利用者がいます")
    role = validation.choice(payload["role"], "role", validation.ROLES) if "role" in payload else profile.get("role")
    admin_target = role == ledger.ROLE_ADMIN
    if admin_target and profile.get("kind") != ledger.KIND_EMPLOYEE:
        raise errors.validation("admin として再招待できるのは社員 (kind=employee) だけです")

    if not profile.get(IN_PROGRESS):
        profile = _start_reinvite(caller, profile, reason, request)
    _retire_sessions(profile.get("cognito_username"), delete=True)
    orphan_deleted = _clear_address(email, user_id)

    password = passwords.generate() if admin_target else None
    created = _create(email, password)
    if admin_target:
        _grant_group_or_undo(created)
    tx = _link_tx(
        caller,
        profile,
        created,
        email=email,
        admin_target=admin_target,
        verified=verified,
        reason=reason,
        orphan_deleted=orphan_deleted,
        request=request,
    )
    _write(
        tx,
        applied=lambda: maps(created["sub"], user_id),
        compensate=lambda: cognito.delete_user(created["username"]),
    )
    _log("user.reinvite", caller, user_id, admin=admin_target, email_changed=email != old_email)
    result = {"user_id": user_id, "email": email, "status": ledger.STATUS_SUSPENDED}
    if password:
        result["temporary_password"] = password
    return result


# =============================================================================
# admin の有効化・一時パスワード・admin を外す
# =============================================================================


def activate_admin(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    """確認待ちの admin を、本人以外の admin が別経路で確かめてから有効にする (ADR D9)。"""
    profile = load(user_id)
    reject_self(caller, profile)
    method = validation.verify_method(payload.get("method"))
    note = validation.text(payload.get("note"), "note", validation.NOTE_MAX)
    if profile.get(IN_PROGRESS):
        raise errors.reinvite_incomplete()
    if (
        profile.get("role") != ledger.ROLE_ADMIN
        or profile.get("admin_state") != ledger.ADMIN_AWAITING
        or profile.get("mfa_enabled_at") is None
    ):
        raise errors.conflict(errors.ADMIN_NOT_AWAITING, "admin の状態が確認待ちではありません")
    username = profile.get("cognito_username")
    state = (
        admin_guard.cognito_state(cognito.client(), cognito.user_pool_id(), username)
        if username
        else admin_guard.MISSING
    )
    if not (state.exists and state.enabled and state.in_admin_group and state.totp and state.sub == profile["sub"]):
        raise errors.conflict(
            errors.ADMIN_NOT_READY, "Cognito で有効な admin で、TOTP が登録されていることを確かめられません"
        )
    restored = _in_reinvite(profile)
    if restored:
        _require_reinvite_complete(profile, cognito.get_user(username))

    set_ = {
        "admin_state": ledger.ADMIN_ACTIVE,
        "admin_verified_by": caller.user_id,
        "admin_verified_at": ledger_writes.now_iso(),
        "admin_verified_method": method,
    }
    remove: tuple[str, ...] = () if note else ("admin_verified_note",)
    if note:
        set_["admin_verified_note"] = note
    if restored:
        set_["status"] = ledger.STATUS_ACTIVE
        remove = (*remove, "restore_requires")
    tx = Tx()
    (
        tx.update_profile(
            profile,
            by=caller.user_id,
            set_=set_,
            remove=remove,
            condition="#c_state = :c_awaiting",
            names={"#c_state": "admin_state"},
            values={":c_awaiting": ledger.ADMIN_AWAITING},
        )
        .add(ledger_writes.admin_set_add(user_id))
        .caller(caller)
        .add(
            audit_item(
                "admin.activate",
                actor=caller,
                target_user_id=user_id,
                request=request,
                detail={"method": method, "restored": restored},
            )
        )
    )
    _write(tx, applied=tx.applied_to(user_id))
    _log("admin.activate", caller, user_id, method=method, restored=restored)
    return {
        "user_id": user_id,
        "admin_state": ledger.ADMIN_ACTIVE,
        "status": ledger.STATUS_ACTIVE if restored else profile.get("status"),
    }


def issue_temporary_password(caller: UserContext, user_id: str, request: dict) -> dict:
    """admin のパスワードの変更 (判断 15): 一時パスワードを出し、本人が NEW_PASSWORD_REQUIRED で決め直す。"""
    profile = load(user_id)
    reject_self(caller, profile)
    username = profile.get("cognito_username")
    if not username or cognito.get_user(username) is None:
        raise errors.conflict(errors.COGNITO_USER_MISSING, "Cognito に利用者がいません")
    if cognito.ADMIN_GROUP not in cognito.groups_of(username):
        raise errors.conflict(errors.TARGET_NOT_ADMIN, "一時パスワードを出せるのは admin だけです")
    password = passwords.generate()
    fields = {"actor": caller, "target_user_id": user_id, "request": request}
    try:
        item = audit_item("admin.temporary_password", **fields, detail={"step": "requested"})
        ledger_writes._client().put_item(**item["Put"])
    except Exception as exc:
        raise errors.temporarily_unavailable("監査に書けませんでした。一時パスワードは発行していません") from exc
    try:
        cognito.set_temporary_password(username, password)
    except Exception:
        audit.emit("admin.temporary_password", **fields, service=SERVICE, result="failed")
        raise
    _log("admin.temporary_password", caller, user_id)
    return {"user_id": user_id, "temporary_password": password}


def revoke_admin(caller: UserContext, user_id: str, payload: dict, request: dict) -> dict:
    """admin を外す。台帳 (有効な集合から外す条件付き) → Cognito のグループの順。付けるのは招待か再招待。"""
    profile = load(user_id)
    if validation.boolean(payload.get("admin"), "admin"):
        raise errors.validation("admin を付けるには社員を招待するか、再招待で役割に admin を選んでください")
    reject_self(caller, profile)
    username = profile.get("cognito_username")
    in_group = bool(username and cognito.get_user(username) and cognito.ADMIN_GROUP in cognito.groups_of(username))
    if not in_group and not _is_admin_profile(profile):
        raise errors.conflict(errors.TARGET_NOT_ADMIN, "admin ではありません")
    remove = ("admin_state", "mfa_enabled_at", *ADMIN_VERIFIED_FIELDS)
    tx = Tx()
    tx.update_profile(profile, by=caller.user_id, set_={"role": ledger.ROLE_USER}, remove=remove)
    if _is_active_admin(profile):
        tx.remove_admin(user_id)
    tx.caller(caller).add(audit_item("admin.revoke", actor=caller, target_user_id=user_id, request=request))
    _write(tx, applied=tx.applied_to(user_id))
    if in_group:
        cognito.remove_from_admin(username)
    _log("admin.revoke", caller, user_id)
    return {"user_id": user_id, "role": ledger.ROLE_USER}
