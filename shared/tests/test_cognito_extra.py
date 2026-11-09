"""`service_platform.cognito_extra` のテスト（計画 2.4、リスク 2）。

Lambda の python3.13 の boto3 (2026-11 時点 1.42 系) には AdminGetUserAuthFactors が無い。
SDK に操作があればそれを使い、無ければ SigV4 で直接呼ぶ。
"""

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from service_platform import cognito_extra

POOL = "ap-northeast-1_EXMPLpool1"
USERNAME = "cognito-user-1"
ENDPOINT = "https://cognito-idp.ap-northeast-1.amazonaws.com"


class OldClient:
    """AdminGetUserAuthFactors を持たない (Lambda のランタイムの) boto3 クライアントの代わり。"""

    meta = SimpleNamespace(region_name="ap-northeast-1", endpoint_url=ENDPOINT)


class NewClient(OldClient):
    def __init__(self):
        self.calls = []

    def admin_get_user_auth_factors(self, **kwargs):
        self.calls.append(kwargs)
        return {"Username": kwargs["Username"], "ConfiguredUserAuthFactors": ["PASSWORD"]}


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")


@pytest.fixture
def sent(monkeypatch):
    """送った要求を記録し、決めた応答を返す。"""
    requests = []
    reply = {"status": 200, "body": {"Username": USERNAME, "ConfiguredUserAuthFactors": ["EMAIL_OTP", "WEB_AUTHN"]}}

    def _send(prepared):
        requests.append(prepared)
        return SimpleNamespace(status_code=reply["status"], content=json.dumps(reply["body"]).encode())

    monkeypatch.setattr(cognito_extra, "_send", _send)
    return SimpleNamespace(requests=requests, reply=reply)


def test_uses_the_sdk_operation_when_available(sent):
    client = NewClient()

    result = cognito_extra.admin_get_user_auth_factors(client, POOL, USERNAME)

    assert result["ConfiguredUserAuthFactors"] == ["PASSWORD"]
    assert client.calls == [{"UserPoolId": POOL, "Username": USERNAME}]
    assert sent.requests == []


def test_signs_and_posts_when_the_sdk_lacks_the_operation(sent):
    result = cognito_extra.admin_get_user_auth_factors(OldClient(), POOL, USERNAME)

    assert result["ConfiguredUserAuthFactors"] == ["EMAIL_OTP", "WEB_AUTHN"]
    (request,) = sent.requests
    assert request.method == "POST"
    assert request.url.rstrip("/") == ENDPOINT
    assert request.headers["X-Amz-Target"] == "AWSCognitoIdentityProviderService.AdminGetUserAuthFactors"
    assert request.headers["Content-Type"] == "application/x-amz-json-1.1"
    assert request.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "/ap-northeast-1/cognito-idp/aws4_request" in request.headers["Authorization"]
    body = request.body.decode() if isinstance(request.body, bytes) else request.body
    assert json.loads(body) == {"UserPoolId": POOL, "Username": USERNAME}


def test_errors_become_client_errors_with_the_cognito_code(sent):
    sent.reply.update(
        status=400,
        body={"__type": "com.amazonaws#UserNotFoundException", "message": "User does not exist."},
    )

    with pytest.raises(ClientError) as excinfo:
        cognito_extra.admin_get_user_auth_factors(OldClient(), POOL, USERNAME)

    error = excinfo.value.response
    assert error["Error"]["Code"] == "UserNotFoundException"
    assert error["ResponseMetadata"]["HTTPStatusCode"] == 400
    assert excinfo.value.operation_name == "AdminGetUserAuthFactors"


def test_server_errors_keep_the_status(sent):
    sent.reply.update(status=500, body={"__type": "InternalErrorException", "message": "x"})

    with pytest.raises(ClientError) as excinfo:
        cognito_extra.admin_get_user_auth_factors(OldClient(), POOL, USERNAME)

    assert excinfo.value.response["ResponseMetadata"]["HTTPStatusCode"] == 500
    assert excinfo.value.response["Error"]["Code"] == "InternalErrorException"


@pytest.mark.parametrize(
    "factors,expected",
    [
        (["PASSWORD", "WEB_AUTHN", "EMAIL_OTP"], {"password": True, "web_authn": True, "email_otp": True}),
        (["EMAIL_OTP"], {"password": False, "web_authn": False, "email_otp": True}),
        (None, {"password": False, "web_authn": False, "email_otp": False}),
    ],
)
def test_factor_flags(factors, expected):
    assert cognito_extra.factor_flags({"ConfiguredUserAuthFactors": factors}) == expected


@pytest.mark.parametrize("supported", [False, True])
def test_passkey_mfa_uses_sdk_only_when_the_parameter_is_supported(sent, supported):
    from unittest.mock import Mock

    members = {"WebAuthnMfaSettings": object()} if supported else {}
    client = OldClient()
    client.meta = SimpleNamespace(
        region_name="ap-northeast-1",
        endpoint_url=ENDPOINT,
        service_model=SimpleNamespace(
            operation_model=lambda operation: SimpleNamespace(input_shape=SimpleNamespace(members=members))
        ),
    )
    client.admin_set_user_mfa_preference = Mock(return_value={})
    cognito_extra.admin_enable_passkey_mfa(client, POOL, USERNAME)
    expected = {
        "UserPoolId": POOL,
        "Username": USERNAME,
        "SoftwareTokenMfaSettings": {"Enabled": True, "PreferredMfa": True},
        "WebAuthnMfaSettings": {"Enabled": True},
    }
    if supported:
        client.admin_set_user_mfa_preference.assert_called_once_with(**expected)
        assert sent.requests == []
    else:
        client.admin_set_user_mfa_preference.assert_not_called()
        (request,) = sent.requests
        assert request.headers["X-Amz-Target"] == "AWSCognitoIdentityProviderService.AdminSetUserMFAPreference"
        assert request.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
        assert json.loads(request.body) == expected


def test_passkey_mfa_propagates_direct_api_failures(sent):
    sent.reply.update(status=500, body={"__type": "InternalErrorException"})
    with pytest.raises(ClientError):
        cognito_extra.admin_enable_passkey_mfa(OldClient(), POOL, USERNAME)
