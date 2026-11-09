"""device-backend の薄いクライアントと通信機設定の要約。"""

import http.client
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from device_data import ACCOUNT, PIN, SERIAL, SETTINGS_PATH, SETTINGS_PAYLOAD, STATUS_PATH

# =============================================================================
# device_backend_client
# =============================================================================


def test_get_settings_sends_pin_header_and_serial(device_backend):
    from base_api import device_backend_client

    device_backend.on("GET", SETTINGS_PATH, 200, {"devices": []})

    assert device_backend_client.get_settings(ACCOUNT, SERIAL, PIN) == (200, {"devices": []})
    sent = device_backend.calls[0]
    assert sent["query"] == {"serial": SERIAL}
    assert sent["headers"]["x-example-pin"] == PIN
    assert sent["timeout"] == 10


@pytest.mark.parametrize("status", [401, 403])
def test_get_settings_returns_401_and_403(device_backend, status):
    from base_api import device_backend_client

    device_backend.on("GET", SETTINGS_PATH, status, {"message": "x"})
    assert device_backend_client.get_settings(ACCOUNT, SERIAL, PIN) == (status, {"message": "x"})


@pytest.mark.parametrize("status", [404, 500, 503])
def test_get_settings_raises_for_other_status(device_backend, status):
    from base_api import device_backend_client

    device_backend.on("GET", SETTINGS_PATH, status)
    with pytest.raises(device_backend_client.DeviceBackendError) as info:
        device_backend_client.get_settings(ACCOUNT, SERIAL, PIN)
    assert info.value.status == status


def test_unreachable_backend_is_502(device_backend):
    from base_api import device_backend_client

    with pytest.raises(device_backend_client.DeviceBackendError) as info:
        device_backend_client.get_settings(ACCOUNT, SERIAL, PIN)
    assert info.value.status == 502


