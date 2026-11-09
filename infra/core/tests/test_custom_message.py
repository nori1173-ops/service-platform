"""`custom_message.app` のテスト（計画 3 章、ADR-001 D1）。

招待 (`CustomMessage_AdminCreateUser`) とメールのコード (`CustomMessage_Authentication`) の
件名と本文を日本語にし、Cognito が渡すプレースホルダを本文に入れる。ほかの triggerSource は触らない。
"""

import copy
import json
import re

import pytest
from custom_message.app import handler

SITE_URL = "https://example.com/"
JAPANESE = re.compile(r"[぀-ヿ]")
MAX_EMAIL_CHARS = 20_000
# 既定の `{####}` / `{username}` と違う値で、リクエストの値を使っていることを確かめる
CODE_PARAMETER = "{##code##}"
USERNAME_PARAMETER = "{##user##}"
EMAIL = "customer@example.com"
UNTOUCHED_SOURCES = [
    "CustomMessage_SignUp",
    "CustomMessage_ResendCode",
    "CustomMessage_ForgotPassword",
    "CustomMessage_UpdateUserAttribute",
    "CustomMessage_VerifyUserAttribute",
    "CustomMessage_Unknown",
]


@pytest.fixture(autouse=True)
def site_url(monkeypatch):
    monkeypatch.setenv("SITE_URL", SITE_URL)


def make_event(trigger_source: str, *, code: str | None = CODE_PARAMETER, username: str | None = None) -> dict:
    request = {"userAttributes": {"email": EMAIL, "email_verified": "true", "sub": "0000-sub"}}
    if code is not None:
        request["codeParameter"] = code
    if username is not None:
        request["usernameParameter"] = username
    return {
        "version": "1",
        "triggerSource": trigger_source,
        "region": "ap-northeast-1",
        "userPoolId": "ap-northeast-1_EXMPLpool1",
        "userName": "0000-sub",
        "callerContext": {"awsSdkVersion": "aws-sdk-unknown", "clientId": "example"},
        "request": request,
        "response": {"smsMessage": None, "emailMessage": None, "emailSubject": None},
    }


def visible_text(html: str) -> str:
    """HTML のコメントを除いた本文。"""
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def invite_event() -> dict:
    return make_event("CustomMessage_AdminCreateUser", username=USERNAME_PARAMETER)


def test_invitation_is_in_japanese_with_the_sign_in_page():
    result = handler(invite_event(), None)
    response = result["response"]

    assert "SERVICE-PLATFORM" in response["emailSubject"]
    assert JAPANESE.search(response["emailSubject"])
    assert JAPANESE.search(response["emailMessage"])
    assert SITE_URL in visible_text(response["emailMessage"])


@pytest.mark.parametrize(
    ("email", "encoded_email"),
    [
        (EMAIL, "customer%40example.com"),
        ("member+alerts&news@example.com", "member%2Balerts%26news%40example.com"),
        ("member/50%'@example.com", "member%2F50%25%27%40example.com"),
    ],
)
def test_invitation_link_prefills_the_encoded_email(email, encoded_email):
    event = invite_event()
    event["request"]["userAttributes"]["email"] = email

    message = handler(event, None)["response"]["emailMessage"]

    url = f"{SITE_URL}login#email={encoded_email}"
    assert f'<a href="{url}">{SITE_URL}login</a>' in message


@pytest.mark.parametrize(
    "configured_url",
    ["https://example.com", "https://example.com/", 'https://example.com/?a="b"&c=<d>'],
)
def test_invitation_link_joins_login_path(monkeypatch, configured_url):
    monkeypatch.setenv("SITE_URL", configured_url)

    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert '<a href="https://example.com/login#email=customer%40example.com">https://example.com/login</a>' in message


def test_invitation_instructions_explain_how_to_correct_the_email():
    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert (
        "開くと、メールアドレスが入った状態で表示されます。違っていたら、このメールのアドレスに直してください。</li>"
    ) in message


def test_invitation_asks_to_open_the_link_in_the_standard_browser():
    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert (
        "<li>スマホでは、リンクを標準のブラウザ (Safari や Chrome) で開いてください。"
        "メールのアプリでは、リンクを長押しして「リンクを開く」「Safari で開く」などを選びます。"
        "開くと、メールアドレスが入った状態で表示されます。"
        "違っていたら、このメールのアドレスに直してください。</li>"
    ) in message


@pytest.mark.parametrize("attributes", [None, {}, {"email": ""}, {"email": None}, {"sub": "0000-sub"}])
def test_invitation_without_email_keeps_the_original_link(attributes):
    event = invite_event()
    if attributes is None:
        event["request"].pop("userAttributes")
    else:
        event["request"]["userAttributes"] = attributes

    message = handler(event, None)["response"]["emailMessage"]

    assert f'<a href="{SITE_URL}">{SITE_URL}</a>' in message
    assert "#email=" not in message
    assert "サインインのページで、このメールのアドレスを入れてください。" in visible_text(message)


@pytest.mark.parametrize("configured_url", [None, ""])
def test_invitation_without_site_url_keeps_the_original_instructions(monkeypatch, configured_url):
    if configured_url is None:
        monkeypatch.delenv("SITE_URL")
    else:
        monkeypatch.setenv("SITE_URL", configured_url)

    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert "<a " not in message
    assert "#email=" not in message
    assert "サインインのページで、このメールのアドレスを入れてください。" in visible_text(message)
    assert "メールアドレスが入った状態" not in visible_text(message)
    assert "標準のブラウザ" not in visible_text(message)
    assert "長押し" not in visible_text(message)


