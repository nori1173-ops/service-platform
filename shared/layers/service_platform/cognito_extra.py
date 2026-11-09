"""Lambda のランタイムの boto3 に無い Cognito の操作（計画 2.4、リスク 2）。

AdminGetUserAuthFactors は、2026-11 時点の Lambda の python3.13 の boto3 (1.42 系) に無い。
SDK に操作・項目があればそれを使い、無ければ SigV4 で署名して直接呼ぶ (PoC と同じ方式)。
AdminSetUserMFAPreference の WebAuthnMfaSettings も実行時のモデルで判定する。
"""

import json

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError
from botocore.httpsession import URLLib3Session

SERVICE = "cognito-idp"
TARGET_PREFIX = "AWSCognitoIdentityProviderService."
CONTENT_TYPE = "application/x-amz-json-1.1"
TIMEOUT_SECONDS = 10
AUTH_FACTORS = "AdminGetUserAuthFactors"

FACTOR_FLAGS = {"PASSWORD": "password", "WEB_AUTHN": "web_authn", "EMAIL_OTP": "email_otp"}


def _send(prepared):
    return URLLib3Session(timeout=TIMEOUT_SECONDS).send(prepared)


def _call(client, operation: str, payload: dict) -> dict:
    request = AWSRequest(
        method="POST",
        url=f"{client.meta.endpoint_url}/",
        data=json.dumps(payload),
        headers={"Content-Type": CONTENT_TYPE, "X-Amz-Target": f"{TARGET_PREFIX}{operation}"},
    )
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    SigV4Auth(credentials, SERVICE, client.meta.region_name).add_auth(request)
    response = _send(request.prepare())
    body = json.loads(response.content or b"{}")
    if response.status_code >= 400:
        code = str(body.get("__type") or "UnknownError").rsplit("#", 1)[-1]
        error = {
            "Error": {"Code": code, "Message": str(body.get("message") or body.get("Message") or "")},
            "ResponseMetadata": {"HTTPStatusCode": response.status_code},
        }
        raise ClientError(error, operation)
    return body


def admin_get_user_auth_factors(client, user_pool_id: str, username: str) -> dict:
    """AdminGetUserAuthFactors の応答 (`ConfiguredUserAuthFactors` ほか)。"""
    payload = {"UserPoolId": user_pool_id, "Username": username}
    method = getattr(client, "admin_get_user_auth_factors", None)
    if callable(method):
        return method(**payload)
    return _call(client, AUTH_FACTORS, payload)


def factor_flags(response: dict) -> dict[str, bool]:
    """サインインの要素ごとの有無 (`password`・`web_authn`・`email_otp`)。TOTP はここで見ない (判断 13)。"""
    configured = set(response.get("ConfiguredUserAuthFactors") or [])
    return {flag: factor in configured for factor, flag in FACTOR_FLAGS.items()}


def admin_enable_passkey_mfa(client, user_pool_id: str, username: str) -> dict:
    """TOTP を有効・優先に保ち、本人確認つきのパスキーでも MFA を満たせるようにする。"""
    payload = {
        "UserPoolId": user_pool_id,
        "Username": username,
        "SoftwareTokenMfaSettings": {"Enabled": True, "PreferredMfa": True},
        "WebAuthnMfaSettings": {"Enabled": True},
    }
    method = getattr(client, "admin_set_user_mfa_preference", None)
    model = getattr(getattr(client, "meta", None), "service_model", None)
    if callable(method) and (
        model is None or "WebAuthnMfaSettings" in model.operation_model("AdminSetUserMFAPreference").input_shape.members
    ):
        return method(**payload)
    return _call(client, "AdminSetUserMFAPreference", payload)
