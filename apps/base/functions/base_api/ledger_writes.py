"""base が書く台帳の行（計画 2.1。台帳の書き手は admin-users と base だけ）。

台帳の変更と監査の行は 1 つの TransactWriteItems にまとめる (計画 2.1 の書き込みのまとまり)。
再試行と成否の確定は shared の `transactions.write` (同じ ClientRequestToken の送り直し、読み直し)。
読み直しでは、利用者の行の `last_op_id` がこのトランザクションの操作 ID か (反映されたか) を確かめる。
"""

from datetime import UTC, datetime

from boto3.dynamodb.types import TypeSerializer

from base_api import clients
from service_platform import audit, ledger, transactions
from service_platform.ids import new_ulid
from service_platform.ledger import UserContext

SERVICE = "base"
_serializer = TypeSerializer()


def _serialize(values: dict) -> dict:
    return {key: _serializer.serialize(value) for key, value in values.items()}


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_totp_registered(user: UserContext, *, at: int, request: dict) -> bool:
    """自分の行を `admin_state=awaiting`・`mfa_enabled_at=at` にする。準備中でなくなっていたら `False`。

    確定した失敗 (条件以外) は `transactions.WriteFailed`、確かめられなければ `transactions.OutcomeUnknown`。
    """
    op_id = new_ulid()
    update = {
        "Update": {
            "TableName": ledger.ledger_table_name(),
            "Key": _serialize({"PK": ledger.user_pk(user.user_id), "SK": ledger.PROFILE_SK}),
            "UpdateExpression": (
                "SET admin_state = :awaiting, mfa_enabled_at = :at, updated_by = :by, updated_at = :iso, "
                "last_op_id = :op, version = if_not_exists(version, :zero) + :one"
            ),
            "ConditionExpression": "admin_state = :pending AND #sub = :sub",
            "ExpressionAttributeNames": {"#sub": "sub"},
            "ExpressionAttributeValues": _serialize(
                {
                    ":awaiting": ledger.ADMIN_AWAITING,
                    ":pending": ledger.ADMIN_PENDING,
                    ":at": at,
                    ":by": user.user_id,
                    ":iso": _iso(at),
                    ":zero": 0,
                    ":one": 1,
                    ":sub": user.sub,
                    ":op": op_id,
                }
            ),
        }
    }
    record = audit.put_item_for_transaction(
        "admin.totp_registered",
        actor=user,
        service=SERVICE,
        target_user_id=user.user_id,
        request=request,
        detail={"mfa_enabled_at": at},
    )

    def applied() -> bool:
        return (ledger.get_profile(user.user_id) or {}).get("last_op_id") == op_id

    try:
        transactions.write(clients.dynamodb(), [update, record], applied=applied)
    except transactions.WriteFailed as exc:
        if exc.failed_at(0):
            return False
        raise
    return True
