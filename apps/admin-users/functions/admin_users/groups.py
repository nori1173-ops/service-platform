"""グループ (ADR D13、admin-users 設計書の「グループと access」)。

木は 3 段まで。段の名前は持たない。名前の重なりは同じ親の下 (根は `GROUP#ROOT` の下) の子の控え (`CHILD#`) を
強い整合で読んで確かめ、親の PROFILE (根は `GROUP#ROOT` / `META`) の version で競合を落とす (GSI1 は結果整合
なので使わない)。付け替えは無い。終えるのは稼働中の子・所属・終わっていない割り当てが無いときだけで、行は消さない。
子・所属・割り当ての追加はどれもグループの PROFILE の version を上げるので、終了の読み取りの後の追加は競合で落ちる。

グループの名前とメモは本文として扱い、監査とログに入れない。
"""

from boto3.dynamodb.conditions import Key

from admin_users import errors, ledger_writes, validation
from admin_users.tx import Tx, write
from service_platform import audit, ledger
from service_platform.ids import ULID_PATTERN, new_ulid
from service_platform.ledger import UserContext

MAX_DEPTH = 3
STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"
CREATE_FIELDS = frozenset({"name", "parent_group_id", "note"})
UPDATE_FIELDS = frozenset({"name", "note"})
FIELDS = (
    "group_id",
    "name",
    "parent_group_id",
    "status",
    "note",
    "created_by",
    "created_at",
    "updated_by",
    "updated_at",
    "ended_by",
    "ended_at",
)


def view(row: dict, path_names: list | None = None) -> dict:
    result = {field: row.get(field) for field in FIELDS}
    result["path"] = [str(group_id) for group_id in row.get("path") or []]
    result["depth"] = int(row["depth"]) if row.get("depth") is not None else len(result["path"])
    if path_names is not None:
        result["path_names"] = path_names
    return result


def load(group_id: object) -> dict:
    if not isinstance(group_id, str) or not ULID_PATTERN.fullmatch(group_id):
        raise errors.group_not_found()
    row = ledger._get(ledger.group_pk(group_id), ledger.GROUP_PROFILE_SK)
    if not row or row.get("group_id") != group_id:
        raise errors.group_not_found()
    return row


def load_active(group_id: object) -> dict:
    row = load(group_id)
    if row.get("status") != STATUS_ACTIVE:
        raise errors.group_ended()
    return row


def path_names(path: list, cache: dict | None = None) -> list:
    """経路の group_id ごとの名前 (根から)。読めないものは None。"""
    cache = {} if cache is None else cache
    names = []
    for group_id in path:
        if group_id not in cache:
            cache[group_id] = (ledger._get(ledger.group_pk(group_id), ledger.GROUP_PROFILE_SK) or {}).get("name")
        names.append(cache[group_id])
    return names


def _parent_pk(parent_group_id: str | None) -> str:
    return ledger.group_pk(parent_group_id) if parent_group_id else ledger.GROUP_ROOT_PK


def _partition(pk: str) -> list[dict]:
    from admin_users import device_periods

    return device_periods.query_all(KeyConditionExpression=Key("PK").eq(pk), ConsistentRead=True)


def _ensure_name_free(parent_pk: str, name: str, *, except_id: str | None = None) -> None:
    for child in ledger.query_prefix(parent_pk, ledger.CHILD_PREFIX):
        if child.get("status") == STATUS_ACTIVE and child.get("name") == name and child.get("group_id") != except_id:
            raise errors.group_name_taken()


def version_update(tx: Tx, pk: str, sk: str, version: object, *, require_active: bool) -> None:
    """読んだ version を条件に version を上げる (行が無ければ version 1 で作る。根の META のため)。"""
    names = {"#c_version": "version"}
    values: dict = {}
    if version is None:
        condition = "attribute_not_exists(#c_version)"
    else:
        condition = "#c_version = :c_version"
        values[":c_version"] = version
    if require_active:
        condition += " AND #c_status = :c_active"
        names["#c_status"] = "status"
        values[":c_active"] = STATUS_ACTIVE
    tx.add(
        ledger_writes.update(pk, sk, set_={"last_op_id": tx.op_id}, condition=condition, names=names, values=values),
        errors.stale,
    )


def lock_group(tx: Tx, group: dict) -> None:
    """グループの PROFILE の version を上げる (稼働中であること)。子・所属・割り当ての追加で使う。"""
    version_update(tx, group["PK"], ledger.GROUP_PROFILE_SK, group.get("version"), require_active=True)


def _lock_parent(tx: Tx, parent: dict | None, root_meta: dict | None) -> None:
    if parent is not None:
        lock_group(tx, parent)
    else:
        version_update(
            tx, ledger.GROUP_ROOT_PK, ledger.GROUP_META_SK, (root_meta or {}).get("version"), require_active=False
        )


def _audit(action: str, caller: UserContext, request: dict, detail: dict) -> dict:
    return audit.put_item_for_transaction(
        action, actor=caller, service=ledger_writes.SERVICE, request=request, detail=detail
    )


