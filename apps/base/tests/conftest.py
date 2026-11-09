"""土台 (base) のテスト用フィクスチャ。

`functions/` を `sys.path` に足し、`base_api.app` を Lambda と同じモジュール名で import できるようにする
（`CodeUri: functions/`、`Handler: base_api.app.handler`）。台帳と監査は moto、Cognito は薄い偽物で包む
（moto は TOTP の登録で UserMFASettingList を埋めず、パスワード無しの招待も持たないため）。
"""

import io
import json
import pathlib
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib import error as urllib_error
from urllib import parse as urllib_parse

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

FUNCTIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "functions"
TESTS_DIR = pathlib.Path(__file__).resolve().parent
for _path in (FUNCTIONS_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from device_data import NOW  # noqa: E402 (sys.path に tests を足した後に読む)

REGION = "ap-northeast-1"
APP_ID = "base"
API_PREFIX = f"/api/{APP_ID}"
LEDGER_TABLE = "service-platform-core-ledger-test"
AUDIT_TABLE = "service-platform-core-audit-test"
USER_POOL_ID = "ap-northeast-1_EXMPLpool1"
DEVICE_ACTIVITY_TABLE = "service-platform-device-activity-test"
DEVICE_ACTIVITY_TABLE_PARAMETER = "/service-platform/test/devices/activity-table-name"
DEVICE_BACKEND_BASE_URL = "https://device_backend.example.test"

USER_ID = "01JBZ0000000000000000USER1"
SUB = "11111111-2222-3333-4444-210000000001"
OLD_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
USERNAME = "cognito-user-1"
EMAIL = "someone@example.com"
IDS = SimpleNamespace(user_id=USER_ID, sub=SUB, old_sub=OLD_SUB, username=USERNAME, email=EMAIL)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    from service_platform import transactions

    monkeypatch.setattr(transactions, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _lambda_environment(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("LEDGER_TABLE", LEDGER_TABLE)
    monkeypatch.setenv("AUDIT_TABLE", AUDIT_TABLE)
    monkeypatch.setenv("DEVICE_BACKEND_API_BASE_URL", DEVICE_BACKEND_BASE_URL)
    monkeypatch.setenv("DEVICE_ACTIVITY_TABLE_PARAMETER", DEVICE_ACTIVITY_TABLE_PARAMETER)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


def _create_table(name: str, indexes: tuple[str, ...]) -> object:
    names = ["PK", "SK"] + [f"{index}{part}" for index in indexes for part in ("PK", "SK")]
    return boto3.resource("dynamodb", region_name=REGION).create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in names],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": index,
                "KeySchema": [
                    {"AttributeName": f"{index}PK", "KeyType": "HASH"},
                    {"AttributeName": f"{index}SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for index in indexes
        ],
    )


class DynamoPlan:
    """TransactWriteItems に失敗を差し込む (`plan` の要素ごとに 1 回。例外なら書かずに投げる)。

    要素が呼び出せるものなら、呼んでから (ほかの要求の書き込みを挟んでから) 書く。
    """

    def __init__(self, client):
        self._client = client
        self.plan: list = []
        self.calls: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._client, name)

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        step = self.plan.pop(0) if self.plan else None
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step()
        return self._client.transact_write_items(**kwargs)


class FakeCognito:
    """base が使う Cognito の操作だけを持つ偽物。呼び出しを記録する。"""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.on_sign_out = None
        self.sign_out_error = None

    def add(self, username=USERNAME, *, sub=SUB, enabled=True, totp=True, groups=("admin",)):
        self.users[username] = {"sub": sub, "enabled": enabled, "totp": totp, "groups": list(groups)}

    def _user(self, operation: str, username: str) -> dict:
        if username not in self.users:
            raise ClientError({"Error": {"Code": "UserNotFoundException", "Message": "x"}}, operation)
        return self.users[username]

    def admin_get_user(self, **kwargs):
        self.calls.append(("admin_get_user", kwargs))
        user = self._user("AdminGetUser", kwargs["Username"])
        return {
            "Username": kwargs["Username"],
            "Enabled": user["enabled"],
            "UserStatus": "CONFIRMED",
            "UserMFASettingList": ["SOFTWARE_TOKEN_MFA"] if user["totp"] else [],
            "UserAttributes": [{"Name": "sub", "Value": user["sub"]}],
        }

    def admin_list_groups_for_user(self, **kwargs):
        self.calls.append(("admin_list_groups_for_user", kwargs))
        user = self._user("AdminListGroupsForUser", kwargs["Username"])
        return {"Groups": [{"GroupName": name} for name in user["groups"]]}

    def admin_set_user_mfa_preference(self, **kwargs):
        self.calls.append(("admin_set_user_mfa_preference", kwargs))
        return {}

    def admin_user_global_sign_out(self, **kwargs):
        self.calls.append(("admin_user_global_sign_out", kwargs))
        self._user("AdminUserGlobalSignOut", kwargs["Username"])
        if self.sign_out_error:
            raise self.sign_out_error
        if self.on_sign_out:
            self.on_sign_out()
        return {}

    def called(self, operation: str) -> list[dict]:
        return [kwargs for name, kwargs in self.calls if name == operation]


@pytest.fixture
def aws(monkeypatch):
    """moto の台帳・監査と Cognito の偽物を張り、base のクライアントのキャッシュを捨てる。"""
    from base_api import clients

    from service_platform import audit, ledger

    with mock_aws():
        ledger_table = _create_table(LEDGER_TABLE, ("GSI1",))
        audit_table = _create_table(AUDIT_TABLE, ("GSI1", "GSI2"))
        activity_table = _create_activity_table()
        boto3.client("ssm", region_name=REGION).put_parameter(
            Name=DEVICE_ACTIVITY_TABLE_PARAMETER, Type="String", Value=DEVICE_ACTIVITY_TABLE
        )
        cognito = FakeCognito()
        ledger.reset_clients()
        audit.reset_clients()
        clients.reset()
        monkeypatch.setattr(clients, "cognito", lambda: cognito)
        dynamodb = DynamoPlan(boto3.client("dynamodb", region_name=REGION))
        monkeypatch.setattr(clients, "dynamodb", lambda: dynamodb)
        ns = SimpleNamespace(
            ledger=ledger_table, audit=audit_table, cameras=activity_table, cognito=cognito, ids=IDS, dynamodb=dynamodb
        )
        ns.put_user = lambda **kwargs: put_user(ns, **kwargs)
        ns.put_admin = lambda **kwargs: put_admin(ns, **kwargs)
        ns.profile = lambda: profile(ns)
        ns.audit_items = lambda: audit_items(ns)
        yield ns
        ledger.reset_clients()
        audit.reset_clients()
        clients.reset()


def _create_activity_table() -> object:
    """外部のデバイス活動台帳 (camera-images の CamerasTable と同じキー)。"""
    return boto3.resource("dynamodb", region_name=REGION).create_table(
        TableName=DEVICE_ACTIVITY_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "account", "AttributeType": "S"},
            {"AttributeName": "address", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "account", "KeyType": "HASH"},
            {"AttributeName": "address", "KeyType": "RANGE"},
        ],
    )


