"""`admin_users.cognito` の薄いラッパのテスト。"""

import pytest
from admin_users import cognito


def test_list_users_follows_pagination(aws, monkeypatch):
    """ページングを辿らないと 60 人目以降が歯抜けになる。"""
    monkeypatch.setattr(cognito, "PAGE_SIZE", 2)
    for index in range(5):
        aws.make_user(f"user{index}@example.com", ledger_row=False)

    assert len(cognito.list_users()) == 5


def test_admin_usernames_follows_pagination(aws, monkeypatch):
    monkeypatch.setattr(cognito, "PAGE_SIZE", 1)
    admins = [aws.make_admin(f"admin{index}@example.com") for index in range(3)]
    aws.make_user("user@example.com")

    assert cognito.admin_usernames() == {admin.username for admin in admins}


def test_get_user_returns_none_when_missing(aws):
    assert cognito.get_user("00000000-0000-0000-0000-210000000001") is None


def test_find_by_email_uses_the_alias(aws):
    user = aws.make_user("someone@example.com", ledger_row=False)

    found = cognito.find_by_email("someone@example.com")

    assert found["Username"] == user.username
    assert cognito.attribute(found, "sub") == user.sub


def test_create_passwordless_user_sends_the_invitation_by_email(aws):
    created = cognito.create_user("new@example.com", temporary_password=None)

    (request,) = aws.cognito.called("admin_create_user")
    assert request["DesiredDeliveryMediums"] == ["EMAIL"]
    assert "TemporaryPassword" not in request
    assert "MessageAction" not in request
    assert {"Name": "email_verified", "Value": "true"} in request["UserAttributes"]
    assert created["username"] != "new@example.com"
    assert created["sub"]


def test_create_admin_user_suppresses_the_invitation(aws):
    cognito.create_user("admin@example.com", temporary_password="Abcdefgh12345678")

    (request,) = aws.cognito.called("admin_create_user")
    assert request["MessageAction"] == "SUPPRESS"
    assert request["TemporaryPassword"] == "Abcdefgh12345678"


def test_delete_user_ignores_a_missing_user(aws):
    cognito.delete_user("00000000-0000-0000-0000-210000000001")


@pytest.mark.parametrize(
    "factors,expected",
    [(["PASSWORD", "WEB_AUTHN"], {"password": True, "web_authn": True, "email_otp": False})],
)
def test_auth_factors(aws, factors, expected):
    user = aws.make_user("someone@example.com", ledger_row=False)
    aws.cognito.factors[user.username] = factors

    assert cognito.auth_factors(user.username) == expected


def test_auth_factors_of_a_missing_user_is_none(aws):
    assert cognito.auth_factors("00000000-0000-0000-0000-210000000001") is None
