"""edge スタックのテンプレート検査。

サイトの入口（S3 + OAC + CloudFront + Route53 + 標準ログ）が設計どおりであることを静的に確かめる。
CloudFront Function `spa-fallback` は default の behavior でディープリンクを `/index.html`
へ書き換え、
IP 制限は持たない（ADR D6）。`CustomErrorResponses` の不在（設計 5.4）は、抜けると JWT の 401/403 が
200 に化ける種類の項目なので明示的に検査する。
"""

import pathlib

import pytest

TEMPLATE_PATH = "infra/edge/template.yaml"
SPA_FALLBACK_JS_PATH = "infra/edge/functions/spa-fallback.js"
DEPLOY_EDGE_PATH = "scripts/deploy-edge.sh"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# アプリ id → edge のパラメータ・条件名に使う PascalCase 名。アプリを足したらここに 1 行足す。
API_APPS = {
    "admin-users": "AdminUsers",
    "base": "Base",
}
API_PATH_PREFIX = "/api/"


CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ALL_VIEWER_EXCEPT_HOST_HEADER = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

SEVEN_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"})

SPA_FALLBACK_ARN = {"Fn::GetAtt": "SpaFallbackFunction.FunctionARN"}

ORIGIN_VERIFY_HEADER = "x-origin-verify"

HSTS_MAX_AGE_SECONDS = 31536000
CSP_DIRECTIVES = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data: blob:",
    "font-src": "'self' data:",
    "connect-src": "'self' https://cognito-idp.${AWS::Region}.amazonaws.com",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "object-src": "'none'",
    "form-action": "'self'",
}


def _load_or_skip(load_template) -> dict:
    template = load_template(TEMPLATE_PATH)
    if template is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return template


def _resource(template: dict, logical_id: str) -> dict:
    resources = template.get("Resources") or {}
    assert logical_id in resources, f"リソース {logical_id} が無い"
    return resources[logical_id]


def _properties(template: dict, logical_id: str) -> dict:
    return _resource(template, logical_id).get("Properties") or {}


def _stack_parameter(template: dict, name: str) -> dict:
    return ((template.get("Parameters") or {}).get(name)) or {}


def _distribution_config(template: dict) -> dict:
    return _properties(template, "Distribution").get("DistributionConfig") or {}


def _unwrap_condition(value):
    """`Fn::If` を `(条件名, 真側の値)` に分解する。条件が無ければ `(None, 値)`。"""
    if isinstance(value, dict) and set(value) == {"Fn::If"}:
        condition, if_true, _if_false = value["Fn::If"]
        return condition, if_true
    return None, value


def _conditional_items(value) -> list[tuple[str | None, dict]]:
    """リスト（リスト全体が `Fn::If` の場合も含む）を `(条件名, 要素)` の並びへ正規化する。

    `AWS::NoValue`（＝ dict でない要素）は取り除く。要素ごとの `Fn::If` と
    プロパティ全体の `Fn::If`、どちらの書き方でも同じ形で扱えるようにする。
    """
    outer_condition, items = _unwrap_condition(value)
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        condition, element = _unwrap_condition(item)
        if isinstance(element, dict):
            normalized.append((condition or outer_condition, element))
    return normalized


def _origins(template: dict) -> list[tuple[str | None, dict]]:
    return _conditional_items(_distribution_config(template).get("Origins") or [])


def _cache_behaviors(template: dict) -> list[tuple[str | None, dict]]:
    return _conditional_items(_distribution_config(template).get("CacheBehaviors") or [])


def _default_behavior(template: dict) -> dict:
    return _distribution_config(template).get("DefaultCacheBehavior") or {}


def _path_pattern(app_id: str) -> str:
    return f"{API_PATH_PREFIX}{app_id}/*"


def _origin_id(app_id: str) -> str:
    return f"api-{app_id}"


def _api_behavior(template: dict, app_id: str) -> tuple[str | None, dict]:
    pattern = _path_pattern(app_id)
    matches = [(c, b) for c, b in _cache_behaviors(template) if b.get("PathPattern") == pattern]
    assert len(matches) == 1, f"{pattern} の behavior はちょうど 1 つであること"
    return matches[0]


