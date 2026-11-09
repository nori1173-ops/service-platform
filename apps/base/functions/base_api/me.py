"""自分の状態、admin の TOTP の登録を終えた、自分の割り当てと許可の一覧、通信機ログインの一覧
（ADR-001 D5 / D9 / D13 / D14、計画 4.2 / 4.5 / 4.12）。"""

import time
from collections.abc import Iterable

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from base_api import clients, clock, errors, ledger_writes
from service_platform import access, admin_guard, audit, auth, cognito_extra, ledger, services, transactions
from service_platform.admin_guard import AdminMfaMissingError
from service_platform.auth import Claims, ForbiddenError
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

logger = get_logger()


class NotAdminError(ForbiddenError):
    """admin グループの利用者ではない（403）。"""

    code = "NOT_ADMIN"

    def __init__(self, message: str = "管理者として招待された利用者だけが使えます") -> None:
        super().__init__(message)


class AdminNotPendingError(ForbiddenError):
    """admin の状態が準備中ではない（403）。"""

    code = "ADMIN_NOT_PENDING"

    def __init__(self, message: str = "TOTP の登録を受け付ける状態ではありません") -> None:
        super().__init__(message)


class TemporarilyUnavailableError(Exception):
    """Cognito か台帳が一時的に使えない (503)。要求の監査は記録済みの場合もあり、呼び直せば続きから進む。"""

    code = "TEMPORARILY_UNAVAILABLE"

    def __init__(self, message: str = "一時的に処理できませんでした。しばらくしてからもう一度呼んでください") -> None:
        super().__init__(message)


def view(claims: Claims, user: UserContext) -> dict:
    """自分の状態 (例外 2)。ほかの利用者のこと、連絡先、`mfa_enabled_at` の値は返さない。"""
    profile = ledger.get_profile(user.user_id) or {}
    return {
        "user_id": user.user_id,
        "email": user.email,
        "status": user.status,
        "restore_requires": profile.get("restore_requires"),
        "kind": user.kind,
        "org_name": profile.get("org_name"),
        "is_admin": auth.is_admin(claims),
        "admin_state": user.admin_state,
        "mfa_registered": user.mfa_enabled_at is not None,
    }


def register_totp(claims: Claims, user: UserContext, *, request: dict) -> str:
    """TOTP の登録を終えた (例外 1)。自分の行だけを準備中 → 確認待ちにし、それまでのセッションを失効させる。

    現在の TOTP を確かめ、全セッションの失効を台帳の更新より先に行う。台帳の更新が失敗しても、本人は手元の ID トークンで
    (期限内なら) 呼び直せる。`mfa_enabled_at` は失効の後の時刻なので、失効の前のセッションは
    admin の API で `ADMIN_SESSION_STALE` になる。
    """
    if not auth.is_admin(claims) or not user.cognito_username:
        raise NotAdminError()
    if user.admin_state != ledger.ADMIN_PENDING:
        raise AdminNotPendingError()

    cognito = clients.cognito()
    state = admin_guard.cognito_state(cognito, clients.user_pool_id(), user.cognito_username)
    if not (state.exists and state.enabled and state.in_admin_group and state.sub == user.sub):
        raise NotAdminError()
    if not state.totp:
        raise AdminMfaMissingError("TOTP が登録されていません。登録を終えてから呼んでください")

    try:
        cognito.admin_user_global_sign_out(UserPoolId=clients.user_pool_id(), Username=user.cognito_username)
    except (ClientError, BotoCoreError) as exc:
        logger.error("admin.totp_registered sign-out failed", {"user_id": user.user_id, "error": type(exc).__name__})
        raise TemporarilyUnavailableError() from exc
    try:
        registered = ledger_writes.mark_totp_registered(user, at=int(time.time()), request=request)
    except (transactions.WriteFailed, transactions.OutcomeUnknown) as exc:
        logger.error("admin.totp_registered ledger failed", {"user_id": user.user_id, "error": type(exc).__name__})
        raise TemporarilyUnavailableError() from exc
    if not registered:
        raise AdminNotPendingError()
    logger.info("admin.totp_registered", {"user_id": user.user_id, "result": "ok"})
    return ledger.ADMIN_AWAITING


STATE_ORDER = {"current": 0, "upcoming": 1, "ended": 2}


def _state(period: dict | None, row: dict, at: str) -> str:
    if not period or period.get("status") != access.STATUS_ACTIVE:
        return "ended"
    valid_from, valid_to = row.get("valid_from"), row.get("valid_to")
    if not isinstance(valid_from, str) or (valid_to is not None and (not isinstance(valid_to, str) or valid_to <= at)):
        return "ended"
    return "current" if valid_from <= at else "upcoming"


