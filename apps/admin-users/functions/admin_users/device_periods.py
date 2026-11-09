"""通信機の利用期間と、割り当て・許可を含む一括終了。

利用期間の access (ADR D14): everyone (既定) か assigned。正本と CURRENT に access・access_changed_at・
access_changed_by を書き、everyone にするたびに正本に新しい世代 (access_gen) を振る (前の everyone のログイン記録を
条件 (3) で落とすため。assigned にするときは変えない)。属性の無い既存の行は everyone・started_at・None と読む。
"""

import re
from datetime import UTC, datetime

from boto3.dynamodb.conditions import Key

from admin_users import errors, ledger_writes, validation
from admin_users.tx import Tx, write
from service_platform import audit, ledger
from service_platform.ids import ULID_PATTERN, new_ulid
from service_platform.ledger import UserContext

ACCOUNT_PATTERN = re.compile(r"^[0-9]{8}$")
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$"
)
META_SK = "META"
FIELDS = (
    "device_period_id",
    "account",
    "org_name",
    "status",
    "started_at",
    "started_by",
    "ended_at",
    "ended_by",
    "note",
)


def view(row: dict) -> dict:
    return {
        **{field: row.get(field) for field in FIELDS},
        "access": ledger.period_access(row),
        "access_changed_at": ledger.access_changed_at(row),
        "access_changed_by": ledger.access_changed_by(row),
    }


def query_all(**query) -> list[dict]:
    table = ledger._table()
    rows = []
    while True:
        page = table.query(**query)
        rows.extend(page.get("Items") or [])
        if not page.get("LastEvaluatedKey"):
            return rows
        query = {**query, "ExclusiveStartKey": page["LastEvaluatedKey"]}


def children(period_id: str, prefix: str = "") -> list[dict]:
    condition = Key("PK").eq(ledger.dperiod_pk(period_id))
    if prefix:
        condition &= Key("SK").begins_with(prefix)
    return query_all(KeyConditionExpression=condition, ConsistentRead=True)


def load(period_id: str) -> dict:
    """ID の参照行から DEVICE 配下の正本を強い整合で引く。GSI は使わない。"""
    if isinstance(period_id, str) and ULID_PATTERN.fullmatch(period_id):
        locator = ledger._get(ledger.dperiod_pk(period_id), META_SK)
        if locator and isinstance(locator.get("account"), str):
            period = ledger._get(ledger.device_pk(locator["account"]), ledger.dperiod_sk(period_id))
            if period and period.get("device_period_id") == period_id:
                return period
    raise errors.ApiError(404, "DEVICE_PERIOD_NOT_FOUND", "利用期間が見つかりません")


def start(caller: UserContext, payload: dict, request: dict) -> dict:
    account = payload.get("account")
    if not isinstance(account, str) or not ACCOUNT_PATTERN.fullmatch(account):
        raise errors.validation("account は半角数字 8 桁で指定してください")
    org_name = validation.text(payload.get("org_name"), "org_name", validation.ORG_NAME_MAX, required=True)
    note = validation.text(payload.get("note"), "note", validation.NOTE_MAX)
    access = validation.choice(payload.get("access", ledger.ACCESS_EVERYONE), "access", validation.ACCESS_CHOICES)
    period_id = new_ulid()
    at = ledger_writes.now_iso()
    tx = Tx()
    access_fields = {"access": access, "access_changed_at": at, "access_changed_by": caller.user_id}
    period = {
        "PK": ledger.device_pk(account),
        "SK": ledger.dperiod_sk(period_id),
        "GSI1PK": ledger.DPERIOD_GSI1PK,
        "GSI1SK": ledger.dperiod_gsi1sk(account, period_id),
        "device_period_id": period_id,
        "account": account,
        "org_name": org_name,
        "note": note,
        "status": "active",
        "started_at": at,
        "started_by": caller.user_id,
        **access_fields,
        "version": 1,
        "last_op_id": tx.op_id,
    }
    if access == ledger.ACCESS_EVERYONE:
        period["access_gen"] = new_ulid()
    current = {
        "PK": ledger.device_pk(account),
        "SK": ledger.CURRENT_SK,
        "GSI1PK": ledger.DEVICE_GSI1PK,
        "GSI1SK": ledger.device_gsi1sk(account),
        "device_period_id": period_id,
        "org_name": org_name,
        "started_at": at,
        **access_fields,
    }
    locator = {"PK": ledger.dperiod_pk(period_id), "SK": META_SK, "account": account, "device_period_id": period_id}
    tx.add(
        ledger_writes.put(current),
        lambda: errors.conflict("DEVICE_PERIOD_ACTIVE", "通信機には稼働中の利用期間があります"),
    )
    tx.add(ledger_writes.put(period)).add(ledger_writes.put(locator)).caller(caller).add(
        audit.put_item_for_transaction(
            "dperiod.start",
            actor=caller,
            service=ledger_writes.SERVICE,
            account=account,
            device_period_id=period_id,
            request=request,
            detail={"access": access},
            billing=True,
        )
    )
    write_checked(tx, period)
    return view(period)


