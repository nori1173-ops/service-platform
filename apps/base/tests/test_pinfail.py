"""照会の失敗回数の同時実行（計画 2.1 の PINFAIL、ADR-001 D5）。

台帳への書き込みの直前に、ほかの要求の処理を挟めるスタブで順序を決めて確かめる。
挟む処理は 1 回だけ (先に取り外してから呼ぶので、挟んだ処理の中の書き込みには挟まない)。
"""

from datetime import timedelta

import pytest
from device_data import ACCOUNT, NOW, Interleaver, ledger_row

from service_platform import ledger


def is_add(kwargs: dict) -> bool:
    return kwargs.get("UpdateExpression", "").startswith("ADD")


def is_lock(kwargs: dict) -> bool:
    return "#locked = :until" in kwargs.get("UpdateExpression", "")


def always(_kwargs: dict) -> bool:
    return True


@pytest.fixture
def table(aws, monkeypatch):
    stub = Interleaver(aws.ledger)
    monkeypatch.setattr(ledger, "_table", lambda: stub)
    return stub


def row(aws) -> dict | None:
    return ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.PINFAIL_SK)


def test_two_first_failures_open_one_window_and_count_two(aws, table):
    """窓の初回作成の競合: 2 つの要求が同時に「窓が無い」を見ても、窓は 1 つで 2 回と数える。"""
    from base_api import pinfail

    table.before("put_item", always, lambda: pinfail.record_failure(ACCOUNT, NOW))

    assert pinfail.record_failure(ACCOUNT, NOW) is None

    current = row(aws)
    assert int(current["count"]) == 2
    assert int(current["window_started_at"]) == int(NOW.timestamp())


def test_the_fifth_and_sixth_failures_at_once_lock_exactly_once(aws, table):
    """5 回目の加算とロックの設定の間に 6 回目が入っても、止めるのは 1 回 (監査の device.pin_locked も 1 回)。"""
    from base_api import pinfail

    for _ in range(4):
        pinfail.record_failure(ACCOUNT, NOW)
    other: list[int | None] = []
    table.before("update_item", is_lock, lambda: other.append(pinfail.record_failure(ACCOUNT, NOW)))

    mine = pinfail.record_failure(ACCOUNT, NOW)

    until = int(NOW.timestamp()) + pinfail.LOCK_SECONDS
    assert other == [until]
    assert mine is None
    current = row(aws)
    assert int(current["count"]) == 6
    assert int(current["locked_until"]) == until


def test_a_success_does_not_remove_a_lock_set_just_before(aws, table):
    """成功による削除と、ほかの要求のロックが交差したら、ロックを残す。"""
    from base_api import pinfail

    for _ in range(4):
        pinfail.record_failure(ACCOUNT, NOW)
    table.before("delete_item", always, lambda: pinfail.record_failure(ACCOUNT, NOW))

    pinfail.clear(ACCOUNT, NOW)

    assert int(row(aws)["locked_until"]) == int(NOW.timestamp()) + pinfail.LOCK_SECONDS
    with pytest.raises(pinfail.PinLockedError):
        pinfail.ensure_not_locked(ACCOUNT, NOW)


def test_a_success_between_the_fifth_count_and_the_lock_leaves_no_broken_row(aws, table):
    """5 回目を数えた直後に成功で行が消えたら、止めない (行の無いところにロックだけの行を作らない)。"""
    from base_api import pinfail

    for _ in range(4):
        pinfail.record_failure(ACCOUNT, NOW)
    table.before("update_item", is_lock, lambda: pinfail.clear(ACCOUNT, NOW))

    assert pinfail.record_failure(ACCOUNT, NOW) is None

    assert row(aws) is None
    assert pinfail.record_failure(ACCOUNT, NOW + timedelta(seconds=1)) is None
    assert int(row(aws)["count"]) == 1


