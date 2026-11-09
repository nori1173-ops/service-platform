"""core スタックのテンプレート検査（設計 4.2 / 6.x / 8.x / 11.x / 12.x、ADR-001 D1 / D2 / D9 / D12）。

Cognito 一式 (ユーザープール・アプリクライアント・PreSignUp と CustomMessage のトリガー・
ユーザープールの web ACL)、台帳と監査のテーブル、通知基盤 (SQS / notifier / SES / 添付バケット /
DLQ アラーム)、core が書く SSM パラメータが設計どおりであることを静的に確かめる。
"""

import pytest

TEMPLATE_PATH = "infra/core/template.yaml"

# dev の旧プールは email 必須で作られ、必須かどうかは作成後に変えられない。論理 ID を替えて
# CloudFormation にプールを作り直させた (計画の判断 1)。この ID をまた替えるとプールが作り直され、
# 利用者・パスキー・クライアント ID が全部変わる。
USER_POOL_ID = "SignInUserPool"
USER_POOL_ARN = {"Fn::GetAtt": f"{USER_POOL_ID}.Arn"}

USER_POOL_ID_KEY = "/${ProjectName}/${Env}/cognito/user-pool-id"
CLIENT_ID_KEY = "/${ProjectName}/${Env}/cognito/client-id"
ISSUER_KEY = "/${ProjectName}/${Env}/cognito/issuer"
ISSUER_VALUE = "https://cognito-idp.${AWS::Region}.amazonaws.com/${" + USER_POOL_ID + "}"
LEDGER_TABLE_KEY = "/${ProjectName}/${Env}/ledger/table-name"
AUDIT_TABLE_KEY = "/${ProjectName}/${Env}/audit/table-name"

NOTIFY_QUEUE_URL_KEY = "/${ProjectName}/${Env}/notify/queue-url"
NOTIFY_QUEUE_ARN_KEY = "/${ProjectName}/${Env}/notify/queue-arn"
ATTACHMENTS_BUCKET_KEY = "/${ProjectName}/${Env}/attachments/bucket"
OPS_TOPIC_ARN_KEY = "/${ProjectName}/${Env}/ops/topic-arn"

PASSWORD_REQUIREMENTS = ("RequireUppercase", "RequireLowercase", "RequireNumbers")
PASSWORD_MIN_LENGTH = 12
TEMPORARY_PASSWORD_DAYS = 3
FACTORS_WITH_SES = ["PASSWORD", "WEB_AUTHN", "EMAIL_OTP"]
FACTORS_WITHOUT_SES = ["PASSWORD", "WEB_AUTHN"]
SITE_DOMAIN_DEV = "service-platform-dev.example-cloud.com"
TOKEN_MINUTES = 60
REFRESH_TOKEN_MINUTES = 24 * 60
AUTH_SESSION_MINUTES = 10
REFRESH_GRACE_SECONDS = 30

CHANGE_PASSWORD_TARGET = "AWSCognitoIdentityProviderService.ChangePassword"
RATE_WINDOW_SECONDS = 300
RATE_LIMIT_DEFAULT = 300
WAF_LOG_GROUP_NAME = "aws-waf-logs-${ProjectName}-userpool-${Env}"
WAF_LOG_DESTINATION = "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${UserPoolWebAclLogGroup}"

LEDGER_TABLE_NAME = "${ProjectName}-core-ledger-${Env}"
AUDIT_TABLE_NAME = "${ProjectName}-core-audit-${Env}"
IDEMPOTENCY_TABLE_NAME = "${ProjectName}-core-idempotency-${Env}"
PROD_ONLY = {"Fn::If": ["IsProd", True, False]}

VISIBILITY_TIMEOUT_SECONDS = 360
NOTIFY_QUEUE_RETENTION_SECONDS = 4 * 24 * 60 * 60
DLQ_RETENTION_SECONDS = 14 * 24 * 60 * 60
MAX_RECEIVE_COUNT = 3
ATTACHMENT_EXPIRATION_DAYS = 400
NONCURRENT_VERSION_EXPIRATION_DAYS = 30
PUBLIC_ACCESS_BLOCK_KEYS = ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")

TOKYO_MX_RECORD = "10 feedback-smtp.ap-northeast-1.amazonses.com"
SPF_RECORD = '"v=spf1 include:amazonses.com ~all"'
DMARC_RECORD = '"v=DMARC1; p=quarantine;"'