def _open_grants(device_period_id: str, at: str) -> list[dict]:
    """利用期間の許可のうち、終わっていないもの (今有効なものと、これから始まるもの)。"""
    rows = access._query(ledger.dperiod_pk(device_period_id), "GRANT#")
    grants = [
        {
            "service": row["service"],
            "name": services.SERVICES[row["service"]].name,
            "starts_at": row["starts_at"],
            "ends_at": row.get("ends_at"),
            "current": row["starts_at"] <= at,
        }
        for row in rows
        if row.get("status") == access.STATUS_ACTIVE
        and row.get("service") in services.SERVICES
        and isinstance(row.get("starts_at"), str)
        and (row.get("ends_at") is None or at < row["ends_at"])
    ]
    return sorted(grants, key=lambda grant: (grant["service"], grant["starts_at"]))


def _group_label(group_id: str, cache: dict) -> str | None:
    """グループの経路の名前を「 > 」でつないだもの (グループの PROFILE を GetItem)。"""
    if group_id not in cache:
        group = ledger._get(ledger.group_pk(group_id), ledger.GROUP_PROFILE_SK) or {}
        names = []
        for item in group.get("path") or []:
            if item == group_id:
                names.append(group.get("name"))
            else:
                names.append((ledger._get(ledger.group_pk(str(item)), ledger.GROUP_PROFILE_SK) or {}).get("name"))
        cache[group_id] = " > ".join(str(name) for name in names if name) or None
    return cache[group_id]


def _saved_serials(user: UserContext) -> dict[str, str]:
    """本人の製造番号の控え (`SERIAL#`) を 1 回の Query で読み、device_period_id から引ける辞書にする。"""
    serials = {}
    for row in ledger.query_prefix(ledger.user_pk(user.user_id), ledger.SERIAL_PREFIX):
        sk, serial = row.get("SK"), row.get("serial")
        if isinstance(sk, str) and isinstance(serial, str) and serial:
            serials[sk[len(ledger.SERIAL_PREFIX) :]] = serial
    return serials


def _group_rows(user: UserContext) -> list[dict]:
    """本人の所属の経路にあるグループ宛の割り当ての逆引き (`GROUP#{group_id}` / `ASSIGN#...`)。"""
    group_ids = sorted(
        {
            str(group_id)
            for row in ledger.list_memberships(user.user_id)
            for group_id in row.get("path") or []
            if isinstance(group_id, str)
        }
    )
    rows = []
    for group_id in group_ids:
        for row in ledger.query_prefix(ledger.group_pk(group_id), ledger.ASSIGN_PREFIX):
            if isinstance(row.get("device_period_id"), str) and isinstance(row.get("account"), str):
                rows.append({**row, "group_id": group_id})
    return rows


def assignments(user: UserContext) -> dict:
    """自分の割り当て (人宛とグループ宛。今有効・これから・終わった) と、終わっていない割り当ての利用期間の許可。

    ほかの利用者の割り当ては読まない (利用期間の中の自分の行と、自分の所属の経路のグループの行だけを Query する)。
    """
    return _assignments(user, _saved_serials(user))


def _assignments(user: UserContext, serials: dict[str, str]) -> dict:
    at = access.iso(clock.now())
    by_period: dict[str, list[dict]] = {}
    for row in ledger.list_user_assignments(user.user_id):
        if isinstance(row.get("device_period_id"), str) and isinstance(row.get("account"), str):
            by_period.setdefault(row["device_period_id"], []).append({**row, "_target": ledger.TARGET_USER})
    for row in _group_rows(user):
        by_period.setdefault(row["device_period_id"], []).append({**row, "_target": ledger.TARGET_GROUP})
    labels: dict = {}
    result = []
    for period_id, reverse_rows in by_period.items():
        account = reverse_rows[0]["account"]
        period = ledger._get(ledger.device_pk(account), ledger.dperiod_sk(period_id))
        own = {}
        if any(row["_target"] == ledger.TARGET_USER for row in reverse_rows):
            own = {
                row["assignment_id"]: row
                for row in access._query(ledger.dperiod_pk(period_id), ledger.assignment_prefix(user.user_id))
                if isinstance(row.get("assignment_id"), str)
            }
        grants = None
        for reverse in reverse_rows:
            group = reverse["_target"] == ledger.TARGET_GROUP
            row = reverse if group else own.get(reverse.get("assignment_id")) or reverse
            state = _state(period, row, at)
            if state != "ended" and grants is None:
                grants = _open_grants(period_id, at)
            serial = serials.get(period_id) if group else row.get("serial") or None
            result.append(
                {
                    "assignment_id": reverse.get("assignment_id"),
                    "device_period_id": period_id,
                    "account": account,
                    "org_name": reverse.get("org_name") or (period or {}).get("org_name"),
                    "target_type": reverse["_target"],
                    "group_id": reverse["group_id"] if group else None,
                    "group_label": _group_label(reverse["group_id"], labels) if group else None,
                    "valid_from": row.get("valid_from"),
                    "valid_to": row.get("valid_to"),
                    "serial": serial if state != "ended" else None,
                    "state": state,
                    "grants": grants if state != "ended" else [],
                }
            )
    result.sort(key=lambda item: item["valid_from"] or "", reverse=True)
    result.sort(key=lambda item: STATE_ORDER[item["state"]])
    return {"assignments": result}


