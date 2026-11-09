"""Cognito CustomMessage トリガー（計画 3 章、ADR-001 D1）。

招待 (`CustomMessage_AdminCreateUser`) とメールのコード (`CustomMessage_Authentication`) の
件名と本文を日本語にする。ほかの triggerSource と、プレースホルダが欠けたリクエストは
触らない (Cognito の既定の文面のまま送られる)。

ログには triggerSource と差し替えたかだけを出す。メールアドレスや sub は出さない。
"""

import json
import os

try:  # Lambda ではハンドラと同階層に配置されるため直接 import できる
    import templates
except ImportError:
    from custom_message import templates

INVITE = "CustomMessage_AdminCreateUser"
AUTHENTICATION = "CustomMessage_Authentication"
DEFAULT_CODE_PARAMETER = "{####}"


def _log(trigger_source: object, *, customized: bool, request: dict) -> None:
    print(
        json.dumps(
            {
                "message": "custom_message",
                "trigger_source": trigger_source,
                "customized": customized,
                "code_parameter_is_default": request.get("codeParameter") == DEFAULT_CODE_PARAMETER,
                "has_username_parameter": bool(request.get("usernameParameter")),
            },
            ensure_ascii=False,
        )
    )


def _build(trigger_source: object, request: dict) -> tuple[str, str] | None:
    code = request.get("codeParameter")
    if not code:
        return None
    if trigger_source == INVITE:
        username = request.get("usernameParameter")
        if not username:
            return None
        return templates.invite(
            username_parameter=username,
            code_parameter=code,
            site_url=os.environ.get("SITE_URL", ""),
            email=(request.get("userAttributes") or {}).get("email", ""),
        )
    if trigger_source == AUTHENTICATION:
        return templates.sign_in_code(code_parameter=code)
    return None


def handler(event: dict, context) -> dict:
    trigger_source = event.get("triggerSource")
    request = event.get("request") or {}
    message = _build(trigger_source, request)
    _log(trigger_source, customized=message is not None, request=request)
    if message is None:
        return event

    subject, body = message
    return {**event, "response": {**(event.get("response") or {}), "emailSubject": subject, "emailMessage": body}}
