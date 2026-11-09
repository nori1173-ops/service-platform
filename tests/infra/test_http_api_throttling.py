"""全スタックの全 API に明示的なスロットリングを必須にする。"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted(
    str(p.relative_to(ROOT)) for parent in (ROOT / "apps", ROOT / "infra") for p in parent.glob("*/template.yaml")
)


@pytest.mark.parametrize("path", TEMPLATES)
def test_every_http_api_has_default_throttling(load_template, path):
    template = load_template(path)
    assert template is not None
    _assert_api_throttling(template)


def _assert_api_throttling(template):
    resources = template["Resources"]
    for name, resource in resources.items():
        resource_type = resource["Type"]
        properties = resource.get("Properties", {})
        if resource_type == "AWS::Serverless::Function":
            for event_name, event in properties.get("Events", {}).items():
                if event["Type"] == "HttpApi":
                    assert event.get("Properties", {}).get("ApiId"), f"{name}.{event_name}: 明示の HttpApi を使うこと"
            continue
        if resource_type == "AWS::Serverless::HttpApi":
            defaults = [properties.get("DefaultRouteSettings", {})]
        elif resource_type == "AWS::Serverless::Api":
            defaults = [
                settings
                for settings in properties.get("MethodSettings", [])
                if settings.get("HttpMethod") == "*" and settings.get("ResourcePath") == "/*"
            ]
        elif resource_type == "AWS::ApiGatewayV2::Api":
            defaults = [
                stage.get("Properties", {}).get("DefaultRouteSettings", {})
                for stage in resources.values()
                if stage["Type"] == "AWS::ApiGatewayV2::Stage"
                and stage.get("Properties", {}).get("ApiId") == {"Ref": name}
            ]
        else:
            continue
        assert defaults, f"{name}: スロットリングの既定設定がありません"
        for settings in defaults:
            assert settings.get("ThrottlingRateLimit") == 20, name
            assert settings.get("ThrottlingBurstLimit") == 40, name


@pytest.fixture
def check_template(tmp_path, load_template_file):
    def check(resources):
        path = tmp_path / "template.yaml"
        path.write_text(json.dumps({"Resources": resources}) + "\n", encoding="utf-8")
        _assert_api_throttling(load_template_file(path))

    return check


@pytest.mark.parametrize(
    "resource_type", ["AWS::Serverless::HttpApi", "AWS::Serverless::Api", "AWS::ApiGatewayV2::Api"]
)
def test_rejects_api_without_throttling(check_template, resource_type):
    with pytest.raises(AssertionError):
        check_template({"Api": {"Type": resource_type, "Properties": {}}})


@pytest.mark.parametrize("api_id", [None, ""])
def test_rejects_implicit_http_api_even_with_a_throttled_api(check_template, api_id):
    properties = {"Path": "/", "Method": "GET"}
    if api_id is not None:
        properties["ApiId"] = api_id
    resources = {
        "Api": {
            "Type": "AWS::Serverless::HttpApi",
            "Properties": {"DefaultRouteSettings": {"ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40}},
        },
        "Function": {
            "Type": "AWS::Serverless::Function",
            "Properties": {"Events": {"Request": {"Type": "HttpApi", "Properties": properties}}},
        },
    }
    with pytest.raises(AssertionError, match="明示の HttpApi を使うこと"):
        check_template(resources)


@pytest.fixture(params=["AWS::Serverless::HttpApi", "AWS::Serverless::Api", "AWS::ApiGatewayV2::Api"])
def throttled_resources(request):
    settings = {"ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40}
    resource_type = request.param
    resources = {"Api": {"Type": resource_type, "Properties": {}}}
    if resource_type == "AWS::Serverless::HttpApi":
        resources["Api"]["Properties"]["DefaultRouteSettings"] = settings
        resources["Function"] = {
            "Type": "AWS::Serverless::Function",
            "Properties": {
                "Events": {
                    "Request": {
                        "Type": "HttpApi",
                        "Properties": {"ApiId": {"Ref": "Api"}, "Path": "/", "Method": "GET"},
                    }
                }
            },
        }
    elif resource_type == "AWS::Serverless::Api":
        resources["Api"]["Properties"] = {
            "StageName": "dev",
            "MethodSettings": [{"HttpMethod": "*", "ResourcePath": "/*", **settings}],
        }
    else:
        resources["Api"]["Properties"]["ProtocolType"] = "HTTP"
        resources["Stage"] = {
            "Type": "AWS::ApiGatewayV2::Stage",
            "Properties": {"ApiId": {"Ref": "Api"}, "StageName": "$default", "DefaultRouteSettings": settings},
        }
    return resources


def test_accepts_api_with_default_throttling(check_template, throttled_resources):
    check_template(throttled_resources)


@pytest.mark.parametrize("key,value", [("ThrottlingRateLimit", None), ("ThrottlingBurstLimit", 80)])
def test_rejects_incomplete_or_incorrect_throttling(check_template, throttled_resources, key, value):
    for resource in throttled_resources.values():
        properties = resource["Properties"]
        if "DefaultRouteSettings" in properties:
            settings = properties["DefaultRouteSettings"]
        elif "MethodSettings" in properties:
            settings = properties["MethodSettings"][0]
        else:
            continue
        if value is None:
            del settings[key]
        else:
            settings[key] = value
    with pytest.raises(AssertionError):
        check_template(throttled_resources)


@pytest.mark.parametrize("key,value", [("HttpMethod", "GET"), ("ResourcePath", "/only")])
def test_rejects_rest_api_throttling_limited_to_one_method_or_path(check_template, key, value):
    settings = {"HttpMethod": "*", "ResourcePath": "/*", "ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40}
    settings[key] = value
    with pytest.raises(AssertionError):
        check_template({"Api": {"Type": "AWS::Serverless::Api", "Properties": {"MethodSettings": [settings]}}})


@pytest.mark.parametrize("other_api", [False, True])
def test_rejects_v2_api_with_an_unthrottled_stage(check_template, other_api):
    resources = {
        "Api": {"Type": "AWS::ApiGatewayV2::Api", "Properties": {"ProtocolType": "HTTP"}},
        "Stage": {
            "Type": "AWS::ApiGatewayV2::Stage",
            "Properties": {
                "ApiId": {"Ref": "OtherApi" if other_api else "Api"},
                "StageName": "$default",
                "DefaultRouteSettings": {"ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40},
            },
        },
    }
    if not other_api:
        resources["UnthrottledStage"] = {
            "Type": "AWS::ApiGatewayV2::Stage",
            "Properties": {"ApiId": {"Ref": "Api"}, "StageName": "dev"},
        }
    with pytest.raises(AssertionError):
        check_template(resources)
