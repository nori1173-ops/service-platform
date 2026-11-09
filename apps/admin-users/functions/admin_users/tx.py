"""1 つの TransactWriteItems の組み立てと、失敗を利用者への応答に写すこと（admin-users）。

条件に落ちた項目は、最後の admin (409 `LAST_ADMIN`) → 呼び出し元 (403 `ADMIN_NOT_ACTIVE`) →
利用者の行の version (409 `CONFLICT`) の順に見て、先に当たったものを返す。補償 (作った Cognito の利用者の削除) は
確定した失敗のときだけ行い、結果不明では行わない。

トランザクションごとに操作 ID (ULID) を採番し、利用者の行の `last_op_id` に書く。失敗の後の読み直しでは、
状態が同じかではなく `last_op_id` がこの操作 ID か (このトランザクションが反映されたか) を確かめる。
"""

from collections.abc import Callable

from admin_users import errors, ledger_writes
from service_platform import ledger
from service_platform.ids import new_ulid
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

PRIORITY_LAST_ADMIN = 0
PRIORITY_CALLER = 1
PRIORITY_PROFILE = 2

logger = get_logger()


class Tx:
    """1 つの TransactWriteItems。条件に落ちた項目を利用者への応答に写すための印を持つ。"""

    def __init__(self) -> None:
        self.op_id = new_ulid()
        self.items: list[dict] = []
        self.on_condition: list[tuple[int, int, Callable[[], errors.ApiError]]] = []

    def add(self, item: dict, on_condition: Callable[[], errors.ApiError] | None = None, priority: int = 9) -> "Tx":
        if on_condition is not None:
            self.on_condition.append((priority, len(self.items), on_condition))
        self.items.append(item)
        return self

    def profile(self, item: dict) -> "Tx":
        return self.add(item, errors.stale, PRIORITY_PROFILE)

    def update_profile(self, profile: dict, *, by: str, **changes) -> "Tx":
        """利用者の行の条件付きの更新 (sub と version、`last_op_id` にこの操作 ID)。"""
        return self.profile(ledger_writes.profile_update(profile, by=by, op_id=self.op_id, **changes))

    def applied_to(self, user_id: str) -> Callable[[], bool]:
        """失敗の後の読み直し: この操作のトランザクションが利用者の行に反映されたか。"""
        return lambda: (ledger.get_profile(user_id) or {}).get("last_op_id") == self.op_id

    def remove_admin(self, user_id: str) -> "Tx":
        return self.add(ledger_writes.admin_set_remove(user_id), errors.last_admin, PRIORITY_LAST_ADMIN)

    def caller(self, caller: UserContext) -> "Tx":
        return self.add(ledger_writes.caller_check(caller), errors.caller_not_active, PRIORITY_CALLER)

    def error_for(self, failure: ledger_writes.WriteFailed) -> errors.ApiError | None:
        for _priority, index, make in sorted(self.on_condition, key=lambda entry: entry[:2]):
            if failure.failed_at(index):
                return make()
        return None


def write(tx: Tx, *, applied, compensate=None) -> None:
    try:
        ledger_writes.write(tx.items, applied=applied)
    except ledger_writes.WriteFailed as exc:
        if compensate is not None:
            try:
                compensate()
            except Exception as compensation:
                logger.error("compensation failed: the Cognito user may remain", {"error": type(compensation).__name__})
                raise errors.compensation_failed() from compensation
        mapped = tx.error_for(exc)
        if mapped is not None:
            raise mapped from exc
        raise errors.ledger_write_failed() from exc
    except ledger_writes.OutcomeUnknown as exc:
        raise errors.outcome_unknown() from exc
