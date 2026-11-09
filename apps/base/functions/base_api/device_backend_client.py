"""device-backend の薄いクライアント。

- get_settings: 通信機設定の照会 (前回値)。device-backend は読み取り済みの設定の暗証番号と照合する。
  401 = 暗証番号の不一致または未読取り、403 = 製造番号の不一致
- request_settings_read: 通信機へ状態と設定の報告を要求する (最新値)。暗証番号は送らない

暗証番号は `X-Example-Pin` ヘッダで渡すだけで、ログ・例外に入れない。リダイレクトは追わない。
"""

import http.client
import json
import os
from urllib import error, parse, request

from service_platform.logging import get_logger

logger = get_logger()

# device-backend の読取り要求の履歴に残る表示名 (X-Example-User)。認証には使われない
OPERATOR = "service-platform"
# API Gateway の 29 秒に収める (照会は通信機と通信せず即時に返る)
TIMEOUT_SECONDS = 10


class _NoRedirect(request.HTTPRedirectHandler):
    """リダイレクトを追わない。X-Example-Pin が別ホストへ送られるのを防ぐ。"""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = request.build_opener(_NoRedirect)


class DeviceBackendError(Exception):
    def __init__(self, status: int, payload: dict):
        super().__init__(f"DeviceBackend request failed: {status}")
        self.status = status
        self.payload = payload


def _decode_payload(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _request(method: str, path: str, *, query: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    base_url = os.environ["DEVICE_BACKEND_API_BASE_URL"].rstrip("/")
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    http_request = request.Request(  # noqa: S310 (接続先は samconfig の https の URL に限る)
        url, headers={"Accept": "application/json", **(headers or {})}, method=method
    )
    try:
        with _opener.open(http_request, timeout=TIMEOUT_SECONDS) as response:
            status, payload = response.status, _decode_payload(response.read())
    except error.HTTPError as exc:
        status, payload = exc.code, _decode_payload(exc.read())
    except (
        error.URLError,
        TimeoutError,
        ConnectionResetError,
        http.client.RemoteDisconnected,
        http.client.IncompleteRead,
    ) as exc:
        raise DeviceBackendError(502, {}) from exc
    if 300 <= status < 400:
        raise DeviceBackendError(502, {})
    logger.info("device_backend response", {"method": method, "resource": path.rsplit("/", 1)[-1], "status": status})
    return status, payload


def _account_path(account: str) -> str:
    return parse.quote(account, safe="")


def get_settings(account: str, serial: str, pin: str) -> tuple[int, dict]:
    status, payload = _request(
        "GET",
        f"/api/v2/accounts/{_account_path(account)}/settings",
        query={"serial": serial},
        headers={"X-Example-Pin": pin},
    )
    if status not in {200, 401, 403}:
        raise DeviceBackendError(status, {})
    return status, payload


def request_settings_read(account: str, serial: str) -> None:
    for resource in ("status", "settings"):
        status, _payload = _request(
            "POST",
            f"/api/v2/accounts/{_account_path(account)}/{resource}",
            query={"serial": serial},
            headers={"X-Example-User": OPERATOR},
        )
        if status != 202:
            raise DeviceBackendError(status, {})