def test_timeout_is_502(monkeypatch):
    from base_api import device_backend_client

    def slow(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(device_backend_client._opener, "open", slow)
    with pytest.raises(device_backend_client.DeviceBackendError) as info:
        device_backend_client.get_settings(ACCOUNT, SERIAL, PIN)
    assert info.value.status == 502


def test_decode_payload_tolerates_non_object():
    from base_api import device_backend_client

    assert device_backend_client._decode_payload(b"") == {}
    assert device_backend_client._decode_payload(b"not json") == {}
    assert device_backend_client._decode_payload(b"[1]") == {}
    assert device_backend_client._decode_payload(b"\xff") == {}


def test_request_settings_read_posts_status_then_settings_without_pin(device_backend):
    from base_api import device_backend_client

    device_backend.on("POST", STATUS_PATH, 202)
    device_backend.on("POST", SETTINGS_PATH, 202)

    device_backend_client.request_settings_read(ACCOUNT, SERIAL)

    assert [(sent["method"], sent["path"]) for sent in device_backend.calls] == [
        ("POST", STATUS_PATH),
        ("POST", SETTINGS_PATH),
    ]
    for sent in device_backend.calls:
        assert sent["query"] == {"serial": SERIAL}
        assert "x-example-pin" not in sent["headers"]
        assert sent["headers"]["x-example-user"] == "service-platform"


def test_request_settings_read_raises_on_non_202(device_backend):
    from base_api import device_backend_client

    device_backend.on("POST", STATUS_PATH, 403)
    with pytest.raises(device_backend_client.DeviceBackendError) as info:
        device_backend_client.request_settings_read(ACCOUNT, SERIAL)
    assert info.value.status == 403
    assert len(device_backend.calls) == 1


def test_the_log_has_neither_the_pin_nor_the_serial(device_backend, caplog):
    from base_api import device_backend_client

    caplog.set_level(logging.DEBUG)
    device_backend.on("GET", SETTINGS_PATH, 200, SETTINGS_PAYLOAD)

    device_backend_client.get_settings(ACCOUNT, SERIAL, PIN)

    assert PIN not in caplog.text
    assert SERIAL not in caplog.text


class _Recorder(BaseHTTPRequestHandler):
    hits = None
    redirect_to = None

    def do_GET(self):
        type(self).hits.append((self.path, self.headers.get("X-Example-Pin")))
        if type(self).redirect_to:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def _serve(redirect_to=None):
    handler = type("Handler", (_Recorder,), {"hits": [], "redirect_to": redirect_to})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def test_redirect_is_not_followed_and_is_502(monkeypatch):
    """リダイレクトを追わない。X-Example-Pin が別ホストへ送られるのを防ぐ。"""
    from base_api import device_backend_client

    target, target_handler = _serve()
    origin, origin_handler = _serve(redirect_to=f"http://127.0.0.1:{target.server_port}/stolen")
    monkeypatch.setenv("DEVICE_BACKEND_API_BASE_URL", f"http://127.0.0.1:{origin.server_port}")
    try:
        with pytest.raises(device_backend_client.DeviceBackendError) as info:
            device_backend_client.get_settings(ACCOUNT, SERIAL, PIN)
    finally:
        origin.shutdown()
        target.shutdown()
    assert info.value.status == 502
    assert len(origin_handler.hits) == 1
    assert target_handler.hits == []


@pytest.mark.parametrize(
    "exc",
    [
        http.client.RemoteDisconnected("closed"),
        ConnectionResetError("reset"),
        http.client.IncompleteRead(b"x"),
    ],
)
def test_connection_failures_are_502_without_leaking_pin_or_url(monkeypatch, caplog, exc):
    from base_api import device_backend_client

    caplog.set_level(logging.DEBUG)

    def broken(*args, **kwargs):
        raise exc

    monkeypatch.setattr(device_backend_client._opener, "open", broken)
    with pytest.raises(device_backend_client.DeviceBackendError) as info:
        device_backend_client.get_settings(ACCOUNT, SERIAL, PIN)
    assert info.value.status == 502
    text = caplog.text + str(info.value) + repr(info.value.payload)
    assert PIN not in text
    assert "device_backend.example.test" not in text


# =============================================================================
# device_settings (暗証番号と通知先を落とす)
# =============================================================================


def test_summarize_settings_drops_pin_and_unknown_fields():
    from base_api import device_settings

    summary = device_settings.summarize_settings(SETTINGS_PAYLOAD)

    assert summary == {
        "location_name": "見本の現場",
        "devices": [
            {"address": 5, "model": "CAM-UNIT-1", "site": "ｶﾒﾗ", "action": "L"},
            {"address": 12, "model": "LOGGER-UNIT-A", "site": "ｽｲｲ", "action": "S"},
        ],
        "observation": {
            "enabled": True,
            "interval": "10M",
            "interval_short": "5M",
            "interval_min": 10,
            "interval_short_min": 5,
        },
    }
    assert PIN not in repr(summary)
    assert "notify@example.jp" not in repr(summary)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5M", 5),
        ("1H", 60),
        ("12H", 720),
        ("1D", 1440),
        ("999D", 1438560),
        (None, None),
        ("", None),
        (10, None),
        ("0M", None),
        ("1000M", None),
        ("01M", None),
        ("１０M", None),
        ("10m", None),
        ("10分", None),
    ],
)
def test_interval_minutes(text, expected):
    from base_api import device_settings

    assert device_settings.interval_minutes(text) == expected


@pytest.mark.parametrize(
    "payload", [{}, {"email": None}, {"email": {"data": []}}, {"email": {"data": {"devproto": None}}}]
)
def test_missing_observation_has_only_none(payload):
    from base_api import device_settings

    assert device_settings.summarize_settings(payload)["observation"] == {
        "enabled": None,
        "interval": None,
        "interval_short": None,
        "interval_min": None,
        "interval_short_min": None,
    }


def test_location_falls_back_to_kana_then_none():
    from base_api import device_settings

    assert device_settings.summarize_settings({"location": {"kanji": " ", "kana": "ﾐﾎﾝ"}})["location_name"] == "ﾐﾎﾝ"
    assert device_settings.summarize_settings({"location": "x"})["location_name"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("#05", 5), ("05", 5), (" #9 ", 9), ("#3E", None), ("#123", None), ("", None), (None, None), (5, None)],
)
def test_parse_address_reads_decimal(raw, expected):
    from base_api import device_settings

    assert device_settings.parse_address(raw) == expected


def test_devices_are_sorted_and_unknown_actions_become_none():
    from base_api import device_settings

    summary = device_settings.summarize_settings(
        {"devices": [{"address": "#20", "model": " ", "action": "X"}, {"address": "#03", "model": "CAM-UNIT-1"}, "x"]}
    )
    assert summary["devices"] == [
        {"address": 3, "model": "CAM-UNIT-1", "site": None, "action": None},
        {"address": 20, "model": None, "site": None, "action": None},
    ]
