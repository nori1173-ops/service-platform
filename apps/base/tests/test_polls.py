"""最新値の待機中の照会の同時実行（refresh_guard.check_poll、修正ラウンド 2）。

同じ受付への 2 本の照会が、どちらも更新の前の受付の行を読んでも、照会の間隔 (8 秒) を守らせる。
条件付きの更新に落ちた側は、受付を読み直して 429 `POLL_TOO_SOON` (device_backend を呼ばず、失敗回数に数えない)。
"""

from datetime import timedelta

import pytest
from device_data import ACCOUNT, NOW, PIN, Interleaver, ledger_row

from service_platform import ledger

ACCEPTANCE = "01JBZ0000000000000ACCEPT1"
TOKEN = "poll-token-for-tests-0123456789abcdefghijklmn"


@pytest.fixture
def table(aws, monkeypatch):
    stub = Interleaver(aws.ledger)
    monkeypatch.setattr(ledger, "_table", lambda: stub)
    return stub


@pytest.fixture
def accepted(aws, table):
    from base_api import refresh_guard

    refresh_guard.acquire(
        ACCOUNT,
        aws.ids.user_id,
        NOW,
        acceptance_id=ACCEPTANCE,
        mac=refresh_guard.pin_mac(TOKEN, ACCOUNT, PIN),
    )
    return refresh_guard


def is_poll(kwargs: dict) -> bool:
    return kwargs.get("UpdateExpression", "").startswith("ADD polls")


def test_two_polls_at_once_free_one_and_refuse_the_other(aws, table, accepted):
    later = NOW + timedelta(seconds=10)
    first: list = []
    table.before(
        "update_item",
        is_poll,
        lambda: first.append(accepted.check_poll(ACCOUNT, aws.ids.user_id, ACCEPTANCE, TOKEN, PIN, later)),
    )

    second = accepted.check_poll(ACCOUNT, aws.ids.user_id, ACCEPTANCE, TOKEN, PIN, later)

    assert first == [accepted.PollCheck(matched=True, outcome=accepted.POLL_FREE)]
    assert second == accepted.PollCheck(matched=True, outcome=accepted.POLL_TOO_SOON)
    row = ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.REFRESH_SK)
    assert int(row["polls"]) == 1


def test_a_lost_update_after_the_limit_is_counted(aws, table, accepted, monkeypatch):
    """回数の上限に達して落ちた側は、間隔ではなく上限なので普通に数える。"""
    monkeypatch.setattr(accepted, "MAX_POLLS", 1)
    later = NOW + timedelta(seconds=10)
    table.before(
        "update_item",
        is_poll,
        lambda: accepted.check_poll(ACCOUNT, aws.ids.user_id, ACCEPTANCE, TOKEN, PIN, later + timedelta(seconds=30)),
    )

    second = accepted.check_poll(ACCOUNT, aws.ids.user_id, ACCEPTANCE, TOKEN, PIN, later + timedelta(seconds=60))

    assert second == accepted.PollCheck(matched=True, outcome=accepted.POLL_COUNTED)


def test_concurrent_polls_through_the_api_are_429_without_device_backend_or_counting(device_api, call, monkeypatch):
    """API の上でも、後の 1 本は 429 で、device_backend を呼ばず、失敗回数に数えない。"""
    from base_api import refresh_guard

    aws, started = device_api
    aws.device_backend.on("GET", f"/api/v2/accounts/{ACCOUNT}/settings", 401)
    aws.clock.advance(10)
    body = {
        "account": ACCOUNT,
        "serial": "87654321",
        "pin": PIN,
        "mode": "latest",
        "acceptance_id": started["acceptance_id"],
        "poll_token": started["poll_token"],
    }
    original = refresh_guard.check_poll
    results: list = []

    def racing(*args):
        stub = Interleaver(aws.ledger)
        monkeypatch.setattr(ledger, "_table", lambda: stub)
        stub.before("update_item", is_poll, lambda: results.append(original(*args)))
        monkeypatch.setattr(refresh_guard, "check_poll", original)
        return original(*args)

    monkeypatch.setattr(refresh_guard, "check_poll", racing)
    aws.device_backend.calls.clear()

    status, response = call("POST", "/devices/login", body=body)

    assert results[0].outcome == refresh_guard.POLL_FREE
    assert status == 429
    assert response["error"]["code"] == "POLL_TOO_SOON"
    assert aws.device_backend.calls == []
    assert ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.PINFAIL_SK) is None


@pytest.fixture
def device_api(aws, clock, device_backend, call):
    from device_data import SETTINGS_PATH, SETTINGS_PAYLOAD, STATUS_PATH, put_assignment, put_grant, put_period

    aws.put_user()
    put_period(aws)
    put_assignment(aws)
    put_grant(aws)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)
    device_backend.on("POST", STATUS_PATH, 202)
    device_backend.on("POST", SETTINGS_PATH, 202)
    status, body = call("POST", "/devices/refresh", body={"account": ACCOUNT, "serial": "87654321", "pin": PIN})
    assert status == 202
    aws.clock = clock
    aws.device_backend = device_backend
    return aws, body["data"]