def _write(tx: Tx, group_id: str) -> None:
    pk = ledger.group_pk(group_id)
    write(tx, applied=lambda: (ledger._get(pk, ledger.GROUP_PROFILE_SK) or {}).get("last_op_id") == tx.op_id)


def _reject_unknown(payload: dict, allowed: frozenset) -> None:
    if set(payload) - allowed:
        raise errors.validation("受け付けない項目があります (段の名前は指定しません)")


def list_groups(status: str = "active") -> list[dict]:
    """一覧 (GSI1 の `GROUP`。結果整合)。経路の順 (木の順)。"""
    from admin_users import device_periods

    validation.choice(status, "status", ("active", "all"))
    rows = device_periods.query_all(IndexName=ledger.GSI1, KeyConditionExpression=Key("GSI1PK").eq(ledger.GROUP_GSI1PK))
    rows.sort(key=lambda row: str(row.get("GSI1SK") or ""))
    return [view(row) for row in rows if status == "all" or row.get("status") == STATUS_ACTIVE]


def create(caller: UserContext, payload: dict, request: dict) -> dict:
    _reject_unknown(payload, CREATE_FIELDS)
    name = validation.text(payload.get("name"), "name", validation.GROUP_NAME_MAX, required=True)
    note = validation.text(payload.get("note"), "note", validation.NOTE_MAX)
    parent_id = payload.get("parent_group_id")
    if parent_id is not None and not isinstance(parent_id, str):
        raise errors.validation("parent_group_id は文字列で指定してください")
    group_id = new_ulid()
    parent = root_meta = None
    if parent_id is not None:
        parent = load_active(parent_id)
        depth = int(parent.get("depth") or 0) + 1
        if depth > MAX_DEPTH:
            raise errors.group_depth_exceeded()
        path = [*[str(item) for item in parent.get("path") or []], group_id]
    else:
        root_meta = ledger._get(ledger.GROUP_ROOT_PK, ledger.GROUP_META_SK)
        depth, path = 1, [group_id]
    parent_pk = _parent_pk(parent_id)
    _ensure_name_free(parent_pk, name)
    at = ledger_writes.now_iso()
    tx = Tx()
    row = {
        "PK": ledger.group_pk(group_id),
        "SK": ledger.GROUP_PROFILE_SK,
        "GSI1PK": ledger.GROUP_GSI1PK,
        "GSI1SK": ledger.group_gsi1sk(path),
        "group_id": group_id,
        "name": name,
        "path": path,
        "depth": depth,
        "status": STATUS_ACTIVE,
        "note": note,
        "created_by": caller.user_id,
        "created_at": at,
        "updated_by": caller.user_id,
        "updated_at": at,
        "version": 1,
        "last_op_id": tx.op_id,
    }
    if parent_id is not None:
        row["parent_group_id"] = parent_id
    child = {
        "PK": parent_pk,
        "SK": ledger.child_sk(group_id),
        "group_id": group_id,
        "name": name,
        "status": STATUS_ACTIVE,
        "last_op_id": tx.op_id,
    }
    tx.add(ledger_writes.put(row)).add(ledger_writes.put(child), errors.stale)
    _lock_parent(tx, parent, root_meta)
    tx.caller(caller).add(
        _audit("group.create", caller, request, {"group_id": group_id, "parent_group_id": parent_id, "depth": depth})
    )
    _write(tx, group_id)
    return view(row, path_names(path[:-1]) + [name])


def detail(group_id: str) -> dict:
    """詳細: 経路の名前、子、所属 (利用者の控え)、このグループ宛の割り当て。"""
    row = load(group_id)
    at = ledger_writes.now_iso()
    children, members, assignments = [], [], []
    for item in _partition(ledger.group_pk(group_id)):
        sk = item["SK"]
        if sk.startswith(ledger.CHILD_PREFIX):
            children.append({"group_id": item.get("group_id"), "name": item.get("name"), "status": item.get("status")})
        elif sk.startswith(ledger.MEMBER_PREFIX):
            profile = ledger.get_profile(str(item.get("user_id"))) or {}
            members.append(
                {
                    "membership_id": item.get("membership_id"),
                    "user_id": item.get("user_id"),
                    "email": profile.get("email"),
                    "kind": profile.get("kind"),
                    "org_name": profile.get("org_name"),
                    "status": profile.get("status"),
                    "added_at": item.get("added_at"),
                    "added_by": item.get("added_by"),
                }
            )
        elif sk.startswith(ledger.ASSIGN_PREFIX):
            assignments.append(
                {
                    "assignment_id": item.get("assignment_id"),
                    "device_period_id": item.get("device_period_id"),
                    "account": item.get("account"),
                    "org_name": item.get("org_name"),
                    "valid_from": item.get("valid_from"),
                    "valid_to": item.get("valid_to"),
                    "state": _state(item.get("valid_from"), item.get("valid_to"), at),
                }
            )
    members.sort(key=lambda member: (member["email"] or "", member["user_id"] or ""))
    assignments.sort(key=lambda item: item["valid_from"] or "", reverse=True)
    return {
        **view(row, path_names([str(item) for item in row.get("path") or []])),
        "children": children,
        "members": members,
        "assignments": assignments,
    }


