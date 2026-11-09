"""照会の失敗回数（計画 2.1 / 4.12、ADR-001 D5 / D14 の総当たり対策）。

- 通信機ごと `DEVICE#{account}` / `PINFAIL`: 10 分の窓で 5 回失敗したら 10 分止める (429 `PIN_LOCKED`)。
  成功で行を消す。
- 利用者ごと `USER#{user_id}` / `PINFAIL` : すべての通信機を合わせて、60 分の窓で 10 回失敗したら 60 分
  止める (429 `USER_PIN_LOCKED`)。1 人が多くの通信機を順に試すのを抑える。成功では消さない (知っている通信機で
  成功して数え直すことを許さない)。窓と止める時間が過ぎれば数え直す。

窓は最初の失敗から数える。止めている間は device-backend を呼ばないので、止めている間の失敗は数えない。
DynamoDB の TTL は削除が遅れるので、期限は読むたびに確かめる。
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from botocore.exceptions import ClientError

from service_platform import ledger
from service_platform.auth import TooManyRequestsError
from service_platform.logging import get_logger

WINDOW_SECONDS = 10 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 10 * 60
USER_WINDOW_SECONDS = 60 * 60
USER_MAX_FAILURES = 10
USER_LOCK_SECONDS = 60 * 60
TTL_MARGIN_SECONDS = 24 * 60 * 60

logger = get_logger()


class PinLockedError(TooManyRequestsError):
    code = "PIN_LOCKED"

    def __init__(self, message: str = "暗証番号の誤りが続いたため、この通信機の照会を 10 分止めています") -> None:
        super().__init__(message)


class UserPinLockedError(TooManyRequestsError):
    code = "USER_PIN_LOCKED"

    def __init__(self, message: str = "暗証番号の誤りが続いたため、通信機の照会を 60 分止めています") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class Policy:
    pk: str
    sk: str
    window: int
    limit: int
    lock: int
    owner: dict

    @property
    def key(self) -> dict:
        return {"PK": self.pk, "SK": self.sk}


def device_policy(account: str) -> Policy:
    return Policy(
        ledger.device_pk(account), ledger.PINFAIL_SK, WINDOW_SECONDS, MAX_FAILURES, LOCK_SECONDS, {"account": account}
    )


def user_policy(user_id: str) -> Policy:
    return Policy(
        ledger.user_pk(user_id),
        ledger.USER_PINFAIL_SK,
        USER_WINDOW_SECONDS,
        USER_MAX_FAILURES,
        USER_LOCK_SECONDS,
        {"user_id": user_id},
    )


def _epoch(value: object) -> int | None:
    return int(value) if isinstance(value, Decimal | int) and not isinstance(value, bool) else None


def _condition_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _locked_until(policy: Policy, now: datetime) -> int | None:
    row = ledger._get(policy.pk, policy.sk) or {}
    until = _epoch(row.get("locked_until"))
    return until if until is not None and until > int(now.timestamp()) else None


def locked_until(account: str, now: datetime) -> int | None:
    """通信機ごとに止めている期限 (epoch 秒)。止めていなければ None。"""
    return _locked_until(device_policy(account), now)


def user_locked_until(user_id: str, now: datetime) -> int | None:
    """利用者ごとに止めている期限 (epoch 秒)。止めていなければ None。"""
    return _locked_until(user_policy(user_id), now)


def ensure_not_locked(account: str, now: datetime) -> None:
    if locked_until(account, now) is not None:
        raise PinLockedError()


def ensure_user_not_locked(user_id: str, now: datetime) -> None:
    if user_locked_until(user_id, now) is not None:
        raise UserPinLockedError()


def _ttl(policy: Policy, at: int) -> int:
    return at + policy.window + policy.lock + TTL_MARGIN_SECONDS


def _add(table, policy: Policy, at: int) -> tuple[int, int]:
    """今の窓の回数に 1 を足し、(回数, 窓の数え始め) を返す。窓が無い・過ぎていれば ConditionalCheckFailed。"""
    result = table.update_item(
        Key=policy.key,
        UpdateExpression="ADD #count :one SET #ttl = :ttl",
        ConditionExpression="#window > :floor",
        ExpressionAttributeNames={"#count": "count", "#ttl": "ttl", "#window": "window_started_at"},
        ExpressionAttributeValues={":one": 1, ":ttl": _ttl(policy, at), ":floor": at - policy.window},
        ReturnValues="ALL_NEW",
    )
    attributes = result["Attributes"]
    return int(attributes["count"]), int(attributes["window_started_at"])


def _open_window(table, policy: Policy, at: int) -> tuple[int, int]:
    """新しい窓を 1 回目で始める。今の窓がまだ開いているか、止めている間なら ConditionalCheckFailed。

    窓の属性の無い行 (数え始めの無い行) も、新しい窓で置き換える (数えられない行を残さない)。
    """
    table.put_item(
        Item={**policy.key, **policy.owner, "count": 1, "window_started_at": at, "ttl": _ttl(policy, at)},
        ConditionExpression=(
            "(attribute_not_exists(PK) OR attribute_not_exists(#window) OR #window <= :floor) "
            "AND (attribute_not_exists(#locked) OR #locked <= :now)"
        ),
        ExpressionAttributeNames={"#window": "window_started_at", "#locked": "locked_until"},
        ExpressionAttributeValues={":floor": at - policy.window, ":now": at},
    )
    return 1, at


def _count(table, policy: Policy, at: int) -> tuple[int, int] | None:
    for step in (_add, _open_window, _add):
        try:
            return step(table, policy, at)
        except ClientError as exc:
            if not _condition_failed(exc):
                raise
    return None


def _lock(table, policy: Policy, at: int, *, window: int, count: int) -> int | None:
    """数えた窓がそのままで、回数が数えたとき以上のときだけ止める。

    数えた直後に成功で行が消えた、または消えた後に別の失敗で行が作り直された (回数が戻った) ときは止めない
    (ロックだけの行を作らない。作り直した窓を古い回数で止めない)。
    """
    until = at + policy.lock
    try:
        table.update_item(
            Key=policy.key,
            UpdateExpression="SET #locked = :until, #ttl = :ttl",
            ConditionExpression=(
                "attribute_exists(PK) AND #window = :window AND #count >= :count "
                "AND (attribute_not_exists(#locked) OR #locked <= :now)"
            ),
            ExpressionAttributeNames={
                "#locked": "locked_until",
                "#ttl": "ttl",
                "#window": "window_started_at",
                "#count": "count",
            },
            ExpressionAttributeValues={
                ":until": until,
                ":now": at,
                ":ttl": _ttl(policy, until),
                ":window": window,
                ":count": count,
            },
        )
    except ClientError as exc:
        if _condition_failed(exc):
            return None
        raise
    return until


def _record(policy: Policy, now: datetime) -> int | None:
    table = ledger._table()
    at = int(now.timestamp())
    counted = _count(table, policy, at)
    if counted is None:
        logger.warning("pinfail count skipped", policy.owner)
        return None
    count, window = counted
    if count < policy.limit:
        return None
    return _lock(table, policy, at, window=window, count=count)


def record_failure(account: str, now: datetime) -> int | None:
    """通信機ごとの失敗を 1 回数える。この失敗で止めたときは止める期限 (epoch 秒) を返す。"""
    return _record(device_policy(account), now)


def record_user_failure(user_id: str, now: datetime) -> int | None:
    """利用者ごとの失敗を 1 回数える。この失敗で止めたときは止める期限 (epoch 秒) を返す。"""
    return _record(user_policy(user_id), now)


def clear(account: str, now: datetime) -> None:
    """通信機ごとの回数を成功で数え直す。止めている間 (別の要求が直前に止めた) は消さない。"""
    try:
        ledger._table().delete_item(
            Key=device_policy(account).key,
            ConditionExpression="attribute_not_exists(#locked) OR #locked <= :now",
            ExpressionAttributeNames={"#locked": "locked_until"},
            ExpressionAttributeValues={":now": int(now.timestamp())},
        )
    except ClientError as exc:
        if not _condition_failed(exc):
            raise
