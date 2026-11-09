#!/usr/bin/env python3
"""dev だけでメールを送らず E2E 利用者を作成・削除する。秘密はリポジトリ外の 600 の JSON に保存する。"""

import argparse
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.request

import admin_ops as ops
from botocore.exceptions import ClientError

from service_platform import ledger
from service_platform.ids import new_ulid

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE_URL = "https://service-platform-dev.example-cloud.com"
make_clients = ops.make_clients


class E2EError(Exception):
    """秘密を含まないエラー。"""


def validate_email(email: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.+_-]*@example\.com", email):
        raise E2EError("E2E の宛先は example.com だけを使用できます")
    return email


def check_config(config):
    if config.env != "dev" or config.ledger_table != "service-platform-core-ledger-dev":
        raise E2EError("E2E は dev の台帳だけを使用できます")


def secret_path(path: pathlib.Path) -> pathlib.Path:
    path = path.expanduser().absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise E2EError("秘密ファイルにシンボリックリンクは使用できません")
    path = path.resolve()
    if path.is_relative_to("/mnt"):
        raise E2EError("秘密ファイルは /mnt/ の下に置けません。Linux 側の専用ディレクトリを指定してください")
    if path.is_relative_to(REPO_ROOT):
        raise E2EError("秘密ファイルはリポジトリ外に置いてください")
    missing = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise E2EError("秘密ディレクトリの権限は 700 にしてください (既存の権限は変更しません)")
    return path


