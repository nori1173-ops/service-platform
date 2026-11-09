#!/usr/bin/env python3
"""Cognito の ID トークンを取得して標準出力へ 1 行で出す（手動の API 確認用。計画 4.2、判断 6）。

使い方::

    ID_TOKEN=$(uv run python scripts/get_id_token.py dev someone@example.com)
    ID_TOKEN=$(uv run python scripts/get_id_token.py dev someone@example.com --totp-code 123456)
    # 一時パスワード → 本パスワード (NEW_PASSWORD_REQUIRED)
    ID_TOKEN=$(uv run python scripts/get_id_token.py dev someone@example.com \\
        --password-file /path/to/temporary.txt --new-password-file /path/to/permanent.txt)

USER_AUTH (choice-based) の PASSWORD で始め、NEW_PASSWORD_REQUIRED と SOFTWARE_TOKEN_MFA の問いに答える。
User Pool ID と App Client ID は SSM（設計 4.2）から読み、SPA 用 App Client をそのまま使う。
パスワードは引数では受け取らない。`--password-file` (権限 600 のファイル)、環境変数 COGNITO_PASSWORD、
対話入力の順で読む。本パスワードは `--new-password-file` か環境変数 COGNITO_NEW_PASSWORD で渡す。
標準出力に出るのは ID トークンだけで、パスワード・アクセストークン・リフレッシュトークンは出さない。
"""

import argparse
import getpass
import os
import pathlib
import sys

import boto3

PROJECT = "service-platform"
VALID_ENVS = ("dev", "prod")
DEFAULT_REGION = "ap-northeast-1"
MAX_CHALLENGES = 5


class SignInError(Exception):
    """理由を標準エラーに出して止める (秘密を含めない)。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="get_id_token.py", description="Cognito の ID トークンを標準出力へ出す")
    parser.add_argument("env", help="dev または prod")
    parser.add_argument("email", help="サインインする利用者のメールアドレス")
    parser.add_argument(
        "--password-file", type=pathlib.Path, help="パスワード (一時パスワード) を 1 行で書いたファイル"
    )
    parser.add_argument(
        "--new-password-file", type=pathlib.Path, help="NEW_PASSWORD_REQUIRED で決める本パスワードのファイル"
    )
    parser.add_argument("--totp-code", default=None, help="TOTP の 6 桁のコード（省略時は問われたら対話入力）")
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", DEFAULT_REGION),
        help=f"SSM / Cognito のリージョン（既定 {DEFAULT_REGION}）",
    )
    return parser


def make_clients(region: str):
    return boto3.client("ssm", region_name=region), boto3.client("cognito-idp", region_name=region)


def read_secret(path: pathlib.Path | None, env_name: str, prompt: str | None) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8").strip()
    value = os.environ.get(env_name)
    if value:
        return value
    if prompt is None:
        return ""
    return getpass.getpass(prompt)


def answer(challenge: str, *, email: str, password: str, args) -> dict:
    if challenge == "SELECT_CHALLENGE":
        return {"USERNAME": email, "ANSWER": "PASSWORD", "PASSWORD": password}
    if challenge == "NEW_PASSWORD_REQUIRED":
        new_password = read_secret(args.new_password_file, "COGNITO_NEW_PASSWORD", None)
        if not new_password:
            raise SignInError(
                "本パスワードを決める必要があります。--new-password-file か COGNITO_NEW_PASSWORD で渡してください"
            )
        return {"USERNAME": email, "NEW_PASSWORD": new_password}
    if challenge == "SOFTWARE_TOKEN_MFA":
        if not args.totp_code:
            print("TOTP code: ", end="", file=sys.stderr, flush=True)
        code = (args.totp_code or input()).strip()
        if not code:
            raise SignInError("TOTP のコードが空です")
        return {"USERNAME": email, "SOFTWARE_TOKEN_MFA_CODE": code}
    raise SignInError(f"この問いには答えられません: {challenge}")


def sign_in(cognito, client_id: str, email: str, password: str, args) -> str:
    response = cognito.initiate_auth(
        AuthFlow="USER_AUTH",
        ClientId=client_id,
        AuthParameters={"USERNAME": email, "PREFERRED_CHALLENGE": "PASSWORD", "PASSWORD": password},
    )
    for _ in range(MAX_CHALLENGES):
        token = (response.get("AuthenticationResult") or {}).get("IdToken")
        if token:
            return token
        challenge = response.get("ChallengeName") or ""
        response = cognito.respond_to_auth_challenge(
            ClientId=client_id,
            ChallengeName=challenge,
            Session=response.get("Session"),
            ChallengeResponses=answer(challenge, email=email, password=password, args=args),
        )
    raise SignInError("ID トークンを取得できませんでした")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.env not in VALID_ENVS:
        parser.print_usage(sys.stderr)
        print(f"env は {' / '.join(VALID_ENVS)} のいずれかを指定してください", file=sys.stderr)
        return 1

    ssm, cognito = make_clients(args.region)
    client_id = ssm.get_parameter(Name=f"/{PROJECT}/{args.env}/cognito/client-id")["Parameter"]["Value"]
    password = read_secret(args.password_file, "COGNITO_PASSWORD", f"Password for {args.email}: ")
    if not password:
        print("パスワードが空です", file=sys.stderr)
        return 1
    try:
        token = sign_in(cognito, client_id, args.email, password, args)
    except SignInError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
