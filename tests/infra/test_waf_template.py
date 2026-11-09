"""CloudFront 用 WAF のリージョン、Block、ログと SSM の検査。"""

import pytest


@pytest.fixture
def template(load_template):
    loaded = load_template("infra/waf/template.yaml")
    assert loaded is not None, "CloudFront 用 WAF のテンプレートが無い"
    return loaded


def test_acl_blocks_2000_requests_per_ip_in_five_minutes(template):
    resources = template["Resources"]
    acls = [r["Properties"] for r in resources.values() if r["Type"] == "AWS::WAFv2::WebACL"]
    assert len(acls) == 1
    acl = acls[0]
    assert acl["Scope"] == "CLOUDFRONT"
    assert acl["DefaultAction"] == {"Allow": {}}
    assert "Condition" not in resources["CloudFrontWebAcl"]
    assert len(acl["Rules"]) == 1
    rule = acl["Rules"][0]
    assert rule["Action"] == {"Block": {}}
    assert rule["Statement"] == {
        "RateBasedStatement": {"Limit": 2000, "EvaluationWindowSec": 300, "AggregateKeyType": "IP"}
    }
    for visibility in (acl["VisibilityConfig"], rule["VisibilityConfig"]):
        assert visibility["CloudWatchMetricsEnabled"] is True
        assert visibility["SampledRequestsEnabled"] is True
    assert not any(r["Type"] == "AWS::WAFv2::WebACLAssociation" for r in resources.values())


def test_logs_keep_only_count_and_block_for_365_days(template):
    resources = template["Resources"]
    logs = resources["CloudFrontWebAclLogGroup"]["Properties"]
    assert logs["LogGroupName"] == {"Fn::Sub": "aws-waf-logs-${ProjectName}-cf-${Env}"}
    assert logs["RetentionInDays"] == 365
    config = resources["CloudFrontWebAclLogging"]["Properties"]
    assert config["ResourceArn"] == {"Fn::GetAtt": "CloudFrontWebAcl.Arn"}
    assert config["LogDestinationConfigs"] == [
        {"Fn::Sub": "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:${CloudFrontWebAclLogGroup}"}
    ]
    assert config["LoggingFilter"] == {
        "DefaultBehavior": "DROP",
        "Filters": [
            {
                "Behavior": "KEEP",
                "Requirement": "MEETS_ANY",
                "Conditions": [{"ActionCondition": {"Action": "BLOCK"}}, {"ActionCondition": {"Action": "COUNT"}}],
            }
        ],
    }


def test_acl_arn_is_published_in_ssm_in_the_same_stack(template):
    parameters = [r["Properties"] for r in template["Resources"].values() if r["Type"] == "AWS::SSM::Parameter"]
    assert len(parameters) == 1
    assert parameters[0]["Type"] == "String"
    assert parameters[0]["Name"] == {"Fn::Sub": "/${ProjectName}/${Env}/waf/cloudfront-web-acl-arn"}
    assert parameters[0]["Value"] == {"Fn::GetAtt": "CloudFrontWebAcl.Arn"}