def set_access(caller: UserContext, period_id: str, payload: dict, request: dict) -> dict:
    """access の変更。同じ値なら書かない。P の version を上げ、正本と CURRENT に変えた時刻と人を書く。"""
    target = validation.choice(payload.get("access"), "access", validation.ACCESS_CHOICES)
    period = load_active(period_id)
    before = ledger.period_access(period)
    if target == before:
        return view(period)
    at = ledger_writes.now_iso()
    tx = Tx()
    changes = {"access": target, "access_changed_at": at, "access_changed_by": caller.user_id}
    generation = {"access_gen": new_ulid()} if target == ledger.ACCESS_EVERYONE else {}
    period_update(tx, period, **changes, **generation)
    tx.add(
        ledger_writes.update(
            ledger.device_pk(period["account"]),
            ledger.CURRENT_SK,
            set_=changes,
            increment=(),
            condition="#c_p = :c_p",
            names={"#c_p": "device_period_id"},
            values={":c_p": period_id},
        ),
        errors.stale,
    )
    tx.caller(caller).add(
        audit.put_item_for_transaction(
            "dperiod.access",
            actor=caller,
            service=ledger_writes.SERVICE,
            account=period["account"],
            device_period_id=period_id,
            request=request,
            detail={"from": before, "to": target},
            billing=True,
        )
    )
    write_checked(tx, period)
    return view({**period, **changes})


def load_active(period_id: str) -> dict:
    period = load(period_id)
    if period.get("status") != "active":
        raise errors.conflict("DEVICE_PERIOD_ENDED", "利用期間は終了しています")
    return period


def timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise errors.validation(f"{name} は秒単位のタイムゾーン付き日時で指定してください")
    try:
        parsed = datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise errors.validation(f"{name} の日時が正しくありません") from exc
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def interval(payload: dict, start_name: str, end_name: str, period: dict) -> tuple[str, str | None]:
    start = timestamp(payload.get(start_name), start_name)
    end = timestamp(payload[end_name], end_name) if payload.get(end_name) is not None else None
    if start < period["started_at"] or (end is not None and end <= start):
        raise errors.validation("有効期間は利用期間の開始以後で、終了は開始より後にしてください")
    if period.get("ended_at") and (end is None or end > period["ended_at"] or start >= period["ended_at"]):
        raise errors.validation("有効期間を利用期間の中に収めてください")
    return start, end


def overlap(start: str, end: str | None, other_start: str, other_end: str | None) -> bool:
    return (end is None or other_start < end) and (other_end is None or start < other_end)


def is_open(end: str | None, at: str) -> bool:
    return end is None or at < end


def period_update(tx: Tx, period: dict, **changes) -> None:
    tx.add(
        ledger_writes.update(
            period["PK"],
            period["SK"],
            set_={**changes, "last_op_id": tx.op_id},
            condition="#c_status = :c_active AND #c_version = :c_version",
            names={"#c_status": "status", "#c_version": "version"},
            values={":c_active": "active", ":c_version": period["version"]},
        ),
        errors.stale,
    )


def finish(tx: Tx, period: dict, caller: UserContext, action: str, request: dict, **audit_fields) -> None:
    """親の version も更新し、一括終了の読み取り後の子行追加と競合したら全体を断る。"""
    period_update(tx, period)
    tx.caller(caller).add(
        audit.put_item_for_transaction(
            action,
            actor=caller,
            service=ledger_writes.SERVICE,
            account=period["account"],
            device_period_id=period["device_period_id"],
            request=request,
            billing=True,
            **audit_fields,
        )
    )
    write_checked(tx, period)