def _api_origin(template: dict, app_id: str) -> tuple[str | None, dict]:
    origin_id = _origin_id(app_id)
    matches = [(c, o) for c, o in _origins(template) if o.get("Id") == origin_id]
    assert len(matches) == 1, f"origin {origin_id} はちょうど 1 つであること"
    return matches[0]


def _all_behaviors(template: dict) -> dict[str, dict]:
    behaviors = {"default": _default_behavior(template)}
    for _condition, behavior in _cache_behaviors(template):
        behaviors[behavior.get("PathPattern", "?")] = behavior
    return behaviors


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def _public_access_block(template: dict, logical_id: str) -> dict:
    return _properties(template, logical_id).get("PublicAccessBlockConfiguration") or {}


def _object_ownership(template: dict, logical_id: str) -> str | None:
    rules = (_properties(template, logical_id).get("OwnershipControls") or {}).get("Rules") or []
    return rules[0].get("ObjectOwnership") if rules else None


def _sse_algorithm(template: dict, logical_id: str) -> str | None:
    rules = (_properties(template, logical_id).get("BucketEncryption") or {}).get(
        "ServerSideEncryptionConfiguration"
    ) or []
    if not rules:
        return None
    return (rules[0].get("ServerSideEncryptionByDefault") or {}).get("SSEAlgorithm")


def test_no_custom_error_responses(load_template):
    """403/404 → index.html の一括書き換えは使わない。"""
    template = _load_or_skip(load_template)
    assert "CustomErrorResponses" not in _distribution_config(template)


def test_default_behavior_uses_caching_optimized_and_spa_fallback(load_template):
    """default は S3 origin + CachingOptimized + 自前のセキュリティヘッダ + spa-fallback。"""
    template = _load_or_skip(load_template)
    behavior = _default_behavior(template)
    spa_origins = [o for _c, o in _origins(template) if "S3OriginConfig" in o]
    assert len(spa_origins) == 1, "S3 origin はちょうど 1 つであること"
    assert behavior.get("TargetOriginId") == spa_origins[0].get("Id")
    assert behavior.get("CachePolicyId") == CACHING_OPTIMIZED
    assert behavior.get("ResponseHeadersPolicyId") == {"Fn::GetAtt": "SecurityHeadersPolicy.Id"}
    assert behavior.get("Compress") is True
    assert behavior.get("FunctionAssociations") == [{"EventType": "viewer-request", "FunctionARN": SPA_FALLBACK_ARN}]


def test_security_headers_policy_sets_a_csp(load_template):
    """AWS 管理ポリシーは CSP を持たないため自前で持つ。

    Amplify は ID / access / refresh トークンを localStorage に置くので、XSS が 1 件でも
    入るとトークンごと持ち出される。connect-src は self と Cognito だけに絞る。
    """
    template = _load_or_skip(load_template)
    config = _properties(template, "SecurityHeadersPolicy")["ResponseHeadersPolicyConfig"]
    security = config["SecurityHeadersConfig"]

    csp = security["ContentSecurityPolicy"]
    assert csp["Override"] is True
    body = csp["ContentSecurityPolicy"]["Fn::Sub"]
    directives = dict(part.strip().split(" ", 1) for part in body.split(";") if part.strip())
    assert directives == CSP_DIRECTIVES

    assert security["ContentTypeOptions"] == {"Override": True}
    assert security["FrameOptions"] == {"Override": True, "FrameOption": "DENY"}
    assert security["ReferrerPolicy"] == {"Override": True, "ReferrerPolicy": "strict-origin-when-cross-origin"}
    hsts = security["StrictTransportSecurity"]
    assert hsts["AccessControlMaxAgeSec"] == HSTS_MAX_AGE_SECONDS
    assert hsts["IncludeSubdomains"] is True
    assert hsts["Preload"] is False
    assert hsts["Override"] is True


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_behavior_uses_caching_disabled_and_all_viewer_except_host(load_template, app_id):
    """API は CachingDisabled + AllViewerExceptHostHeader（`AllViewer` だと API Gateway が 403）。"""
    template = _load_or_skip(load_template)
    _condition, behavior = _api_behavior(template, app_id)
    assert behavior.get("TargetOriginId") == _origin_id(app_id)
    assert behavior.get("CachePolicyId") == CACHING_DISABLED
    assert behavior.get("OriginRequestPolicyId") == ALL_VIEWER_EXCEPT_HOST_HEADER
    assert behavior.get("Compress") is True


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_behavior_allows_seven_methods(load_template, app_id):
    """`AllowedMethods` を省くと GET / HEAD だけになり POST が拒否される。"""
    template = _load_or_skip(load_template)
    _condition, behavior = _api_behavior(template, app_id)
    assert set(behavior.get("AllowedMethods") or []) == SEVEN_METHODS


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_origin_has_x_origin_verify_custom_header(load_template, app_id):
    """execute-api 直叩きを塞ぐ共有秘密をオリジンヘッダで渡す。"""
    template = _load_or_skip(load_template)
    _condition, origin = _api_origin(template, app_id)
    assert origin.get("DomainName") == {"Ref": f"{API_APPS[app_id]}ApiDomain"}
    assert origin.get("OriginCustomHeaders") == [
        {"HeaderName": ORIGIN_VERIFY_HEADER, "HeaderValue": {"Ref": "OriginVerifySecret"}}
    ]
    custom_origin = origin.get("CustomOriginConfig") or {}
    assert custom_origin.get("OriginProtocolPolicy") == "https-only"
    assert custom_origin.get("OriginSSLProtocols") == ["TLSv1.2"]


