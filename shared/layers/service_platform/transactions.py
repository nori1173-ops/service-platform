"""台帳と監査の TransactWriteItems（計画 2.1 の書き込みのまとまり）。

処理中・スロットリング・5xx・接続の失敗は、同じ ClientRequestToken で送り直す (DynamoDB は 10 分の間、
同じ token の同じ要求を冪等に扱う)。それでも失敗したら `applied` で台帳を読み直し (強い整合)、
反映されていれば成功とみなす。反映されておらず確定した失敗 (4xx) なら `WriteFailed` (呼び出し元が補償して
よい)、それ以外は `OutcomeUnknown` (補償しない)。考え方は scripts/admin_ops.py の transact_checked と同じ。
"""

import time
import uuid
from collections.abc import Callable

from botocore.exceptions import BotoCoreError, ClientError

from service_platform.logging import get_logger

RETRY_DELAYS = (0.2, 0.5, 1.0)
RETRYABLE_CODES = frozenset(
    {
        "TransactionInProgressException",
        "ThrottlingException",
        "RequestLimitExceeded",
        "ProvisionedThroughputExceededException",
        "InternalServerError",
        "ServiceUnavailable",
    }
)
CONDITION_FAILED = "ConditionalCheckFailed"
# 取消理由のうち、同じ token で送り直してよい一時的なもの
RETRYABLE_REASONS = frozenset({"TransactionConflict", "ThrottlingError", "ProvisionedThroughputExceeded"})

logger = get_logger()
sleep = time.sleep


class WriteFailed(Exception):
    """確定した失敗。台帳は変わっていない。`reasons` は TransactItems と同じ順の取り消しの理由。"""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("transaction failed")
        self.reasons = reasons

    @property
    def condition_failed(self) -> bool:
        return CONDITION_FAILED in self.reasons

    def failed_at(self, index: int) -> bool:
        return 0 <= index < len(self.reasons) and self.reasons[index] == CONDITION_FAILED


class OutcomeUnknown(Exception):
    """反映されたか確かめられない。補償しない。"""


def _reasons(exc: ClientError) -> list[str]:
    return [str(reason.get("Code") or "") for reason in exc.response.get("CancellationReasons") or []]


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, BotoCoreError):
        return True
    error = exc.response.get("Error", {})
    status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0
    if error.get("Code") in RETRYABLE_CODES or status >= 500:
        return True
    if error.get("Code") == "TransactionCanceledException":
        reasons = _reasons(exc)
        return bool(RETRYABLE_REASONS & set(reasons)) and CONDITION_FAILED not in reasons
    return False


def write(client, items: list[dict], *, applied: Callable[[], bool], token: str | None = None) -> None:
    """TransactWriteItems を書く。成否が確定しなければ `OutcomeUnknown`、確定した失敗は `WriteFailed`。"""
    token = token or uuid.uuid4().hex
    failure: Exception | None = None
    for delay in (*RETRY_DELAYS, None):
        try:
            client.transact_write_items(TransactItems=items, ClientRequestToken=token)
            return
        except (ClientError, BotoCoreError) as exc:
            failure = exc
            if delay is None or not _is_retryable(exc):
                break
            sleep(delay)
    try:
        done = applied()
    except Exception as exc:
        raise OutcomeUnknown() from exc
    if done:
        logger.warning("transaction reported a failure but was applied", {"error": type(failure).__name__})
        return
    if isinstance(failure, ClientError) and not _is_retryable(failure):
        raise WriteFailed(_reasons(failure)) from failure
    raise OutcomeUnknown() from failure
