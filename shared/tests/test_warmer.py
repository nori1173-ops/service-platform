"""Scheduler の ping の判定、待機と業務処理の迂回を検査する。"""

import importlib
import json
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "event,expected",
    [
        ({"warmer": True}, True),
        ({"warmer": False}, False),
        ({"warmer": "true"}, False),
        ({"warmer": 1}, False),
        ({}, False),
        (None, False),
        ([], False),
        ({"body": json.dumps({"warmer": True})}, False),
    ],
)
def test_only_boolean_true_is_a_ping(event, expected):
    warmer = importlib.import_module("service_platform.warmer")
    assert warmer.is_warmer_event(event) is expected


@pytest.mark.parametrize("sleep_ms,seconds", [(None, 0), (-10, 0), (500, 0.5), (2000, 2), (9999, 2), ("250", 0.25)])
def test_ping_returns_slot_and_clamps_sleep(monkeypatch, caplog, sleep_ms, seconds):
    warmer = importlib.import_module("service_platform.warmer")
    sleep = Mock()
    monkeypatch.setattr(warmer.time, "sleep", sleep)
    assert warmer.handle_warmer({"warmer": True, "slot": 2, "sleep_ms": sleep_ms}) == {"warmer": True, "slot": 2}
    sleep.assert_called_once_with(seconds)
    assert not caplog.records


def test_minimal_ping_returns_a_null_slot(monkeypatch):
    warmer = importlib.import_module("service_platform.warmer")
    monkeypatch.setattr(warmer.time, "sleep", Mock())
    assert warmer.handle_warmer({"warmer": True}) == {"warmer": True, "slot": None}


def test_decorator_forwards_normal_events_and_context():
    warmer = importlib.import_module("service_platform.warmer")
    inner = Mock(return_value={"statusCode": 200})
    event, context = {"body": "normal"}, object()
    assert warmer.warm(inner)(event, context) == {"statusCode": 200}
    inner.assert_called_once_with(event, context)
