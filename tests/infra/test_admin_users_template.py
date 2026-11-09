"""admin-users のアプリスタックの静的検査（設計 4.2 / 6.3 / 12.2 / 14.1、計画 4.2）。

このアプリは Cognito User Pool を直接書き換える唯一のアプリで、権限を広げると
User Pool ごと壊せてしまう。**Cognito の 14 操作と Resource の 1 本**、台帳の読み書きと監査の追記
(PutItem だけ)、JWT オーソライザが既定であること、User Pool ID と表の名前の受け口（SSM のキー名と
環境変数）を機械検査する。雛形から削ったリソース（DynamoDB / Scheduler / ステートマシン）が
戻っていないことも見る (台帳と監査は core のテーブルを使う)。

タグ規約（`Project` + `Name`）とログ保持期間は `test_template_tags.py` が
`apps/*/template.yaml` を自動で拾うため、ここでは重複させない。
"""

import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

APP_ID = "admin-users"
TEMPLATE_PATH = f"apps/{APP_ID}/template.yaml"
SAMCONFIG_PATH = REPO_ROOT / "apps" / APP_ID / "samconfig.toml"
EDGE_SNIPPET_PATH = REPO_ROOT / "apps" / APP_ID / "edge-snippet.yaml"

API_FUNCTION = "ApiFunction"
HANDLER = "admin_users.app.handler"

SSM_PARAMETER_DEFAULTS = {
    "UserPoolIssuer": "/service-platform/dev/cognito/issuer",
    "UserPoolClientId": "/service-platform/dev/cognito/client-id",
    "UserPoolId": "/service-platform/dev/cognito/user-pool-id",
    "SharedLayerArn": "/service-platform/dev/shared/layer-arn",
    "LedgerTableName": "/service-platform/dev/ledger/table-name",
    "AuditTableName": "/service-platform/dev/audit/table-name",
}

# 利用者の管理 (計画 2.6 の users 系) に必要な最小集合。増減したらここと設計書を直す。
# パスワードのリセット (AdminResetUserPassword) はお客様がパスワードを持たないので外した。
COGNITO_ACTIONS = {
    "cognito-idp:ListUsers",
    "cognito-idp:ListUsersInGroup",
    "cognito-idp:AdminGetUser",
    "cognito-idp:AdminGetUserAuthFactors",
    "cognito-idp:AdminListGroupsForUser",
    "cognito-idp:AdminAddUserToGroup",
    "cognito-idp:AdminRemoveUserFromGroup",
    "cognito-idp:AdminCreateUser",
    "cognito-idp:AdminUpdateUserAttributes",
    "cognito-idp:AdminSetUserPassword",
    "cognito-idp:AdminEnableUser",
    "cognito-idp:AdminDisableUser",
    "cognito-idp:AdminUserGlobalSignOut",
    "cognito-idp:AdminDeleteUser",
}
USER_POOL_ARN = {"Fn::Sub": "arn:aws:cognito-idp:${AWS::Region}:${AWS::AccountId}:userpool/${UserPoolId}"}
# ConditionCheckItem は呼び出し元がまだ有効な admin であることを書き込みと同じトランザクションで確かめるため
LEDGER_ACTIONS = {
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:ConditionCheckItem",
}
LEDGER_ARN = "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${LedgerTableName}"
AUDIT_ARN = {"Fn::Sub": "arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${AuditTableName}"}

