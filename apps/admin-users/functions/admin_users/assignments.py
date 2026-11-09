"""利用期間の割り当てと逆引きを同じトランザクションで書く。

割り当ての相手は人かグループ (ADR D13)。人宛は `DPERIOD#{id}` / `USER#{user_id}#{assignment_id}` と
`USER#{user_id}` / `ASSIGN#...`、グループ宛は `DPERIOD#{id}` / `GROUP#{group_id}#{assignment_id}` と
`GROUP#{group_id}` / `ASSIGN#...`。重なりは同じ相手の中で見る。`target_type` の無い既存の行は人宛と読む。
ログイン記録を消すのは人宛の終了だけ (グループ宛の根拠の記録は条件 (3) で落ちる)。
"""

from admin_users import device_periods as periods
from admin_users import errors, groups, ledger_writes, users, validation
from admin_users.tx import Tx
from service_platform import ledger
from service_platform.ids import ULID_PATTERN, new_ulid
from service_platform.ledger import UserContext

FIELDS = (
    "assignment_id",
    "device_period_id",
    "account",
    "user_id",
    "group_id",
    "valid_from",
    "valid_to",
    "role",
    "serial",
    "assigned_by",
    "assigned_at",
    "ended_by",
)


def target_type(row: dict) -> str:
    return ledger.TARGET_GROUP if row.get("target_type") == ledger.TARGET_GROUP else ledger.TARGET_USER


def view(row: dict) -> dict:
    return {**{field: row.get(field) for field in FIELDS}, "target_type": target_type(row)}


def detail_view(row: dict, cache: dict | None = None) -> dict:
    """利用期間の詳細の 1 行。グループ宛にはグループの経路の名前を足す。"""
    result = view(row)
    if result["target_type"] == ledger.TARGET_GROUP and isinstance(row.get("group_id"), str):
        group = ledger._get(ledger.group_pk(row["group_id"]), ledger.GROUP_PROFILE_SK) or {}
        result["group_path_names"] = groups.path_names([str(item) for item in group.get("path") or []], cache)
    return result


def _audit_detail(row: dict) -> dict:
    if target_type(row) == ledger.TARGET_GROUP:
        return {"target_type": ledger.TARGET_GROUP, "group_id": row.get("group_id")}
    return {"target_type": ledger.TARGET_USER}


def add(caller: UserContext, period_id: str, payload: dict, request: dict) -> dict:
    period = periods.load_active(period_id)
    kind = validation.choice(payload.get("target_type", ledger.TARGET_USER), "target_type", validation.TARGET_TYPES)
    if kind == ledger.TARGET_GROUP:
        if payload.get("user_id") is not None:
            raise errors.validation("グループ宛の割り当てに user_id は指定できません")
        if payload.get("group_id") is None:
            raise errors.validation("group_id を指定してください")
        return _add_group(caller, period, payload, request)
    if payload.get("group_id") is not None:
        raise errors.validation("人宛の割り当てに group_id は指定できません")
    profile = users.load(payload.get("user_id"))
    user_id = profile["user_id"]
    start, end = periods.interval(payload, "valid_from", "valid_to", period)
    existing = periods.children(period_id, ledger.assignment_prefix(user_id))
    if any(periods.overlap(start, end, row["valid_from"], row.get("valid_to")) for row in existing):
        raise errors.conflict("ASSIGNMENT_OVERLAP", "同じ利用者の割り当ての期間が重なっています")
    assignment_id = new_ulid()
    tx = Tx()
    row = {
        "PK": ledger.dperiod_pk(period_id),
        "SK": ledger.assignment_sk(user_id, assignment_id),
        "assignment_id": assignment_id,
        "device_period_id": period_id,
        "account": period["account"],
        "target_type": ledger.TARGET_USER,
        "user_id": user_id,
        "valid_from": start,
        "valid_to": end,
        "role": ledger.ROLE_USER,
        "assigned_by": caller.user_id,
        "assigned_at": ledger_writes.now_iso(),
        "last_op_id": tx.op_id,
    }
    reverse = {
        "PK": ledger.user_pk(user_id),
        "SK": ledger.assign_sk(period_id, assignment_id),
        "account": period["account"],
        "device_period_id": period_id,
        "assignment_id": assignment_id,
        "valid_from": start,
        "valid_to": end,
        "org_name": period["org_name"],
        "last_op_id": tx.op_id,
    }
    tx.add(ledger_writes.put(row)).add(ledger_writes.put(reverse))
    periods.finish(
        tx,
        period,
        caller,
        "assignment.add",
        request,
        assignment_id=assignment_id,
        target_user_id=user_id,
        detail=_audit_detail(row),
    )
    return view(row)


