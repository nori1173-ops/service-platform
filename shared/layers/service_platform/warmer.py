"""Scheduler の ping に早期応答し、業務処理とログを迂回する。"""

import time
from functools import wraps

_MAX_SLEEP_MS = 2000


def is_warmer_event(event) -> bool:
    """bool の True だけを受け付ける。HTTP の body 内の値は ping と扱わない。"""
    return isinstance(event, dict) and event.get("warmer") is True


def handle_warmer(event: dict) -> dict:
    """既存のカメラと同じ形で応答し、待機を 0〜2000 ms に収める。"""
    sleep_ms = max(0, min(int(event.get("sleep_ms") or 0), _MAX_SLEEP_MS))
    time.sleep(sleep_ms / 1000)
    return {"warmer": True, "slot": event.get("slot")}


def warm(handler):
    """認証や業務ログのデコレータより外側に付け、ping だけを先に処理する。"""

    @wraps(handler)
    def wrapped(event, context):
        if is_warmer_event(event):
            return handle_warmer(event)
        return handler(event, context)

    return wrapped