def put_user(aws, *, sub=SUB, status="active", revoked_sub=None, **attributes) -> None:
    """台帳に利用者の行と sub の引き当てを書く。`revoked_sub` は無効の印の付いた古い sub。"""
    from service_platform import ledger

    item = {
        "PK": ledger.user_pk(USER_ID),
        "SK": ledger.PROFILE_SK,
        "GSI1PK": ledger.USER_GSI1PK,
        "GSI1SK": ledger.user_gsi1sk(EMAIL, USER_ID),
        "user_id": USER_ID,
        "cognito_username": USERNAME,
        "sub": sub,
        "email": EMAIL,
        "status": status,
        "kind": "customer",
        "role": "user",
        "org_name": "組織A",
        "contact": "連絡先の控え",
        "note": "メモ",
        "version": 1,
        **attributes,
    }
    aws.ledger.put_item(Item=item)
    aws.ledger.put_item(Item={"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK, "user_id": USER_ID})
    if revoked_sub:
        aws.ledger.put_item(
            Item={
                "PK": ledger.sub_pk(revoked_sub),
                "SK": ledger.SUB_SK,
                "user_id": USER_ID,
                "revoked_at": "2026-11-04T00:00:00Z",
            }
        )


def put_admin(aws, *, admin_state="pending", **attributes) -> None:
    put_user(aws, kind="employee", role="admin", admin_state=admin_state, org_name=None, **attributes)


def profile(aws) -> dict:
    from service_platform import ledger

    return aws.ledger.get_item(Key={"PK": ledger.user_pk(USER_ID), "SK": ledger.PROFILE_SK})["Item"]


def audit_items(aws) -> list[dict]:
    return aws.audit.scan()["Items"]


def make_event(method: str, path: str, *, sub=SUB, groups=(), body=None, auth_time=1_800_000_000) -> dict:
    full_path = f"{API_PREFIX}{path}"
    claims = {
        "sub": sub,
        "email": EMAIL,
        "cognito:groups": f"[{' '.join(groups)}]",
        "token_use": "id",
        "auth_time": str(auth_time),
    }
    return {
        "version": "2.0",
        "routeKey": f"ANY {API_PREFIX}/" + "{proxy+}",
        "rawPath": full_path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json", "x-origin-verify": "test-secret", "user-agent": "pytest"},
        "requestContext": {
            "accountId": "210000000001",
            "apiId": "testapi",
            "domainName": "testapi.execute-api.ap-northeast-1.amazonaws.com",
            "http": {
                "method": method,
                "path": full_path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "pytest",
            },
            "requestId": "test-request-id",
            "stage": "$default",
            "authorizer": {"jwt": {"claims": claims}},
        },
        "body": json.dumps(body, ensure_ascii=False) if body is not None else None,
        "isBase64Encoded": False,
    }


@pytest.fixture
def call():
    """ハンドラを呼び、`(ステータス, 本文)` を返す。"""

    def _call(method: str, path: str, **kwargs) -> tuple[int, dict | None]:
        from base_api.app import handler

        result = handler(make_event(method, path, **kwargs), None)
        return result["statusCode"], json.loads(result["body"]) if result.get("body") else None

    return _call


@pytest.fixture
def event():
    """イベントを組み立てる関数 (ヘッダを書き換えるテスト用)。"""
    return make_event


# =============================================================================
# 通信機ログイン : 時計と device-backend の偽物。値と台帳の行は device_data.py
# =============================================================================


class Clock:
    """`base_api.clock.now` の差し替え。`advance` で時刻を進める。"""

    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)

    @property
    def epoch(self) -> int:
        return int(self.at.timestamp())


@pytest.fixture
def clock(monkeypatch):
    from base_api import clock as clock_module

    fake = Clock()
    monkeypatch.setattr(clock_module, "now", fake.now)
    return fake


class _FakeResponse:
    def __init__(self, status, raw):
        self.status = status
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDeviceBackend:
    """device_backend_client の `_opener.open` を差し替え、(method, path) ごとの応答を返して呼び出しを残す。"""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def on(self, method, path, status, body=None):
        self.routes[(method, path)] = (status, body if body is not None else {})

    def urlopen(self, http_request, timeout=None):
        parsed = urllib_parse.urlsplit(http_request.full_url)
        method = http_request.get_method()
        self.calls.append(
            {
                "method": method,
                "url": http_request.full_url,
                "path": parsed.path,
                "query": dict(urllib_parse.parse_qsl(parsed.query)),
                "headers": {key.lower(): value for key, value in http_request.header_items()},
                "timeout": timeout,
            }
        )
        route = self.routes.get((method, parsed.path))
        if route is None:
            raise urllib_error.URLError("no route")
        status, body = route
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if status >= 400:
            raise urllib_error.HTTPError(http_request.full_url, status, "error", {}, io.BytesIO(raw))
        return _FakeResponse(status, raw)


@pytest.fixture
def device_backend(monkeypatch):
    from base_api import device_backend_client

    fake = FakeDeviceBackend()
    monkeypatch.setattr(device_backend_client._opener, "open", fake.urlopen)
    return fake
