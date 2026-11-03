"""各スタックの samconfig の「値」を守る。

テンプレート側の検査（`test_*_template.py`）は形を見るが、`samconfig.toml` の値は誰も見ていない。
1 文字直し忘れると prod スタックが dev のレイヤ・ドメイン・キューを掴み、しかもデプロイは成功するため
気づけない。ここでは全スタック分の `[default]` / `[production]` を突き合わせる。
"""

import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

PROD_CONFIG_ENV = "production"
CONFIG_ENV_BY_ENV = {"dev": "default", "prod": PROD_CONFIG_ENV}

SAMCONFIGS = {
    "admin-users": "apps/admin-users/samconfig.toml",
    "shared": "shared/samconfig.toml",
    "core": "infra/core/samconfig.toml",
    "edge": "infra/edge/samconfig.toml",
}
STACK_NAMES = {
    "admin-users": "service-platform-app-admin-users",
    "shared": "service-platform-shared",
    "core": "service-platform-core",
    "edge": "service-platform-edge",
}

SITE_DOMAIN = {"dev": "service-platform-dev.example-cloud.com", "prod": "service-platform.example-cloud.com"}
REGION = "ap-northeast-1"

# 環境ごとに値が変わるパラメータ
EXPECTED_OVERRIDES = {
    "admin-users": {
        "dev": {
            "Env": "dev",
            "LogLevel": "DEBUG",
            "UserPoolId": "/service-platform/dev/cognito/user-pool-id",
        },
        "prod": {
            "Env": "prod",
            "LogLevel": "INFO",
            "UserPoolId": "/service-platform/prod/cognito/user-pool-id",
        },
    },
    "shared": {
        "dev": {"Env": "dev", "SlackMode": "bot"},
        "prod": {"Env": "prod", "SlackMode": "bot"},
    },
    "core": {
        "dev": {
            "Env": "dev",
            "LogLevel": "DEBUG",
            "SharedLayerArn": "/service-platform/dev/shared/layer-arn",
            "MailDomain": SITE_DOMAIN["dev"],
            "CognitoEmailSendingAccount": "DEVELOPER",
        },
        "prod": {
            "Env": "prod",
            "LogLevel": "INFO",
            "SharedLayerArn": "/service-platform/prod/shared/layer-arn",
            "MailDomain": SITE_DOMAIN["prod"],
            # 初回だけ SES Identity が未検証なので --parameter-overrides で
            # COGNITO_DEFAULT に上書きして立てる
            "CognitoEmailSendingAccount": "DEVELOPER",
        },
    },
    "edge": {
        "dev": {"Env": "dev", "SiteDomain": SITE_DOMAIN["dev"]},
        "prod": {"Env": "prod", "SiteDomain": SITE_DOMAIN["prod"]},
    },
}


def _load(stack: str) -> dict:
    path = REPO_ROOT / SAMCONFIGS[stack]
    if not path.is_file():
        pytest.skip(f"{path} は未作成")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _deploy_parameters(stack: str, env: str) -> dict:
    return _load(stack)[CONFIG_ENV_BY_ENV[env]]["deploy"]["parameters"]


def _overrides(stack: str, env: str) -> dict[str, str]:
    raw = _deploy_parameters(stack, env).get("parameter_overrides", "")
    return dict(pair.split("=", 1) for pair in raw.split() if "=" in pair)


STACKS = sorted(SAMCONFIGS)
CASES = [(stack, env) for stack in STACKS for env in ("dev", "prod")]


@pytest.mark.parametrize("stack", STACKS)
def test_sections_are_default_and_production(stack):
    """セクション名を全スタックで揃える（片方だけ `prod` だと一括デプロイが落ちる）。"""
    assert set(_load(stack)) - {"version"} == {"default", PROD_CONFIG_ENV}, stack


@pytest.mark.parametrize("stack,env", CASES)
def test_stack_name_region_and_tags(stack, env):
    """スタック名・S3 プレフィックス・コスト配分タグが環境と一致する。"""
    parameters = _deploy_parameters(stack, env)
    assert parameters["stack_name"] == f"{STACK_NAMES[stack]}-{env}"
    assert parameters["s3_prefix"] == f"{STACK_NAMES[stack]}-{env}"
    assert parameters["region"] == REGION
    assert parameters["tags"].endswith(f"Env={env}"), parameters["tags"]
    assert "Project=service-platform" in parameters["tags"]


@pytest.mark.parametrize("stack,env", CASES)
def test_only_production_confirms_the_changeset(stack, env):
    """prod は変更セットを人が確認してから流す。dev は自動で通す。"""
    assert _deploy_parameters(stack, env)["confirm_changeset"] is (env == "prod")


@pytest.mark.parametrize("stack,env", CASES)
def test_parameter_overrides_match_the_environment(stack, env):
    """環境ごとに値が変わるパラメータを固定する。"""
    overrides = _overrides(stack, env)
    for key, value in EXPECTED_OVERRIDES[stack][env].items():
        assert overrides.get(key) == value, f"{stack} {env} {key}"


@pytest.mark.parametrize("stack", STACKS)
def test_production_never_points_at_dev_resources(stack):
    """prod の値に dev の SSM キー・ドメインが 1 つも残っていないこと。

    1 本でも残ると prod スタックが dev のレイヤ・キュー・バケットを掴む。
    デプロイは成功してしまうので、ここで落とす。
    """
    for value in _overrides(stack, "prod").values():
        assert "/dev/" not in value, value
        assert SITE_DOMAIN["dev"] not in value, value
    assert "Env=dev" not in _deploy_parameters(stack, "prod")["tags"]