# 雛形から削ったもの。復活させるならアプリ設計書を先に直す。
REMOVED_RESOURCE_TYPES = (
    "AWS::DynamoDB::Table",
    "AWS::Serverless::StateMachine",
    "AWS::StepFunctions::StateMachine",
    "AWS::SQS::Queue",
    "AWS::Scheduler::Schedule",
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


def _statements(properties: dict) -> list[dict]:
    return [statement for policy in properties.get("Policies") or [] for statement in policy.get("Statement") or []]


def _statement(template: dict, sid: str) -> dict:
    matches = [s for s in _statements(_properties(template, API_FUNCTION)) if s.get("Sid") == sid]
    assert len(matches) == 1, f"{sid} の Statement はちょうど 1 つであること"
    return matches[0]


def _environment(template: dict) -> dict:
    globals_env = ((template.get("Globals") or {}).get("Function") or {}).get("Environment") or {}
    function_env = _properties(template, API_FUNCTION).get("Environment") or {}
    return {**(globals_env.get("Variables") or {}), **(function_env.get("Variables") or {})}


def test_http_api_requires_the_cognito_jwt_authorizer(template):
    """未認証を API Gateway の手前で落とす。"""
    auth = _properties(template, "HttpApi").get("Auth") or {}
    assert auth.get("DefaultAuthorizer") == "CognitoJwtAuthorizer"
    authorizer = (auth.get("Authorizers") or {}).get("CognitoJwtAuthorizer") or {}
    assert authorizer.get("IdentitySource") == "$request.header.Authorization"
    jwt = authorizer.get("JwtConfiguration") or {}
    assert jwt.get("issuer") == {"Ref": "UserPoolIssuer"}
    assert jwt.get("audience") == [{"Ref": "UserPoolClientId"}]


def test_ssm_parameter_defaults_match_the_registry(template):
    """SSM 型パラメータの `Default` は dev のキー名。"""
    parameters = template.get("Parameters") or {}
    for name, key in SSM_PARAMETER_DEFAULTS.items():
        assert parameters[name]["Type"] == "AWS::SSM::Parameter::Value<String>", name
        assert parameters[name]["Default"] == key, name


def test_user_pool_id_and_tables_are_passed_to_the_function(template):
    """User Pool ID と台帳・監査の表の名前は SSM から解決して環境変数へ渡す。"""
    environment = _environment(template)
    assert environment.get("USER_POOL_ID") == {"Ref": "UserPoolId"}
    assert environment.get("LEDGER_TABLE") == {"Ref": "LedgerTableName"}
    assert environment.get("AUDIT_TABLE") == {"Ref": "AuditTableName"}


def test_cognito_actions_are_exactly_the_ones_needed(template):
    """権限を広げると User Pool ごと壊せる。増やすときは設計書を先に直す。"""
    statement = _statement(template, "ManageUsersInOwnUserPool")
    assert set(statement["Action"]) == COGNITO_ACTIONS
    assert statement["Effect"] == "Allow"


def test_cognito_resource_is_a_single_user_pool_without_wildcards(template):
    """自環境の User Pool 1 つに限定する（`*` だと他環境の User Pool も操作できる）。"""
    resource = _statement(template, "ManageUsersInOwnUserPool")["Resource"]
    assert resource == USER_POOL_ARN
    assert "*" not in resource["Fn::Sub"]


def test_no_other_cognito_permissions_are_granted(template):
    """cognito-idp の許可はこの 1 文だけ（別の文から `*` が忍び込まないこと）。"""
    statements = [
        s
        for s in _statements(_properties(template, API_FUNCTION))
        if any(str(action).startswith("cognito-idp:") for action in (s.get("Action") or []))
    ]
    assert [s.get("Sid") for s in statements] == ["ManageUsersInOwnUserPool"]


def test_origin_verify_secret_is_the_only_ssm_read(template):
    """実行時に読む SSM は origin-verify の秘密だけ。"""
    statement = _statement(template, "ReadOriginVerifyParameter")
    assert set(statement["Action"]) == {"ssm:GetParameter", "ssm:GetParameters"}
    expected = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter"
    assert statement["Resource"] == {"Fn::Sub": f"{expected}/${{ProjectName}}/${{Env}}/edge/origin-verify-secret"}


def test_ledger_access_is_read_and_conditional_write(template):
    """台帳は条件付きの読み書きと LOGIN・CURRENT の削除、一覧用 GSI1 の Query (Scan は持たない)。"""
    statement = _statement(template, "ReadAndWriteLedger")
    assert set(statement["Action"]) == LEDGER_ACTIONS
    assert statement["Resource"] == [{"Fn::Sub": LEDGER_ARN}, {"Fn::Sub": f"{LEDGER_ARN}/index/GSI1"}]


def test_audit_access_is_put_item_only(template):
    """監査は追記だけ (ADR D12)。"""
    statement = _statement(template, "AppendAudit")
    assert statement["Action"] == ["dynamodb:PutItem"]
    assert statement["Resource"] == AUDIT_ARN


def test_no_other_dynamodb_permissions(template):
    statements = [
        s.get("Sid")
        for s in _statements(_properties(template, API_FUNCTION))
        if any(str(action).startswith("dynamodb:") for action in (s.get("Action") or []))
    ]
    assert sorted(statements) == ["AppendAudit", "ReadAndWriteLedger"]


def test_api_function_name_and_handler_follow_the_convention(template):
    """関数名は。ハンドラは `functions/` 直下の名前空間。"""
    properties = _properties(template, API_FUNCTION)
    assert properties["FunctionName"] == {"Fn::Sub": "${ProjectName}-admin-users-api-${Env}"}
    assert properties["Handler"] == HANDLER
    assert properties["CodeUri"] == "functions/"
    event = properties["Events"]["Api"]["Properties"]
    assert event["Path"] == f"/api/{APP_ID}/{{proxy+}}"
    assert event["Method"] == "ANY"


def test_api_domain_parameter_is_written_for_edge(template):
    """edge が origin の DomainName として読む取り決めのキー。"""
    properties = _properties(template, "ApiDomainParameter")
    assert properties["Name"] == {"Fn::Sub": f"/${{ProjectName}}/${{Env}}/apps/{APP_ID}/api-domain"}
    assert properties["Type"] == "String"


def test_stack_has_no_table_queue_or_state_machine(template):
    """このアプリは自前の永続領域を持たない（台帳と監査は core のテーブル）。"""
    types = {resource.get("Type") for resource in (template.get("Resources") or {}).values()}
    assert not types & set(REMOVED_RESOURCE_TYPES), types


def test_samconfig_tags_carry_the_app_id(samconfig):
    """コスト配分タグ。"""
    for config_env, env in (("default", "dev"), ("production", "prod")):
        tags = samconfig[config_env]["deploy"]["parameters"]["tags"]
        assert f"App={APP_ID}" in tags
        assert f"Env={env}" in tags


def test_edge_snippet_is_kept_for_transcription(template):
    """edge へ転記した断片を残す（ずれの検査は test_edge_template.py が行う）。"""
    assert EDGE_SNIPPET_PATH.is_file(), f"{EDGE_SNIPPET_PATH} が無い"
    body = "\n".join(
        line for line in EDGE_SNIPPET_PATH.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    assert f"PathPattern: /api/{APP_ID}/*" in body
    assert "FunctionAssociations" not in body