def _add_group(caller: UserContext, period: dict, payload: dict, request: dict) -> dict:
    group = groups.load_active(payload.get("group_id"))
    group_id, period_id = group["group_id"], period["device_period_id"]
    start, end = periods.interval(payload, "valid_from", "valid_to", period)
    existing = periods.children(period_id, ledger.group_assignment_prefix(group_id))
    if any(periods.overlap(start, end, row["valid_from"], row.get("valid_to")) for row in existing):
        raise errors.conflict("ASSIGNMENT_OVERLAP", "同じグループの割り当ての期間が重なっています")
    assignment_id = new_ulid()
    tx = Tx()
    row = {
        "PK": ledger.dperiod_pk(period_id),
        "SK": ledger.group_assignment_sk(group_id, assignment_id),
        "assignment_id": assignment_id,
        "device_period_id": period_id,
        "account": period["account"],
        "target_type": ledger.TARGET_GROUP,
        "group_id": group_id,
        "valid_from": start,
        "valid_to": end,
        "role": ledger.ROLE_USER,
        "assigned_by": caller.user_id,
        "assigned_at": ledger_writes.now_iso(),
        "last_op_id": tx.op_id,
    }
    reverse = {
        "PK": ledger.group_pk(group_id),
        "SK": ledger.group_assign_sk(period_id, assignment_id),
        "account": period["account"],
        "device_period_id": period_id,
        "assignment_id": assignment_id,
        "valid_from": start,
        "valid_to": end,
        "org_name": period["org_name"],
        "last_op_id": tx.op_id,
    }
    tx.add(ledger_writes.put(row)).add(ledger_writes.put(reverse))
    groups.lock_group(tx, group)
    periods.finish(
        tx, period, caller, "assignment.add", request, assignment_id=assignment_id, detail=_audit_detail(row)
    )
    return view(row)


def _reverse_key(row: dict) -> tuple[str, str]:
    if target_type(row) == ledger.TARGET_GROUP:
        return ledger.group_pk(row["group_id"]), ledger.group_assign_sk(row["device_period_id"], row["assignment_id"])
    return ledger.user_pk(row["user_id"]), ledger.assign_sk(row["device_period_id"], row["assignment_id"])


def close(tx: Tx, row: dict, *, by: str, at: str) -> None:
    """割り当てと逆引きの終了を追加する。LOGIN は呼び出し側で利用者ごとに一度だけ扱う。"""
    previous = row.get("valid_to")
    for pk, sk in ((row["PK"], row["SK"]), _reverse_key(row)):
        tx.add(
            ledger_writes.update(
                pk,
                sk,
                set_={"valid_to": at, "ended_by": by, "last_op_id": tx.op_id},
                increment=(),
                condition="attribute_exists(PK) AND #c_end = :c_end",
                names={"#c_end": "valid_to"},
                values={":c_end": previous},
            ),
            errors.stale,
        )


def end(caller: UserContext, period_id: str, assignment_id: str, request: dict) -> dict:
    period = periods.load_active(period_id)
    row = None
    if ULID_PATTERN.fullmatch(assignment_id):
        rows = periods.children(period_id, "USER#") + periods.children(period_id, ledger.GROUP_ASSIGNMENT_PREFIX)
        row = next((r for r in rows if r.get("assignment_id") == assignment_id), None)
    if row is None:
        raise errors.ApiError(404, "ASSIGNMENT_NOT_FOUND", "割り当てが見つかりません")
    at = ledger_writes.now_iso()
    if not periods.is_open(row.get("valid_to"), at):
        raise errors.conflict("ASSIGNMENT_ENDED", "割り当ては終了しています")
    tx = Tx()
    close(tx, row, by=caller.user_id, at=at)
    audit_fields: dict = {"assignment_id": assignment_id, "detail": _audit_detail(row)}
    if target_type(row) == ledger.TARGET_USER:
        periods.delete_login(tx, row["user_id"], period, assignment_id=assignment_id)
        audit_fields["target_user_id"] = row["user_id"]
    periods.finish(tx, period, caller, "assignment.end", request, **audit_fields)
    return view({**row, "valid_to": at, "ended_by": caller.user_id})
