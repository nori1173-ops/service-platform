"""土台のアプリ base のスタックの静的検査（計画 4.2、判断 2、ADR-001 D9 / D12）。

base は利用者本人が呼ぶ API (停止中でも呼べる例外 1・2 を含む) を持つ。admin の権限と分けて IAM を狭く保つ:
Cognito は自プールの 3 操作、台帳は行の読み書き (GSI と Scan を持たない)、監査は PutItem だけ。
通信機ログイン  で、外部のデバイス活動台帳の Query とその表の名前の SSM の読み取り (判断 4) を足した。
権限を足すチケットは、ここを先に直す。
"""

import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

APP_ID = "base"
TEMPLATE_PATH = f"apps/{APP_ID}/template.yaml"
SAMCONFIG_PATH = REPO_ROOT / "apps" / APP_ID / "samconfig.toml"
EDGE_SNIPPET_PATH = REPO_ROOT / "apps" / APP_ID / "edge-snippet.yaml"

API_FUNCTION = "ApiFunction"
HANDLER = "base_api.app.handler"

SSM_PARAMETER_DEFAULTS = {
    "UserPoolIssuer": "/service-platform/dev/cognito/issuer",
    "UserPoolClientId": "/service-platform/dev/cognito/client-id",
    "UserPoolId": "/service-platform/dev/cognito/user-pool-id",
    "SharedLayerArn": "/service-platform/dev/shared/layer-arn",
    "LedgerTableName": "/service-platform/dev/ledger/table-name",
    "AuditTableName": "/service-platform/dev/audit/table-name",
}

COGNITO_ACTIONS = {
    "cognito-idp:AdminGetUser",
    "cognito-idp:AdminListGroupsForUser",
    "cognito-idp:AdminUserGlobalSignOut",
    "cognito-idp:AdminSetUserMFAPreference",
}
USER_POOL_ARN = {"Fn::Sub": "arn:aws:cognito-idp:${AWS::Region}:${AWS::AccountId}:userpool/${UserPoolId}"}
# ConditionCheckItem: ログイン記録を、割り当てと利用期間の ConditionCheck と同じトランザクションで書く
LEDGER_ACTIONS = {
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:ConditionCheckItem",
}
LEDGER_ARN = {"Fn::Sub": "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${LedgerTableName}"}
AUDIT_ARN = {"Fn::Sub": "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${AuditTableName}"}
DEVICE_ACTIVITY_TABLE_ARN = {
    "Fn::Sub": "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${ProjectName}-device-activity-${Env}"
}
CAMERAS_PARAMETER_ARN = {
    "Fn::Sub": "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter${DeviceActivityTableNameParameter}"
}
CAMERAS_PARAMETER_KEYS = {
    "default": "/service-platform/dev/devices/activity-table-name",
    "production": "/service-platform/prod/devices/activity-table-name",
}
DEVICE_LOGIN_ROUTE = "POST /api/base/devices/login"

REMOVED_RESOURCE_TYPES = (
    "AWS::DynamoDB::Table",
    "AWS::Serverless::StateMachine",
    "AWS::StepFunctions::StateMachine",
    "AWS::SQS::Queue",
)


@pytest.fixture
def template(load_template) -> dict:
    loaded = load_template(TEMPLATE_PATH)
    if loaded is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return loaded


@pytest.fixture
def samconfig() -> dict:
    if not SAMCONFIG_PATH.is_file():
        pytest.skip(f"{SAMCONFIG_PATH} は未作成")
    return tomllib.loads(SAMCONFIG_PATH.read_text(encoding="utf-8"))


def _properties(template: dict, logical_id: str) -> dict:
    resource = (template.get("Resources") or {}).get(logical_id)
    assert resource is not None, f"{logical_id} が無い"
    return resource.get("Properties") or {}


def _unwrap(statement: dict) -> dict:
    """`!If [条件, 文, !Ref AWS::NoValue]` の文は、条件が真のときの文として読む。"""
    branches = statement.get("Fn::If")
    return branches[1] if isinstance(branches, list) else statement


