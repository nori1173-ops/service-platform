"""`scripts/admin_ops.py` のテスト用フィクスチャ (moto の Cognito・DynamoDB・SSM・STS)。"""

from types import SimpleNamespace

import admin_ops
import boto3
import pytest
from moto import mock_aws

from service_platform import audit, ledger

REGION = "ap-northeast-1"
LEDGER_TABLE = "service-platform-core-ledger-dev"
AUDIT_TABLE = "service-platform-core-audit-dev"
CLIENT_ID = "exampleclientid"


def _create_table(client, name: str, gsis: list[str]) -> None:
    attributes = ["PK", "SK"] + [f"{gsi}{suffix}" for gsi in gsis for suffix in ("PK", "SK")]
    client.create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attributes],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": gsi,
                "KeySchema": [
                    {"AttributeName": f"{gsi}PK", "KeyType": "HASH"},
                    {"AttributeName": f"{gsi}SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for gsi in gsis
        ],
    )


class FaultProxy:
    """moto のクライアントに、呼び出しの記録と失敗の注入を足す。

    `failures[name]` は毎回、呼ぶ前に投げる。`inject(name, exc, after=True)` は 1 回だけ、
    AWS 側で処理した後に投げる (応答を失った状態。結果不明の失敗の再現)。`skip` 回は素通しする。
    同じ API に何度も `inject` すると、積んだ順に 1 回ずつ使う。
    """

    def __init__(self, client):
        self._client = client
        self.calls: list[tuple[str, dict]] = []
        self.failures: dict[str, Exception] = {}
        self._injections: dict[str, list] = {}

    def inject(self, name: str, exc: Exception, *, after: bool = False, skip: int = 0) -> None:
        self._injections.setdefault(name, []).append([exc, after, skip])

    def _take_injection(self, name: str):
        queue = self._injections.get(name)
        if not queue:
            return None
        if queue[0][2] > 0:
            queue[0][2] -= 1
            return None
        return queue.pop(0)

    def _result(self, name: str, result):
        return result

    def __getattr__(self, name):
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def call(**kwargs):
            self.calls.append((name, kwargs))
            if name in self.failures:
                raise self.failures[name]
            injection = self._take_injection(name)
            if injection is not None and not injection[1]:
                raise injection[0]
            result = target(**kwargs)
            if injection is not None:
                raise injection[0]
            return self._result(name, result)

        return call

    def called(self, name: str) -> list[dict]:
        return [kwargs for called_name, kwargs in self.calls if called_name == name]


class CognitoProxy(FaultProxy):
    """TOTP の設定を上書きできる Cognito (moto は TOTP の登録にトークンが要るため)。"""

    def __init__(self, client):
        super().__init__(client)
        self.totp_users: set[str] = set()

    def _result(self, name: str, result):
        if name == "admin_get_user" and result.get("Username") in self.totp_users:
            return {**result, "UserMFASettingList": ["SOFTWARE_TOKEN_MFA"], "PreferredMfaSetting": "SOFTWARE_TOKEN_MFA"}
        return result


@pytest.fixture(autouse=True)
def retry_sleeps(monkeypatch) -> list[float]:
    """再試行の待ち時間を実際には待たずに記録する。"""
    sleeps: list[float] = []
    monkeypatch.setattr(admin_ops, "retry_sleep", sleeps.append)
    return sleeps


@pytest.fixture
def aws(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("MOTO_ACCOUNT_ID", "210000000001")
    # admin_ops は SSM から読んだ表名を環境変数に置く。テストの後に戻るよう先に setenv しておく
    monkeypatch.setenv("LEDGER_TABLE", LEDGER_TABLE)
    monkeypatch.setenv("AUDIT_TABLE", AUDIT_TABLE)
    monkeypatch.delenv(admin_ops.ACCESS_TOKEN_ENV, raising=False)
    with mock_aws():
        cognito = boto3.client("cognito-idp", region_name=REGION)
        pool_id = cognito.create_user_pool(
            PoolName="service-platform-dev",
            UsernameAttributes=["email"],
            AutoVerifiedAttributes=["email"],
            # core の PasswordPolicy と同じ (moto の既定は記号を求める)
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
        cognito.create_group(GroupName="admin", UserPoolId=pool_id)
        dynamodb = boto3.client("dynamodb", region_name=REGION)
        _create_table(dynamodb, LEDGER_TABLE, ["GSI1"])
        _create_table(dynamodb, AUDIT_TABLE, ["GSI1", "GSI2"])
        ssm = boto3.client("ssm", region_name=REGION)
        for env in ("dev", "prod"):
            values = {
                "cognito/user-pool-id": pool_id,
                "cognito/client-id": CLIENT_ID,
                "ledger/table-name": LEDGER_TABLE,
                "audit/table-name": AUDIT_TABLE,
            }
            for key, value in values.items():
                ssm.put_parameter(Name=f"/service-platform/{env}/{key}", Value=value, Type="String")
        proxy = CognitoProxy(cognito)
        dynamodb_proxy = FaultProxy(dynamodb)
        clients = admin_ops.Clients(
            cognito=proxy,
            dynamodb=dynamodb_proxy,
            ssm=ssm,
            sts=boto3.client("sts", region_name=REGION),
            wafv2=boto3.client("wafv2", region_name=REGION),
        )
        resource = boto3.resource("dynamodb", region_name=REGION)
        ledger.reset_clients()
        audit.reset_clients()
        yield SimpleNamespace(
            clients=clients,
            cognito=proxy,
            dynamodb=dynamodb_proxy,
            raw_cognito=cognito,
            pool_id=pool_id,
            ledger=resource.Table(LEDGER_TABLE),
            audit=resource.Table(AUDIT_TABLE),
            output_dir=tmp_path / "secrets",
        )
    ledger.reset_clients()
    audit.reset_clients()