def _state(valid_from: object, valid_to: object, at: str) -> str:
    if isinstance(valid_to, str) and valid_to <= at:
        return "ended"
    return "upcoming" if isinstance(valid_from, str) and at < valid_from else "current"


def update(caller: UserContext, group_id: str, payload: dict, request: dict) -> dict:
    _reject_unknown(payload, UPDATE_FIELDS)
    if not set(payload) & UPDATE_FIELDS:
        raise errors.validation("name か note を指定してください")
    changes: dict = {}
    name = None
    if "name" in payload:
        name = validation.text(payload.get("name"), "name", validation.GROUP_NAME_MAX, required=True)
    note = validation.text(payload.get("note"), "note", validation.NOTE_MAX) if "note" in payload else None
    row = load_active(group_id)
    if name is not None and name != row.get("name"):
        changes["name"] = name
    if "note" in payload and note != row.get("note"):
        changes["note"] = note
    path = [str(item) for item in row.get("path") or []]
    if not changes:
        return view(row, path_names(path))
    tx = Tx()
    if "name" in changes:
        parent_id = row.get("parent_group_id")
        parent = ledger._get(ledger.group_pk(parent_id), ledger.GROUP_PROFILE_SK) if parent_id else None
        root_meta = None if parent_id else ledger._get(ledger.GROUP_ROOT_PK, ledger.GROUP_META_SK)
        parent_pk = _parent_pk(parent_id)
        _ensure_name_free(parent_pk, changes["name"], except_id=group_id)
    at = ledger_writes.now_iso()
    tx.add(
        ledger_writes.update(
            row["PK"],
            ledger.GROUP_PROFILE_SK,
            set_={**changes, "updated_by": caller.user_id, "updated_at": at, "last_op_id": tx.op_id},
            condition="#c_status = :c_active AND #c_version = :c_version",
            names={"#c_status": "status", "#c_version": "version"},
            values={":c_active": STATUS_ACTIVE, ":c_version": row.get("version")},
        ),
        errors.stale,
    )
    if "name" in changes:
        tx.add(
            ledger_writes.update(
                parent_pk,
                ledger.child_sk(group_id),
                set_={"name": changes["name"], "last_op_id": tx.op_id},
                increment=(),
                condition="attribute_exists(PK)",
            ),
            errors.stale,
        )
        if parent is not None:
            version_update(tx, parent["PK"], ledger.GROUP_PROFILE_SK, parent.get("version"), require_active=False)
        else:
            _lock_parent(tx, None, root_meta)
    tx.caller(caller).add(_audit("group.update", caller, request, {"group_id": group_id, "changed": sorted(changes)}))
    _write(tx, group_id)
    return view({**row, **changes, "updated_by": caller.user_id, "updated_at": at}, path_names(path))


def end(caller: UserContext, group_id: str, request: dict) -> dict:
    row = load_active(group_id)
    at = ledger_writes.now_iso()
    for item in _partition(ledger.group_pk(group_id)):
        sk = item["SK"]
        if (
            (sk.startswith(ledger.CHILD_PREFIX) and item.get("status") == STATUS_ACTIVE)
            or sk.startswith(ledger.MEMBER_PREFIX)
            or (
                sk.startswith(ledger.ASSIGN_PREFIX)
                and _state(item.get("valid_from"), item.get("valid_to"), at) != "ended"
            )
        ):
            raise errors.group_in_use()
    tx = Tx()
    ended = {"status": STATUS_ENDED, "ended_by": caller.user_id, "ended_at": at}
    tx.add(
        ledger_writes.update(
            row["PK"],
            ledger.GROUP_PROFILE_SK,
            set_={**ended, "updated_by": caller.user_id, "updated_at": at, "last_op_id": tx.op_id},
            condition="#c_status = :c_active AND #c_version = :c_version",
            names={"#c_status": "status", "#c_version": "version"},
            values={":c_active": STATUS_ACTIVE, ":c_version": row.get("version")},
        ),
        errors.stale,
    )
    tx.add(
        ledger_writes.update(
            _parent_pk(row.get("parent_group_id")),
            ledger.child_sk(group_id),
            set_={"status": STATUS_ENDED, "last_op_id": tx.op_id},
            increment=(),
            condition="attribute_exists(PK)",
        ),
        errors.stale,
    )
    tx.caller(caller).add(_audit("group.end", caller, request, {"group_id": group_id}))
    _write(tx, group_id)
    path = [str(item) for item in row.get("path") or []]
    return view({**row, **ended, "updated_by": caller.user_id, "updated_at": at}, path_names(path))