def write_checked(tx: Tx, period: dict) -> None:
    if len(tx.items) > 100:
        raise errors.conflict(
            "TRANSACTION_LIMIT_EXCEEDED", "一括操作の上限を超えています。先に割り当てや許可を終えてください"
        )
    write(tx, applied=lambda: (ledger._get(period["PK"], period["SK"]) or {}).get("last_op_id") == tx.op_id)


def delete_login(tx: Tx, user_id: str, period: dict, *, assignment_id: str | None = None) -> None:
    """有効な記録だけを削除する。読み取り後の作成も、終了した利用期間の認可では通らない。"""
    pk, sk = ledger.user_pk(user_id), ledger.login_sk(period["account"])
    row = ledger._get(pk, sk)
    if not row:
        return
    expires_at = row.get("expires_at")
    if expires_at is not None and expires_at <= datetime.fromisoformat(ledger_writes.now_iso()).timestamp():
        return
    if row.get("device_period_id") != period["device_period_id"] or (
        assignment_id is not None and row.get("assignment_id") != assignment_id
    ):
        return
    names = {"#p": "device_period_id"}
    values = {":p": period["device_period_id"]}
    condition = "#p = :p"
    if assignment_id is not None:
        condition += " AND #a = :a"
        names["#a"] = "assignment_id"
        values[":a"] = assignment_id
    tx.add(
        {
            "Delete": {
                "TableName": ledger.ledger_table_name(),
                "Key": ledger_writes._serialize({"PK": pk, "SK": sk}),
                "ConditionExpression": f"attribute_not_exists(PK) OR ({condition})",
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": ledger_writes._serialize(values),
            }
        },
        errors.stale,
    )


def detail(period_id: str) -> dict:
    from admin_users import assignments, grants

    period = load(period_id)
    rows = children(period_id)
    cache: dict = {}
    return {
        **view(period),
        "assignments": [
            assignments.detail_view(row, cache)
            for row in rows
            if row["SK"].startswith("USER#") or row["SK"].startswith(ledger.GROUP_ASSIGNMENT_PREFIX)
        ],
        "grants": [grants.view(row) for row in rows if row["SK"].startswith("GRANT#")],
    }


def list_periods(status: str = "active") -> list[dict]:
    validation.choice(status, "status", ("active", "all"))
    index_pk = ledger.DEVICE_GSI1PK if status == "active" else ledger.DPERIOD_GSI1PK
    rows = query_all(IndexName=ledger.GSI1, KeyConditionExpression=Key("GSI1PK").eq(index_pk))
    if status == "active":
        rows = [{**row, "account": row["GSI1SK"], "status": "active"} for row in rows]
    return [view(row) for row in rows]


def end(caller: UserContext, period_id: str, request: dict) -> dict:
    from admin_users import assignments, grants

    period = load_active(period_id)
    rows = children(period_id)
    at = ledger_writes.now_iso()
    tx = Tx()
    period_update(tx, period, status="ended", ended_at=at, ended_by=caller.user_id)
    tx.add(
        {
            "Delete": {
                "TableName": ledger.ledger_table_name(),
                "Key": ledger_writes._serialize({"PK": ledger.device_pk(period["account"]), "SK": ledger.CURRENT_SK}),
                "ConditionExpression": "device_period_id = :p",
                "ExpressionAttributeValues": ledger_writes._serialize({":p": period_id}),
            }
        },
        errors.stale,
    )
    assigned_users = set()
    for row in rows:
        if row["SK"].startswith("USER#"):
            assigned_users.add(row["user_id"])
            if is_open(row.get("valid_to"), at):
                assignments.close(tx, row, by=caller.user_id, at=at)
        elif row["SK"].startswith(ledger.GROUP_ASSIGNMENT_PREFIX) and is_open(row.get("valid_to"), at):
            assignments.close(tx, row, by=caller.user_id, at=at)
        elif row["SK"].startswith("GRANT#") and row.get("status") == "active" and is_open(row.get("ends_at"), at):
            grants.close(tx, row, by=caller.user_id, at=at)
    for user_id in sorted(assigned_users):
        delete_login(tx, user_id, period)
    tx.caller(caller).add(
        audit.put_item_for_transaction(
            "dperiod.end",
            actor=caller,
            service=ledger_writes.SERVICE,
            account=period["account"],
            device_period_id=period_id,
            request=request,
            billing=True,
        )
    )
    write_checked(tx, period)
    return view({**period, "status": "ended", "ended_at": at, "ended_by": caller.user_id})
