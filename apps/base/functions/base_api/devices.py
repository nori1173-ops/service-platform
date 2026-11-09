"""通信機ログインと最新値と、サービスの一覧（計画 2.6 / 4.5、ADR-001 D4 / D5、判断 4・9）。

通信機ログインの流れ:
1. 入力の検査 (`account`・`serial` は 8 桁、`pin` は 4 桁、`mode`)。
2. 利用者ごとの失敗回数で止めていれば 429 `USER_PIN_LOCKED` (通信機のことを何も知らせない。)。
3. 条件 (1)(2) (稼働中の利用期間と、P を扱う根拠: 人宛・グループ宛の割り当てか everyone)。無ければ 403
   `DEVICE_NOT_ASSIGNED` で、device_backend を呼ばない。
4. 本人のログイン記録を強い整合で読む (その版を書き込みの条件にする)。
5. 通信機ごとの失敗回数で止めていれば 429 `PIN_LOCKED`。止めているかどうかを、根拠の無い人には知らせない。
6. 最新値の待機中の照会 (mode=latest) なら、受付の poll_token と暗証番号の HMAC を確かめる (refresh_guard)。
7. device-backend の照会。401 / 403 なら通信機ごとと利用者ごとの両方の失敗回数を数えて 403 `PIN_MISMATCH` /
   `SERIAL_MISMATCH`。数えないのは、6 で受付と合った待機中の照会の 401 だけ (両方とも数えない)。
8. 成功: 通信機ごとの失敗回数を消し、ログイン記録 (12 時間)・製造番号 (まだ無いときだけ)・監査を 1 つの
   トランザクションで書き (device_writes。条件は書く直前の時刻、3 で読んだ根拠と親の version、4 で読んだ記録の版)、
   6 で受付と合った照会ならその受付の記録を消す。

最新値の開始は、前回値の照会で暗証番号を確かめてから受け付ける (違えば数えて 403。読取り要求は送らない)。

暗証番号は device-backend への照会と HMAC に使うだけで、保存しない。応答・ログ・監査・例外のメッセージに入れない。
poll_token は開始の応答で 1 回だけ返し、保存しない。
"""

import re
from datetime import datetime

from base_api import clock, device_backend_client, device_settings, device_writes, errors, pinfail, refresh_guard
from service_platform import access, audit, ledger, services
from service_platform.access import DeviceAccess
from service_platform.auth import ForbiddenError
from service_platform.ids import new_ulid
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

SERVICE = "base"
MODES = ("previous", "latest")
# Python の \d は全角数字も通すため、ASCII の [0-9] で検査する
_EIGHT_DIGITS = re.compile(r"[0-9]{8}")
_FOUR_DIGITS = re.compile(r"[0-9]{4}")
IN_PROGRESS_MESSAGE = "ほかの方が最新値を取得しています。終わってからやり直してください"

logger = get_logger()

ApiError = errors.ApiError


class PinMismatchError(ForbiddenError):
    code = "PIN_MISMATCH"

    def __init__(self) -> None:
        super().__init__("暗証番号が違うか、この通信機の設定がまだ読み取られていません")


class SerialMismatchError(ForbiddenError):
    code = "SERIAL_MISMATCH"

    def __init__(self) -> None:
        super().__init__("製造番号が違います")


def _device_backend_unavailable() -> ApiError:
    return ApiError(
        502, "DEVICE_BACKEND_UNAVAILABLE", "通信機の情報を取得できませんでした。時間をおいてやり直してください"
    )


def valid_account(value: object) -> bool:
    return isinstance(value, str) and _EIGHT_DIGITS.fullmatch(value) is not None


def _iso(epoch: int) -> str:
    return refresh_guard.iso(epoch)


def _body(body: object) -> dict:
    if not isinstance(body, dict):
        raise errors.validation("本文は JSON のオブジェクトで送ってください")
    return body


def _account_and_serial(body: dict) -> tuple[str, str]:
    account, serial = body.get("account"), body.get("serial")
    if not valid_account(account) or not valid_account(serial):
        raise errors.validation("通信機アカウントと製造番号は半角数字 8 桁で入力してください")
    return account, serial


def _pin(body: dict) -> str:
    pin = body.get("pin")
    if not isinstance(pin, str) or _FOUR_DIGITS.fullmatch(pin) is None:
        raise errors.validation("暗証番号は半角数字 4 桁で入力してください")
    return pin


def _audit(action: str, user: UserContext, request: dict, **fields) -> None:
    audit.emit(action, actor=user, service=SERVICE, request=request, **fields)


def _fields(found: DeviceAccess) -> dict:
    return {"account": found.account, "device_period_id": found.device_period_id, "assignment_id": found.assignment_id}


def _user_not_locked(user: UserContext, account: str, now: datetime, request: dict, action: str) -> None:
    try:
        pinfail.ensure_user_not_locked(user.user_id, now)
    except pinfail.UserPinLockedError as exc:
        _audit(action, user, request, result="denied", code=exc.code, account=account)
        raise


