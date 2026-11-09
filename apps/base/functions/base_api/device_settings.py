"""device-backend の通信機設定 (GET settings の 200 本文) の要約。

本文には暗証番号や通知先が含まれるため、現場名・子機一覧・自動観測だけを取り出す。
読取り時刻は本文に無いので要約にも持たない。
"""

import re

# 設定の子機アドレスは 10 進の `#NN`。画像メールの $LG 行の 16 進 (#3E) とは別物
_ADDRESS = re.compile(r"#?([0-9]{1,2})")
_INTERVAL = re.compile(r"([1-9][0-9]{0,2})(M|H|D)")
_ACTIONS = ("A", "B", "L", "S", "R", "N")


def interval_minutes(text) -> int | None:
    if not isinstance(text, str):
        return None
    matched = _INTERVAL.fullmatch(text)
    if not matched:
        return None
    amount, unit = matched.groups()
    return int(amount) * {"M": 1, "H": 60, "D": 1440}[unit]


def _observation(payload: dict) -> dict:
    value = payload
    for key in ("email", "data", "devproto"):
        value = value.get(key) if isinstance(value, dict) else None
    devproto = value if isinstance(value, dict) else {}
    intervals = devproto.get("interval")
    intervals = intervals if isinstance(intervals, dict) else {}
    long, short = intervals.get("long"), intervals.get("short")
    long_min, short_min = interval_minutes(long), interval_minutes(short)
    return {
        "enabled": devproto.get("enabled") if isinstance(devproto.get("enabled"), bool) else None,
        "interval": long if long_min is not None else None,
        "interval_short": short if short_min is not None else None,
        "interval_min": long_min,
        "interval_short_min": short_min,
    }


def parse_address(value) -> int | None:
    if not isinstance(value, str):
        return None
    matched = _ADDRESS.fullmatch(value.strip())
    return int(matched.group(1)) if matched else None


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def summarize_settings(payload: dict) -> dict:
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    raw_devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    devices = []
    for device in raw_devices:
        if not isinstance(device, dict):
            continue
        address = parse_address(device.get("address"))
        if address is None:
            continue
        action = device.get("action")
        devices.append(
            {
                "address": address,
                "model": _text(device.get("model")),
                "site": _text(device.get("site")),
                "action": action if action in _ACTIONS else None,
            }
        )
    devices.sort(key=lambda item: item["address"])
    return {
        "location_name": _text(location.get("kanji")) or _text(location.get("kana")),
        "devices": devices,
        "observation": _observation(payload),
    }