def test_viewer_protocol_policy_is_https_only_everywhere(load_template):
    """default と全 behavior で `https-only`。"""
    template = _load_or_skip(load_template)
    offenders = {
        name: behavior.get("ViewerProtocolPolicy")
        for name, behavior in _all_behaviors(template).items()
        if behavior.get("ViewerProtocolPolicy") != "https-only"
    }
    assert not offenders, f"https-only でない behavior: {offenders}"


def test_minimum_tls_is_1_2_2021(load_template):
    """証明書は既存の共有 ACM を再利用し、TLS は 1.2_2021 以上。"""
    template = _load_or_skip(load_template)
    certificate = _distribution_config(template).get("ViewerCertificate") or {}
    assert certificate.get("MinimumProtocolVersion") == "TLSv1.2_2021"
    assert certificate.get("SslSupportMethod") == "sni-only"
    assert certificate.get("AcmCertificateArn") == {"Ref": "AcmCertificateArn"}


def test_spa_bucket_is_private_and_uses_oac(load_template):
    """SPA バケットは非公開・SSE-S3 で、CloudFront からは OAC 経由でのみ読む。"""
    template = _load_or_skip(load_template)
    block = _public_access_block(template, "SpaBucket")
    assert all(
        block.get(key) is True
        for key in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
    ), block
    assert _sse_algorithm(template, "SpaBucket") == "AES256"

    spa_origins = [o for _c, o in _origins(template) if "S3OriginConfig" in o]
    assert len(spa_origins) == 1
    assert spa_origins[0].get("OriginAccessControlId") == {"Fn::GetAtt": "Oac.Id"}
    assert _resource(template, "Oac")["Type"] == "AWS::CloudFront::OriginAccessControl"


def test_bucket_policy_scopes_to_this_distribution(load_template):
    """バケットポリシーはこのディストリビューション ARN に限定する。"""
    template = _load_or_skip(load_template)
    statements = (_properties(template, "SpaBucketPolicy").get("PolicyDocument") or {}).get("Statement") or []
    assert len(statements) == 1
    statement = statements[0]
    assert statement.get("Principal") == {"Service": "cloudfront.amazonaws.com"}
    source_arn = ((statement.get("Condition") or {}).get("StringEquals") or {}).get("AWS:SourceArn")
    assert source_arn == {"Fn::Sub": "arn:aws:cloudfront::${AWS::AccountId}:distribution/${Distribution}"}