def test_invitation_explains_delivery_delay_and_spam_folder():
    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert (
        "<li>画面の案内に従い、このアドレスに届く確認コードを入れてください。"
        "コードのメールは届くまで数分かかることがあります。"
        "迷惑メールのフォルダもご確認ください。</li>"
    ) in message


def test_invitation_includes_the_username_and_code_parameters():
    """招待の応答は usernameParameter と codeParameter の両方を含める必要がある（Cognito の仕様）。"""
    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert USERNAME_PARAMETER in visible_text(message)
    assert message.count(CODE_PARAMETER) == 1


def test_invitation_does_not_show_the_code_parameter():
    """お客様はパスワード無しで招待するので、一時パスワードの欄を見せない（ADR D1 の案 B）。"""
    message = handler(invite_event(), None)["response"]["emailMessage"]

    assert CODE_PARAMETER not in visible_text(message)


def test_sign_in_code_is_in_japanese_and_shows_the_code():
    result = handler(make_event("CustomMessage_Authentication"), None)
    response = result["response"]

    assert "SERVICE-PLATFORM" in response["emailSubject"]
    assert JAPANESE.search(response["emailSubject"])
    assert JAPANESE.search(response["emailMessage"])
    assert visible_text(response["emailMessage"]).count(CODE_PARAMETER) == 1


def test_sign_in_code_is_large_and_bold():
    message = handler(make_event("CustomMessage_Authentication"), None)["response"]["emailMessage"]

    assert f'<p style="font-size:20px;"><strong>{CODE_PARAMETER}</strong></p>' in message


def test_sign_in_code_explains_the_time_limit_and_resend():
    message = handler(make_event("CustomMessage_Authentication"), None)["response"]["emailMessage"]

    assert (
        "このコードは 10 分以内に入れてください。"
        "コードを送り直したときは、いちばん新しいメールのコードをお使いください。"
    ) in visible_text(message)


def test_sign_in_code_does_not_include_links():
    message = handler(make_event("CustomMessage_Authentication"), None)["response"]["emailMessage"]

    assert re.search(r"<a\b|href\s*=|https?://", visible_text(message), re.IGNORECASE) is None


@pytest.mark.parametrize("event_factory", [invite_event, lambda: make_event("CustomMessage_Authentication")])
def test_messages_fit_the_cognito_limit(event_factory):
    """20,000 文字を超えると Cognito は黙って既定の文面を送る。"""
    message = handler(event_factory(), None)["response"]["emailMessage"]

    assert len(message) < MAX_EMAIL_CHARS


@pytest.mark.parametrize("event_factory", [invite_event, lambda: make_event("CustomMessage_Authentication")])
def test_keeps_the_rest_of_the_event(event_factory):
    event = event_factory()
    before = copy.deepcopy(event)

    result = handler(event, None)

    assert {k: v for k, v in result.items() if k != "response"} == {k: v for k, v in before.items() if k != "response"}
    assert result["response"]["smsMessage"] is None


@pytest.mark.parametrize("trigger_source", UNTOUCHED_SOURCES)
def test_other_trigger_sources_are_left_alone(trigger_source):
    event = make_event(trigger_source, username=USERNAME_PARAMETER)
    before = copy.deepcopy(event)

    assert handler(event, None) == before


@pytest.mark.parametrize(
    "event",
    [
        make_event("CustomMessage_AdminCreateUser", code=None, username=USERNAME_PARAMETER),
        make_event("CustomMessage_AdminCreateUser", username=None),
        make_event("CustomMessage_Authentication", code=None),
    ],
)
def test_missing_placeholders_fall_back_to_the_default_message(event):
    """プレースホルダが無いと Cognito が文面を受け付けないので、既定の文面のままにする。"""
    before = copy.deepcopy(event)

    assert handler(event, None) == before


@pytest.mark.parametrize(
    ("site_url", "email", "escaped_url"),
    [
        ('https://example.com/?a="b"&c=<d>', "", "https://example.com/?a=&quot;b&quot;&amp;c=&lt;d&gt;"),
        ("https://example.com/team&support/", EMAIL, "https://example.com/team&amp;support/login"),
    ],
)
def test_site_url_is_escaped(monkeypatch, site_url, email, escaped_url):
    monkeypatch.setenv("SITE_URL", site_url)
    event = invite_event()
    event["request"]["userAttributes"]["email"] = email

    message = handler(event, None)["response"]["emailMessage"]

    href = f"{escaped_url}#email=customer%40example.com" if email else escaped_url
    assert f'<a href="{href}">{escaped_url}</a>' in message
    assert "<d>" not in message
    assert "team&support" not in message


@pytest.mark.parametrize("email", [EMAIL, "member+alerts&news@example.com"])
def test_logs_the_trigger_without_personal_data(capsys, email):
    """ログには triggerSource と差し替えたかだけを出す。メールアドレスや sub は出さない。"""
    event = invite_event()
    event["request"]["userAttributes"]["email"] = email
    handler(event, None)
    handler(make_event("CustomMessage_ForgotPassword"), None)

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert [(line["trigger_source"], line["customized"]) for line in lines] == [
        ("CustomMessage_AdminCreateUser", True),
        ("CustomMessage_ForgotPassword", False),
    ]
    text = json.dumps(lines)
    assert email not in text
    assert "email=" not in text
    assert "0000-sub" not in text
