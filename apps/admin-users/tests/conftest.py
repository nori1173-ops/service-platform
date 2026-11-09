"""ユーザー管理のテスト共通フィクスチャ。

`functions/` を `sys.path` に足し、`admin_users.app` を Lambda と同じモジュール名で import できるようにする
（`CodeUri: functions/`、`Handler: admin_users.app.handler`）。ハンドラのモジュール名をアプリ id 由来に
しているのは、複数アプリのテストを 1 つの pytest セッションで走らせると同名パッケージが sys.modules で
衝突するため（ADD-APP-GUIDE「2. 雛形と SSM の受け渡し」）。

台帳・監査・Cognito は moto。moto の Cognito は TOTP の登録 (UserMFASettingList) と
AdminGetUserAuthFactors を持たないので、その 2 つと失敗の注入だけを薄い代理 (`CognitoProxy`) で足す。
テストからは conftest を import できないため、補助の関数と定数は `aws` フィクスチャの属性で渡す。
"""

import json
import pathlib
import sys
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

FUNCTIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

REGION = "ap-northeast-1"
APP_ID = "admin-users"
API_PREFIX = f"/api/{APP_ID}"
LEDGER_TABLE = "service-platform-core-ledger-test"
AUDIT_TABLE = "service-platform-core-audit-test"
ADMIN_GROUP = "admin"
PASSWORD = "Passw0rdTest1234"
MFA_ENABLED_AT = 1_700_000_000
AUTH_TIME = 1_800_000_000


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """台帳の書き込みの再試行の待ちを飛ばす。"""
    from service_platform import transactions

    monkeypatch.setattr(transactions, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _lambda_environment(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LEDGER_TABLE", LEDGER_TABLE)
    monkeypatch.setenv("AUDIT_TABLE", AUDIT_TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


def _create_table(name: str, indexes: tuple[str, ...]):
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


class CognitoProxy:
    """moto の Cognito に、TOTP の状態・AdminGetUserAuthFactors・呼び出しの記録・失敗の注入を足す。"""

    def __init__(self, client):
        self._client = client
        self.meta = client.meta
        self.totp: set[str] = set()
        self.factors: dict[str, list[str]] = {}
        self.failures: dict[str, Exception] = {}
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        operation = getattr(self._client, name)

        def _call(**kwargs):
            self.calls.append((name, kwargs))
            if name in self.failures:
                raise self.failures[name]
            result = operation(**kwargs)
            if name == "admin_get_user":
                result["UserMFASettingList"] = ["SOFTWARE_TOKEN_MFA"] if result["Username"] in self.totp else []
            return result

        return _call

    def admin_get_user_auth_factors(self, **kwargs):
        self.calls.append(("admin_get_user_auth_factors", kwargs))
        username = self._client.admin_get_user(**kwargs)["Username"]
        return {"Username": username, "ConfiguredUserAuthFactors": self.factors.get(username, ["EMAIL_OTP"])}

    def called(self, name: str) -> list[dict]:
        return [kwargs for operation, kwargs in self.calls if operation == name]

    def user(self, username: str) -> dict | None:
        try:
            return self._client.admin_get_user(UserPoolId=self.pool_id, Username=username)
        except ClientError:
            return None

    def groups(self, username: str) -> list[str]:
        groups = self._client.admin_list_groups_for_user(UserPoolId=self.pool_id, Username=username)["Groups"]
        return [group["GroupName"] for group in groups]


class DynamoProxy:
    """台帳と監査の TransactWriteItems に失敗を差し込む。`plan` の要素ごとに 1 回分を決める。

    - 例外: 書かずにその例外を投げる
    - `("after", 例外)`: 書いてから例外を投げる (応答を失った)
    - `None`: そのまま書く
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
        if isinstance(step, tuple) and step[0] == "after":
            self._client.transact_write_items(**kwargs)
            raise step[1]
        if isinstance(step, Exception):
            raise step
        return self._client.transact_write_items(**kwargs)


def client_error(code: str, status: int = 400, operation: str = "Operation") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation
    )


def _attributes(user: dict) -> dict:
    return {item["Name"]: item["Value"] for item in user.get("UserAttributes") or user.get("Attributes") or []}


@pytest.fixture
def aws(monkeypatch):
    from admin_users import cognito, ledger_writes

    from service_platform import audit, ledger
    from service_platform.ids import new_ulid

    with mock_aws():
        ledger_table = _create_table(LEDGER_TABLE, ("GSI1",))
        audit_table = _create_table(AUDIT_TABLE, ("GSI1", "GSI2"))
        client = boto3.client("cognito-idp", region_name=REGION)
        pool_id = client.create_user_pool(
            PoolName="service-platform-test",
            UsernameAttributes=["email"],
            AutoVerifiedAttributes=["email"],
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 12,
                    "RequireUppercase": True,
                    "RequireLowercase": True,
                    "RequireNumbers": True,
                    "RequireSymbols": False,
                }
            },
        )["UserPool"]["Id"]
        client.create_group(GroupName=ADMIN_GROUP, UserPoolId=pool_id)
        monkeypatch.setenv("USER_POOL_ID", pool_id)
        proxy = CognitoProxy(client)
        proxy.pool_id = pool_id
        ledger.reset_clients()
        audit.reset_clients()
        cognito.reset_client()
        monkeypatch.setattr(cognito, "client", lambda: proxy)
        dynamodb = DynamoProxy(boto3.client("dynamodb", region_name=REGION))
        monkeypatch.setattr(ledger_writes, "_client", lambda: dynamodb)

        ns = SimpleNamespace(ledger=ledger_table, audit=audit_table, cognito=proxy, pool_id=pool_id, dynamodb=dynamodb)

        def make_user(
            email: str,
            *,
            role: str = "user",
            kind: str | None = None,
            status: str = "active",
            admin_state: str | None = None,
            totp: bool = False,
            mfa_enabled_at: int | None = None,
            in_group: bool | None = None,
            org_name: str | None = "組織A",
            ledger_row: bool = True,
            **attributes,
        ) -> SimpleNamespace:
            created = client.admin_create_user(
                UserPoolId=pool_id,
                Username=email,
                MessageAction="SUPPRESS",
                UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
            )["User"]
            username = created["Username"]
            sub = _attributes(created)["sub"]
            client.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=PASSWORD, Permanent=True)
            if in_group if in_group is not None else role == "admin":
                client.admin_add_user_to_group(UserPoolId=pool_id, Username=username, GroupName=ADMIN_GROUP)
            if totp:
                proxy.totp.add(username)
            user_id = new_ulid()
            if ledger_row:
                item = {
                    "PK": ledger.user_pk(user_id),
                    "SK": ledger.PROFILE_SK,
                    "GSI1PK": ledger.USER_GSI1PK,
                    "GSI1SK": ledger.user_gsi1sk(email, user_id),
                    "user_id": user_id,
                    "cognito_username": username,
                    "sub": sub,
                    "email": email,
                    "status": status,
                    "kind": kind or ("employee" if role == "admin" else "customer"),
                    "role": role,
                    "created_by": "script",
                    "created_at": "2026-11-04T00:00:00Z",
                    "updated_by": "script",
                    "updated_at": "2026-11-04T00:00:00Z",
                    "version": 1,
                    **attributes,
                }
                if org_name is not None:
                    item["org_name"] = org_name
                if admin_state is not None:
                    item["admin_state"] = admin_state
                if mfa_enabled_at is not None:
                    item["mfa_enabled_at"] = mfa_enabled_at
                ledger_table.put_item(Item=item)
                ledger_table.put_item(
                    Item={"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK, "user_id": user_id, "created_at": "x"}
                )
                if admin_state == "active" and status == "active":
                    ledger_table.update_item(
                        Key={"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK},
                        UpdateExpression="ADD members :m",
                        ExpressionAttributeValues={":m": {user_id}},
                    )
            return SimpleNamespace(user_id=user_id, sub=sub, username=username, email=email, role=role)

        def make_admin(email: str, **kwargs) -> SimpleNamespace:
            kwargs.setdefault("admin_state", "active")
            kwargs.setdefault("totp", True)
            kwargs.setdefault("mfa_enabled_at", MFA_ENABLED_AT)
            return make_user(email, role="admin", org_name=None, **kwargs)

        def profile(user_id: str) -> dict | None:
            key = {"PK": ledger.user_pk(user_id), "SK": ledger.PROFILE_SK}
            return ledger_table.get_item(Key=key, ConsistentRead=True).get("Item")

        def sub_row(sub: str) -> dict | None:
            key = {"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK}
            return ledger_table.get_item(Key=key, ConsistentRead=True).get("Item")

        def admin_set() -> set[str]:
            key = {"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK}
            return set((ledger_table.get_item(Key=key, ConsistentRead=True).get("Item") or {}).get("members") or ())

        def context(user) -> object:
            """その時点の台帳の利用者 (認可を済ませた後に古くなる呼び出し元を作るため)。"""
            return ledger.resolve_user(user.sub, allow_suspended=True)

        def audit_items(action: str | None = None) -> list[dict]:
            items = sorted(audit_table.scan()["Items"], key=lambda item: item["SK"])
            return [item for item in items if action is None or item["action"] == action]

        ns.make_user = make_user
        ns.make_admin = make_admin
        ns.profile = profile
        ns.sub_row = sub_row
        ns.audit_items = audit_items
        ns.admin_set = admin_set
        ns.context = context
        ns.request = make_api_event("POST", "/", caller=SimpleNamespace(sub="x", email="x", role="admin"))
        ns.attributes = _attributes
        ns.client_error = client_error
        ns.mfa_enabled_at = MFA_ENABLED_AT
        ns.auth_time = AUTH_TIME
        yield ns
        ledger.reset_clients()
        audit.reset_clients()
        cognito.reset_client()


@pytest.fixture
def actor(aws):
    """呼び出し元。台帳と Cognito の両方で有効な admin (TOTP あり、有効化済み)。"""
    return aws.make_admin("actor@example.com")


def make_api_event(method: str, path: str, *, caller, body=None, groups=None, auth_time=AUTH_TIME) -> dict:
    full_path = f"{API_PREFIX}{path}"
    if groups is None:
        groups = [ADMIN_GROUP] if caller.role == "admin" else []
    claims = {
        "sub": caller.sub,
        "email": caller.email,
        "cognito:groups": f"[{' '.join(groups)}]",
        "token_use": "id",
        "auth_time": str(auth_time),
    }
    raw_path, _, query = full_path.partition("?")
    return {
        "version": "2.0",
        "routeKey": f"ANY {API_PREFIX}/" + "{proxy+}",
        "rawPath": raw_path,
        "rawQueryString": query,
        "headers": {"content-type": "application/json", "x-origin-verify": "test-origin-verify"},
        "requestContext": {
            "accountId": "210000000001",
            "apiId": "testapi",
            "domainName": "testapi.execute-api.ap-northeast-1.amazonaws.com",
            "http": {
                "method": method,
                "path": raw_path,
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
def call(actor):
    """ハンドラを呼び、`(ステータス, 本文)` を返す。呼び出し元の既定は `actor`。"""

    def _call(method: str, path: str, *, caller=None, **kwargs) -> tuple[int, dict | None]:
        from admin_users.app import handler

        result = handler(make_api_event(method, path, caller=caller or actor, **kwargs), None)
        return result["statusCode"], json.loads(result["body"]) if result.get("body") else None

    return _call


@pytest.fixture
def period_api(aws, call, monkeypatch):
    from admin_users import ledger_writes

    from service_platform import ledger

    clock = ["2026-11-04T01:00:00Z"]
    monkeypatch.setattr(ledger_writes, "now_iso", lambda: clock[0])

    def start(**fields):
        status, body = call("POST", "/device-periods", body={"account": "12345678", "org_name": "組織A", **fields})
        assert status == 201, body
        return body["data"]

    def assign(period, user_id, **fields):
        status, body = call(
            "POST",
            f"/device-periods/{period['device_period_id']}/assignments",
            body={"user_id": user_id, "valid_from": period["started_at"], "valid_to": None, **fields},
        )
        assert status == 201, body
        return body["data"]

    def grant(period, **fields):
        status, body = call(
            "POST",
            f"/device-periods/{period['device_period_id']}/grants",
            body={"service": "camera", "starts_at": period["started_at"], "ends_at": None, **fields},
        )
        assert status == 201, body
        return body["data"]

    def row(pk, sk):
        return aws.ledger.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")

    def login(period, user_id, assignment_id):
        aws.ledger.put_item(
            Item={
                "PK": ledger.user_pk(user_id),
                "SK": ledger.login_sk(period["account"]),
                "device_period_id": period["device_period_id"],
                "assignment_id": assignment_id,
            }
        )

    return SimpleNamespace(start=start, assign=assign, grant=grant, row=row, login=login, clock=clock)


@pytest.fixture
def period(period_api):
    return period_api.start()


@pytest.fixture
def group_api(aws, call, period_api):
    """グループと所属の API  を呼ぶ補助。時計は period_api と共有する。"""

    def create(name="会社A", parent=None, **fields):
        body = {"name": name, **fields}
        if parent is not None:
            body["parent_group_id"] = parent["group_id"]
        status, result = call("POST", "/groups", body=body)
        assert status == 201, result
        return result["data"]

    def add_member(group, user_id):
        status, result = call("POST", f"/groups/{group['group_id']}/members", body={"user_id": user_id})
        assert status == 201, result
        return result["data"]

    def remove_member(group, user_id):
        return call("POST", f"/groups/{group['group_id']}/members/{user_id}/remove")

    def detail(group):
        status, result = call("GET", f"/groups/{group['group_id']}")
        assert status == 200, result
        return result["data"]

    def assign(period, group, **fields):
        status, result = call(
            "POST",
            f"/device-periods/{period['device_period_id']}/assignments",
            body={
                "target_type": "group",
                "group_id": group["group_id"],
                "valid_from": period["started_at"],
                "valid_to": None,
                **fields,
            },
        )
        assert status == 201, result
        return result["data"]

    return SimpleNamespace(
        create=create, add_member=add_member, remove_member=remove_member, detail=detail, assign=assign
    )