def _statements(template: dict) -> list[dict]:
    properties = _properties(template, API_FUNCTION)
    return [
        _unwrap(statement) for policy in properties.get("Policies") or [] for statement in policy.get("Statement") or []
    ]


def _statement(template: dict, sid: str) -> dict:
    matches = [s for s in _statements(template) if s.get("Sid") == sid]
    assert len(matches) == 1, f"{sid} の Statement はちょうど 1 つであること"
    return matches[0]


def _actions(statement: dict) -> set[str]:
    action = statement.get("Action") or []
    return {action} if isinstance(action, str) else set(action)


def _environment(template: dict) -> dict:
    globals_env = ((template.get("Globals") or {}).get("Function") or {}).get("Environment") or {}
    function_env = _properties(template, API_FUNCTION).get("Environment") or {}
    return {**(globals_env.get("Variables") or {}), **(function_env.get("Variables") or {})}


def test_http_api_requires_the_cognito_jwt_authorizer(template):
    auth = _properties(template, "HttpApi").get("Auth") or {}
    assert auth.get("DefaultAuthorizer") == "CognitoJwtAuthorizer"
    jwt = ((auth.get("Authorizers") or {}).get("CognitoJwtAuthorizer") or {}).get("JwtConfiguration") or {}
    assert jwt.get("issuer") == {"Ref": "UserPoolIssuer"}
    assert jwt.get("audience") == [{"Ref": "UserPoolClientId"}]


def test_ssm_parameter_defaults_match_the_registry(template):
    """SSM 型パラメータの `Default` は dev のキー名（設計 4.2 / 4.4）。"""
    parameters = template.get("Parameters") or {}
    for name, key in SSM_PARAMETER_DEFAULTS.items():
        assert parameters[name]["Type"] == "AWS::SSM::Parameter::Value<String>", name
        assert parameters[name]["Default"] == key, name


def test_environment_carries_the_pool_and_tables(template):
    environment = _environment(template)
    assert environment.get("USER_POOL_ID") == {"Ref": "UserPoolId"}
    assert environment.get("LEDGER_TABLE") == {"Ref": "LedgerTableName"}
    assert environment.get("AUDIT_TABLE") == {"Ref": "AuditTableName"}


def test_cognito_actions_are_scoped_to_the_own_pool(template):
    statement = _statement(template, "ReadSetMfaAndSignOutInOwnUserPool")
    assert _actions(statement) == COGNITO_ACTIONS
    assert statement["Resource"] == USER_POOL_ARN
    cognito_statements = [s for s in _statements(template) if any(a.startswith("cognito-idp:") for a in _actions(s))]
    assert [s.get("Sid") for s in cognito_statements] == ["ReadSetMfaAndSignOutInOwnUserPool"]


def test_ledger_access_is_row_reads_and_writes_only(template):
    """台帳の行の読み書きだけ (GSI・Scan・一括の操作は持たない。表の ARN だけで索引の ARN を含めない)。

    書くのはログイン記録・失敗回数・最新値の受付・割り当ての製造番号・自分の行 (計画 2.1)。
    """
    statement = _statement(template, "ReadAndWriteLedger")
    assert _actions(statement) == LEDGER_ACTIONS
    assert statement["Resource"] == LEDGER_ARN


def test_audit_access_is_put_item_only(template):
    """監査は追記だけ (ADR D12)。"""
    statement = _statement(template, "AppendAudit")
    assert _actions(statement) == {"dynamodb:PutItem"}
    assert statement["Resource"] == AUDIT_ARN


def test_the_ledger_index_is_queried_only_for_the_device_list(template):
    """`/me/devices` は稼働中の利用期間の指し (GSI1 の DEVICE。結果整合) を読む 。認可には使わない。"""
    statement = _statement(template, "QueryLedgerIndex")
    assert _actions(statement) == {"dynamodb:Query"}
    assert statement["Resource"] == {
        "Fn::Sub": "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${LedgerTableName}/index/GSI1"
    }