def test_distribution_attaches_the_optional_web_acl(load_template):
    template = _load_or_skip(load_template)
    parameter = template["Parameters"]["WebAclArn"]
    assert parameter["Type"] == "String"
    assert parameter["Default"] == ""
    assert template["Conditions"]["HasWebAcl"] == {"Fn::Not": [{"Fn::Equals": [{"Ref": "WebAclArn"}, ""]}]}
    assert _distribution_config(template)["WebACLId"] == {
        "Fn::If": ["HasWebAcl", {"Ref": "WebAclArn"}, {"Ref": "AWS::NoValue"}]
    }


def test_ipv6_is_disabled(load_template):
    """IPv6 は無効のまま（DNS も A のみで揃える。有効にするなら AAAA と一緒に変える）。"""
    template = _load_or_skip(load_template)
    assert _distribution_config(template).get("IPV6Enabled") is False


def test_dns_records_have_no_aaaa(load_template):
    """A の Alias のみ（IPv6 を無効にしているので AAAA は作らない）。"""
    template = _load_or_skip(load_template)
    record_sets = _properties(template, "DnsRecords").get("RecordSets") or []
    assert [r.get("Type") for r in record_sets] == ["A"]
    alias = record_sets[0].get("AliasTarget") or {}
    assert alias.get("HostedZoneId") == "Z2FDTNDATAQYW2"
    assert alias.get("DNSName") == {"Fn::GetAtt": "Distribution.DomainName"}


def test_api_behaviors_have_no_function_associations(load_template):
    """SPA フォールバックは default だけに付ける。`/api/{app}/*` は書き換えの対象外なので関数を付けない。

    behavior は列挙ではなくテンプレートから拾い、`API_APPS` に無い behavior が増えたら落とす。
    """
    template = _load_or_skip(load_template)
    behaviors = _all_behaviors(template)
    assert set(behaviors) == {"default", *(_path_pattern(app_id) for app_id in API_APPS)}
    offenders = {
        name: behavior.get("FunctionAssociations")
        for name, behavior in behaviors.items()
        if name != "default" and "FunctionAssociations" in behavior
    }
    assert not offenders, f"関数が付いている API behavior: {offenders}"


def test_cloudfront_function_is_a_single_function(load_template):
    """CloudFront Function は 1 本のまま（DESIGN 17.2 #10 の決め）。default が同じ関数を使う。"""
    template = _load_or_skip(load_template)
    functions = [
        logical_id
        for logical_id, resource in (template.get("Resources") or {}).items()
        if resource.get("Type") == "AWS::CloudFront::Function"
    ]
    assert functions == ["SpaFallbackFunction"]


def test_origin_verify_secret_parameter_is_no_echo(load_template):
    """共有秘密はイベント・コンソールに出さない。"""
    template = _load_or_skip(load_template)
    parameter = _stack_parameter(template, "OriginVerifySecret")
    assert parameter.get("Type") == "String"
    assert parameter.get("NoEcho") is True


def test_cloudfront_function_code_matches_js_file(load_template):
    """インラインの FunctionCode が `spa-fallback.js` の正本と一致する（片方だけ直す事故を防ぐ）。"""
    template = _load_or_skip(load_template)
    function = _resource(template, "SpaFallbackFunction")
    assert function["Type"] == "AWS::CloudFront::Function"
    properties = function.get("Properties") or {}
    assert properties.get("AutoPublish") is True
    assert (properties.get("FunctionConfig") or {}).get("Runtime") == "cloudfront-js-2.0"

    function_code = properties.get("FunctionCode")
    assert isinstance(function_code, str), "FunctionCode は変数を持たないので !Sub を使わずブロックリテラルで書く"

    source = (REPO_ROOT / SPA_FALLBACK_JS_PATH).read_text(encoding="utf-8")
    assert _normalize_newlines(function_code) == _normalize_newlines(source)


def test_logging_is_enabled(load_template):
    """標準ログを edge 所有のバケットへ出す。"""
    template = _load_or_skip(load_template)
    logging = _distribution_config(template).get("Logging") or {}
    assert logging.get("Bucket") == {"Fn::Sub": "${CloudFrontLogBucket}.s3.amazonaws.com"}
    assert logging.get("Prefix") == "cloudfront/"
    assert logging.get("IncludeCookies") is False

    rules = (_properties(template, "CloudFrontLogBucket").get("LifecycleConfiguration") or {}).get("Rules") or []
    assert [r.get("ExpirationInDays") for r in rules] == [365], "ログ保持は 365 日"