def _everyone_pointers() -> list[dict]:
    """稼働中の利用期間の指しの写し (GSI1 の DEVICE。結果整合) のうち、access が everyone (か無い) のもの。"""
    table = ledger._table()
    query = {"IndexName": ledger.GSI1, "KeyConditionExpression": Key("GSI1PK").eq(ledger.DEVICE_GSI1PK)}
    rows: list[dict] = []
    while True:
        page = table.query(**query)
        rows.extend(page.get("Items") or [])
        if not page.get("LastEvaluatedKey"):
            break
        query = {**query, "ExclusiveStartKey": page["LastEvaluatedKey"]}
    return [
        row
        for row in rows
        if ledger.period_access(row) == ledger.ACCESS_EVERYONE
        and isinstance(row.get("GSI1SK"), str)
        and isinstance(row.get("device_period_id"), str)
    ]


def _service_count(service_ids: Iterable[str]) -> int:
    """今有効な許可のサービスの数 (同じサービスは 1 つ、定義に無いサービスは数えない)。"""
    return len({service for service in service_ids if service in services.SERVICES})


def devices(user: UserContext) -> dict:
    """通信機ログインの一覧: 今有効な割り当て (人宛・グループ宛) と、everyone の稼働中の通信機。

    通信機アカウントで 1 行にする (割り当てを優先、人宛 → グループ宛)。並びは割り当て → everyone、それぞれ
    通信機アカウントの順。表示だけで、認可には使わない (通信機ログインは強い整合の条件で確かめ直す)。
    `service_count` は画面の近道  の判定だけに使う。割り当ては `/me/assignments` と同じ許可の読み取りから数え、
    everyone は利用期間ごとに許可を Query する。
    """
    now = clock.now()
    serials = _saved_serials(user)
    assigned: dict[str, dict] = {}
    for item in _assignments(user, serials)["assignments"]:
        if item["state"] != "current":
            continue
        current = assigned.get(item["account"])
        if current is None or (current["basis"] == ledger.TARGET_GROUP and item["target_type"] == ledger.TARGET_USER):
            assigned[item["account"]] = {
                "account": item["account"],
                "device_period_id": item["device_period_id"],
                "org_name": item["org_name"],
                "basis": item["target_type"],
                "assignment_id": item["assignment_id"],
                "group_id": item["group_id"],
                "group_label": item["group_label"],
                "valid_from": item["valid_from"],
                "valid_to": item["valid_to"],
                "serial": item["serial"],
                "service_count": _service_count(grant["service"] for grant in item["grants"] if grant["current"]),
            }
    everyone = []
    for pointer in _everyone_pointers():
        account = pointer["GSI1SK"]
        if account in assigned:
            continue
        everyone.append(
            {
                "account": account,
                "device_period_id": pointer["device_period_id"],
                "org_name": pointer.get("org_name"),
                "basis": access.BASIS_EVERYONE,
                "assignment_id": None,
                "group_id": None,
                "group_label": None,
                "valid_from": None,
                "valid_to": None,
                "serial": serials.get(pointer["device_period_id"]),
                "service_count": _service_count(
                    grant.service for grant in access.active_grants(pointer["device_period_id"], now)
                ),
            }
        )
    rows = [assigned[account] for account in sorted(assigned)] + sorted(everyone, key=lambda row: row["account"])
    return {"devices": rows}


def enable_passkey_mfa(claims: Claims, user: UserContext, *, request: dict) -> None:
    """有効な admin 本人が、パスキーの登録後に MFA として使えるようにする。"""
    cognito = clients.cognito()
    admin_guard.require_admin(claims, user, cognito, user_pool_id=clients.user_pool_id())
    record = audit.put_item_for_transaction(
        "admin.passkey_mfa_requested",
        actor=user,
        service="base",
        target_user_id=user.user_id,
        request=request,
    )
    try:
        clients.dynamodb().transact_write_items(TransactItems=[record])
    except (ClientError, BotoCoreError) as exc:
        logger.error("admin.passkey_mfa audit failed", {"user_id": user.user_id, "error": type(exc).__name__})
        raise TemporarilyUnavailableError() from exc
    try:
        cognito_extra.admin_enable_passkey_mfa(cognito, clients.user_pool_id(), user.cognito_username)
    except (ClientError, BotoCoreError) as exc:
        missing_passkey = (
            isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == "InvalidParameterException"
        )
        code = "PASSKEY_REQUIRED" if missing_passkey else "TEMPORARILY_UNAVAILABLE"
        audit.emit(
            "admin.passkey_mfa_enabled",
            actor=user,
            service="base",
            target_user_id=user.user_id,
            request=request,
            result="failed",
            code=code,
        )
        logger.error("admin.passkey_mfa failed", {"user_id": user.user_id, "error": type(exc).__name__})
        if missing_passkey:
            raise errors.ApiError(409, "PASSKEY_REQUIRED", "先にパスキーを登録してください") from exc
        raise TemporarilyUnavailableError() from exc
    audit.emit(
        "admin.passkey_mfa_enabled",
        actor=user,
        service="base",
        target_user_id=user.user_id,
        request=request,
        result="ok",
    )