def test_no_other_dynamodb_permissions(template):
    dynamodb = [s.get("Sid") for s in _statements(template) if any(a.startswith("dynamodb:") for a in _actions(s))]
    assert sorted(dynamodb) == ["AppendAudit", "QueryDeviceActivityLedger", "QueryLedgerIndex", "ReadAndWriteLedger"]


def test_the_camera_ledger_is_queried_read_only(template):
    """最新値のガードは外部のデバイス活動台帳を読み取りだけで引く (判断 4)。表の名前は命名の規約で決まる。"""
    statement = _statement(template, "QueryDeviceActivityLedger")
    assert _actions(statement) == {"dynamodb:Query"}
    assert statement["Resource"] == DEVICE_ACTIVITY_TABLE_ARN


def test_the_camera_ledger_name_is_read_from_ssm_at_run_time(template):
    """画像基盤が無い環境でも base が立つよう、SSM の型パラメータにせず、実行時に読む。空なら不可。"""
    parameter = (template.get("Parameters") or {})["DeviceActivityTableNameParameter"]
    assert parameter["Type"] == "String"
    assert parameter["Default"] == CAMERAS_PARAMETER_KEYS["default"]
    assert template["Conditions"]["HasDeviceActivityTable"] == {
        "Fn::Not": [{"Fn::Equals": [{"Ref": "DeviceActivityTableNameParameter"}, ""]}]
    }
    assert _environment(template)["DEVICE_ACTIVITY_TABLE_PARAMETER"] == {"Ref": "DeviceActivityTableNameParameter"}
    statement = _statement(template, "ReadDeviceActivityLedgerName")
    assert _actions(statement) == {"ssm:GetParameter"}
    assert statement["Resource"] == CAMERAS_PARAMETER_ARN


def test_camera_ledger_statements_exist_only_with_the_parameter(template):
    properties = _properties(template, API_FUNCTION)
    raw = [statement for policy in properties["Policies"] for statement in policy["Statement"]]
    conditional = {statement["Fn::If"][1]["Sid"]: statement["Fn::If"] for statement in raw if "Fn::If" in statement}
    assert set(conditional) == {"QueryDeviceActivityLedger", "ReadDeviceActivityLedgerName"}
    for branches in conditional.values():
        assert branches[0] == "HasDeviceActivityTable"
        assert branches[2] == {"Ref": "AWS::NoValue"}


def test_device_backend_backend_url_is_a_plain_parameter(template):
    """接続先は秘密ではないので samconfig の String (計画 2.5)。未設定は届かない既定値で失敗させる。"""
    parameter = (template.get("Parameters") or {})["DeviceBackendApiBaseUrl"]
    assert parameter["Type"] == "String"
    assert parameter["Default"] == "https://placeholder.invalid"
    assert _environment(template)["DEVICE_BACKEND_API_BASE_URL"] == {"Ref": "DeviceBackendApiBaseUrl"}


