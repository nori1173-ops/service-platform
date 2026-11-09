"""利用者の管理の HTTP API ハンドラ（計画 2.6 / 4.2、ADR-001 D3 / D8 / D9 / D12）。

ルートは `/api/admin-users` を剥がした形で書く。全ルートが admin 専用で、毎回 shared の
`identity.require_user` (台帳の利用者と状態) と `admin_guard.require_admin` (クレーム、台帳の admin の状態、
TOTP を通ったセッション、Cognito の現在状態) を通してから処理する。画面のメニューの非表示とボタンの
disabled は使い勝手であって権限ではない。

利用者の識別子は台帳の user_id。操作の記録は監査テーブル (ADR D12) と CloudWatch Logs の構造化ログに残す。
"""

from urllib.parse import parse_qs

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response

from admin_users import (
    assignments,
    cognito,
    device_periods,
    email_change,
    errors,
    grants,
    group_members,
    groups,
    users,
    validation,
    views,
)
from service_platform import admin_guard, identity, origin_verify, response
from service_platform.auth import ForbiddenError
from service_platform.ledger import UserContext
from service_platform.logging import LambdaLogger, get_logger

API_PREFIX = "/api/admin-users"

logger = get_logger()
app = APIGatewayHttpResolver(strip_prefixes=[API_PREFIX])
response.install_error_handlers(app)


def _caller() -> UserContext:
    """呼び出し元が「今」有効な admin であることを確かめる (ADR D9)。"""
    claims, user = identity.require_user(app.current_event.raw_event)
    try:
        admin_guard.require_admin(claims, user, cognito.client())
    except ForbiddenError as exc:
        logger.warning(
            "forbidden", {"operation": "authorize", "actor_user_id": user.user_id, "result": getattr(exc, "code", "")}
        )
        raise
    return user


def _event() -> dict:
    return app.current_event.raw_event


def _body() -> dict:
    return validation.body(app.current_event.body)


@app.get("/health")
def health() -> dict:
    _caller()
    return response.ok({"status": "ok"})


@app.get("/users")
def get_users() -> dict:
    caller = _caller()
    rows = views.list_users()
    logger.info("list_users", {"operation": "list_users", "actor_user_id": caller.user_id, "count": len(rows)})
    return response.ok({"users": rows, "self_user_id": caller.user_id})


@app.post("/users")
def post_users() -> Response:
    caller = _caller()
    return response.json_response(response.ok(users.invite(caller, _body(), _event())), 201)


@app.get("/users/<user_id>")
def get_user(user_id: str) -> dict:
    _caller()
    return response.ok(views.detail(user_id))


@app.put("/users/<user_id>/profile")
def put_profile(user_id: str) -> dict:
    return response.ok(users.update_profile(_caller(), user_id, _body(), _event()))


@app.put("/users/<user_id>/email")
def put_email(user_id: str) -> dict:
    return response.ok(email_change.change_email(_caller(), user_id, _body(), _event()))


@app.post("/users/<user_id>/suspend")
def post_suspend(user_id: str) -> dict:
    return response.ok(users.suspend(_caller(), user_id, _body(), _event()))


@app.post("/users/<user_id>/restore")
def post_restore(user_id: str) -> dict:
    return response.ok(users.restore(_caller(), user_id, _body(), _event()))


@app.post("/users/<user_id>/reinvite")
def post_reinvite(user_id: str) -> dict:
    return response.ok(users.reinvite(_caller(), user_id, _body(), _event()))


@app.post("/users/<user_id>/admin-activation")
def post_admin_activation(user_id: str) -> dict:
    return response.ok(users.activate_admin(_caller(), user_id, _body(), _event()))


@app.post("/users/<user_id>/temporary-password")
def post_temporary_password(user_id: str) -> dict:
    return response.ok(users.issue_temporary_password(_caller(), user_id, _event()))


@app.put("/users/<user_id>/admin")
def put_admin(user_id: str) -> dict:
    return response.ok(users.revoke_admin(_caller(), user_id, _body(), _event()))


def _status_query() -> str:
    query = parse_qs(app.current_event.raw_query_string, keep_blank_values=True)
    status = query.get("status", ["active"])
    if len(status) != 1:
        raise errors.validation("status は一つだけ指定してください")
    return status[0]


@app.get("/device-periods")
def get_device_periods() -> dict:
    _caller()
    return response.ok({"device_periods": device_periods.list_periods(_status_query())})


@app.post("/device-periods")
def post_device_periods() -> Response:
    caller = _caller()
    return response.json_response(response.ok(device_periods.start(caller, _body(), _event())), 201)


@app.get("/device-periods/<period_id>")
def get_device_period(period_id: str) -> dict:
    _caller()
    return response.ok(device_periods.detail(period_id))


@app.put("/device-periods/<period_id>/access")
def put_device_period_access(period_id: str) -> dict:
    return response.ok(device_periods.set_access(_caller(), period_id, _body(), _event()))


@app.post("/device-periods/<period_id>/end")
def end_device_period(period_id: str) -> dict:
    return response.ok(device_periods.end(_caller(), period_id, _event()))


@app.post("/device-periods/<period_id>/assignments")
def post_assignment(period_id: str) -> Response:
    caller = _caller()
    return response.json_response(response.ok(assignments.add(caller, period_id, _body(), _event())), 201)


@app.post("/device-periods/<period_id>/assignments/<assignment_id>/end")
def end_assignment(period_id: str, assignment_id: str) -> dict:
    return response.ok(assignments.end(_caller(), period_id, assignment_id, _event()))


@app.post("/device-periods/<period_id>/grants")
def post_grant(period_id: str) -> Response:
    caller = _caller()
    return response.json_response(response.ok(grants.add(caller, period_id, _body(), _event())), 201)


@app.post("/device-periods/<period_id>/grants/<grant_id>/end")
def end_grant(period_id: str, grant_id: str) -> dict:
    return response.ok(grants.end(_caller(), period_id, grant_id, _event()))


@app.get("/groups")
def get_groups() -> dict:
    _caller()
    return response.ok({"groups": groups.list_groups(_status_query())})


@app.post("/groups")
def post_groups() -> Response:
    caller = _caller()
    return response.json_response(response.ok(groups.create(caller, _body(), _event())), 201)


@app.get("/groups/<group_id>")
def get_group(group_id: str) -> dict:
    _caller()
    return response.ok(groups.detail(group_id))


@app.put("/groups/<group_id>")
def put_group(group_id: str) -> dict:
    return response.ok(groups.update(_caller(), group_id, _body(), _event()))


@app.post("/groups/<group_id>/end")
def end_group(group_id: str) -> dict:
    return response.ok(groups.end(_caller(), group_id, _event()))


@app.post("/groups/<group_id>/members")
def post_group_member(group_id: str) -> Response:
    caller = _caller()
    return response.json_response(response.ok(group_members.add(caller, group_id, _body(), _event())), 201)


@app.post("/groups/<group_id>/members/<user_id>/remove")
def remove_group_member(group_id: str, user_id: str) -> dict:
    return response.ok(group_members.remove(_caller(), group_id, user_id, _event()))


@app.exception_handler(errors.ApiError)
def handle_api_error(exc: errors.ApiError) -> Response:
    return response.error(exc.code, str(exc), exc.status)


@LambdaLogger.contextualize
@origin_verify.origin_verify
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
