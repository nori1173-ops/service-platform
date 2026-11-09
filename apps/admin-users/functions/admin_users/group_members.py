"""所属 (ADR D13)。利用者 × グループ。

所属の ID (membership_id。ULID) は属させるたびに新しい値で、外して付け直せば別の値になる (条件 (3) の照合に使う)。
所属と逆引きは同じトランザクションで書き、グループの PROFILE の version を上げる。外すときは読んだ membership_id を
削除の条件にする (読んだ後に外して付け直されていれば 409 `CONFLICT`)。ログイン記録は消さない (根拠が無くなれば
条件 (3) で落ちる)。停止中の利用者も属させられる。
"""

from admin_users import errors, groups, ledger_writes, users
from admin_users.tx import Tx, write
from service_platform import audit, ledger
from service_platform.ids import ULID_PATTERN, new_ulid
from service_platform.ledger import UserContext


def _view(row: dict, user_id: str, group_id: str) -> dict:
    return {
        "membership_id": row.get("membership_id"),
        "user_id": user_id,
        "group_id": group_id,
        "path": [str(item) for item in row.get("path") or []],
        "added_at": row.get("added_at"),
        "added_by": row.get("added_by"),
    }


def _audit(action: str, caller: UserContext, request: dict, user_id: str, group_id: str, membership_id: str) -> dict:
    return audit.put_item_for_transaction(
        action,
        actor=caller,
        service=ledger_writes.SERVICE,
        target_user_id=user_id,
        request=request,
        detail={"group_id": group_id, "membership_id": membership_id},
        billing=True,
    )


def _user_exists(user_id: str) -> dict:
    return {
        "ConditionCheck": {
            "TableName": ledger.ledger_table_name(),
            "Key": ledger_writes._serialize({"PK": ledger.user_pk(user_id), "SK": ledger.PROFILE_SK}),
            "ConditionExpression": "attribute_exists(PK)",
        }
    }


def _delete(pk: str, sk: str, membership_id: str) -> dict:
    return {
        "Delete": {
            "TableName": ledger.ledger_table_name(),
            "Key": ledger_writes._serialize({"PK": pk, "SK": sk}),
            "ConditionExpression": "attribute_exists(PK) AND #m = :m",
            "ExpressionAttributeNames": {"#m": "membership_id"},
            "ExpressionAttributeValues": ledger_writes._serialize({":m": membership_id}),
        }
    }


def add(caller: UserContext, group_id: str, payload: dict, request: dict) -> dict:
    group = groups.load_active(group_id)
    profile = users.load(payload.get("user_id"))
    user_id = profile["user_id"]
    own_pk, own_sk = ledger.user_pk(user_id), ledger.member_sk(group_id)
    if ledger._get(own_pk, own_sk):
        raise errors.member_exists()
    tx = Tx()
    membership_id = new_ulid()
    at = ledger_writes.now_iso()
    path = [str(item) for item in group.get("path") or []]
    own = {
        "PK": own_pk,
        "SK": own_sk,
        "membership_id": membership_id,
        "group_id": group_id,
        "path": path,
        "added_at": at,
        "added_by": caller.user_id,
        "last_op_id": tx.op_id,
    }
    reverse = {
        "PK": ledger.group_pk(group_id),
        "SK": ledger.group_member_sk(user_id),
        "membership_id": membership_id,
        "user_id": user_id,
        "added_at": at,
        "added_by": caller.user_id,
        "last_op_id": tx.op_id,
    }
    tx.add(ledger_writes.put(own), errors.member_exists).add(ledger_writes.put(reverse), errors.member_exists)
    groups.lock_group(tx, group)
    if user_id != caller.user_id:
        # 自分自身なら呼び出し元の検査 (行があることを含む) 1 つにまとめる (同じ行への操作を 2 つ入れられない)
        tx.add(_user_exists(user_id), errors.not_found)
    tx.caller(caller).add(_audit("group.member_add", caller, request, user_id, group_id, membership_id))
    write(tx, applied=lambda: (ledger._get(own_pk, own_sk) or {}).get("last_op_id") == tx.op_id)
    return _view(own, user_id, group_id)


def remove(caller: UserContext, group_id: str, user_id: str, request: dict) -> dict:
    group = groups.load(group_id)
    if not isinstance(user_id, str) or not ULID_PATTERN.fullmatch(user_id):
        raise errors.member_not_found()
    own = ledger._get(ledger.user_pk(user_id), ledger.member_sk(group_id))
    if not own or not isinstance(own.get("membership_id"), str):
        raise errors.member_not_found()
    membership_id = own["membership_id"]
    tx = Tx()
    tx.add(_delete(ledger.user_pk(user_id), ledger.member_sk(group_id), membership_id), errors.stale)
    tx.add(_delete(ledger.group_pk(group_id), ledger.group_member_sk(user_id), membership_id), errors.stale)
    groups.version_update(tx, group["PK"], ledger.GROUP_PROFILE_SK, group.get("version"), require_active=False)
    tx.caller(caller).add(_audit("group.member_remove", caller, request, user_id, group_id, membership_id))
    pk = ledger.group_pk(group_id)
    write(tx, applied=lambda: (ledger._get(pk, ledger.GROUP_PROFILE_SK) or {}).get("last_op_id") == tx.op_id)
    return _view(own, user_id, group_id)
