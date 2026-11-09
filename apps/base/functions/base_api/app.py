"""土台のアプリ base の HTTP API ハンドラ（計画 2.6 / 4.2 / 4.5、判断 2）。

利用者本人が呼ぶ API を置く。admin の操作は admin-users に置き、ここには置かない。
全ルートが `identity.require_user` で台帳の利用者と状態を確かめる。停止中でも呼べるのは
ADR D9 の例外 1 (`POST /me/totp-registered`) と例外 2 (`GET /me`) だけで、準備中・確認待ちの admin も
この 2 本だけを呼べる (ほかは shared の既定で 403 `ADMIN_NOT_ACTIVE`)。
通信機を伴うルートは、加えて shared の `access` で ADR D4 の条件を確かめる。
"""

import json

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response

from base_api import devices, errors, me
from service_platform import identity, origin_verify, response, warmer
from service_platform.logging import LambdaLogger, get_logger

API_PREFIX = "/api/base"

logger = get_logger()
app = APIGatewayHttpResolver(strip_prefixes=[API_PREFIX])
response.install_error_handlers(app)


@app.get("/health")
def health() -> dict:
    identity.require_user(app.current_event.raw_event)
    return response.ok({"status": "ok"})


@app.get("/me")
def get_me() -> dict:
    claims, user = identity.require_user(app.current_event.raw_event, allow_suspended=True, allow_inactive_admin=True)
    return response.ok(me.view(claims, user))


@app.post("/me/totp-registered")
def post_totp_registered() -> dict:
    event = app.current_event.raw_event
    claims, user = identity.require_user(event, allow_suspended=True, allow_inactive_admin=True)
    return response.ok({"admin_state": me.register_totp(claims, user, request=event)})


@app.post("/me/passkey-mfa")
def post_passkey_mfa() -> dict:
    event = app.current_event.raw_event
    claims, user = identity.require_user(event)
    me.enable_passkey_mfa(claims, user, request=event)
    return response.ok({"passkey_mfa_enabled": True})


@app.get("/me/assignments")
def get_my_assignments() -> dict:
    _claims, user = identity.require_user(app.current_event.raw_event)
    return response.ok(me.assignments(user))


@app.get("/me/devices")
def get_my_devices() -> dict:
    _claims, user = identity.require_user(app.current_event.raw_event)
    return response.ok(me.devices(user))


def _json_body() -> object:
    raw = app.current_event.body
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@app.post("/devices/login")
def post_device_login() -> dict:
    event = app.current_event.raw_event
    _claims, user = identity.require_user(event)
    return response.ok(devices.login(user, _json_body(), event))


@app.post("/devices/refresh")
def post_device_refresh() -> Response:
    event = app.current_event.raw_event
    _claims, user = identity.require_user(event)
    return response.json_response(response.ok(devices.refresh_start(user, _json_body(), event)), 202)


@app.get("/devices/<account>/refresh-availability")
def get_refresh_availability(account: str) -> dict:
    _claims, user = identity.require_user(app.current_event.raw_event)
    return response.ok(devices.refresh_availability(user, account))


@app.get("/devices/<account>/services")
def get_device_services(account: str) -> dict:
    _claims, user = identity.require_user(app.current_event.raw_event)
    return response.ok(devices.services_of(user, account))


@app.exception_handler(errors.ApiError)
def handle_api_error(exc: errors.ApiError) -> Response:
    return response.error(exc.code, str(exc), exc.status)


@app.exception_handler(me.TemporarilyUnavailableError)
def handle_unavailable(exc: me.TemporarilyUnavailableError) -> Response:
    return response.error(exc.code, str(exc), 503)


@warmer.warm
@LambdaLogger.contextualize
@origin_verify.origin_verify
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
