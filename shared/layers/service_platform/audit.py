"""監査の行の組み立てと書き込み（計画 2.2、ADR-001 D12）。

監査の行の組み立ての唯一の置き場。各サービスに渡すのは監査テーブルへの `PutItem` だけで、
`PutItem` は同じキーの行を置き換えられるため、`attribute_not_exists(PK)` を付け、SK に ULID を
入れて既存の行を上書きさせない。

入れないもの: 暗証番号、トークン、一時パスワード、連絡先、現場名・地点名・宛先などの本文。
`detail` に秘密らしい名前の項目があれば落とし、落とした項目の名前だけを `detail_redacted` に残す。
本文は名前では見分けられないので、呼び出し側が `detail` に入れない。
"""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer

from service_platform.ids import new_ulid
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

AUDIT_TABLE_ENV = "AUDIT_TABLE"
CONDITION = "attribute_not_exists(PK)"
TTL = timedelta(days=365)

SERVICES = frozenset({"base", "admin-users", "camera", "scripts"})
RESULTS = frozenset({"ok", "denied", "failed"})
SCRIPT_ACTOR = "script"
SYSTEM_ACTOR_PREFIX = "system:"
ACTION_PATTERN = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")

SECRET_KEYS = frozenset({"pin", "contact", "otp", "session", "authorization", "cookie"})
SECRET_KEY_PARTS = ("password", "passcode", "token", "secret")

logger = get_logger()
_serializer = TypeSerializer()
_client_cache = None


def reset_clients() -> None:
    """boto3 クライアントのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _client_cache
    _client_cache = None


def _client():
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("dynamodb")
    return _client_cache


def audit_table_name() -> str:
    """監査のテーブル名（環境変数 `AUDIT_TABLE`）。"""
    name = os.environ.get(AUDIT_TABLE_ENV) or ""
    if not name:
        raise RuntimeError(f"環境変数 {AUDIT_TABLE_ENV} が設定されていません")
    return name


def _actor_fields(actor: object) -> dict:
    if isinstance(actor, UserContext):
        return {"PK": f"ACTOR#{actor.user_id}", "user_id": actor.user_id, "email": actor.email, "role": actor.role}
    if actor == SCRIPT_ACTOR:
        return {"PK": f"ACTOR#{SCRIPT_ACTOR}"}
    if isinstance(actor, str) and actor.startswith(SYSTEM_ACTOR_PREFIX):
        name = actor[len(SYSTEM_ACTOR_PREFIX) :]
        if name and "#" not in name:
            return {"PK": f"ACTOR#{actor}"}
    raise ValueError("actor は UserContext、'script'、'system:{名前}' のどれかです")


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SECRET_KEYS or any(part in lowered for part in SECRET_KEY_PARTS)


def _redact(value: object, path: str, dropped: list[str]) -> object:
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            name = f"{path}{key}"
            if _is_secret_key(str(key)):
                dropped.append(name)
                continue
            kept[key] = _redact(item, f"{name}.", dropped)
        return kept
    if isinstance(value, list | tuple):
        return [_redact(item, path, dropped) for item in value]
    return value


def _request_fields(request: dict | None) -> dict:
    http = ((request or {}).get("requestContext") or {}).get("http") or {}
    fields = {"source_ip": http.get("sourceIp"), "user_agent": http.get("userAgent")}
    return {key: value for key, value in fields.items() if value}


def build_item(
    action: str,
    *,
    actor: UserContext | str,
    service: str,
    result: str = "ok",
    code: str | None = None,
    account: str | None = None,
    device_period_id: str | None = None,
    assignment_id: str | None = None,
    camera_address: str | None = None,
    period_id: str | None = None,
    target_user_id: str | None = None,
    request: dict | None = None,
    detail: dict | None = None,
    billing: bool = False,
    now: datetime | None = None,
) -> dict:
    """監査の 1 行を組み立てる。値が不正なら `ValueError`。"""
    if not isinstance(action, str) or not ACTION_PATTERN.fullmatch(action):
        raise ValueError("action は '区分.操作' の形です")
    if service not in SERVICES:
        raise ValueError("service が不明です")
    if result not in RESULTS:
        raise ValueError("result は ok / denied / failed のどれかです")

    occurred = (now or datetime.now(UTC)).astimezone(UTC)
    occurred_at = occurred.strftime("%Y-%m-%dT%H:%M:%SZ")
    sk = f"{occurred_at}#{new_ulid(int(occurred.timestamp() * 1000))}"

    item = {
        **_actor_fields(actor),
        "SK": sk,
        "GSI2PK": f"DAY#{occurred:%Y-%m-%d}",
        "GSI2SK": sk,
        "action": action,
        "result": result,
        "service": service,
        "occurred_at": occurred_at,
        "billing": bool(billing),
        **_request_fields(request),
    }
    values = {
        "code": code,
        "account": account,
        "device_period_id": device_period_id,
        "assignment_id": assignment_id,
        "camera_address": camera_address,
        "period_id": period_id,
        "target_user_id": target_user_id,
    }
    item.update(values)
    item = {key: value for key, value in item.items() if value is not None}
    if account is not None:
        item["GSI1PK"] = f"DEVICE#{account}"
        item["GSI1SK"] = sk
    if detail is not None:
        dropped: list[str] = []
        redacted = _redact(detail, "", dropped)
        item["detail"] = json.loads(json.dumps(redacted, default=str), parse_float=Decimal)
        if dropped:
            item["detail_redacted"] = sorted(dropped)
    if not billing:
        item["ttl"] = int((occurred + TTL).timestamp())
    return item


def _serialize(item: dict) -> dict:
    return {key: _serializer.serialize(value) for key, value in item.items()}


def put_item_for_transaction(action: str, **fields) -> dict:
    """TransactWriteItems に入れる条件付きの Put。組み立てに失敗したら投げる。"""
    item = build_item(action, **fields)
    return {"Put": {"TableName": audit_table_name(), "Item": _serialize(item), "ConditionExpression": CONDITION}}


def emit(action: str, **fields) -> None:
    """監査に 1 行を追記する。失敗しても投げずにログに残す（業務を止めない）。"""
    try:
        item = build_item(action, **fields)
        _client().put_item(TableName=audit_table_name(), Item=_serialize(item), ConditionExpression=CONDITION)
    except Exception as exc:
        error_code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
        logger.error("audit.emit failed", {"action": action, "error": type(exc).__name__, "error_code": error_code})