SLACK_PARAMETER_ARN = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/${ProjectName}/${Env}/shared/slack/*"


def _load_or_skip(load_template) -> dict:
    template = load_template(TEMPLATE_PATH)
    if template is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return template


def _resources_by_type(template: dict, resource_type: str) -> dict:
    return {k: v for k, v in (template.get("Resources") or {}).items() if v.get("Type") == resource_type}


def _properties(resource: dict) -> dict:
    return resource.get("Properties") or {}


def _sole_resource(template: dict, resource_type: str) -> dict:
    resources = _resources_by_type(template, resource_type)
    assert len(resources) == 1, f"{resource_type} は 1 つであること: {sorted(resources)}"
    return next(iter(resources.values()))


def _user_pool_properties(template: dict) -> dict:
    return _properties(_sole_resource(template, "AWS::Cognito::UserPool"))


def _client_properties(template: dict) -> dict:
    return _properties(_sole_resource(template, "AWS::Cognito::UserPoolClient"))


def _ssm_parameter_name(resource: dict) -> str | None:
    """SSM パラメータの `Name` を文字列で返す（`!Sub` は展開前のテンプレート文字列）。"""
    name = _properties(resource).get("Name")
    if isinstance(name, dict):
        name = name.get("Fn::Sub")
    return name if isinstance(name, str) else None


def _cognito_ssm_parameters(template: dict) -> dict:
    """`/cognito/` 配下の SSM パラメータを {キー名: リソース} で返す。"""
    return {
        name: resource
        for resource in _resources_by_type(template, "AWS::SSM::Parameter").values()
        if (name := _ssm_parameter_name(resource)) and "/cognito/" in name
    }


def _ssm_parameter(template: dict, key: str) -> dict:
    matched = [
        resource
        for resource in _resources_by_type(template, "AWS::SSM::Parameter").values()
        if _ssm_parameter_name(resource) == key
    ]
    assert len(matched) == 1, f"{key} を書く SSM パラメータは 1 つであること"
    return _properties(matched[0])


def _resource_with(template: dict, resource_type: str, predicate) -> tuple[str, dict]:
    """`predicate(Properties)` が真になるリソースを 1 つだけ選び `(論理 ID, Properties)` を返す。"""
    matched = {
        logical_id: _properties(resource)
        for logical_id, resource in _resources_by_type(template, resource_type).items()
        if predicate(_properties(resource))
    }
    assert len(matched) == 1, f"{resource_type} の該当は 1 つであること: {sorted(matched)}"
    return next(iter(matched.items()))


def _table(template: dict, table_name: str) -> tuple[str, dict]:
    """`TableName` (`!Sub` の展開前) でテーブルを選び `(論理 ID, リソース)` を返す。"""
    matched = {
        logical_id: resource
        for logical_id, resource in _resources_by_type(template, "AWS::DynamoDB::Table").items()
        if _properties(resource).get("TableName") == {"Fn::Sub": table_name}
    }
    assert len(matched) == 1, f"{table_name} のテーブルは 1 つであること: {sorted(matched)}"
    return next(iter(matched.items()))


def _web_acl(template: dict) -> tuple[str, dict]:
    resources = _resources_by_type(template, "AWS::WAFv2::WebACL")
    assert len(resources) == 1, f"web ACL は 1 つであること: {sorted(resources)}"
    logical_id, resource = next(iter(resources.items()))
    return logical_id, _properties(resource)


def _web_acl_rule(template: dict, name: str) -> dict:
    _, properties = _web_acl(template)
    rules = [rule for rule in properties.get("Rules") or [] if rule.get("Name") == name]
    assert len(rules) == 1, f"web ACL の規則 {name} は 1 つであること"
    return rules[0]


def _function_by_code_uri(template: dict, code_uri: str) -> tuple[str, dict]:
    return _resource_with(template, "AWS::Serverless::Function", lambda p: p.get("CodeUri") == code_uri)


def _invoke_permissions(template: dict, function_id: str) -> list[dict]:
    return [
        _properties(resource)
        for resource in _resources_by_type(template, "AWS::Lambda::Permission").values()
        if _properties(resource).get("FunctionName") == {"Fn::GetAtt": f"{function_id}.Arn"}
    ]


def _notify_queue(template: dict) -> tuple[str, dict]:
    """DLQ を持つ側が通知キュー（論理 ID ではなく構造で見分ける）。"""
    return _resource_with(template, "AWS::SQS::Queue", lambda p: "RedrivePolicy" in p)


def _notify_dlq(template: dict) -> tuple[str, dict]:
    return _resource_with(template, "AWS::SQS::Queue", lambda p: "RedrivePolicy" not in p)


def _has_sqs_event(properties: dict) -> bool:
    return any((event or {}).get("Type") == "SQS" for event in (properties.get("Events") or {}).values())


def _notifier(template: dict) -> tuple[str, dict]:
    return _resource_with(template, "AWS::Serverless::Function", _has_sqs_event)


def _notifier_sqs_event(template: dict) -> dict:
    _, properties = _notifier(template)
    events = [e for e in (properties.get("Events") or {}).values() if (e or {}).get("Type") == "SQS"]
    assert len(events) == 1, "notifier の SQS イベントソースは 1 つであること"
    return events[0].get("Properties") or {}


def _notifier_statements(template: dict) -> list[dict]:
    """notifier のインラインポリシー（SAM `Policies`）の Statement を平らにして返す。"""
    _, properties = _notifier(template)
    policies = properties.get("Policies") or []
    assert policies, "notifier に IAM ポリシーが無い"
    return [statement for policy in policies for statement in (policy.get("Statement") or [])]


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _ses_record_sets(template: dict) -> list[dict]:
    group = _sole_resource(template, "AWS::Route53::RecordSetGroup")
    return _properties(group).get("RecordSets") or []


def _records_of_type(template: dict, record_type: str) -> list[dict]:
    return [r for r in _ses_record_sets(template) if r.get("Type") == record_type]


def _notify_ssm_parameters(template: dict) -> dict:
    """`/notify/` と `/attachments/` 配下の SSM パラメータを {キー名: リソース} で返す。"""
    return {
        name: resource
        for resource in _resources_by_type(template, "AWS::SSM::Parameter").values()
        if (name := _ssm_parameter_name(resource)) and ("/notify/" in name or "/attachments/" in name)
    }


def _attachments_bucket(template: dict) -> dict:
    return _properties(_sole_resource(template, "AWS::S3::Bucket"))


# =============================================================================
# ユーザープール（ADR-001 D1 / D2 / D7 / D9、計画 3 章）
# =============================================================================


def test_user_pool_logical_id_replaces_the_email_required_pool(load_template):
    """旧プール (論理 ID `UserPool`、email 必須) は論理 ID の変更で置き換える（計画の判断 1）。"""
    template = _load_or_skip(load_template)
    resources = template.get("Resources") or {}
    assert USER_POOL_ID in resources
    assert resources[USER_POOL_ID].get("Type") == "AWS::Cognito::UserPool"
    assert "UserPool" not in resources


def test_user_pool_uses_email_as_username(load_template):
    """サインイン識別子はメールアドレスで、大文字小文字を区別しない（作成後に変えられない）。"""
    template = _load_or_skip(load_template)
    properties = _user_pool_properties(template)
    assert properties.get("UsernameAttributes") == ["email"]
    assert (properties.get("UsernameConfiguration") or {}).get("CaseSensitive") is False
    assert properties.get("AutoVerifiedAttributes") == ["email"]


def test_user_pool_tier_is_essentials(load_template):
    """パスキーは Lite で使えないので Essentials にする（ADR D1）。Plus は使わない。"""
    template = _load_or_skip(load_template)
    assert _user_pool_properties(template).get("UserPoolTier") == "ESSENTIALS"


def test_email_is_not_a_required_attribute(load_template):
    """必須の属性はどのクライアントからも書けるので、email を必須にしない（ADR D2、PoC 1）。"""
    template = _load_or_skip(load_template)
    properties = _user_pool_properties(template)
    assert properties.get("Schema") == [
        {"Name": "email", "AttributeDataType": "String", "Required": False, "Mutable": True}
    ]
    assert properties.get("UserAttributeUpdateSettings") == {"AttributesRequireVerificationBeforeUpdate": ["email"]}


def test_self_signup_is_disabled(load_template):
    """招待制。プールが SignUp を受け付けない（ADR D2）。"""
    template = _load_or_skip(load_template)
    admin_create_user_config = _user_pool_properties(template).get("AdminCreateUserConfig") or {}
    assert admin_create_user_config.get("AllowAdminCreateUserOnly") is True


def test_account_recovery_is_admin_only(load_template):
    """「パスワードを忘れた」からの回復を止める（ADR D1、PoC 3）。"""
    template = _load_or_skip(load_template)
    recovery = _user_pool_properties(template).get("AccountRecoverySetting") or {}
    assert recovery.get("RecoveryMechanisms") == [{"Name": "admin_only", "Priority": 1}]


def test_password_policy_for_admins(load_template):
    """パスワードを持つのは admin だけ。12 文字以上、英大小・数字、記号は求めない、一時パスワード 3 日（計画 3 章）。"""
    template = _load_or_skip(load_template)
    policy = (_user_pool_properties(template).get("Policies") or {}).get("PasswordPolicy") or {}
    assert policy.get("MinimumLength") == PASSWORD_MIN_LENGTH
    for key in PASSWORD_REQUIREMENTS:
        assert policy.get(key) is True, key
    assert policy.get("RequireSymbols") is False
    assert policy.get("TemporaryPasswordValidityDays") == TEMPORARY_PASSWORD_DAYS


def test_email_otp_follows_the_email_sending_account(load_template):
    """EMAIL_OTP は SES (DEVELOPER) が前提なので、同じパラメータで切り替える（計画の判断 7）。"""
    template = _load_or_skip(load_template)
    sign_in = (_user_pool_properties(template).get("Policies") or {}).get("SignInPolicy") or {}
    assert sign_in.get("AllowedFirstAuthFactors") == {
        "Fn::If": ["UseSesForCognitoEmail", FACTORS_WITH_SES, FACTORS_WITHOUT_SES]
    }
    assert (template.get("Conditions") or {}).get("UseSesForCognitoEmail") == {
        "Fn::Equals": [{"Ref": "CognitoEmailSendingAccount"}, "DEVELOPER"]
    }


def test_webauthn_relying_party_is_the_site_domain(load_template):
    """パスキーの RP ID は環境の FQDN。利用者の確認 (UV) を必須にする（ADR D1 / D7）。"""
    template = _load_or_skip(load_template)
    properties = _user_pool_properties(template)
    assert properties.get("WebAuthnRelyingPartyID") == {"Ref": "SiteDomain"}
    assert properties.get("WebAuthnUserVerification") == "required"
    assert properties.get("WebAuthnFactorConfiguration") == "MULTI_FACTOR_WITH_USER_VERIFICATION"

    parameter = (template.get("Parameters") or {}).get("SiteDomain") or {}
    assert parameter.get("Type") == "String"
    assert parameter.get("Default") == SITE_DOMAIN_DEV


def test_user_pool_mfa_is_optional_totp_only(load_template):
    """MFA は任意で TOTP だけ。TOTP を持つのは admin だけにする（ADR D1 / D9）。

    必須にするとメールのコードが使えない。SMS は一度有効にすると外せないので構成しない。
    """
    template = _load_or_skip(load_template)
    properties = _user_pool_properties(template)
    assert properties.get("MfaConfiguration") == "OPTIONAL"
    assert properties.get("EnabledMfas") == ["SOFTWARE_TOKEN_MFA"]
    assert "SmsConfiguration" not in properties
    assert "SmsAuthenticationMessage" not in properties


def test_user_pool_email_is_switchable_to_ses(load_template):
    """Cognito のメールは SES (DEVELOPER) へ切り替えられ、送信者名は SERVICE-PLATFORM（計画 3 章）。

    SES Identity が未検証の間は `DEVELOPER` での作成に失敗するため、新規の環境は
    `COGNITO_DEFAULT` で立ち上げて検証後に切り替えられるようパラメータで選ぶ（設計 13.4）。
    """
    template = _load_or_skip(load_template)
    parameter = (template.get("Parameters") or {}).get("CognitoEmailSendingAccount") or {}
    assert parameter.get("AllowedValues") == ["COGNITO_DEFAULT", "DEVELOPER"]
    assert parameter.get("Default") == "COGNITO_DEFAULT"

    email = _user_pool_properties(template).get("EmailConfiguration") or {}
    developer, cognito_default = email["Fn::If"][1], email["Fn::If"][2]
    assert email["Fn::If"][0] == "UseSesForCognitoEmail"
    assert developer == {
        "EmailSendingAccount": "DEVELOPER",
        "From": {"Fn::Sub": "SERVICE-PLATFORM <noreply@${MailDomain}>"},
        "SourceArn": {"Fn::Sub": "arn:aws:ses:${AWS::Region}:${AWS::AccountId}:identity/${MailDomain}"},
        "ConfigurationSet": {"Ref": "SesConfigurationSet"},
    }
    # 独自 From を COGNITO_DEFAULT で使うには SES の送信認可ポリシーが要る。切替前は既定の
    # no-reply@verificationemail.com を使い、ポリシーを別途作らなくても立ち上がるようにする。
    assert cognito_default == {"EmailSendingAccount": "COGNITO_DEFAULT"}


def test_user_pool_waits_for_the_ses_identity(load_template):
    """SES Identity より先に User Pool を作ると DEVELOPER 切替が失敗する。"""
    template = _load_or_skip(load_template)
    identity_id, _ = next(iter(_resources_by_type(template, "AWS::SES::EmailIdentity").items()))
    depends_on = template["Resources"][USER_POOL_ID].get("DependsOn")
    assert identity_id in (depends_on if isinstance(depends_on, list) else [depends_on])


def test_lambda_triggers_are_wired(load_template):
    """PreSignUp と CustomMessage のトリガーと、Cognito からの呼び出し許可が揃っている（計画 3 章）。"""
    template = _load_or_skip(load_template)
    pre_signup_id, _ = _function_by_code_uri(template, "src/pre_signup/")
    custom_message_id, _ = _function_by_code_uri(template, "src/custom_message/")
    assert _user_pool_properties(template).get("LambdaConfig") == {
        "PreSignUp": {"Fn::GetAtt": f"{pre_signup_id}.Arn"},
        "CustomMessage": {"Fn::GetAtt": f"{custom_message_id}.Arn"},
    }

    for function_id in (pre_signup_id, custom_message_id):
        permissions = _invoke_permissions(template, function_id)
        assert len(permissions) == 1, f"{function_id} を呼ぶ Lambda::Permission が 1 つであること"
        properties = permissions[0]
        assert properties.get("Action") == "lambda:InvokeFunction"
        assert properties.get("Principal") == "cognito-idp.amazonaws.com"
        assert properties.get("SourceArn") == USER_POOL_ARN


def test_custom_message_function_knows_the_site_url(load_template):
    """招待の文面にサインインのページを載せる。ログは関数ごとに 365 日（設計 12.4）。"""
    template = _load_or_skip(load_template)
    function_id, properties = _function_by_code_uri(template, "src/custom_message/")
    assert properties.get("Handler") == "app.handler"
    variables = (properties.get("Environment") or {}).get("Variables") or {}
    assert variables.get("SITE_URL") == {"Fn::Sub": "https://${SiteDomain}/"}

    log_groups = [
        _properties(resource)
        for resource in _resources_by_type(template, "AWS::Logs::LogGroup").values()
        if _properties(resource).get("LogGroupName") == {"Fn::Sub": f"/aws/lambda/${{{function_id}}}"}
    ]
    assert len(log_groups) == 1
    assert log_groups[0].get("RetentionInDays") == 365


def test_app_client_uses_choice_based_sign_in_without_refresh_token_auth(load_template):
    """USER_AUTH だけ。SRP は  で外し（判断 6）、REFRESH_TOKEN_AUTH は rotation と併用不可（判断 14）。"""
    template = _load_or_skip(load_template)
    properties = _client_properties(template)
    assert properties.get("UserPoolId") == {"Ref": USER_POOL_ID}
    assert properties.get("GenerateSecret") is False
    assert sorted(properties.get("ExplicitAuthFlows") or []) == ["ALLOW_USER_AUTH"]
    assert "ALLOW_REFRESH_TOKEN_AUTH" not in (properties.get("ExplicitAuthFlows") or [])
    assert properties.get("PreventUserExistenceErrors") == "ENABLED"
    assert properties.get("EnableTokenRevocation") is True


def test_app_client_auth_session_matches_the_code_deadline(load_template):
    """画面とメールで案内するコードの期限を Cognito のセッションにそろえる。"""
    # frontend/src/utils/authConstants.ts と infra/core/src/custom_message/templates.py の期限も同時に見直す。
    template = _load_or_skip(load_template)
    assert _client_properties(template).get("AuthSessionValidity") == AUTH_SESSION_MINUTES == 10


def test_app_client_rotates_refresh_tokens(load_template):
    """更新トークンの rotation。猶予 30 秒で複数のタブと /camera/ の同時の更新を許す（判断 14）。"""
    template = _load_or_skip(load_template)
    assert _client_properties(template).get("RefreshTokenRotation") == {
        "Feature": "ENABLED",
        "RetryGracePeriodSeconds": REFRESH_GRACE_SECONDS,
    }


def test_token_validity(load_template):
    """ID / アクセストークンは 60 分、更新トークンは 24 時間。単位は分にそろえる（ADR D1、判断 7）。"""
    template = _load_or_skip(load_template)
    properties = _client_properties(template)
    assert properties.get("IdTokenValidity") == TOKEN_MINUTES
    assert properties.get("AccessTokenValidity") == TOKEN_MINUTES
    assert properties.get("RefreshTokenValidity") == REFRESH_TOKEN_MINUTES
    assert properties.get("TokenValidityUnits") == {
        "IdToken": "minutes",
        "AccessToken": "minutes",
        "RefreshToken": "minutes",
    }


def test_users_cannot_write_their_email(load_template):
    """WriteAttributes を明示し、email と email_verified を入れない（ADR D2、PoC 2）。

    Cognito は WriteAttributes が ReadAttributes に含まれないクライアントを作らない
    (InvalidParameterException。2026-11-04 に dev で確認) ので、読める属性に name も入れる。
    """
    template = _load_or_skip(load_template)
    properties = _client_properties(template)
    write = properties.get("WriteAttributes")
    read = properties.get("ReadAttributes")
    assert write == ["name"]
    assert "email" not in write
    assert "email_verified" not in write
    assert read == ["email", "email_verified", "name"]
    assert set(write) <= set(read)


def test_only_one_user_pool_client_exists(load_template):
    """E2E 用の 2 つ目の App Client は作らない。"""
    template = _load_or_skip(load_template)
    clients = _resources_by_type(template, "AWS::Cognito::UserPoolClient")
    assert len(clients) == 1, f"App Client は 1 つであること: {sorted(clients)}"


def test_admin_group_exists(load_template):
    """認可はグループで表現する。初期リリースのグループは `admin` のみ。"""
    template = _load_or_skip(load_template)
    groups = _resources_by_type(template, "AWS::Cognito::UserPoolGroup")
    assert len(groups) == 1, f"グループは 1 つであること: {sorted(groups)}"
    properties = _properties(next(iter(groups.values())))
    assert properties.get("GroupName") == "admin"
    assert properties.get("UserPoolId") == {"Ref": USER_POOL_ID}
    assert properties.get("Precedence") == 1


def test_writes_three_cognito_ssm_parameters(load_template):
    """core が `/cognito/*` の 3 本を書く（設計 4.2）。"""
    template = _load_or_skip(load_template)
    parameters = _cognito_ssm_parameters(template)
    assert sorted(parameters) == sorted([USER_POOL_ID_KEY, CLIENT_ID_KEY, ISSUER_KEY])
    for key, resource in parameters.items():
        assert _properties(resource).get("Type") == "String", key
    assert _properties(parameters[USER_POOL_ID_KEY]).get("Value") == {"Ref": USER_POOL_ID}
    assert _properties(parameters[CLIENT_ID_KEY]).get("Value") == {"Ref": "UserPoolClient"}


def test_issuer_value_format(load_template):
    """issuer は JWT authorizer がそのまま使える形で書く（設計 4.2 / 6.5）。"""
    template = _load_or_skip(load_template)
    issuer = _cognito_ssm_parameters(template).get(ISSUER_KEY)
    assert issuer is not None, f"{ISSUER_KEY} を書く SSM パラメータが無い"
    assert _properties(issuer).get("Value") == {"Fn::Sub": ISSUER_VALUE}


def test_deletion_protection_is_conditional_on_prod(load_template):
    """削除保護は prod のみ有効（設計 6.4）。"""
    template = _load_or_skip(load_template)
    assert (template.get("Conditions") or {}).get("IsProd") == {"Fn::Equals": [{"Ref": "Env"}, "prod"]}
    assert _user_pool_properties(template).get("DeletionProtection") == {"Fn::If": ["IsProd", "ACTIVE", "INACTIVE"]}


# =============================================================================
# ユーザープールの web ACL（ADR-001 D9、判断 15、計画 3 章）
# =============================================================================


def test_web_acl_is_regional_and_allows_by_default(load_template):
    template = _load_or_skip(load_template)
    _, properties = _web_acl(template)
    assert properties.get("Scope") == "REGIONAL"
    assert properties.get("DefaultAction") == {"Allow": {}}
    assert [rule.get("Name") for rule in properties.get("Rules") or []] == ["change-password", "rate-per-ip"]


def test_web_acl_blocks_change_password_from_the_start(load_template):
    """お客様のパスワードの後付けを止める。dev も本番も最初から Block（判断 15、PoC 14）。"""
    template = _load_or_skip(load_template)
    rule = _web_acl_rule(template, "change-password")
    assert rule.get("Priority") == 0
    assert rule.get("Action") == {"Block": {}}
    assert (rule.get("Statement") or {}).get("ByteMatchStatement") == {
        "FieldToMatch": {"SingleHeader": {"Name": "x-amz-target"}},
        "PositionalConstraint": "EXACTLY",
        "SearchString": CHANGE_PASSWORD_TARGET,
        "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
    }
    assert (rule.get("VisibilityConfig") or {}).get("CloudWatchMetricsEnabled") is True


def test_web_acl_counts_requests_per_ip(load_template):
    """公開 API への IP ごとのレート制限。実測を見るまでは数えるだけ（ADR D9）。"""
    template = _load_or_skip(load_template)
    rule = _web_acl_rule(template, "rate-per-ip")
    assert rule.get("Priority") == 1
    assert rule.get("Action") == {"Block": {}}
    assert (rule.get("Statement") or {}).get("RateBasedStatement") == {
        "Limit": {"Ref": "UserPoolRateLimit"},
        "EvaluationWindowSec": RATE_WINDOW_SECONDS,
        "AggregateKeyType": "IP",
    }
    parameter = (template.get("Parameters") or {}).get("UserPoolRateLimit") or {}
    assert parameter.get("Type") == "Number"
    assert parameter.get("Default") == RATE_LIMIT_DEFAULT


def test_web_acl_is_associated_with_the_user_pool(load_template):
    template = _load_or_skip(load_template)
    acl_id, _ = _web_acl(template)
    association = _properties(_sole_resource(template, "AWS::WAFv2::WebACLAssociation"))
    assert association.get("ResourceArn") == USER_POOL_ARN
    assert association.get("WebACLArn") == {"Fn::GetAtt": f"{acl_id}.Arn"}


def test_web_acl_logs_only_counted_and_blocked_requests(load_template):
    """WAF のログは Count と Block に当たった要求だけを CloudWatch Logs に 365 日（計画 3 章）。"""
    template = _load_or_skip(load_template)
    acl_id, _ = _web_acl(template)
    logging = _properties(_sole_resource(template, "AWS::WAFv2::LoggingConfiguration"))
    assert logging.get("ResourceArn") == {"Fn::GetAtt": f"{acl_id}.Arn"}
    # WAF は `:*` の付いたロググループの ARN (GetAtt の Arn) を受け付けない
    assert logging.get("LogDestinationConfigs") == [{"Fn::Sub": WAF_LOG_DESTINATION}]
    assert logging.get("LoggingFilter") == {
        "DefaultBehavior": "DROP",
        "Filters": [
            {
                "Behavior": "KEEP",
                "Requirement": "MEETS_ANY",
                "Conditions": [
                    {"ActionCondition": {"Action": "BLOCK"}},
                    {"ActionCondition": {"Action": "COUNT"}},
                ],
            }
        ],
    }

    log_group = _properties(template["Resources"]["UserPoolWebAclLogGroup"])
    assert log_group.get("LogGroupName") == {"Fn::Sub": WAF_LOG_GROUP_NAME}
    assert log_group.get("RetentionInDays") == 365


# =============================================================================
# 台帳と監査のテーブル（ADR-001 D4 / D12、計画 2.1 / 2.2）
# =============================================================================


def _key_schema(properties: dict) -> list[dict]:
    return properties.get("KeySchema") or []


def _gsis(properties: dict) -> dict:
    return {gsi["IndexName"]: gsi for gsi in properties.get("GlobalSecondaryIndexes") or []}


def _assert_pk_sk_table(properties: dict, gsi_names: list[str]) -> None:
    assert properties.get("BillingMode") == "PAY_PER_REQUEST"
    assert _key_schema(properties) == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    expected_attributes = ["PK", "SK"] + [f"{name}{suffix}" for name in gsi_names for suffix in ("PK", "SK")]
    assert sorted(properties.get("AttributeDefinitions") or [], key=lambda a: a["AttributeName"]) == sorted(
        ({"AttributeName": name, "AttributeType": "S"} for name in expected_attributes),
        key=lambda a: a["AttributeName"],
    )
    gsis = _gsis(properties)
    assert sorted(gsis) == sorted(gsi_names)
    for name in gsi_names:
        assert gsis[name].get("KeySchema") == [
            {"AttributeName": f"{name}PK", "KeyType": "HASH"},
            {"AttributeName": f"{name}SK", "KeyType": "RANGE"},
        ]
        assert gsis[name].get("Projection") == {"ProjectionType": "ALL"}
    assert properties.get("TimeToLiveSpecification") == {"AttributeName": "ttl", "Enabled": True}
    assert properties.get("SSESpecification") == {"SSEEnabled": True}


def test_ledger_table_keys_and_gsi(load_template):
    """台帳は PK / SK と一覧のための GSI1（計画 2.1）。"""
    template = _load_or_skip(load_template)
    _, resource = _table(template, LEDGER_TABLE_NAME)
    _assert_pk_sk_table(_properties(resource), ["GSI1"])


def test_audit_table_keys_and_gsis(load_template):
    """監査は PK / SK、通信機で引く GSI1、日ごとに引く GSI2（計画 2.2）。"""
    template = _load_or_skip(load_template)
    _, resource = _table(template, AUDIT_TABLE_NAME)
    _assert_pk_sk_table(_properties(resource), ["GSI1", "GSI2"])


@pytest.mark.parametrize("table_name", [LEDGER_TABLE_NAME, AUDIT_TABLE_NAME])
def test_ledger_and_audit_are_retained(load_template, table_name):
    """請求の実績の基礎なので dev も Retain。prod は PITR と削除保護（計画 2.1 / 2.2）。"""
    template = _load_or_skip(load_template)
    _, resource = _table(template, table_name)
    assert resource.get("DeletionPolicy") == "Retain"
    assert resource.get("UpdateReplacePolicy") == "Retain"
    properties = _properties(resource)
    assert properties.get("PointInTimeRecoverySpecification") == {"PointInTimeRecoveryEnabled": PROD_ONLY}
    assert properties.get("DeletionProtectionEnabled") == PROD_ONLY


def test_writes_ledger_and_audit_ssm_parameters(load_template):
    """台帳と監査のテーブル名を SSM で公開する（設計 4.2）。"""
    template = _load_or_skip(load_template)
    for key, table_name in ((LEDGER_TABLE_KEY, LEDGER_TABLE_NAME), (AUDIT_TABLE_KEY, AUDIT_TABLE_NAME)):
        table_id, _ = _table(template, table_name)
        properties = _ssm_parameter(template, key)
        assert properties.get("Type") == "String", key
        assert properties.get("Value") == {"Ref": table_id}, key


# =============================================================================
# 通知基盤（SQS / notifier / SES / 添付バケット / DLQ アラーム）
# =============================================================================


def test_notify_queue_has_dlq_with_max_receive_3(load_template):
    """3 回失敗したメッセージは DLQ へ移す。"""
    template = _load_or_skip(load_template)
    _, properties = _notify_queue(template)
    dlq_id, _ = _notify_dlq(template)
    redrive = properties.get("RedrivePolicy") or {}
    assert redrive.get("deadLetterTargetArn") == {"Fn::GetAtt": f"{dlq_id}.Arn"}
    assert redrive.get("maxReceiveCount") == MAX_RECEIVE_COUNT
    assert properties.get("MessageRetentionPeriod") == NOTIFY_QUEUE_RETENTION_SECONDS
    assert properties.get("SqsManagedSseEnabled") is True


def test_notify_queue_visibility_timeout_is_360(load_template):
    """可視性タイムアウトは notifier のタイムアウト 60 秒の 6 倍。"""
    template = _load_or_skip(load_template)
    _, properties = _notify_queue(template)
    _, notifier = _notifier(template)
    assert properties.get("VisibilityTimeout") == VISIBILITY_TIMEOUT_SECONDS
    assert notifier.get("Timeout") * 6 <= VISIBILITY_TIMEOUT_SECONDS


def test_dlq_retention_is_14_days(load_template):
    """DLQ は 14 日保持。原因解消後の再駆動までメッセージを残す。"""
    template = _load_or_skip(load_template)
    _, properties = _notify_dlq(template)
    assert properties.get("MessageRetentionPeriod") == DLQ_RETENTION_SECONDS
    assert properties.get("SqsManagedSseEnabled") is True


def test_dlq_alarm_exists_and_targets_ops_topic(load_template):
    """DLQ に 1 件でも入ったら基盤担当へ通知する。"""
    template = _load_or_skip(load_template)
    properties = _properties(_sole_resource(template, "AWS::CloudWatch::Alarm"))
    topic_id, _ = next(iter(_resources_by_type(template, "AWS::SNS::Topic").items()))
    dlq_id, _ = _notify_dlq(template)

    assert properties.get("Namespace") == "AWS/SQS"
    assert properties.get("MetricName") == "ApproximateNumberOfMessagesVisible"
    assert properties.get("ComparisonOperator") == "GreaterThanOrEqualToThreshold"
    assert properties.get("Threshold") == 1
    assert properties.get("TreatMissingData") == "notBreaching"
    assert properties.get("AlarmActions") == [{"Ref": topic_id}]
    assert properties.get("Dimensions") == [{"Name": "QueueName", "Value": {"Fn::GetAtt": f"{dlq_id}.QueueName"}}]


def test_writes_ops_topic_arn_parameter(load_template):
    """アラームの通知先をアプリが読めるように SSM へ公開する（SP-02）。

    これが無いとアプリ側は SNS トピック ARN を samconfig に手で書くことになる。
    """
    template = _load_or_skip(load_template)
    topic_id, _ = next(iter(_resources_by_type(template, "AWS::SNS::Topic").items()))
    parameters = {
        name: resource
        for resource in _resources_by_type(template, "AWS::SSM::Parameter").values()
        if (name := _ssm_parameter_name(resource)) == OPS_TOPIC_ARN_KEY
    }
    assert len(parameters) == 1, f"{OPS_TOPIC_ARN_KEY} を書く SSM パラメータは 1 つであること"

    properties = _properties(next(iter(parameters.values())))
    assert properties.get("Type") == "String"
    assert properties.get("Value") == {"Ref": topic_id}


def test_ops_topic_display_name_is_the_brand(load_template):
    """Ops 通知メールの送信者名はブランド表記 SERVICE-PLATFORM にそろえる。"""
    template = _load_or_skip(load_template)
    _, topic = next(iter(_resources_by_type(template, "AWS::SNS::Topic").items()))
    assert _properties(topic).get("DisplayName") == "SERVICE-PLATFORM ops"


def test_notifier_uses_batch_size_1_and_report_batch_item_failures(load_template):
    """1 件ずつ処理し、一時エラーのレコードだけを再配信させる。"""
    template = _load_or_skip(load_template)
    queue_id, _ = _notify_queue(template)
    event = _notifier_sqs_event(template)
    assert event.get("Queue") == {"Fn::GetAtt": f"{queue_id}.Arn"}
    assert event.get("BatchSize") == 1
    assert event.get("FunctionResponseTypes") == ["ReportBatchItemFailures"]


def test_notifier_timeout_60_memory_512(load_template):
    """notifier はメモリ 512 MB・タイムアウト 60 秒。"""
    template = _load_or_skip(load_template)
    _, properties = _notifier(template)
    assert properties.get("Timeout") == 60
    assert properties.get("MemorySize") == 512

    variables = (properties.get("Environment") or {}).get("Variables") or {}
    assert sorted(variables) == [
        "ALLOWED_MAIL_DOMAINS",
        "DLQ_URL",
        "ENV",
        "IDEMPOTENCY_TABLE",
        "LOG_LEVEL",
        "POWERTOOLS_SERVICE_NAME",
        "SES_FROM_ADDRESS",
        "SSM_PREFIX",
    ]
    assert variables["SES_FROM_ADDRESS"] == {"Fn::Sub": "noreply@${MailDomain}"}
    assert variables["SSM_PREFIX"] == {"Fn::Sub": "/${ProjectName}/${Env}"}


def test_notifier_declares_allowed_mail_domains(load_template):
    """宛先ドメインの許可リストをコード既定値任せにせずテンプレートで明示する。"""
    template = _load_or_skip(load_template)
    _, properties = _notifier(template)
    variables = (properties.get("Environment") or {}).get("Variables") or {}
    assert variables.get("ALLOWED_MAIL_DOMAINS") == "example.co.jp"


def test_idempotency_table_has_ttl(load_template):
    """Powertools Idempotency が書く `expiration` で古いレコードを自動削除する。"""
    template = _load_or_skip(load_template)
    _, resource = _table(template, IDEMPOTENCY_TABLE_NAME)
    properties = _properties(resource)
    assert properties.get("TimeToLiveSpecification") == {"AttributeName": "expiration", "Enabled": True}
    assert properties.get("BillingMode") == "PAY_PER_REQUEST"
    assert properties.get("KeySchema") == [{"AttributeName": "id", "KeyType": "HASH"}]
    assert properties.get("AttributeDefinitions") == [{"AttributeName": "id", "AttributeType": "S"}]


def test_attachments_bucket_blocks_public_access(load_template):
    """添付バケットは非公開・SSE-S3・ACL 無効。"""
    template = _load_or_skip(load_template)
    properties = _attachments_bucket(template)

    block = properties.get("PublicAccessBlockConfiguration") or {}
    for key in PUBLIC_ACCESS_BLOCK_KEYS:
        assert block.get(key) is True, key

    ownership = (properties.get("OwnershipControls") or {}).get("Rules") or []
    assert ownership == [{"ObjectOwnership": "BucketOwnerEnforced"}]

    encryption = (properties.get("BucketEncryption") or {}).get("ServerSideEncryptionConfiguration") or []
    assert encryption == [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]


def test_attachments_bucket_is_retained(load_template):
    """業務文書の置き場なのでスタック削除・置換更新でバケットを消さない。"""
    template = _load_or_skip(load_template)
    buckets = _resources_by_type(template, "AWS::S3::Bucket")
    assert len(buckets) == 1, f"S3 バケットは 1 つであること: {sorted(buckets)}"
    bucket = next(iter(buckets.values()))
    assert bucket.get("DeletionPolicy") == "Retain"
    assert bucket.get("UpdateReplacePolicy") == "Retain"


def test_attachments_lifecycle_expires_by_tag_only(load_template):
    """期限付きの添付だけを 400 日で削除し、使い手の無い 90 日の規則を持たない。"""
    template = _load_or_skip(load_template)
    rules = (_attachments_bucket(template).get("LifecycleConfiguration") or {}).get("Rules") or []
    expiring = [r for r in rules if "ExpirationInDays" in r]
    assert len(expiring) == 1
    assert expiring[0]["ExpirationInDays"] == ATTACHMENT_EXPIRATION_DAYS
    assert expiring[0]["Status"] == "Enabled"
    assert expiring[0]["TagFilters"] == [{"Key": "lifecycle", "Value": "expire"}]
    assert "Prefix" not in expiring[0]
    assert all({"Key": "lifecycle", "Value": "expire-90"} not in r.get("TagFilters", []) for r in rules)


def test_attachments_bucket_is_versioned(load_template):
    """成果物の上書き・誤削除から復元できるようにする。

    再実行は同一キーへ上書きする設計なので、バージョニングが無いと復元手段が無い。
    非現行バージョンは 30 日で消してコストを抑える。
    """
    template = _load_or_skip(load_template)
    properties = _attachments_bucket(template)
    assert properties.get("VersioningConfiguration") == {"Status": "Enabled"}

    rules = (properties.get("LifecycleConfiguration") or {}).get("Rules") or []
    noncurrent = [r for r in rules if "NoncurrentVersionExpiration" in r]
    assert len(noncurrent) == 1, f"非現行バージョンの規則は 1 つであること: {rules}"
    assert noncurrent[0]["NoncurrentVersionExpiration"] == {"NoncurrentDays": NONCURRENT_VERSION_EXPIRATION_DAYS}
    assert noncurrent[0].get("Status") == "Enabled"
    # 現行バージョンを消すと delete marker が残り続けるので同じ規則で掃除する
    assert noncurrent[0].get("ExpiredObjectDeleteMarker") is True


def test_ses_identity_has_mail_from_and_dkim_records(load_template):
    """Easy DKIM とカスタム MAIL FROM を有効にし、必要な DNS を core が登録する。"""
    template = _load_or_skip(load_template)
    identity = _properties(_sole_resource(template, "AWS::SES::EmailIdentity"))
    assert identity.get("EmailIdentity") == {"Ref": "MailDomain"}
    assert identity.get("DkimAttributes") == {"SigningEnabled": True}
    assert identity.get("MailFromAttributes") == {"MailFromDomain": {"Fn::Sub": "bounce.${MailDomain}"}}

    identity_id, _ = next(iter(_resources_by_type(template, "AWS::SES::EmailIdentity").items()))
    cnames = _records_of_type(template, "CNAME")
    assert len(cnames) == 3, "DKIM の CNAME は 3 本"
    for index, record in enumerate(cnames, start=1):
        assert record.get("Name") == {"Fn::GetAtt": f"{identity_id}.DkimDNSTokenName{index}"}
        assert record.get("ResourceRecords") == [{"Fn::GetAtt": f"{identity_id}.DkimDNSTokenValue{index}"}]

    txt_records = {
        tuple(sorted((r.get("Name") or {}).items()))[0][1]: r.get("ResourceRecords")
        for r in _records_of_type(template, "TXT")
    }
    assert txt_records.get("bounce.${MailDomain}") == [SPF_RECORD]
    assert txt_records.get("_dmarc.${MailDomain}") == [DMARC_RECORD]

    group = _properties(_sole_resource(template, "AWS::Route53::RecordSetGroup"))
    assert group.get("HostedZoneId") == {"Ref": "HostedZoneId"}


def test_ses_mx_points_to_tokyo_region(load_template):
    """MAIL FROM の MX は東京リージョンのバウンス受け口。"""
    template = _load_or_skip(load_template)
    mx_records = _records_of_type(template, "MX")
    assert len(mx_records) == 1
    assert mx_records[0].get("Name") == {"Fn::Sub": "bounce.${MailDomain}"}
    assert mx_records[0].get("ResourceRecords") == [TOKYO_MX_RECORD]


def test_writes_notify_and_attachments_ssm_parameters(load_template):
    """core が `/notify/*` と `/attachments/bucket` の 3 本を書く（設計 4.2）。"""
    template = _load_or_skip(load_template)
    parameters = _notify_ssm_parameters(template)
    assert sorted(parameters) == sorted([NOTIFY_QUEUE_URL_KEY, NOTIFY_QUEUE_ARN_KEY, ATTACHMENTS_BUCKET_KEY])
    for key, resource in parameters.items():
        assert _properties(resource).get("Type") == "String", key

    queue_id, _ = _notify_queue(template)
    bucket_id, _ = next(iter(_resources_by_type(template, "AWS::S3::Bucket").items()))
    assert _properties(parameters[NOTIFY_QUEUE_URL_KEY]).get("Value") == {"Ref": queue_id}
    assert _properties(parameters[NOTIFY_QUEUE_ARN_KEY]).get("Value") == {"Fn::GetAtt": f"{queue_id}.Arn"}
    assert _properties(parameters[ATTACHMENTS_BUCKET_KEY]).get("Value") == {"Ref": bucket_id}


def test_notifier_iam_has_no_wildcard_resource_except_logs(load_template):
    """notifier のインラインポリシーに `Resource: "*"` を書かない。

    ロググループへの書き込みは SAM が付ける `AWSLambdaBasicExecutionRole` に任せるため、
    インラインポリシー側にワイルドカードは 1 つも要らない。
    """
    template = _load_or_skip(load_template)
    offenders = [
        statement.get("Sid") or statement.get("Action")
        for statement in _notifier_statements(template)
        if "*" in _as_list(statement.get("Resource"))
    ]
    assert not offenders, f"Resource: '*' を使っている Statement: {offenders}"

    _, properties = _notifier(template)
    assert "Role" not in properties, "ロールは関数ごとに SAM が作る（共有ロール禁止）"


def test_notifier_ssm_permission_is_scoped_to_slack_prefix(load_template):
    """Slack の秘密以外の SSM は読めない。"""
    template = _load_or_skip(load_template)
    ssm_statements = [
        statement
        for statement in _notifier_statements(template)
        if any(str(action).startswith("ssm:") for action in _as_list(statement.get("Action")))
    ]
    assert len(ssm_statements) == 1, "SSM を読む Statement は 1 つ"
    assert _as_list(ssm_statements[0].get("Action")) == ["ssm:GetParameter"]
    assert ssm_statements[0].get("Resource") == {"Fn::Sub": SLACK_PARAMETER_ARN}

    kms_statements = [
        statement
        for statement in _notifier_statements(template)
        if any(str(action).startswith("kms:") for action in _as_list(statement.get("Action")))
    ]
    assert len(kms_statements) == 1, "KMS の復号は 1 つの Statement に閉じる"
    string_equals = (kms_statements[0].get("Condition") or {}).get("StringEquals") or {}
    assert string_equals.get("kms:ViaService") == {"Fn::Sub": "ssm.${AWS::Region}.amazonaws.com"}


def test_notifier_can_list_attachments_bucket(load_template):
    """ListBucket が無いと存在しないキーの GetObject が 404 でなく AccessDenied を返し、
    添付欠落を恒久エラー（DLQ 直行）と判定できない。"""
    template = _load_or_skip(load_template)
    list_statements = [
        statement
        for statement in _notifier_statements(template)
        if "s3:ListBucket" in _as_list(statement.get("Action"))
    ]
    assert len(list_statements) == 1, "s3:ListBucket の Statement は 1 つ"
    assert list_statements[0].get("Resource") == {"Fn::Sub": "arn:aws:s3:::${AttachmentsBucket}"}
