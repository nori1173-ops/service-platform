"""今の時刻 (UTC)。テストが差し替える。"""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