def test_device_login_has_its_own_route_with_tighter_throttling(template):
    """総当たり対策の重ね: `/devices/login` はルート単位で rate 5 / burst 10 (ADR D9)。"""
    api = _properties(template, "HttpApi")
    assert api["DefaultRouteSettings"] == {"ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40}
    assert api["RouteSettings"] == {
        DEVICE_LOGIN_ROUTE: {"ThrottlingRateLimit": 5, "ThrottlingBurstLimit": 10},
    }
    event = _properties(template, API_FUNCTION)["Events"]["DeviceLogin"]
    assert event["Type"] == "HttpApi"
    assert event["Properties"] == {"ApiId": {"Ref": "HttpApi"}, "Path": "/api/base/devices/login", "Method": "POST"}


def test_samconfig_points_both_envs_at_the_device_backend_backend_and_the_camera_ledger(samconfig):
    """dev も本番の device-backend に向ける (検証機器が本番にだけある。計画 2.5)。"""
    urls = set()
    for config_env, key in CAMERAS_PARAMETER_KEYS.items():
        overrides = samconfig[config_env]["deploy"]["parameters"]["parameter_overrides"]
        values = dict(item.split("=", 1) for item in overrides.split())
        assert values["DeviceBackendApiBaseUrl"].startswith("https://")
        assert "placeholder" not in values["DeviceBackendApiBaseUrl"]
        urls.add(values["DeviceBackendApiBaseUrl"])
        assert values["DeviceActivityTableNameParameter"] == key
    assert len(urls) == 1


def test_origin_verify_secret_is_the_only_ssm_read(template):
    statement = _statement(template, "ReadOriginVerifyParameter")
    assert _actions(statement) == {"ssm:GetParameter", "ssm:GetParameters"}
    expected = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter"
    assert statement["Resource"] == {"Fn::Sub": f"{expected}/${{ProjectName}}/${{Env}}/edge/origin-verify-secret"}


def test_api_function_name_and_handler_follow_the_convention(template):
    properties = _properties(template, API_FUNCTION)
    assert properties["FunctionName"] == {"Fn::Sub": "${ProjectName}-base-api-${Env}"}
    assert properties["Handler"] == HANDLER
    assert properties["CodeUri"] == "functions/"
    event = properties["Events"]["Api"]["Properties"]
    assert event["Path"] == f"/api/{APP_ID}/{{proxy+}}"
    assert event["Method"] == "ANY"


def test_api_domain_parameter_is_written_for_edge(template):
    properties = _properties(template, "ApiDomainParameter")
    assert properties["Name"] == {"Fn::Sub": f"/${{ProjectName}}/${{Env}}/apps/{APP_ID}/api-domain"}
    assert properties["Type"] == "String"


def test_stack_has_no_table_queue_or_state_machine(template):
    """base の永続領域は core の台帳と監査だけ。"""
    types = {resource.get("Type") for resource in (template.get("Resources") or {}).values()}
    assert not types & set(REMOVED_RESOURCE_TYPES), types


def test_samconfig_tags_carry_the_app_id(samconfig):
    for config_env, env in (("default", "dev"), ("production", "prod")):
        tags = samconfig[config_env]["deploy"]["parameters"]["tags"]
        assert f"App={APP_ID}" in tags
        assert f"Env={env}" in tags


def test_edge_snippet_is_kept_for_transcription(template):
    assert EDGE_SNIPPET_PATH.is_file(), f"{EDGE_SNIPPET_PATH} が無い"
    body = "\n".join(
        line for line in EDGE_SNIPPET_PATH.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    assert f"PathPattern: /api/{APP_ID}/*" in body
    assert "FunctionAssociations" not in body


def test_warmer_pings_only_the_base_api_every_five_minutes(template, load_template):
    import json

    schedule = _properties(template, "ApiWarmerSchedule")
    assert schedule["ScheduleExpression"] == "rate(5 minutes)"
    assert schedule["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert schedule["State"] == "ENABLED"
    assert schedule["GroupName"] == {"Ref": "ApiWarmerScheduleGroup"}
    target = schedule["Target"]
    assert target["Arn"] == {"Fn::GetAtt": "ApiFunction.Arn"}
    assert target["RoleArn"] == {"Fn::GetAtt": "ApiWarmerRole.Arn"}
    assert json.loads(target["Input"]) == {"warmer": True, "slot": 0}
    admin = load_template("apps/admin-users/template.yaml")
    assert not any(r["Type"] == "AWS::Scheduler::Schedule" for r in admin["Resources"].values())


def test_warmer_role_is_scoped_to_its_group_and_api(template):
    role = _properties(template, "ApiWarmerRole")
    assert role["AssumeRolePolicyDocument"]["Statement"] == [
        {
            "Effect": "Allow",
            "Principal": {"Service": "scheduler.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {
                    "aws:SourceAccount": {"Ref": "AWS::AccountId"},
                    "aws:SourceArn": {"Fn::GetAtt": "ApiWarmerScheduleGroup.Arn"},
                }
            },
        }
    ]
    statements = [s for p in role["Policies"] for s in p["PolicyDocument"]["Statement"]]
    assert statements == [
        {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": {"Fn::GetAtt": "ApiFunction.Arn"}}
    ]
