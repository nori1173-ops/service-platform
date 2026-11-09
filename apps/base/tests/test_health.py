"""`GET /api/base/health` と共通の作りのテスト。"""


def test_health_returns_ok_for_a_registered_user(aws, call):
    aws.put_user()

    status, body = call("GET", "/health")

    assert status == 200
    assert body == {"ok": True, "data": {"status": "ok"}, "error": None, "meta": None}


def test_health_requires_a_registered_active_user(aws, call):
    """停止中でも呼べるのは例外 1・2 だけ (ADR D9)。health も台帳を確かめる。"""
    status, body = call("GET", "/health")
    assert status == 403
    assert body["error"]["code"] == "USER_NOT_REGISTERED"

    aws.put_user(status="suspended")
    status, body = call("GET", "/health")
    assert status == 403
    assert body["error"]["code"] == "USER_SUSPENDED"


def test_unknown_route_returns_404_envelope(aws, call):
    aws.put_user()

    status, body = call("GET", "/unknown")

    assert status == 404
    assert body["error"]["code"] == "NOT_FOUND"


def test_request_without_origin_verify_header_is_forbidden(aws, event, monkeypatch):
    from base_api.app import handler

    aws.put_user()
    monkeypatch.delenv("ORIGIN_VERIFY_SKIP")
    request = event("GET", "/health")
    request["headers"].pop("x-origin-verify")

    assert handler(request, None)["statusCode"] == 403


def test_warmer_bypasses_origin_auth_routing_and_business_logs(monkeypatch, caplog):
    from unittest.mock import Mock

    import base_api.app as module

    from service_platform import identity, origin_verify

    monkeypatch.delenv("ORIGIN_VERIFY_SKIP")
    monkeypatch.delenv("AWS_SAM_LOCAL", raising=False)
    forbidden = Mock(side_effect=AssertionError("ping が業務処理へ入った"))
    monkeypatch.setattr(module.app, "resolve", forbidden)
    monkeypatch.setattr(identity, "require_user", forbidden)
    monkeypatch.setattr(origin_verify, "_get_secret", forbidden)
    assert module.handler({"warmer": True, "slot": 0}, None) == {"warmer": True, "slot": 0}
    forbidden.assert_not_called()
    assert not caplog.records
