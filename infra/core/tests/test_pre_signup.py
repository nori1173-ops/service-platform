"""`pre_signup.app` のテスト（ADR-001 D2、計画 3 章）。

招待 (AdminCreateUser) だけを通し、自己登録 (SignUp) と外部の IdP からの作成を拒む。
メールアドレスのドメインでは判定しない（お客様を招待するため）。
"""

import copy

import pytest
from pre_signup.app import handler


def make_event(trigger_source: str | None, email: str = "someone@example.com") -> dict:
    event = {"request": {"userAttributes": {"email": email}}, "response": {}}
    if trigger_source is not None:
        event["triggerSource"] = trigger_source
    return event


def test_allows_admin_create_user():
    event = make_event("PreSignUp_AdminCreateUser")

    assert handler(event, None) == event


@pytest.mark.parametrize("email", ["customer@example.com", "devA@example.co.jp", "a@mail.example.jp"])
def test_allows_invitations_to_any_domain(email):
    event = make_event("PreSignUp_AdminCreateUser", email)

    assert handler(event, None) == event


def test_does_not_auto_confirm_or_verify():
    """招待の検証済みは AdminCreateUser の `email_verified=true` で付ける。トリガーは応答を変えない。"""
    event = make_event("PreSignUp_AdminCreateUser")
    before = copy.deepcopy(event)

    assert handler(event, None)["response"] == before["response"]


def test_rejects_self_sign_up():
    with pytest.raises(Exception, match="sign-up"):
        handler(make_event("PreSignUp_SignUp"), None)


def test_rejects_external_provider():
    with pytest.raises(Exception, match="sign-up"):
        handler(make_event("PreSignUp_ExternalProvider"), None)


@pytest.mark.parametrize("trigger_source", [None, "", "PreSignUp_adminCreateUser"])
def test_rejects_unknown_trigger_sources(trigger_source):
    with pytest.raises(Exception, match="sign-up"):
        handler(make_event(trigger_source), None)
