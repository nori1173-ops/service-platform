"""「最新値を取得」のガードと受付 `DEVICE#{account}` / `REFRESH`（計画 2.1 / 4.5、判断 4、カメラ設計書 §5.5）。

- 運用中 (外部のデバイス活動台帳に、この通信機アカウントの固定の 48 時間以内の活動報告がある) なら使わせない。
  デバイス活動台帳は読み取りだけで引き、表の名前は SSM から実行時に読む
  (環境変数 `DEVICE_ACTIVITY_TABLE_PARAMETER` にキーを指定)。
  キーが空・SSM に無い・表を読めない・`last_seen` を読めない行がある、のどれでも不可 (fail-closed)。
- 受付の記録は 10 分。期限内の記録があれば二重実行を断る (本人の受付なら、新しい poll_token で待機を再開する)。
- 受付は、前回値の照会で暗証番号を確かめてから作る。受付ごとに 32 バイトの乱数の poll_token を作って応答で
  1 回だけ返し、記録には `pin_mac = HMAC-SHA256(key=poll_token, msg=account || pin)` と acceptance_id だけを持つ
  (暗証番号も poll_token も持たない)。
- 待機中の照会 (mode=latest) の 401 は「報告がまだ」と「暗証番号の誤り」を区別できない。受付の本人が、
  受付の acceptance_id と poll_token で、受付で確かめた暗証番号と同じ暗証番号を送ったとき (HMAC が一致) だけ、
  受付ごとに 65 回まで失敗回数に数えない。照会の間隔は 8 秒以上。合わない照会は普通に数える。
"""

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from base_api import clients
from service_platform import ledger
from service_platform.logging import get_logger

RECENT_HOURS = 48
REFRESH_TTL_SECONDS = 10 * 60
# 10 秒ごとの照会で 10 分 = 60 回と、少しの余裕
MAX_POLLS = 65
MIN_POLL_INTERVAL_SECONDS = 8
TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128
MAX_ACCEPTANCE_ID_LENGTH = 64
TTL_MARGIN_SECONDS = 24 * 60 * 60
DEVICE_ACTIVITY_TABLE_PARAMETER_ENV = "DEVICE_ACTIVITY_TABLE_PARAMETER"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

IN_OPERATION = "in_operation"
GUARD_UNAVAILABLE = "guard_unavailable"
POLL_FREE = "free"
POLL_TOO_SOON = "too_soon"
POLL_COUNTED = "counted"

logger = get_logger()


class GuardUnavailable(Exception):
    """運用中かどうかを判定できない。"""


class RefreshInProgress(Exception):
    """期限内の受付の記録がある。"""


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime(TIME_FORMAT)


def _key(account: str) -> dict:
    return {"PK": ledger.device_pk(account), "SK": ledger.REFRESH_SK}


def _condition_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def activity_table_name() -> str:
    """デバイス活動台帳の表の名前。成功した値だけをコンテナの間で使い回す。"""
    cached = clients._cache.get("activity_table_name")
    if isinstance(cached, str):
        return cached
    parameter = os.environ.get(DEVICE_ACTIVITY_TABLE_PARAMETER_ENV) or ""
    if not parameter:
        raise GuardUnavailable("device activity ledger parameter is not configured")
    try:
        value = clients.ssm().get_parameter(Name=parameter)["Parameter"]["Value"]
    except ClientError as exc:
        raise GuardUnavailable("device activity ledger parameter is not readable") from exc
    if not isinstance(value, str) or not value:
        raise GuardUnavailable("device activity ledger parameter is empty")
    clients._cache["activity_table_name"] = value
    return value


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def seen_recently(account: str, now: datetime) -> bool:
    """48 時間以内に報告が届いたデバイスがあるか。読めない行・読み取りの失敗は例外のまま返す (fail-closed)。"""
    table = clients.dynamodb_resource().Table(activity_table_name())
    query = {"KeyConditionExpression": Key("account").eq(account)}
    floor = now - timedelta(hours=RECENT_HOURS)
    recent = False
    while True:
        page = table.query(**query)
        for item in page.get("Items") or []:
            seen = _parse_time(item.get("last_seen"))
            if seen is None:
                raise GuardUnavailable("unreadable last_seen")
            recent = recent or seen >= floor
        if not page.get("LastEvaluatedKey"):
            return recent
        query = {**query, "ExclusiveStartKey": page["LastEvaluatedKey"]}


def get_active(account: str, now: datetime) -> dict | None:
    row = ledger._get(ledger.device_pk(account), ledger.REFRESH_SK)
    expires_at = (row or {}).get("expires_at")
    if not row or not isinstance(expires_at, Decimal | int) or int(expires_at) <= int(now.timestamp()):
        return None
    return row


def availability(account: str, now: datetime) -> dict:
    """最新値の可否。台帳か受付の記録を読めなければ不可 (fail-closed)。"""
    try:
        in_operation = seen_recently(account, now)
        in_progress = get_active(account, now) is not None
    except Exception as exc:
        logger.warning("refresh guard unavailable", {"account": account, "error": type(exc).__name__})
        return {"account": account, "available": False, "reason": GUARD_UNAVAILABLE, "in_progress": False}
    if in_operation:
        return {"account": account, "available": False, "reason": IN_OPERATION, "in_progress": in_progress}
    return {"account": account, "available": True, "reason": None, "in_progress": in_progress}


