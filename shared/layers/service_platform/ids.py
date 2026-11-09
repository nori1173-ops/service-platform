"""ULID の採番（計画 2.4、ADR-001 D3）。

時刻 48 ビット (ミリ秒) + 乱数 80 ビットを Crockford Base32 の 26 文字で表す。
カメラ画像判定の `periods.new_id` と同じ作り。同じミリ秒の中の順は乱数で決まる (単調ではない)。
"""

import re
import secrets
import time

CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_LENGTH = 26
ULID_PATTERN = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
MAX_TIMESTAMP_MS = (1 << 48) - 1
RANDOM_BITS = 80


def new_ulid(timestamp_ms: int | None = None) -> str:
    """ULID を返す。`timestamp_ms` を省くと今の時刻。"""
    ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ms <= MAX_TIMESTAMP_MS:
        raise ValueError("ULID の時刻が範囲外です")
    value = (ms << RANDOM_BITS) | secrets.randbits(RANDOM_BITS)
    chars = []
    for _ in range(ULID_LENGTH):
        chars.append(CROCKFORD_BASE32[value & 31])
        value >>= 5
    return "".join(reversed(chars))
