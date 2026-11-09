"""admin-users が書く台帳の行（計画 2.1。台帳の書き手は admin-users と base だけ）。

台帳の変更と監査の行は 1 つの TransactWriteItems にまとめる。再試行と成否の確定は shared の
`transactions.write` (同じ ClientRequestToken の送り直し、強い整合の読み直し)。

- 利用者の行の更新は、操作の最初に読んだ `version` と今の sub を条件にする (楽観ロック)。
- 有効な admin の集合 (`ADMIN#SET` / `ACTIVE` の `members`) を admin の有効化・停止・外す・再招待と同じ
  トランザクションで更新し、減らすときは「対象が集合にいるなら、ほかにも 1 人以上いる」を条件にする。
  人数の判定に GSI を使わない。
- 呼び出し元の利用者の行を ConditionCheck し、書き込みの時点でもまだ有効な admin であることを確かめる。
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.types import TypeSerializer

from service_platform import audit, ledger, transactions
from service_platform.ledger import UserContext

SERVICE = "admin-users"
NOT_EXISTS = "attribute_not_exists(PK)"

WriteFailed = transactions.WriteFailed
OutcomeUnknown = transactions.OutcomeUnknown

_serializer = TypeSerializer()
_client_cache = None


def reset_client() -> None:
    global _client_cache
    _client_cache = None


def _client():
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("dynamodb")
    return _client_cache


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize(values: dict) -> dict:
    return {key: _serializer.serialize(value) for key, value in values.items()}


def write(items: list[dict], *, applied: Callable[[], bool]) -> None:
    """台帳と監査をまとめて書く。`WriteFailed` (確定した失敗) か `OutcomeUnknown` を投げる。"""
    transactions.write(_client(), items, applied=applied)


def put(item: dict) -> dict:
    return {
        "Put": {"TableName": ledger.ledger_table_name(), "Item": _serialize(item), "ConditionExpression": NOT_EXISTS}
    }


def update(
    pk: str,
    sk: str,
    *,
    set_: dict,
    remove: Iterable[str] = (),
    increment: Iterable[str] = ("version",),
    condition: str,
    names: dict | None = None,
    values: dict | None = None,
) -> dict:
    """条件付きの Update。`set_` は値を入れる属性、`remove` は消す属性、`increment` は 1 を足す属性。

    条件式の名前と値は `names` / `values` で渡す (`#c_*` / `:c_*` の形にして本体の置き換えと紛れさせない)。
    """
    expression_names = dict(names or {})
    expression_values = dict(values or {})
    parts = []
    for index, (field, value) in enumerate(set_.items()):
        expression_names[f"#s{index}"] = field
        expression_values[f":s{index}"] = value
        parts.append(f"#s{index} = :s{index}")
    increment = tuple(increment)
    for index, field in enumerate(increment):
        expression_names[f"#i{index}"] = field
        parts.append(f"#i{index} = if_not_exists(#i{index}, :zero) + :one")
    if increment:
        expression_values.update({":zero": 0, ":one": 1})
    expression = "SET " + ", ".join(parts)
    removed = [field for field in remove if field not in set_]
    if removed:
        for index, field in enumerate(removed):
            expression_names[f"#r{index}"] = field
        expression += " REMOVE " + ", ".join(f"#r{index}" for index in range(len(removed)))
    return {
        "Update": {
            "TableName": ledger.ledger_table_name(),
            "Key": _serialize({"PK": pk, "SK": sk}),
            "UpdateExpression": expression,
            "ConditionExpression": condition,
            "ExpressionAttributeNames": expression_names,
            "ExpressionAttributeValues": _serialize(expression_values),
        }
    }


def profile_update(
    profile: dict,
    *,
    by: str,
    op_id: str,
    set_: dict | None = None,
    remove: Iterable[str] = (),
    increment: Iterable[str] = (),
    condition: str = "",
    names: dict | None = None,
    values: dict | None = None,
    check_version: bool = True,
) -> dict:
    """利用者の行の更新。読んだときの sub と version が変わっていないことを条件にし、`last_op_id` を書く。

    `check_version=False` は、別の条件 (email の変更の途中の印の操作 ID など) で直列化している段だけに使う。
    """
    version = profile.get("version")
    if not check_version:
        base = "#c_sub = :c_sub"
        base_values = {":c_sub": profile["sub"]}
    elif version is None:
        base = "#c_sub = :c_sub AND attribute_not_exists(#c_version)"
        base_values = {":c_sub": profile["sub"]}
    else:
        base = "#c_sub = :c_sub AND #c_version = :c_version"
        base_values = {":c_sub": profile["sub"], ":c_version": version}
    return update(
        profile["PK"],
        ledger.PROFILE_SK,
        set_={**(set_ or {}), "updated_by": by, "updated_at": now_iso(), "last_op_id": op_id},
        remove=remove,
        increment=("version", *increment),
        condition=f"{base} AND ({condition})" if condition else base,
        names={"#c_sub": "sub", **({"#c_version": "version"} if check_version else {}), **(names or {})},
        values={**base_values, **(values or {})},
    )


def _admin_set_key() -> dict:
    return _serialize({"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK})


def admin_set_add(user_id: str) -> dict:
    """有効な admin の集合に入れる (行が無ければ作る)。"""
    return {
        "Update": {
            "TableName": ledger.ledger_table_name(),
            "Key": _admin_set_key(),
            "UpdateExpression": "ADD #m :m",
            "ExpressionAttributeNames": {"#m": ledger.ADMIN_MEMBERS},
            "ExpressionAttributeValues": _serialize({":m": {user_id}}),
        }
    }


def admin_set_remove(user_id: str) -> dict:
    """有効な admin の集合から外す。対象が集合にいるなら、ほかにも 1 人以上いることを条件にする。"""
    return {
        "Update": {
            "TableName": ledger.ledger_table_name(),
            "Key": _admin_set_key(),
            "UpdateExpression": "DELETE #m :m",
            "ConditionExpression": "attribute_not_exists(#m) OR NOT contains(#m, :u) OR size(#m) > :one",
            "ExpressionAttributeNames": {"#m": ledger.ADMIN_MEMBERS},
            "ExpressionAttributeValues": _serialize({":m": {user_id}, ":u": user_id, ":one": 1}),
        }
    }


def caller_check(caller: UserContext) -> dict:
    """呼び出し元が書き込みの時点でもまだ有効な admin (同じ sub) であること。"""
    return {
        "ConditionCheck": {
            "TableName": ledger.ledger_table_name(),
            "Key": _serialize({"PK": ledger.user_pk(caller.user_id), "SK": ledger.PROFILE_SK}),
            "ConditionExpression": "#status = :active AND #state = :active AND #sub = :sub",
            "ExpressionAttributeNames": {"#status": "status", "#state": "admin_state", "#sub": "sub"},
            "ExpressionAttributeValues": _serialize({":active": ledger.STATUS_ACTIVE, ":sub": caller.sub}),
        }
    }


def audit_item(
    action: str,
    *,
    actor,
    target_user_id: str,
    request: dict,
    detail: dict | None = None,
    result: str = "ok",
    code: str | None = None,
) -> dict:
    return audit.put_item_for_transaction(
        action,
        actor=actor,
        service=SERVICE,
        target_user_id=target_user_id,
        request=request,
        detail=detail,
        result=result,
        code=code,
    )
