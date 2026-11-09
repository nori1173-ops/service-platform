"""`service_platform.ids` のテスト（計画 2.4）。"""

import time

import pytest

from service_platform.ids import ULID_PATTERN, new_ulid

CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_TIMESTAMP_MS = (1 << 48) - 1


def timestamp_of(ulid: str) -> int:
    value = 0
    for char in ulid[:10]:
        value = value * 32 + CROCKFORD_BASE32.index(char)
    return value


def test_ulid_is_26_crockford_base32_characters():
    ulid = new_ulid()

    assert len(ulid) == 26
    assert ULID_PATTERN.fullmatch(ulid)
    assert set(ulid) <= set(CROCKFORD_BASE32)


def test_ulid_starts_with_the_current_time():
    before = int(time.time() * 1000)
    ulid = new_ulid()
    after = int(time.time() * 1000)

    assert before <= timestamp_of(ulid) <= after


def test_ulid_encodes_the_given_timestamp():
    assert timestamp_of(new_ulid(1_234_567_890_123)) == 1_234_567_890_123
    assert new_ulid(0).startswith("0000000000")
    assert new_ulid(MAX_TIMESTAMP_MS).startswith("7ZZZZZZZZZ")


def test_ulids_sort_by_time():
    earlier = [new_ulid(1_700_000_000_000 + i) for i in range(50)]

    assert earlier == sorted(earlier)


def test_ulids_are_unique():
    assert len({new_ulid(1_700_000_000_000) for _ in range(1000)}) == 1000


@pytest.mark.parametrize("timestamp_ms", [-1, MAX_TIMESTAMP_MS + 1])
def test_rejects_out_of_range_timestamps(timestamp_ms):
    with pytest.raises(ValueError):
        new_ulid(timestamp_ms)


@pytest.mark.parametrize(
    "value", ["", "01ARZ3NDEKTSV4RRFFQ69G5FA", "01ARZ3NDEKTSV4RRFFQ69G5FAVX", "8ZZZZZZZZZZZZZZZZZZZZZZZZZ"]
)
def test_pattern_rejects_malformed_values(value):
    assert not ULID_PATTERN.fullmatch(value)


@pytest.mark.parametrize("char", ["I", "L", "O", "U", "a"])
def test_pattern_rejects_characters_outside_crockford_base32(char):
    assert not ULID_PATTERN.fullmatch("01ARZ3NDEKTSV4RRFFQ69G5FA" + char)