def save(path, state, *, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC) | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(state, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
        if stat.S_IMODE(os.fstat(output.fileno()).st_mode) != 0o600:
            raise E2EError("秘密ファイルの権限を 600 にできません。Cognito の操作を中止します")
        if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
            raise E2EError("秘密ディレクトリの権限は 700 にしてください。Cognito の操作を中止します")


def totp_code(secret):
    result = subprocess.run(  # noqa: S603 (固定の node コマンド。秘密は stdin)
        [
            shutil.which("node") or "/usr/bin/node",
            "--input-type=module",
            "-e",
            "import {generateSync} from 'otplib'; let s=''; for await(const c of process.stdin)s+=c; "
            "process.stdout.write(generateSync({secret:s.trim()}));",
        ],
        input=secret,
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT / "frontend",
    )
    return result.stdout.strip()


def password_sign_in(clients, config, user):
    result = clients.cognito.initiate_auth(
        ClientId=config.client_id,
        AuthFlow="USER_AUTH",
        AuthParameters={"USERNAME": user["email"], "PREFERRED_CHALLENGE": "PASSWORD", "PASSWORD": user["password"]},
    )
    if result.get("ChallengeName") == "SELECT_CHALLENGE":
        result = clients.cognito.respond_to_auth_challenge(
            ClientId=config.client_id,
            Session=result["Session"],
            ChallengeName="SELECT_CHALLENGE",
            ChallengeResponses={"USERNAME": user["email"], "ANSWER": "PASSWORD", "PASSWORD": user["password"]},
        )
    if "AuthenticationResult" not in result:
        raise E2EError("PASSWORD 認証が完了しませんでした")
    return result["AuthenticationResult"]


def enroll_admin(clients, config, user, persist):
    tokens = password_sign_in(clients, config, user)
    association = clients.cognito.associate_software_token(AccessToken=tokens["AccessToken"])
    user["totp_secret"] = association["SecretCode"]
    persist()
    code = totp_code(user["totp_secret"])
    user["totp_last_step"] = int(time.time()) // 30
    persist()
    verified = clients.cognito.verify_software_token(
        AccessToken=tokens["AccessToken"],
        UserCode=code,
    )
    if verified["Status"] != "SUCCESS":
        raise E2EError("TOTP の検証が完了しませんでした")
    clients.cognito.set_user_mfa_preference(
        AccessToken=tokens["AccessToken"],
        SoftwareTokenMfaSettings={"Enabled": True, "PreferredMfa": True},
    )
    request = urllib.request.Request(  # noqa: S310 (固定の dev URL)
        BASE_URL + "/api/base/me/totp-registered",
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer " + tokens["IdToken"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (固定の dev URL)
        if response.status != 200:
            raise E2EError("TOTP 登録完了 API が失敗しました")
    time.sleep(1.1)
    result = subprocess.run(  # noqa: S603 (引数に秘密なし)
        [
            sys.executable,
            str(REPO_ROOT / "scripts/admin_ops.py"),
            "activate-admin",
            "dev",
            user["email"],
            "--method",
            "other",
            "--yes",
            "--note",
            "dev E2E 専用利用者の TOTP 登録を自動確認",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise E2EError("activate-admin が失敗しました。台帳の状態を確認してください")


def create_suite(clients, config, manifest, *, enroll=True):
    check_config(config)
    manifest = secret_path(manifest)
    if manifest.exists():
        raise E2EError("秘密ファイルが既に存在します。先に delete して別ディレクトリで作成してください")
    run_id = secrets.token_hex(6)
    state = {"env": "dev", "run_id": run_id, "client_id": config.client_id, "users": {}, "deleted": False}
    save(manifest, state, exclusive=True)
    for kind in ("customer", "target", "admin"):
        user = {
            "email": validate_email(f"e2e-suite-{run_id}-{kind}@example.com"),
            "user_id": new_ulid(),
            "password": ops.generate_temporary_password(),
        }
        state["users"][kind] = user
        save(manifest, state)
        created = clients.cognito.admin_create_user(
            UserPoolId=config.user_pool_id,
            Username=user["email"],
            MessageAction="SUPPRESS",
            TemporaryPassword=user["password"],
            UserAttributes=[{"Name": "email", "Value": user["email"]}, {"Name": "email_verified", "Value": "true"}],
        )["User"]
        attributes = {a["Name"]: a["Value"] for a in created["Attributes"]}
        user.update(username=created["Username"], sub=attributes["sub"])
        save(manifest, state)
        clients.cognito.admin_set_user_password(
            UserPoolId=config.user_pool_id,
            Username=user["username"],
            Password=user["password"],
            Permanent=True,
        )
        at = ops.now_iso()
        profile = {
            "PK": ledger.user_pk(user["user_id"]),
            "SK": ledger.PROFILE_SK,
            "GSI1PK": ledger.USER_GSI1PK,
            "GSI1SK": ledger.user_gsi1sk(user["email"], user["user_id"]),
            "user_id": user["user_id"],
            "email": user["email"],
            "sub": user["sub"],
            "cognito_username": user["username"],
            "status": "active",
            "role": "admin" if kind == "admin" else "user",
            "kind": "employee" if kind == "admin" else "customer",
            "org_name": "E2E 検証用",
            "created_by": "script:e2e",
            "created_at": at,
            "updated_by": "script:e2e",
            "updated_at": at,
            "version": 1,
            "e2e_run_id": run_id,
        }
        if kind == "admin":
            profile["admin_state"] = "pending"
            clients.cognito.admin_add_user_to_group(
                UserPoolId=config.user_pool_id,
                Username=user["username"],
                GroupName="admin",
            )
        mapping = {"PK": ledger.sub_pk(user["sub"]), "SK": ledger.SUB_SK, "user_id": user["user_id"], "created_at": at}
        clients.dynamodb.transact_write_items(TransactItems=[ops.put(config, profile), ops.put(config, mapping)])
    if enroll:
        enroll_admin(clients, config, state["users"]["admin"], lambda: save(manifest, state))
    state["ready"] = True
    save(manifest, state)
    print("E2E 利用者を作成しました。秘密ファイル: " + str(manifest))


def delete_member_reverse(table, group_id, user_id, membership_id):
    """所属の逆引き (`GROUP#{group_id}` / `MEMBER#{user_id}`) を、同じ所属の ID のときだけ消す。グループの行は残す。"""
    try:
        table.delete_item(
            Key={"PK": ledger.group_pk(group_id), "SK": ledger.group_member_sk(user_id)},
            ConditionExpression="membership_id = :m",
            ExpressionAttributeValues={":m": membership_id},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def delete_suite(clients, config, manifest):
    check_config(config)
    manifest = secret_path(manifest)
    if stat.S_IMODE(manifest.stat().st_mode) != 0o600:
        raise E2EError("秘密ファイルの権限は 600 にしてください")
    state = json.loads(manifest.read_text())
    if state.get("env") != "dev" or not re.fullmatch(r"[0-9a-f]{12}", state.get("run_id", "")):
        raise E2EError("dev E2E の記録ではありません")
    if state.get("deleted"):
        print("E2E 利用者は削除済みです")
        return
    table = ledger._table()
    targets = []
    for kind, user in state["users"].items():
        expected = f"e2e-suite-{state['run_id']}-{kind}@example.com"
        if kind not in ("customer", "target", "admin") or validate_email(user["email"]) != expected:
            raise E2EError("E2E の作成記録と宛先が一致しません")
        if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", user["user_id"]):
            raise E2EError("E2E の利用者 ID が不正です")
        profile = ledger.get_profile(user["user_id"])
        if profile and (
            profile.get("e2e_run_id") != state["run_id"]
            or profile.get("email") != expected
            or profile.get("sub") != user.get("sub")
        ):
            raise E2EError("E2E 以外の利用者は削除できません")
        current = ops.get_cognito_user(clients, config, expected)
        if current and user.get("sub") and current.sub != user["sub"]:
            raise E2EError("Cognito の利用者が作成時と変わっています")
        if current and user.get("username") and current.username != user["username"]:
            raise E2EError("Cognito の利用者名が作成時と変わっています")
        rows = table.query(
            KeyConditionExpression=ops.Key("PK").eq(ledger.user_pk(user["user_id"])), ConsistentRead=True
        )
        if rows.get("LastEvaluatedKey"):
            raise E2EError("利用者の行が多すぎます。片付け前に確認してください")
        forward_rows = []
        for row in rows["Items"]:
            if not row["SK"].startswith("ASSIGN#"):
                continue
            if not row.get("ended_by") or not row.get("valid_to") or row["valid_to"] > ops.now_iso():
                raise E2EError("割り当てを終了してから delete してください")
            key = {
                "PK": ledger.dperiod_pk(row["device_period_id"]),
                "SK": ledger.assignment_sk(user["user_id"], row["assignment_id"]),
            }
            forward = table.get_item(Key=key, ConsistentRead=True).get("Item")
            if forward and (
                forward.get("user_id") != user["user_id"]
                or not forward.get("ended_by")
                or forward.get("valid_to") != row["valid_to"]
            ):
                raise E2EError("割り当ての正本と逆引きが一致しません")
            if forward:
                forward_rows.append(forward)
        targets.append((user, current, rows["Items"], forward_rows))
    for user, current, rows, forward_rows in targets:
        if current:
            clients.cognito.admin_delete_user(UserPoolId=config.user_pool_id, Username=current.username)
        for row in rows:
            if row["SK"].startswith(ledger.MEMBER_PREFIX) and row.get("group_id") and row.get("membership_id"):
                delete_member_reverse(table, row["group_id"], user["user_id"], row["membership_id"])
        for row in [*rows, *forward_rows]:
            table.delete_item(Key={"PK": row["PK"], "SK": row["SK"]})
        if user.get("sub"):
            try:
                table.delete_item(
                    Key={"PK": ledger.sub_pk(user["sub"]), "SK": ledger.SUB_SK},
                    ConditionExpression="user_id = :u",
                    ExpressionAttributeValues={":u": user["user_id"]},
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        admin_set = table.get_item(Key={"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK}, ConsistentRead=True).get(
            "Item"
        )
        if admin_set and user["user_id"] in admin_set.get("members", set()):
            table.update_item(
                Key={"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK},
                UpdateExpression="DELETE #m :m",
                ExpressionAttributeNames={"#m": "members"},
                ExpressionAttributeValues={":m": {user["user_id"]}},
            )
    state["deleted"] = True
    save(manifest, state)
    print("E2E 利用者を Cognito と台帳から削除しました")


def main(argv=None, *, clients=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "delete"))
    parser.add_argument("env", choices=("dev",))
    parser.add_argument(
        "--secrets-dir",
        type=pathlib.Path,
        required=True,
        help="秘密を置くリポジトリ外の専用ディレクトリ (700、/mnt/ 不可)",
    )
    args = parser.parse_args(argv)
    try:
        manifest = secret_path(args.secrets_dir / "e2e-users.json")
        clients = clients or make_clients("ap-northeast-1")
        config = ops.resolve_config(clients, "dev", "ap-northeast-1")
        if args.command == "create":
            create_suite(clients, config, manifest)
        else:
            delete_suite(clients, config, manifest)
        return 0
    except Exception as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        message = str(exc) if isinstance(exc, E2EError) else code
        print("E2E 処理に失敗しました: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