@pytest.mark.parametrize("gap", [0, 1])
def test_an_old_fifth_failure_does_not_lock_a_window_recreated_after_a_success(aws, table, gap):
    """5 回目を数える → 成功で削除 → 別の失敗で 1 回目の行を作り直す → 古い 5 回目のロック、の順では止めない。

    ロックの条件に、数えた窓 (数え始め) と回数を入れる。作り直しが同じ秒でも回数で落ちる。
    """
    from base_api import pinfail

    for _ in range(4):
        pinfail.record_failure(ACCOUNT, NOW)
    later = NOW + timedelta(seconds=gap)

    def success_then_new_failure():
        pinfail.clear(ACCOUNT, later)
        pinfail.record_failure(ACCOUNT, later)

    table.before("update_item", is_lock, success_then_new_failure)

    assert pinfail.record_failure(ACCOUNT, NOW) is None

    current = row(aws)
    assert int(current["count"]) == 1
    assert "locked_until" not in current
    pinfail.ensure_not_locked(ACCOUNT, later)


def test_a_window_reopened_by_another_request_is_counted_into(aws, table):
    """窓が過ぎた後に 2 つの要求が同時に窓を作り直そうとしても、新しい窓に 2 回と数える。"""
    from base_api import pinfail

    for _ in range(3):
        pinfail.record_failure(ACCOUNT, NOW)
    later = NOW + timedelta(seconds=pinfail.WINDOW_SECONDS)
    table.before("put_item", always, lambda: pinfail.record_failure(ACCOUNT, later))

    pinfail.record_failure(ACCOUNT, later)

    current = row(aws)
    assert int(current["count"]) == 2
    assert int(current["window_started_at"]) == int(later.timestamp())


def test_a_row_without_a_window_is_replaced_by_a_new_window(aws, table):
    """窓の属性の無い行 (古い作りの残り) でも、新しい窓で数え直す。"""
    from base_api import pinfail

    aws.ledger.put_item(Item={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.PINFAIL_SK, "locked_until": 1})

    pinfail.record_failure(ACCOUNT, NOW)

    assert int(row(aws)["count"]) == 1


# =============================================================================
# 利用者ごとの失敗回数 (ADR D14): `USER#{user_id}` / `PINFAIL`、60 分に 10 回で 60 分止める
# =============================================================================


def test_the_user_counter_uses_its_own_key_window_and_limit(aws):
    from base_api import pinfail

    user_id = "01JBZ0000000000000000USER1"
    for _ in range(9):
        assert pinfail.record_user_failure(user_id, NOW) is None
    later = NOW + timedelta(minutes=59)

    until = pinfail.record_user_failure(user_id, later)

    assert until == int(later.timestamp()) + 3600
    current = ledger_row(aws, ledger.user_pk(user_id), ledger.USER_PINFAIL_SK)
    assert int(current["count"]) == 10 and int(current["window_started_at"]) == int(NOW.timestamp())
    assert pinfail.user_locked_until(user_id, later) == until
    with pytest.raises(pinfail.UserPinLockedError) as info:
        pinfail.ensure_user_not_locked(user_id, later)
    assert info.value.code == "USER_PIN_LOCKED"
    assert ledger_row(aws, ledger.device_pk(ACCOUNT), ledger.PINFAIL_SK) is None


def test_the_user_window_restarts_after_an_hour(aws):
    from base_api import pinfail

    user_id = "01JBZ0000000000000000USER1"
    for _ in range(9):
        pinfail.record_user_failure(user_id, NOW)

    assert pinfail.record_user_failure(user_id, NOW + timedelta(minutes=61)) is None
    current = ledger_row(aws, ledger.user_pk(user_id), ledger.USER_PINFAIL_SK)
    assert int(current["count"]) == 1


def test_the_device_counter_keeps_its_window_and_limit(aws):
    from base_api import pinfail

    for _ in range(4):
        assert pinfail.record_failure(ACCOUNT, NOW) is None
    assert pinfail.record_failure(ACCOUNT, NOW) == int(NOW.timestamp()) + 600