def _basis(user: UserContext, account: str, now: datetime, request: dict, action: str) -> DeviceAccess:
    _user_not_locked(user, account, now, request, action)
    try:
        return access.require_basis(user.user_id, account, now)
    except access.DeviceNotAssignedError as exc:
        _audit(action, user, request, result="denied", code=exc.code, account=account)
        raise


def _not_locked(user: UserContext, now: datetime, request: dict, found: DeviceAccess, action: str) -> None:
    try:
        pinfail.ensure_not_locked(found.account, now)
    except pinfail.PinLockedError as exc:
        _audit(action, user, request, result="denied", code=exc.code, **_fields(found))
        raise


def _granted_services(device_period_id: str, account: str, now: datetime) -> list[dict]:
    rows = []
    for grant in access.active_grants(device_period_id, now):
        if grant.service not in services.SERVICES:
            continue
        rows.append(
            {
                "id": grant.service,
                "name": services.SERVICES[grant.service].name,
                "url": services.launch_url(grant.service, account),
                "starts_at": grant.starts_at,
                "ends_at": grant.ends_at,
            }
        )
    return rows


def _rejected(user, found: DeviceAccess, *, status: int, action: str, detail: dict, now, request, counted: bool):
    """照会の失敗 (device_backend の 401 / 403)。数えるなら失敗回数を足し、5 回目なら止める。"""
    error = PinMismatchError() if status == 401 else SerialMismatchError()
    locked_until = pinfail.record_failure(found.account, now) if counted else None
    user_locked_until = pinfail.record_user_failure(user.user_id, now) if counted else None
    _audit(
        action,
        user,
        request,
        result="denied",
        code=error.code,
        detail=device_writes.audit_detail(found, **detail),
        **_fields(found),
    )
    for scope, until in (("device", locked_until), ("user", user_locked_until)):
        if until is not None:
            _audit(
                "device.pin_locked",
                user,
                request,
                detail={"scope": scope, "locked_until": _iso(until)},
                **_fields(found),
            )
    logger.info(
        f"{action} rejected",
        {"user_id": user.user_id, "account": found.account, "code": error.code, "counted": counted, **detail},
    )
    raise error


def _get_settings(user, found: DeviceAccess, *, serial: str, pin: str, action: str, detail: dict, request):
    try:
        return device_backend_client.get_settings(found.account, serial, pin)
    except device_backend_client.DeviceBackendError as exc:
        if action == "device.refresh_start" and exc.status == 404:
            raise ApiError(
                409, "NO_PREVIOUS_SETTINGS", "最新値の取得は、通信機が一度設定を報告した後に使えます"
            ) from exc
        _audit(
            action,
            user,
            request,
            result="failed",
            code="DEVICE_BACKEND_UNAVAILABLE",
            detail={**detail, "status": exc.status},
            **_fields(found),
        )
        raise _device_backend_unavailable() from exc


def login(user: UserContext, body: object, request: dict) -> dict:
    """通信機ログイン。応答は通信機設定の要約と、今許可されているサービス。"""
    payload = _body(body)
    account, serial = _account_and_serial(payload)
    pin = _pin(payload)
    mode = payload.get("mode", "previous")
    if mode not in MODES:
        raise errors.validation("mode は previous か latest で指定してください")
    now = clock.now()
    found = _basis(user, account, now, request, "device.login")
    previous = ledger._get(ledger.user_pk(user.user_id), ledger.login_sk(account))
    _not_locked(user, now, request, found, "device.login")
    poll = refresh_guard.NOT_MATCHED
    if mode == "latest":
        poll = refresh_guard.check_poll(
            account, user.user_id, payload.get("acceptance_id"), payload.get("poll_token"), pin, now
        )
    if poll.outcome == refresh_guard.POLL_TOO_SOON:
        raise ApiError(429, "POLL_TOO_SOON", "照会の間隔が短すぎます。少し待ってからやり直してください")
    detail = {"method": mode}
    status, settings = _get_settings(
        user, found, serial=serial, pin=pin, action="device.login", detail=detail, request=request
    )
    if status in (401, 403):
        counted = not (poll.outcome == refresh_guard.POLL_FREE and status == 401)
        _rejected(
            user, found, status=status, action="device.login", detail=detail, now=now, request=request, counted=counted
        )
    summary = device_settings.summarize_settings(settings)
    pinfail.clear(account, now)
    device_writes.write_login(
        user, found, serial=serial, mode=mode, summary=summary, request=request, previous=previous
    )
    if poll.matched:
        refresh_guard.clear(account, payload["acceptance_id"])
    logger.info(
        "device.login",
        {
            "user_id": user.user_id,
            "account": account,
            "method": mode,
            "basis": found.basis.kind,
            "device_count": len(summary["devices"]),
        },
    )
    return {
        "account": account,
        "method": mode,
        "location_name": summary["location_name"],
        "observation": summary["observation"],
        "devices": [{**device, "address_text": f"{device['address']:02d}"} for device in summary["devices"]],
        "services": [
            {key: item[key] for key in ("id", "name", "url")}
            for item in _granted_services(found.device_period_id, account, now)
        ],
    }


