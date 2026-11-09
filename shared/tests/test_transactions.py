"""`service_platform.transactions` のテスト（台帳と監査の TransactWriteItems の再試行と成否の確定）。

考え方は scripts/admin_ops.py の transact_checked と同じ: 処理中・スロットリング・5xx・接続の失敗は
同じ ClientRequestToken で送り直す。失敗したら台帳を読み直し、反映されていれば成功。反映されておらず
確定した失敗 (4xx) なら `WriteFailed`、確かめられなければ `OutcomeUnknown`。
"""

import pytest
from botocore.exceptions import ClientError, ConnectionClosedError, ReadTimeoutError

from service_platform import transactions
from service_platform.transactions import OutcomeUnknown, WriteFailed

ITEMS = [{"Put": {"TableName": "t", "Item": {"PK": {"S": "A"}}}}]


def error(code: str, status: int, reasons: list[str] | None = None) -> ClientError:
    response = {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
    if reasons is not None:
        response["CancellationReasons"] = [{"Code": reason} for reason in reasons]
    return ClientError(response, "TransactWriteItems")


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return {}


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(transactions, "sleep", recorded.append)
    return recorded


def never():
    return False


def test_success_on_the_first_try(sleeps):
    client = FakeClient()

    transactions.write(client, ITEMS, applied=never)

    (call,) = client.calls
    assert call["TransactItems"] == ITEMS
    assert len(call["ClientRequestToken"]) >= 32
    assert sleeps == []


@pytest.mark.parametrize(
    "first",
    [
        error("TransactionInProgressException", 400),
        error("ThrottlingException", 400),
        error("ProvisionedThroughputExceededException", 400),
        error("InternalServerError", 500),
        error("TransactionCanceledException", 400, ["None", "TransactionConflict"]),
        error("TransactionCanceledException", 400, ["None", "ThrottlingError"]),
        error("TransactionCanceledException", 400, ["ProvisionedThroughputExceeded", "None"]),
        ConnectionClosedError(endpoint_url="https://dynamodb"),
        ReadTimeoutError(endpoint_url="https://dynamodb"),
    ],
    ids=[
        "in-progress",
        "throttling",
        "throughput",
        "5xx",
        "conflict",
        "canceled-throttling",
        "canceled-throughput",
        "closed",
        "timeout",
    ],
)
def test_retries_with_the_same_token(sleeps, first):
    client = FakeClient(first)

    transactions.write(client, ITEMS, applied=never)

    assert len(client.calls) == 2
    assert client.calls[0]["ClientRequestToken"] == client.calls[1]["ClientRequestToken"]
    assert sleeps == [transactions.RETRY_DELAYS[0]]


def test_a_given_token_is_used(sleeps):
    client = FakeClient()

    transactions.write(client, ITEMS, applied=never, token="fixed-token")

    assert client.calls[0]["ClientRequestToken"] == "fixed-token"


def test_unknown_after_all_retries_is_checked_by_reading_back(sleeps):
    client = FakeClient(*[error("InternalServerError", 500)] * 10)

    transactions.write(client, ITEMS, applied=lambda: True)

    assert len(client.calls) == len(transactions.RETRY_DELAYS) + 1
    assert sleeps == list(transactions.RETRY_DELAYS)


def test_unknown_after_all_retries_and_not_applied_is_outcome_unknown(sleeps):
    client = FakeClient(*[ConnectionClosedError(endpoint_url="x")] * 10)

    with pytest.raises(OutcomeUnknown):
        transactions.write(client, ITEMS, applied=never)


def test_a_lost_response_after_commit_is_success_when_applied(sleeps):
    """送り直しが条件で取り消されても、読み直して反映されていれば成功 (応答を失っただけ)。"""
    client = FakeClient(
        ReadTimeoutError(endpoint_url="x"), error("TransactionCanceledException", 400, ["ConditionalCheckFailed"])
    )

    transactions.write(client, ITEMS, applied=lambda: True)

    assert len(client.calls) == 2


def test_a_definite_failure_is_write_failed_with_the_reasons(sleeps):
    client = FakeClient(error("TransactionCanceledException", 400, ["None", "ConditionalCheckFailed"]))

    with pytest.raises(WriteFailed) as excinfo:
        transactions.write(client, ITEMS, applied=never)

    assert len(client.calls) == 1
    assert excinfo.value.reasons == ["None", "ConditionalCheckFailed"]
    assert excinfo.value.condition_failed
    assert excinfo.value.failed_at(1)
    assert not excinfo.value.failed_at(0)
    assert not excinfo.value.failed_at(5)


def test_a_validation_error_is_write_failed_without_reasons(sleeps):
    client = FakeClient(error("ValidationException", 400))

    with pytest.raises(WriteFailed) as excinfo:
        transactions.write(client, ITEMS, applied=never)

    assert excinfo.value.reasons == []
    assert not excinfo.value.condition_failed


def test_a_definite_failure_that_was_applied_is_success(sleeps):
    client = FakeClient(error("TransactionCanceledException", 400, ["ConditionalCheckFailed"]))

    transactions.write(client, ITEMS, applied=lambda: True)


def test_a_failed_read_back_is_outcome_unknown(sleeps):
    def broken():
        raise error("InternalServerError", 500)

    client = FakeClient(error("TransactionCanceledException", 400, ["ConditionalCheckFailed"]))

    with pytest.raises(OutcomeUnknown):
        transactions.write(client, ITEMS, applied=broken)


def test_programming_errors_are_not_swallowed(sleeps):
    client = FakeClient(ValueError("bad item"))

    with pytest.raises(ValueError):
        transactions.write(client, ITEMS, applied=never)


def test_a_condition_failure_is_not_retried_even_with_throttling_reasons(sleeps):
    client = FakeClient(error("TransactionCanceledException", 400, ["ConditionalCheckFailed", "ThrottlingError"]))

    with pytest.raises(WriteFailed):
        transactions.write(client, ITEMS, applied=never)

    assert len(client.calls) == 1