@dataclass(frozen=True)
class PollCheck:
    """待機中の照会の扱い。`matched` は受付の本人・acceptance_id・HMAC が合ったか (受付を消してよいか)。"""

    matched: bool
    outcome: str


NOT_MATCHED = PollCheck(matched=False, outcome=POLL_COUNTED)


def new_poll_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def pin_mac(poll_token: str, account: str, pin: str) -> str:
    return hmac.new(poll_token.encode("utf-8"), f"{account}{pin}".encode(), hashlib.sha256).hexdigest()


def acquire(account: str, user_id: str, now: datetime, *, acceptance_id: str, mac: str) -> tuple[dict, bool]:
    """受付の記録を作る。本人の期限内の受付があれば acceptance_id と pin_mac を付け替える (戻り値の 2 つ目が True)。

    ほかの利用者の期限内の受付があれば `RefreshInProgress`。
    """
    table = ledger._table()
    at = int(now.timestamp())
    expires_at = at + REFRESH_TTL_SECONDS
    item = {
        **_key(account),
        "account": account,
        "accepted_by": user_id,
        "accepted_at": iso(at),
        "expires_at": expires_at,
        "acceptance_id": acceptance_id,
        "pin_mac": mac,
        "polls": 0,
        "ttl": expires_at + TTL_MARGIN_SECONDS,
    }
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) OR expires_at <= :now",
            ExpressionAttributeValues={":now": at},
        )
        return item, False
    except ClientError as exc:
        if not _condition_failed(exc):
            raise
    try:
        result = table.update_item(
            Key=_key(account),
            UpdateExpression="SET acceptance_id = :a, pin_mac = :m",
            ConditionExpression="accepted_by = :me AND expires_at > :now",
            ExpressionAttributeValues={":a": acceptance_id, ":m": mac, ":me": user_id, ":now": at},
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if _condition_failed(exc):
            raise RefreshInProgress(account) from exc
        raise
    return result["Attributes"], True


def _delete_acceptance(account: str, acceptance_id: str, label: str) -> None:
    try:
        ledger._table().delete_item(
            Key=_key(account),
            ConditionExpression="acceptance_id = :a",
            ExpressionAttributeValues={":a": acceptance_id},
        )
    except ClientError as exc:
        if not _condition_failed(exc):
            logger.warning(label, {"account": account, "error": type(exc).__name__})


def release(account: str, acceptance_id: str) -> None:
    """読取り要求に失敗したとき、この受付の記録だけを消す。"""
    _delete_acceptance(account, acceptance_id, "refresh record release failed")


def clear(account: str, acceptance_id: str) -> None:
    """この受付の照会でログインできた (通信機の報告が届いた) ので、この受付の記録だけを消す。"""
    _delete_acceptance(account, acceptance_id, "refresh record cleanup failed")


def _usable(value: object, limit: int) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def check_poll(
    account: str, user_id: str, acceptance_id: object, poll_token: object, pin: str, now: datetime
) -> PollCheck:
    """待機中の照会を確かめ、免除するなら照会の回数を 1 つ使う。"""
    acceptance = _usable(acceptance_id, MAX_ACCEPTANCE_ID_LENGTH)
    token = _usable(poll_token, MAX_TOKEN_LENGTH)
    if acceptance is None or token is None:
        return NOT_MATCHED
    row = get_active(account, now)
    if (
        not row
        or row.get("accepted_by") != user_id
        or row.get("acceptance_id") != acceptance
        or not isinstance(row.get("pin_mac"), str)
        or not hmac.compare_digest(row["pin_mac"], pin_mac(token, account, pin))
    ):
        return NOT_MATCHED
    at = int(now.timestamp())
    limited = _limited(row, at)
    if limited is not None:
        return limited
    try:
        ledger._table().update_item(
            Key=_key(account),
            UpdateExpression="ADD polls :one SET last_poll_at = :at",
            ConditionExpression=(
                "acceptance_id = :a AND expires_at > :at AND (attribute_not_exists(polls) OR polls < :max) "
                "AND (attribute_not_exists(last_poll_at) OR last_poll_at <= :gap)"
            ),
            ExpressionAttributeValues={
                ":one": 1,
                ":at": at,
                ":a": acceptance,
                ":max": MAX_POLLS,
                ":gap": at - MIN_POLL_INTERVAL_SECONDS,
            },
        )
    except ClientError as exc:
        if not _condition_failed(exc):
            raise
        # 同じ受付への同時の照会に先を越された。読み直して、間隔の違反なら 429 (device_backend を呼ばず、数えない)
        again = get_active(account, now)
        if again and again.get("acceptance_id") == acceptance:
            return _limited(again, at) or PollCheck(matched=True, outcome=POLL_COUNTED)
        return PollCheck(matched=True, outcome=POLL_COUNTED)
    return PollCheck(matched=True, outcome=POLL_FREE)


def _limited(row: dict, at: int) -> PollCheck | None:
    """照会の回数の上限 (数える) か、間隔の違反 (429) なら、その扱い。どちらでもなければ None。"""
    last = row.get("last_poll_at")
    if isinstance(last, Decimal | int) and at - int(last) < MIN_POLL_INTERVAL_SECONDS:
        return PollCheck(matched=True, outcome=POLL_TOO_SOON)
    if int(row.get("polls") or 0) >= MAX_POLLS:
        return PollCheck(matched=True, outcome=POLL_COUNTED)
    return None