def _guard(account: str, user: UserContext, now: datetime) -> None:
    guard = refresh_guard.availability(account, now)
    if guard["reason"] == refresh_guard.GUARD_UNAVAILABLE:
        raise ApiError(503, "GUARD_UNAVAILABLE", "運用中かどうかを確認できないため、最新値の取得はできません")
    if guard["reason"] == refresh_guard.IN_OPERATION:
        logger.info("refresh guard", {"account": account, "result": "in_operation"})
        raise ApiError(409, "IN_OPERATION", "この通信機は運用中のため、最新値の取得はできません")
    active = refresh_guard.get_active(account, now)
    if active and active.get("accepted_by") != user.user_id:
        logger.info("refresh guard", {"account": account, "result": "in_progress"})
        raise ApiError(409, "IN_PROGRESS", IN_PROGRESS_MESSAGE)


def refresh_start(user: UserContext, body: object, request: dict) -> dict:
    """最新値の開始。前回値の照会で暗証番号を確かめ、受付を作り、通信機へ読取りを要求する (202)。

    本人の受付が期限内なら、読取りを要求し直さずに新しい poll_token を返す (`resumed`)。
    """
    payload = _body(body)
    account, serial = _account_and_serial(payload)
    pin = _pin(payload)
    now = clock.now()
    action = "device.refresh_start"
    found = _basis(user, account, now, request, action)
    _not_locked(user, now, request, found, action)
    _guard(account, user, now)
    detail = {"stage": "pin_check"}
    status, _settings = _get_settings(
        user, found, serial=serial, pin=pin, action=action, detail=detail, request=request
    )
    if status in (401, 403):
        _rejected(user, found, status=status, action=action, detail=detail, now=now, request=request, counted=True)
    pinfail.clear(account, now)
    poll_token = refresh_guard.new_poll_token()
    acceptance_id = new_ulid()
    mac = refresh_guard.pin_mac(poll_token, account, pin)
    try:
        record, resumed = refresh_guard.acquire(account, user.user_id, now, acceptance_id=acceptance_id, mac=mac)
    except refresh_guard.RefreshInProgress as exc:
        raise ApiError(409, "IN_PROGRESS", IN_PROGRESS_MESSAGE) from exc
    if not resumed:
        _request_read(user, found, serial=serial, acceptance_id=acceptance_id, now=now, request=request)
    _audit(action, user, request, detail={"resumed": resumed}, **_fields(found))
    logger.info("refresh guard", {"account": account, "result": "resumed" if resumed else "allowed"})
    return {
        "account": account,
        "accepted_at": record["accepted_at"],
        "expires_at": _iso(int(record["expires_at"])),
        "timeout_seconds": refresh_guard.REFRESH_TTL_SECONDS,
        "acceptance_id": acceptance_id,
        "poll_token": poll_token,
        "resumed": resumed,
    }


def _request_read(user, found: DeviceAccess, *, serial: str, acceptance_id: str, now, request) -> None:
    try:
        device_backend_client.request_settings_read(found.account, serial)
    except device_backend_client.DeviceBackendError as exc:
        refresh_guard.release(found.account, acceptance_id)
        if exc.status == 403:
            _rejected(
                user,
                found,
                status=403,
                action="device.refresh_start",
                detail={"stage": "read_request"},
                now=now,
                request=request,
                counted=True,
            )
        if exc.status == 404:
            raise ApiError(404, "NO_RESPONSE", "通信機から応答がありません") from exc
        raise _device_backend_unavailable() from exc
    except Exception:
        refresh_guard.release(found.account, acceptance_id)
        raise


def _path_account(account: str) -> str:
    if not valid_account(account):
        raise errors.validation("通信機アカウントは半角数字 8 桁で指定してください")
    return account


def refresh_availability(user: UserContext, account: str) -> dict:
    """最新値の可否。根拠の無い人には答えない (運用中かを第三者に知らせない。カメラ §16 31)。

    everyone の P では、割り当ての無い人にも答える (一覧に出ている通信機のため。ADR D14)。
    """
    _path_account(account)
    now = clock.now()
    access.require_basis(user.user_id, account, now)
    return refresh_guard.availability(account, now)


def services_of(user: UserContext, account: str) -> dict:
    """条件 (1)(2)(3) の後、今有効な許可と起動先。"""
    _path_account(account)
    now = clock.now()
    found = access.require_device(user.user_id, account, now)
    login_row = found.login or {}
    summary = login_row.get("summary") if isinstance(login_row.get("summary"), dict) else {}
    expires_at = login_row.get("expires_at")
    return {
        "account": account,
        "org_name": found.org_name,
        "location_name": summary.get("location_name"),
        "method": login_row.get("method"),
        "logged_in_at": login_row.get("logged_in_at"),
        "expires_at": _iso(int(expires_at)) if expires_at is not None else None,
        "services": _granted_services(found.device_period_id, account, now),
    }