def test_log_bucket_ownership_is_bucket_owner_preferred(load_template):
    """レガシー標準ログは配信先バケットの ACL 書き込みを要求するため、ここだけ例外。"""
    template = _load_or_skip(load_template)
    assert _object_ownership(template, "CloudFrontLogBucket") == "BucketOwnerPreferred"
    assert _sse_algorithm(template, "CloudFrontLogBucket") == "AES256"
    block = _public_access_block(template, "CloudFrontLogBucket")
    assert all(
        block.get(key) is True
        for key in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
    ), block


def test_spa_bucket_ownership_is_bucket_owner_enforced(load_template):
    """例外がログ用バケットに閉じていること。"""
    template = _load_or_skip(load_template)
    assert _object_ownership(template, "SpaBucket") == "BucketOwnerEnforced"


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_domain_parameter_is_optional_string(load_template, app_id):
    """app 未デプロイでも edge をデプロイできるよう、API ドメインは省略可能な素の文字列にする。

    SSM 型（`AWS::SSM::Parameter::Value<String>`）にするとキー未作成でスタック作成自体が失敗し、
    edge → app → edge の順で回さざるを得なくなる。値は `deploy-edge.sh` が SSM
    `/service-platform/{env}/apps/{app}/api-domain`から読んで渡す。
    """
    template = _load_or_skip(load_template)
    parameter = _stack_parameter(template, f"{API_APPS[app_id]}ApiDomain")
    assert parameter.get("Type") == "String"
    assert parameter.get("Default") == ""


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_origin_and_behavior_are_conditional(load_template, app_id):
    """API ドメイン未指定なら origin も behavior も作らない（app 未デプロイでも edge が立つ）。"""
    template = _load_or_skip(load_template)
    pascal = API_APPS[app_id]
    condition = f"Has{pascal}Api"
    conditions = template.get("Conditions") or {}
    assert conditions.get(condition) == {"Fn::Not": [{"Fn::Equals": [{"Ref": f"{pascal}ApiDomain"}, ""]}]}

    origin_condition, _origin = _api_origin(template, app_id)
    behavior_condition, _behavior = _api_behavior(template, app_id)
    assert origin_condition == condition, "API origin が条件付きでない"
    assert behavior_condition == condition, "API behavior が条件付きでない"


def test_origins_and_behaviors_are_conditional_element_by_element(load_template):
    """リスト全体を `!If` にしない（後から足した要素が他アプリの Condition に巻き込まれる）。

    `CacheBehaviors: !If [HasXxxApi, [...], !Ref AWS::NoValue]` の形のまま 2 本目を足すと、
    1 本目のアプリが未デプロイのときに 2 本目ごと消え、`/api/{app}/*` が SPA へ落ちる。
    """
    template = _load_or_skip(load_template)
    config = _distribution_config(template)
    for name in ("Origins", "CacheBehaviors"):
        assert isinstance(config.get(name), list), f"{name} はリストであること（リスト全体の Fn::If は不可）"
    for behavior in config["CacheBehaviors"]:
        assert set(behavior) == {"Fn::If"}, "API の behavior は要素ごとに Fn::If で条件を付けること"


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_deploy_edge_resolves_every_api_domain(app_id):
    """deploy-edge.sh が各アプリの api-domain を SSM から読み、`{App}ApiDomain=` で渡す。

    ADD-APP-GUIDE「7. edge と deploy-edge.sh に 1 組を加える」を参照。

    スクリプトに 1 組を足し忘れると、edge のテンプレートに behavior があっても条件が成り立たず、
    `/api/{app}/*` が SPA へ落ちる。
    """
    script = (REPO_ROOT / DEPLOY_EDGE_PATH).read_text(encoding="utf-8")
    assert f"/apps/{app_id}/api-domain" in script
    assert f"{API_APPS[app_id]}ApiDomain=${{" in script
