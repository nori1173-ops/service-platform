"""招待とメールのコードの文面（計画 3 章）。

Cognito は `emailMessage` を HTML として送るので、段落と改行は HTML で表す。差し込む
プレースホルダ (`{####}`・`{username}` など) は Cognito がリクエストで渡す値で、送る直前に
Cognito が本物の値に置き換える。置き換えを妨げないよう、プレースホルダはエスケープしない。
"""

import html
from urllib.parse import quote, urljoin

INVITE_SUBJECT = "【SERVICE-PLATFORM】ご利用のご案内"
SIGN_IN_CODE_SUBJECT = "【SERVICE-PLATFORM】サインインの確認コード"

FOOTER = (
    "<p>お心当たりのない場合は、このメールを破棄してください。</p>"
    "<p>このメールは送信専用のアドレスから送っています。返信はできません。<br>"
    "SERVICE-PLATFORM (統合サービス基盤)</p>"
)


def invite(*, username_parameter: str, code_parameter: str, site_url: str, email: str = "") -> tuple[str, str]:
    """招待の件名と本文。

    Cognito は招待の応答に codeParameter (一時パスワード) を含めることを求める。お客様は
    パスワード無しで招待し (ADR D1 の案 B)、admin の招待はメールを送らない (SUPPRESS) ので、
    見せる一時パスワードは無い。codeParameter は HTML のコメントに入れて、本文には出さない。
    """
    page = ""
    first_step = "サインインのページで、このメールのアドレスを入れてください。"
    if site_url:
        login_url = urljoin(site_url, "login") if email else site_url
        href = login_url
        if email:
            # メールアドレスをサーバのアクセスログに残さないため、クエリではなくフラグメントにする。
            href = f"{login_url}#email={quote(email, safe='')}"
            first_step = (
                "開くと、メールアドレスが入った状態で表示されます。違っていたら、このメールのアドレスに直してください。"
            )
        escaped_href = html.escape(href, quote=True)
        escaped_url = html.escape(login_url, quote=True)
        page = f'サインインのページ: <a href="{escaped_href}">{escaped_url}</a><br>'
        first_step = (
            "スマホでは、リンクを標準のブラウザ (Safari や Chrome) で開いてください。"
            "メールのアプリでは、リンクを長押しして「リンクを開く」「Safari で開く」などを選びます。"
            f"{first_step}"
        )
    body = (
        "<p>SERVICE-PLATFORM (統合サービス基盤) をご利用いただけるようになりました。</p>"
        f"<p>{page}ユーザー名: {username_parameter}</p>"
        "<p>はじめてのサインインの手順</p>"
        "<ol>"
        f"<li>{first_step}</li>"
        "<li>画面の案内に従い、このアドレスに届く確認コードを入れてください。"
        "コードのメールは届くまで数分かかることがあります。"
        "迷惑メールのフォルダもご確認ください。</li>"
        "<li>サインインした後、画面の案内に従ってパスキー (指紋・顔の認証や PIN) を登録してください。"
        "次からはパスキーでサインインできます。</li>"
        "</ol>"
        f"{FOOTER}"
        f"<!-- {code_parameter} -->"
    )
    return INVITE_SUBJECT, body


def sign_in_code(*, code_parameter: str) -> tuple[str, str]:
    """メールのコード (EMAIL_OTP) の件名と本文。"""
    # AuthSessionValidity (10 分): tests/infra/test_core_template.py・frontend/src/utils/authConstants.ts と同期。
    # なりすまし対策として、確認コードのメールにはリンクを入れない。
    body = (
        "<p>SERVICE-PLATFORM のサインインの確認コードです。</p>"
        f'<p style="font-size:20px;"><strong>{code_parameter}</strong></p>'
        "<p>サインインの画面にこのコードを入れてください。このコードをほかの人に教えないでください。</p>"
        "<p>このコードは 10 分以内に入れてください。"
        "コードを送り直したときは、いちばん新しいメールのコードをお使いください。</p>"
        "<p>サインインしようとしていないのにこのメールが届いた場合は、"
        "ほかの人があなたのメールアドレスでサインインしようとした可能性があります。</p>"
        f"{FOOTER}"
    )
    return SIGN_IN_CODE_SUBJECT, body
