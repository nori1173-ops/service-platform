#!/usr/bin/env python3
"""admin の作成・有効化・緊急の復旧と、ユーザープールの web ACL の確認（ADR-001 D9、計画 4.1）。

使い方::

    uv run python scripts/admin_ops.py create-admin dev someone@example.com [--output-dir DIR]
    uv run python scripts/admin_ops.py show-admin dev someone@example.com
    uv run python scripts/admin_ops.py enable-passkey-mfa dev someone@example.com
    uv run python scripts/admin_ops.py activate-admin dev someone@example.com --method phone [--note "..."] [--yes]
    uv run python scripts/admin_ops.py emergency-reinvite dev someone@example.com --reason "..." [--output-dir DIR]
    uv run python scripts/admin_ops.py check-waf dev

- ユーザープール・台帳・監査の名前は SSM (設計 4.2) から読む。
- 一時パスワードは引数・標準出力・ログ・台帳・監査に出さない。リポジトリの外の権限 600 の
  ファイルに書き、パスだけを示す (既定は権限 700 の新しい一時ディレクトリ)。社内の別経路で本人に渡す。
- 台帳と監査の書き込みは 1 つのトランザクションにまとめる (計画 2.1)。監査の主体は `ACTOR#script` で、
  実行した IAM の主体 (STS GetCallerIdentity の ARN) を `detail` に、ARN の末尾の名前を台帳の
  `*_by` (`script:{名前}`) に残す。
- 一時パスワードのファイルの置き場は、Cognito や台帳を変える前に確かめ、空のファイルを作っておく。
- 台帳のトランザクションには ClientRequestToken を付け、結果不明のときは同じ要求を送り直す。それでも
  確定できなければ、補償 (作った利用者の削除) も一時パスワードのファイルの削除もせずに止める。補償するのは、
  確定した失敗が返り、台帳を読み直しても反映されていないときだけ。emergency-reinvite は同じ `--output-dir` で
  流し直すと、状態のファイル (reinvite_state.py) と Cognito と台帳の今の状態から続きの段を決めて進む。
- 台帳の利用者の行の更新は、読んだ version と sub を条件にし、操作 ID を `last_op_id` に書く (反映の確認に使う)。
  activate-admin は有効な admin の集合 (ADMIN#SET) に入れ、emergency-reinvite は開始と付け替えの両方で外す。
  emergency-reinvite は `reinvite_in_progress` が無ければ必ず開始の段から書く。
- prod は実行の前に確認を求める。
"""

import argparse
import base64
import json
import os
import pathlib
import re
import secrets
import string
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "shared" / "layers"))

from reinvite_state import ReinviteState  # noqa: E402

from service_platform import audit, cognito_extra, ledger  # noqa: E402
from service_platform.ids import new_ulid  # noqa: E402

PROJECT = "service-platform"
VALID_ENVS = ("dev", "prod")
DEFAULT_REGION = "ap-northeast-1"
ADMIN_GROUP = "admin"
SCRIPT_ACTOR = "script"
SERVICE = "scripts"
TOTP = "SOFTWARE_TOKEN_MFA"
FORCE_CHANGE_PASSWORD = "FORCE_CHANGE_PASSWORD"  # noqa: S105 (Cognito の利用者の状態の名前)
VERIFY_METHODS = ("phone", "in_person", "video", "other")
MAX_TEXT_LENGTH = 200
PASSWORD_LENGTH = 20
LEDGER_CONDITION = "attribute_not_exists(PK)"
ACCESS_TOKEN_ENV = "SERVICE_PLATFORM_ACCESS_TOKEN"  # noqa: S105 (環境変数の名前)
# WAF で止まる確認にだけ使う。止まらなければ試験用の利用者ごと消す
WAF_CHECK_PASSWORD = "Waf-Check-Password-1"  # noqa: S105
WAF_BLOCK_MARKER = "WAF"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ADMIN_VERIFIED_FIELDS = ("admin_verified_by", "admin_verified_at", "admin_verified_method", "admin_verified_note")
# 再招待の途中 (新しい sub への付け替えの前)。admin-users の restore / admin-activation と activate-admin が断る
REINVITE_IN_PROGRESS = "reinvite_in_progress"
# 台帳のトランザクションを同じ ClientRequestToken で送り直す間隔 (秒)
RETRY_DELAYS = (2, 5, 10)
# 処理されていないか処理中で、同じ token で送り直してよい (確定した失敗ではない) エラー
RETRYABLE_CODES = frozenset({"TransactionInProgressException", "ThrottlingException", "RequestLimitExceeded"})

_serializer = TypeSerializer()
retry_sleep = time.sleep


class OpsError(Exception):
    """利用者に理由を示して止める。"""


class UnresolvedError(OpsError):
    """成否を確定できずに止める。補償も一時パスワードのファイルの削除もしない。"""


@dataclass
class Clients:
    cognito: object
    dynamodb: object
    ssm: object
    sts: object
    wafv2: object


@dataclass(frozen=True)
class Config:
    env: str
    region: str
    user_pool_id: str
    client_id: str
    ledger_table: str
    audit_table: str


@dataclass(frozen=True)
class CognitoUser:
    username: str
    sub: str
    enabled: bool
    mfa: tuple[str, ...]
    status: str


def make_clients(region: str) -> Clients:
    boto3.setup_default_session(region_name=region)
    return Clients(
        cognito=boto3.client("cognito-idp"),
        dynamodb=boto3.client("dynamodb"),
        ssm=boto3.client("ssm"),
        sts=boto3.client("sts"),
        wafv2=boto3.client("wafv2"),
    )


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")


# =============================================================================
# 設定と読み取り
# =============================================================================


def resolve_config(clients: Clients, env: str, region: str) -> Config:
    """SSM から名前を読み、shared の台帳・監査が読む環境変数にも置く。"""

    def parameter(key: str) -> str:
        return clients.ssm.get_parameter(Name=f"/{PROJECT}/{env}/{key}")["Parameter"]["Value"]

    config = Config(
        env=env,
        region=region,
        user_pool_id=parameter("cognito/user-pool-id"),
        client_id=parameter("cognito/client-id"),
        ledger_table=parameter("ledger/table-name"),
        audit_table=parameter("audit/table-name"),
    )
    os.environ[ledger.LEDGER_TABLE_ENV] = config.ledger_table
    os.environ[audit.AUDIT_TABLE_ENV] = config.audit_table
    ledger.reset_clients()
    audit.reset_clients()
    return config


def caller_arn(clients: Clients) -> str:
    return clients.sts.get_caller_identity()["Arn"]


def get_cognito_user(clients: Clients, config: Config, email: str) -> CognitoUser | None:
    try:
        user = clients.cognito.admin_get_user(UserPoolId=config.user_pool_id, Username=email)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "UserNotFoundException":
            return None
        raise
    attributes = {a["Name"]: a["Value"] for a in user.get("UserAttributes") or []}
    return CognitoUser(
        username=user["Username"],
        sub=attributes["sub"],
        enabled=bool(user.get("Enabled")),
        mfa=tuple(user.get("UserMFASettingList") or ()),
        status=user.get("UserStatus", ""),
    )


def groups_of(clients: Clients, config: Config, username: str) -> list[str]:
    groups = clients.cognito.admin_list_groups_for_user(UserPoolId=config.user_pool_id, Username=username)
    return [group["GroupName"] for group in groups.get("Groups") or []]


def profiles_by_email(email: str) -> list[dict]:
    """台帳の GSI1 で同じメールアドレスの利用者を探す（結果整合。重複の確認と再開のためだけ）。"""
    table = ledger._table()
    response = table.query(
        IndexName=ledger.GSI1,
        KeyConditionExpression=Key("GSI1PK").eq(ledger.USER_GSI1PK)
        & Key("GSI1SK").begins_with(f"{ledger.normalize_email(email)}#"),
    )
    return response.get("Items") or []


def profile_for_sub(sub: str) -> dict:
    try:
        user = ledger.resolve_user(sub, allow_suspended=True)
    except ledger.ForbiddenError as exc:
        raise OpsError(f"台帳で利用者を引けません ({getattr(exc, 'code', 'FORBIDDEN')})") from exc
    profile = ledger.get_profile(user.user_id)
    if profile is None:
        raise OpsError("台帳に利用者の行がありません")
    return profile


def format_mfa_time(value) -> str:
    if value is None:
        return "未登録"
    try:
        return datetime.fromtimestamp(float(value), timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S JST")
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise OpsError("mfa_enabled_at が有効な時刻ではありません") from exc


def print_admin_state(profile: dict | None, user: CognitoUser | None) -> None:
    if profile is None:
        print("PROFILE: 利用者なし (email の GSI1 は結果整合。反映を待って再取得する)")
    else:
        print(f"  user_id               : {profile['user_id']}")
        for field in ("status", "role", "admin_state", REINVITE_IN_PROGRESS, "last_op_id"):
            print(f"  {field:<22}: {profile.get(field, 'なし')}")
        print(f"  mfa_enabled_at        : {format_mfa_time(profile.get('mfa_enabled_at'))}")
    if user is None:
        print("Cognito: 利用者なし")
    else:
        print(f"  Enabled               : {user.enabled}")
        print(f"  UserStatus            : {user.status}")
        print(f"  UserMFASettingList    : {', '.join(user.mfa) or 'なし'}")


def show_admin(clients: Clients, config: Config, args, _confirm: Callable[[str], bool]) -> int:
    user = get_cognito_user(clients, config, args.email)
    mapping = None
    profiles = {}
    if user:
        mapping = (
            ledger._table()
            .get_item(Key={"PK": ledger.sub_pk(user.sub), "SK": ledger.SUB_SK}, ConsistentRead=True)
            .get("Item")
        )
        if mapping:
            profile = ledger.get_profile(mapping["user_id"])
            if profile:
                profiles[profile["user_id"]] = profile
    for row in profiles_by_email(args.email):
        profile = ledger.get_profile(row["user_id"])
        if profile and ledger.normalize_email(profile.get("email", "")) == args.email:
            profiles[profile["user_id"]] = profile
    if len(profiles) > 1:
        raise OpsError("同じ email に複数の PROFILE があります。更新せず台帳を調べてください")
    print_admin_state(next(iter(profiles.values()), None), user)
    if user:
        print(f"SUB: {'対応あり' if mapping else '対応なし'}")
        if mapping and mapping.get("revoked_at"):
            print("SUB: 無効の印あり")
    return 0


# =============================================================================
# 書き込みの部品
# =============================================================================


def script_actor(arn: str) -> str:
    """台帳の `*_by` に残す名前。実行した IAM の主体の ARN の末尾 (`script:{名前}`)。"""
    name = arn.rsplit("/", 1)[-1] if "/" in arn else arn.rsplit(":", 1)[-1]
    return f"{SCRIPT_ACTOR}:{name}"


def is_outcome_unknown(exc: BaseException) -> bool:
    """AWS 側で処理されたかどうか分からない失敗か (応答を失った、接続が切れた、5xx、確定できなかった)。"""
    if isinstance(exc, UnresolvedError):
        return True
    if isinstance(exc, ClientError):
        return ((exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0) >= 500
    return isinstance(exc, HTTPClientError | BotoConnectionError)


def serialize(item: dict) -> dict:
    return {key: _serializer.serialize(value) for key, value in item.items()}


def put(config: Config, item: dict) -> dict:
    return {"Put": {"TableName": config.ledger_table, "Item": serialize(item), "ConditionExpression": LEDGER_CONDITION}}


def update(config: Config, pk: str, sk: str, expression: str, condition: str, values: dict, names=None) -> dict:
    body = {
        "TableName": config.ledger_table,
        "Key": serialize({"PK": pk, "SK": sk}),
        "UpdateExpression": expression,
        "ConditionExpression": condition,
        "ExpressionAttributeValues": serialize(values),
    }
    if names:
        body["ExpressionAttributeNames"] = names
    return {"Update": body}


def admin_set(config: Config, user_id: str, action: str) -> dict:
    """有効な admin の集合 (ADMIN#SET の members) に入れる (ADD) か外す (DELETE)。

    admin-users は最後の 1 人を残す条件を付けるが、scripts は緊急の復旧で最後の 1 人も外せるよう条件を付けない。
    """
    return {
        "Update": {
            "TableName": config.ledger_table,
            "Key": serialize({"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK}),
            "UpdateExpression": f"{action} #m :m",
            "ExpressionAttributeNames": {"#m": ledger.ADMIN_MEMBERS},
            "ExpressionAttributeValues": serialize({":m": {user_id}}),
        }
    }


def last_op_of(user_id: str) -> str | None:
    """利用者の行の `last_op_id` (強い整合)。台帳の 1 段が反映されたかを操作 ID で確かめる。"""
    return (ledger.get_profile(user_id) or {}).get("last_op_id")


def op_of(items: list[dict]) -> str:
    """段の要求 (最初の Update) に入れた操作 ID。状態のファイルから送り直す要求でも同じ値になる。"""
    return items[0]["Update"]["ExpressionAttributeValues"][":op"]["S"]


def is_retryable(exc: BaseException) -> bool:
    if is_outcome_unknown(exc):
        return True
    return isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in RETRYABLE_CODES


def new_token() -> str:
    return uuid.uuid4().hex


def transact_checked(clients: Clients, items: list[dict], *, token: str, applied: Callable[[], bool]) -> None:
    """台帳と監査を 1 つのトランザクションで書く。

    応答を失った・処理中・5xx・接続の失敗のときは、同じ ClientRequestToken で同じ要求を最大 3 回
    送り直す (DynamoDB は 10 分の間、同じ token の同じ要求を冪等に扱う)。全部が結果不明なら
    `UnresolvedError`。確定した失敗が返ったら台帳を読み直し、反映されていれば成功、されていなければ
    その失敗を投げる (呼び出し元が補償する)。読み直しが失敗したら `UnresolvedError`。
    """
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            clients.dynamodb.transact_write_items(TransactItems=items, ClientRequestToken=token)
            return
        except Exception as exc:
            if not is_retryable(exc):
                failure = exc
                break
            if attempt == len(RETRY_DELAYS):
                raise UnresolvedError("台帳の書き込みの結果を、同じ要求の再試行でも確定できません") from exc
            retry_sleep(RETRY_DELAYS[attempt])
    try:
        done = applied()
    except Exception as exc:
        raise UnresolvedError("台帳の書き込みが失敗を返し、反映を確かめる読み取りもできません") from exc
    if done:
        print("台帳の書き込みは失敗を返しましたが、読み直すと反映されていました", file=sys.stderr)
        return
    raise failure


def ledger_maps(sub: str, user_id: str) -> bool:
    """sub が台帳の利用者 user_id の今の sub として引けるか（強い整合）。"""
    try:
        return ledger.resolve_user(sub, allow_suspended=True).user_id == user_id
    except ledger.ForbiddenError:
        return False


def generate_temporary_password(length: int = PASSWORD_LENGTH) -> str:
    """英大小・数字を必ず含む一時パスワード（core の PasswordPolicy を満たす）。"""
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


def check_outside_repo(directory: pathlib.Path) -> pathlib.Path:
    resolved = directory.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise OpsError("一時パスワードのファイルをリポジトリの中には置けません")
    return resolved


def prepare_secret(*, output_dir: pathlib.Path | None, env: str) -> pathlib.Path:
    """一時パスワードのファイルを、Cognito や台帳を変える前に空で作る (権限 600、既存のファイルは使わない)。

    既定の置き場は権限 700 の新しい一時ディレクトリ。リポジトリの中と、書けない場所は拒む。
    """
    if output_dir is None:
        directory = pathlib.Path(tempfile.mkdtemp(prefix="service-platform-admin-"))
    else:
        directory = check_outside_repo(output_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"temporary-password-{env}-{stamp}-{secrets.token_hex(3)}.txt"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise
    except OSError as exc:
        raise OpsError(f"一時パスワードのファイルを {directory} に作れません ({exc.strerror})") from exc
    os.close(fd)
    os.chmod(path, 0o600)
    return path


def fill_secret(path: pathlib.Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{value}\n")
    os.chmod(path, 0o600)


def abandon_secret(path: pathlib.Path, exc: BaseException) -> None:
    """失敗したときの一時パスワードのファイルの扱い。結果不明なら消さずに残す。"""
    if is_outcome_unknown(exc):
        print(
            "AWS の応答を失うなどして、結果が分かりません。状態を確かめてから、同じ --output-dir "
            f"({path.parent}) を付けて同じコマンドを流し直してください。一時パスワードのファイルは残しました: {path}",
            file=sys.stderr,
        )
        return
    if path.exists():
        path.unlink()


def create_cognito_admin(clients: Clients, config: Config, email: str, password: str) -> CognitoUser:
    response = clients.cognito.admin_create_user(
        UserPoolId=config.user_pool_id,
        Username=email,
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        TemporaryPassword=password,
        MessageAction="SUPPRESS",
    )
    user = response["User"]
    attributes = {a["Name"]: a["Value"] for a in user.get("Attributes") or []}
    return CognitoUser(
        username=user["Username"], sub=attributes["sub"], enabled=True, mfa=(), status=user.get("UserStatus", "")
    )


def delete_cognito_user(clients: Clients, config: Config, username: str) -> None:
    """補償の削除。消えたと確かめられなければ `UnresolvedError` (一時パスワードのファイルを残す)。"""
    try:
        clients.cognito.admin_delete_user(UserPoolId=config.user_pool_id, Username=username)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "UserNotFoundException":
            return
        raise UnresolvedError(
            f"補償の削除が失敗しました ({exc.response.get('Error', {}).get('Code')})。"
            "Cognito に利用者が残っている可能性があります。手で確かめてください"
        ) from exc
    except BotoCoreError as exc:
        raise UnresolvedError(
            f"補償の削除の結果が分かりません ({type(exc).__name__})。"
            "Cognito に利用者が残っている可能性があります。手で確かめてください"
        ) from exc


def add_to_admin_group(clients: Clients, config: Config, username: str) -> None:
    clients.cognito.admin_add_user_to_group(UserPoolId=config.user_pool_id, Username=username, GroupName=ADMIN_GROUP)


def confirm_prod(config: Config, confirm: Callable[[str], bool], message: str) -> None:
    if config.env == "prod" and not confirm(f"prod の {message}。続けますか?"):
        raise OpsError("中止しました")


def script_audit(action: str, config: Config, *, user_id: str, arn: str, **detail) -> dict:
    return audit.put_item_for_transaction(
        action,
        actor=SCRIPT_ACTOR,
        service=SERVICE,
        target_user_id=user_id,
        detail={"caller_arn": arn, "env": config.env, **detail},
    )


# =============================================================================
# create-admin
# =============================================================================


def create_admin(clients: Clients, config: Config, args, confirm: Callable[[str], bool]) -> int:
    email = args.email
    arn = caller_arn(clients)
    if get_cognito_user(clients, config, email) is not None:
        raise OpsError("同じメールアドレスの利用者が Cognito にいます")
    if profiles_by_email(email):
        raise OpsError("同じメールアドレスの利用者が台帳にいます")
    confirm_prod(config, confirm, f"{email} を admin (準備中) として作ります")

    secret_path = prepare_secret(output_dir=args.output_dir, env=config.env)
    user_id = new_ulid()
    try:
        password = generate_temporary_password()
        fill_secret(secret_path, password)
        user = create_cognito_admin(clients, config, email, password)
        try:
            add_to_admin_group(clients, config, user.username)
            transact_checked(
                clients,
                new_admin_items(config, user_id=user_id, email=email, user=user, arn=arn),
                token=new_token(),
                applied=lambda: ledger_maps(user.sub, user_id),
            )
        except Exception as exc:
            if not is_outcome_unknown(exc):
                delete_cognito_user(clients, config, user.username)
            raise
    except Exception as exc:
        abandon_secret(secret_path, exc)
        raise

    print("admin を作りました (準備中。本人の TOTP の登録と別経路の確認の後に activate-admin で有効にする)")
    print(f"  Env       : {config.env}")
    print(f"  user_id   : {user_id}")
    print(f"  Email     : {email}")
    print(f"  一時パスワードのファイル (社内の別経路で渡し、渡したら消す): {secret_path}")
    return 0


def new_admin_items(config: Config, *, user_id: str, email: str, user: CognitoUser, arn: str) -> list[dict]:
    at = now_iso()
    by = script_actor(arn)
    profile = {
        "PK": ledger.user_pk(user_id),
        "SK": ledger.PROFILE_SK,
        "GSI1PK": ledger.USER_GSI1PK,
        "GSI1SK": ledger.user_gsi1sk(email, user_id),
        "user_id": user_id,
        "cognito_username": user.username,
        "sub": user.sub,
        "email": email,
        "status": ledger.STATUS_ACTIVE,
        "kind": ledger.KIND_EMPLOYEE,
        "role": ledger.ROLE_ADMIN,
        "admin_state": ledger.ADMIN_PENDING,
        "created_by": by,
        "created_at": at,
        "updated_by": by,
        "updated_at": at,
        "version": 1,
    }
    mapping = {"PK": ledger.sub_pk(user.sub), "SK": ledger.SUB_SK, "user_id": user_id, "created_at": at}
    return [
        put(config, profile),
        put(config, mapping),
        script_audit("script.create_admin", config, user_id=user_id, arn=arn),
    ]


# =============================================================================
# activate-admin
# =============================================================================


def check_cognito_admin(clients: Clients, config: Config, email: str) -> CognitoUser:
    user = get_cognito_user(clients, config, email)
    if user is None:
        raise OpsError("Cognito に利用者がいません")
    if not user.enabled:
        raise OpsError("Cognito で無効になっています")
    if ADMIN_GROUP not in groups_of(clients, config, user.username):
        raise OpsError("admin グループに入っていません")
    if TOTP not in user.mfa:
        raise OpsError("TOTP が登録されていません (本人がアカウントの画面で登録してから流す)")
    return user


def enable_passkey_mfa(clients: Clients, config: Config, args, confirm: Callable[[str], bool]) -> int:
    """既に有効な admin にパスキー MFA を足す。登録時刻と台帳の状態は変えない。"""
    user = check_cognito_admin(clients, config, args.email)
    profile = profile_for_sub(user.sub)
    if (
        profile.get("role") != ledger.ROLE_ADMIN
        or profile.get("status") != ledger.STATUS_ACTIVE
        or profile.get("admin_state") != ledger.ADMIN_ACTIVE
        or profile.get("mfa_enabled_at") is None
        or profile.get(REINVITE_IN_PROGRESS)
    ):
        raise OpsError("TOTP の登録と有効化を終えた admin だけが対象です")
    factors = cognito_extra.admin_get_user_auth_factors(clients.cognito, config.user_pool_id, user.username)
    if not cognito_extra.factor_flags(factors)["web_authn"]:
        raise OpsError("パスキーが登録されていません (本人がアカウントの画面で登録してから流す)")
    confirm_prod(config, confirm, "admin のパスキー MFA を有効にします")
    arn = caller_arn(clients)
    item = script_audit("script.enable_passkey_mfa", config, user_id=profile["user_id"], arn=arn, step="requested")
    transact_checked(clients, [item], token=new_token(), applied=lambda: False)
    cognito_extra.admin_enable_passkey_mfa(clients.cognito, config.user_pool_id, user.username)
    item = script_audit("script.enable_passkey_mfa", config, user_id=profile["user_id"], arn=arn, step="enabled")
    transact_checked(clients, [item], token=new_token(), applied=lambda: False)
    print("パスキー MFA を有効にしました。TOTP は有効・優先のままです")
    return 0


def activate_admin(clients: Clients, config: Config, args, confirm: Callable[[str], bool]) -> int:
    email = args.email
    arn = caller_arn(clients)
    user = check_cognito_admin(clients, config, email)
    profile = profile_for_sub(user.sub)
    if profile.get("role") != ledger.ROLE_ADMIN:
        raise OpsError("台帳の役割が admin ではありません")
    if profile.get("admin_state") != ledger.ADMIN_AWAITING:
        raise OpsError(f"admin の状態が awaiting (確認待ち) ではありません: {profile.get('admin_state')}")
    if profile.get("mfa_enabled_at") is None:
        raise OpsError("台帳に mfa_enabled_at がありません (本人の TOTP の登録が終わっていない)")
    if profile.get(REINVITE_IN_PROGRESS):
        raise OpsError("再招待が終わっていません (emergency-reinvite を同じ --output-dir で流し直す)")
    restore = is_reinvite_in_progress(profile)
    print_admin_state(profile, user)
    prompt = f"{email}: 本人に別経路で登録した時刻を確かめたか"
    if config.env == "prod":
        confirm_prod(config, confirm, prompt)
    elif not args.yes and not confirm(prompt):
        raise OpsError("有効化を中止しました")

    at = now_iso()
    expression = (
        "SET admin_state = :active, admin_verified_by = :by, admin_verified_at = :at, "
        "admin_verified_method = :method, updated_by = :by, updated_at = :at, last_op_id = :op, "
        "version = version + :one"
    )
    op_id = new_ulid()
    values = {
        ":op": op_id,
        ":active": ledger.ADMIN_ACTIVE,
        ":awaiting": ledger.ADMIN_AWAITING,
        ":by": script_actor(arn),
        ":at": at,
        ":method": args.method,
        ":one": 1,
        ":sub": user.sub,
        ":version": profile["version"],
    }
    if args.note:
        expression += ", admin_verified_note = :note"
        values[":note"] = args.note
    if restore:
        expression += ", #status = :status_active REMOVE restore_requires"
        values[":status_active"] = ledger.STATUS_ACTIVE
    profile_update = update(
        config,
        profile["PK"],
        ledger.PROFILE_SK,
        expression,
        "admin_state = :awaiting AND #sub = :sub AND version = :version",
        values,
        names={"#sub": "sub", **({"#status": "status"} if restore else {})},
    )
    transact_checked(
        clients,
        [
            profile_update,
            admin_set(config, profile["user_id"], "ADD"),
            script_audit(
                "script.activate_admin",
                config,
                user_id=profile["user_id"],
                arn=arn,
                method=args.method,
                restored=restore,
            ),
        ],
        token=new_token(),
        applied=lambda: last_op_of(profile["user_id"]) == op_id,
    )
    print("admin を有効にしました。本人がパスワード + TOTP でサインインし直すと admin の操作が通ります")
    print(f"  user_id   : {profile['user_id']}")
    print(f"  利用者の台帳も有効に戻した: {'はい' if restore else 'いいえ'}")
    return 0


# =============================================================================
# emergency-reinvite（ADR D9 の緊急の復旧の手順 2）
# =============================================================================


@dataclass(frozen=True)
class ReinviteTarget:
    """再招待の対象と、どの段から続けるか。"""

    profile: dict
    # 台帳の今の sub の Cognito の利用者。まだ消していなければある
    old_user: CognitoUser | None
    # 前の回に作り直し、台帳がまだ指していない Cognito の利用者
    new_user: CognitoUser | None


def is_reinvite_in_progress(profile: dict) -> bool:
    return profile.get("status") == ledger.STATUS_SUSPENDED and profile.get("restore_requires") == (
        ledger.RESTORE_REINVITE
    )


def sub_mapping(sub: str) -> dict | None:
    key = {"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK}
    return ledger._table().get_item(Key=key, ConsistentRead=True).get("Item")


def single_profile_by_email(email: str) -> dict:
    found = profiles_by_email(email)
    if len(found) != 1:
        raise OpsError("台帳で同じメールアドレスの利用者が 1 人に決まりません")
    profile = ledger.get_profile(found[0]["user_id"])
    if profile is None:
        raise OpsError("台帳に利用者の行がありません")
    return profile


def locate_admin(clients: Clients, config: Config, email: str) -> ReinviteTarget:
    """Cognito と台帳の今の状態から、対象と続きの段を決める。

    - Cognito の利用者の sub が台帳にある: 初めから (停止 → 古い利用者を消す → 作り直す)
    - Cognito に利用者がいない: 古い利用者を消した後で止まった。作り直しから続ける
    - Cognito の利用者の sub が台帳に無い: 作り直した後で台帳が未更新。台帳の段から続ける
      (台帳が再招待の途中で、その利用者が一度もサインインしていない (FORCE_CHANGE_PASSWORD) ときだけ)
    """
    user = get_cognito_user(clients, config, email)
    if user is not None and sub_mapping(user.sub) is not None:
        target = ReinviteTarget(profile_for_sub(user.sub), old_user=user, new_user=None)
    else:
        target = ReinviteTarget(single_profile_by_email(email), old_user=None, new_user=user)
    if target.profile.get("role") != ledger.ROLE_ADMIN:
        raise OpsError("台帳の役割が admin ではありません (admin でない利用者の再招待は admin-users で行う)")
    if target.new_user is not None:
        if not is_reinvite_in_progress(target.profile):
            raise OpsError("Cognito の利用者の sub が台帳に無く、台帳も再招待の途中ではありません")
        if target.new_user.status != FORCE_CHANGE_PASSWORD:
            raise OpsError(
                "台帳に無い sub の Cognito の利用者が FORCE_CHANGE_PASSWORD ではありません (サインイン済み)。"
                "続きとはみなさないので、手で確かめてください"
            )
    return target


def suspend_items(config: Config, profile: dict, *, reason: str, arn: str) -> list[dict]:
    profile_update = update(
        config,
        profile["PK"],
        ledger.PROFILE_SK,
        "SET #status = :suspended, restore_requires = :reinvite, reinvite_in_progress = :true, suspended_by = :by, "
        "suspended_at = :at, suspend_reason = :reason, updated_by = :by, updated_at = :at, last_op_id = :op, "
        "version = version + :one",
        "#sub = :sub AND version = :version",
        {
            ":op": new_ulid(),
            ":version": profile["version"],
            ":suspended": ledger.STATUS_SUSPENDED,
            ":reinvite": ledger.RESTORE_REINVITE,
            ":true": True,
            ":by": script_actor(arn),
            ":at": now_iso(),
            ":reason": reason,
            ":one": 1,
            ":sub": profile["sub"],
        },
        names={"#status": "status", "#sub": "sub"},
    )
    return [
        profile_update,
        admin_set(config, profile["user_id"], "DELETE"),
        script_audit(
            "script.emergency_reinvite", config, user_id=profile["user_id"], arn=arn, step="suspend", reason=reason
        ),
    ]


def reinvite_items(config: Config, profile: dict, user: CognitoUser, *, reason: str, arn: str) -> list[dict]:
    at = now_iso()
    old_sub = profile["sub"]
    profile_update = update(
        config,
        profile["PK"],
        ledger.PROFILE_SK,
        "SET #sub = :new_sub, cognito_username = :username, previous_sub = :old_sub, #status = :suspended, "
        "restore_requires = :reinvite, admin_state = :pending, updated_by = :by, updated_at = :at, last_op_id = :op, "
        "version = version + :one, reinvite_count = if_not_exists(reinvite_count, :zero) + :one "
        "REMOVE reinvite_in_progress, mfa_enabled_at, " + ", ".join(ADMIN_VERIFIED_FIELDS),
        "#sub = :old_sub AND version = :version",
        {
            ":op": new_ulid(),
            ":version": profile["version"],
            ":new_sub": user.sub,
            ":username": user.username,
            ":suspended": ledger.STATUS_SUSPENDED,
            ":reinvite": ledger.RESTORE_REINVITE,
            ":pending": ledger.ADMIN_PENDING,
            ":by": script_actor(arn),
            ":at": at,
            ":zero": 0,
            ":one": 1,
            ":old_sub": old_sub,
        },
        names={"#sub": "sub", "#status": "status"},
    )
    revoke_old = update(
        config,
        ledger.sub_pk(old_sub),
        ledger.SUB_SK,
        "SET revoked_at = :at",
        "attribute_exists(PK) AND attribute_not_exists(revoked_at)",
        {":at": at},
    )
    mapping = {"PK": ledger.sub_pk(user.sub), "SK": ledger.SUB_SK, "user_id": profile["user_id"], "created_at": at}
    return [
        profile_update,
        put(config, mapping),
        revoke_old,
        admin_set(config, profile["user_id"], "DELETE"),
        script_audit(
            "script.emergency_reinvite", config, user_id=profile["user_id"], arn=arn, step="reinvite", reason=reason
        ),
    ]


def retire_cognito_user(clients: Clients, config: Config, username: str) -> None:
    for operation in ("admin_disable_user", "admin_user_global_sign_out", "admin_delete_user"):
        getattr(clients.cognito, operation)(UserPoolId=config.user_pool_id, Username=username)


def issue_new_admin(clients: Clients, config: Config, target: ReinviteTarget, email: str, password: str) -> CognitoUser:
    """作り直す。前の回に作り直した利用者がいれば、作らずに一時パスワードを出し直す。"""
    if target.new_user is None:
        return create_cognito_admin(clients, config, email, password)
    clients.cognito.admin_set_user_password(
        UserPoolId=config.user_pool_id, Username=target.new_user.username, Password=password, Permanent=False
    )
    return target.new_user


def run_step(
    clients: Clients,
    state: ReinviteState,
    step: str,
    *,
    build: Callable[[], list[dict]],
    applied: Callable[[list[dict]], bool],
    new_sub: str | None = None,
    secret_path: pathlib.Path | None = None,
) -> None:
    """台帳の 1 段を書く。送る前に token と要求を状態のファイルに残し、前の回に送りかけた同じ段は同じ中身で送り直す。

    送り直すときは、一時パスワードのファイルの場所だけを今の回のものに書き換える (token と要求の中身は変えない)。
    台帳の書き込みが通った後の状態のファイルの後処理が失敗しても、警告するだけで投げない (補償させない)。
    """
    pending = state.pending_for(step, new_sub)
    if pending is None:
        pending = state.remember(step, build(), new_sub=new_sub, secret_path=secret_path)
    elif secret_path is not None and pending.get("secret_path") != str(secret_path):
        tidy_state(
            lambda: state.point_to(secret_path),
            state,
            f"状態のファイルの一時パスワードの場所を書き換えられませんでした。今の回のファイルは {secret_path}",
        )
    transact_checked(clients, pending["items"], token=pending["token"], applied=lambda: applied(pending["items"]))
    tidy_state(state.forget, state, "台帳は書けています。状態のファイルを手で消してください")


def tidy_state(action: Callable[[], None], state: ReinviteState, message: str) -> None:
    """状態のファイルのローカルの後処理。失敗しても台帳と Cognito には触れず、警告して続ける。"""
    try:
        action()
    except OSError as exc:
        print(f"警告: 状態のファイル {state.path} を書き直せませんでした ({exc.strerror})。{message}", file=sys.stderr)


def reinvite(
    clients: Clients,
    config: Config,
    target: ReinviteTarget,
    secret_path: pathlib.Path,
    state: ReinviteState,
    *,
    email: str,
    reason: str,
) -> CognitoUser:
    arn = caller_arn(clients)
    profile = target.profile
    user_id = profile["user_id"]
    if not profile.get(REINVITE_IN_PROGRESS):
        # 付け替えの途中でなければ必ず開始の段 (途中の印・停止・有効な admin の集合から外す) を書く。
        # 再招待を終えた後の 2 回目でも、古い利用者を消す前に印を立てる
        run_step(
            clients,
            state,
            "suspend",
            build=lambda: suspend_items(config, profile, reason=reason, arn=arn),
            applied=lambda items: last_op_of(user_id) == op_of(items),
        )
        profile = ledger.get_profile(user_id) or profile
    elif state.pending_for("suspend") is not None:
        state.forget()
    if target.old_user is not None:
        retire_cognito_user(clients, config, target.old_user.username)

    password = generate_temporary_password()
    fill_secret(secret_path, password)
    user = issue_new_admin(clients, config, target, email, password)
    try:
        add_to_admin_group(clients, config, user.username)
        run_step(
            clients,
            state,
            "reinvite",
            build=lambda: reinvite_items(config, profile, user, reason=reason, arn=arn),
            applied=lambda _items: ledger_maps(user.sub, user_id),
            new_sub=user.sub,
            secret_path=secret_path,
        )
    except Exception as exc:
        if not is_outcome_unknown(exc):
            delete_cognito_user(clients, config, user.username)
        raise
    return user


def finished_before(target: ReinviteTarget, state: ReinviteState | None) -> dict | None:
    """前の回の台帳の書き込みが、結果不明で止まった後に通っていたか。通っていればその段を返す。"""
    pending = state.pending_for("reinvite", target.old_user.sub) if state and target.old_user else None
    if pending is None or not is_reinvite_in_progress(target.profile):
        return None
    return pending


def emergency_reinvite(clients: Clients, config: Config, args, confirm: Callable[[str], bool]) -> int:
    email = args.email
    target = locate_admin(clients, config, email)
    user_id = target.profile["user_id"]
    previous = ReinviteState.load(check_outside_repo(args.output_dir), config.env, user_id) if args.output_dir else None
    done = finished_before(target, previous)
    if done is not None:
        tidy_state(previous.remove, previous, "状態のファイルを手で消してください")
        print("前の回で済んでいました (台帳の書き込みは通っていた)。Cognito も台帳も変えていません")
        print(f"  user_id   : {user_id}")
        print(f"  一時パスワードのファイル (前の回のもの。社内の別経路で渡し、渡したら消す): {done['secret_path']}")
        return 0
    confirm_prod(config, confirm, f"{email} を削除して admin (準備中) として作り直します")

    secret_path = prepare_secret(output_dir=args.output_dir, env=config.env)
    state = previous or ReinviteState.open(secret_path.parent, config.env, user_id)
    try:
        reinvite(clients, config, target, secret_path, state, email=email, reason=args.reason)
    except Exception as exc:
        abandon_secret(secret_path, exc)
        if not is_outcome_unknown(exc):
            tidy_state(state.remove, state, "状態のファイルを手で消してください")
        raise
    tidy_state(state.remove, state, "台帳と Cognito は書けています。状態のファイルを手で消してください")

    if target.old_user is None:
        print("前の回の続きから進めました")
    print("admin を作り直しました (準備中。利用者の台帳は停止のまま)")
    print("本人が一時パスワードでサインインし、本パスワードと TOTP を登録した後に activate-admin で有効に戻す")
    print(f"  user_id   : {user_id}")
    print(f"  一時パスワードのファイル (社内の別経路で渡し、渡したら消す): {secret_path}")
    return 0


# =============================================================================
# check-waf（計画 4.1 の配備の手順 4）
# =============================================================================


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def forged_access_token(config: Config) -> str:
    """プールとクライアントだけを名乗る、署名の無効なアクセストークン。"""
    issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{config.user_pool_id}"
    now = int(time.time())
    claims = {
        "iss": issuer,
        "client_id": config.client_id,
        "token_use": "access",
        "sub": "00000000-0000-0000-0000-210000000001",
        "username": "00000000-0000-0000-0000-210000000001",
        "iat": now,
        "exp": now + 300,
        "jti": secrets.token_hex(8),
    }
    return f"{_b64({'alg': 'RS256', 'kid': 'waf-check'})}.{_b64(claims)}.{_b64({'sig': 'invalid'})}"


def rule_actions(clients: Clients, config: Config) -> dict[str, str]:
    account = clients.sts.get_caller_identity()["Account"]
    pool_arn = f"arn:aws:cognito-idp:{config.region}:{account}:userpool/{config.user_pool_id}"
    acl = clients.wafv2.get_web_acl_for_resource(ResourceArn=pool_arn).get("WebACL")
    if not acl:
        raise OpsError("ユーザープールに web ACL が付いていません")
    print("=== ユーザープールの web ACL ===")
    print(f"  web ACL   : {acl['Name']}")
    actions = {rule["Name"]: next(iter(rule.get("Action") or {"(なし)": {}})) for rule in acl.get("Rules") or []}
    for name, action in actions.items():
        print(f"  {name:<16}: {action}")
    return actions


def check_waf(clients: Clients, config: Config, args, confirm: Callable[[str], bool]) -> int:
    actions = rule_actions(clients, config)
    if actions.get("change-password") != "Block":
        raise OpsError("規則 change-password が Block ではありません")
    # 本物のトークンで WAF が止めなければパスワードが足される (状態を変えうる)
    confirm_prod(config, confirm, "ユーザープールに ChangePassword の要求を送ります")

    token = os.environ.get(ACCESS_TOKEN_ENV)
    print("=== ChangePassword の要求 ===")
    source = f"環境変数 {ACCESS_TOKEN_ENV}" if token else "偽のトークン (プールとクライアントだけを名乗る)"
    print(f"  トークン  : {source}")
    try:
        clients.cognito.change_password(
            ProposedPassword=WAF_CHECK_PASSWORD, AccessToken=token or forged_access_token(config)
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        message = exc.response["Error"].get("Message", "")
        print(f"  結果      : {code} ({message})")
        if code == "ForbiddenException" and WAF_BLOCK_MARKER in message:
            print("OK: ChangePassword は WAF で止まる")
            return 0
        raise OpsError(
            "WAF より先に Cognito が拒んだため、WAF で止まるかを確かめられていません。試験用の利用者で"
            f"サインインし、そのアクセストークンを環境変数 {ACCESS_TOKEN_ENV} に入れて流し直してください"
        ) from exc
    raise OpsError("ChangePassword が通り、パスワードが足されました。WAF が止めていません。その利用者を消してください")


# =============================================================================
# 入口
# =============================================================================


def email_arg(value: str) -> str:
    email = ledger.normalize_email(value)
    if not EMAIL_PATTERN.fullmatch(email):
        raise argparse.ArgumentTypeError("メールアドレスの形ではありません")
    return email


def text_arg(value: str) -> str:
    text = value.strip()
    if not text or len(text) > MAX_TEXT_LENGTH or "\n" in text or "\r" in text:
        raise argparse.ArgumentTypeError(f"1 行で {MAX_TEXT_LENGTH} 文字以内にしてください")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="admin_ops.py", description="admin の作成・有効化・緊急の復旧と WAF の確認")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION), help=f"既定 {DEFAULT_REGION}")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str, *, email: bool = True) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("env", choices=VALID_ENVS)
        if email:
            sub.add_argument("email", type=email_arg)
        return sub

    create = command("create-admin", "最初の admin を作る (準備中)")
    create.add_argument("--output-dir", type=pathlib.Path, default=None, help="一時パスワードのファイルの置き場")
    command("show-admin", "台帳と Cognito の状態を表示する (読み取りのみ)")
    command("enable-passkey-mfa", "有効な admin に本人確認つきのパスキーの入口を足す")
    activate = command("activate-admin", "確認待ちの admin を有効にする")
    activate.add_argument("--yes", action="store_true", help="別経路で登録時刻を確認済み (prod は確認を省略しない)")
    activate.add_argument("--method", required=True, choices=VERIFY_METHODS, help="別経路の確認の方法")
    activate.add_argument("--note", type=text_arg, default=None, help="確認のメモ (台帳にだけ残す)")
    reinvite = command("emergency-reinvite", "admin を削除して作り直す (緊急の復旧)")
    reinvite.add_argument("--reason", type=text_arg, required=True, help="理由 (監査に残す)")
    reinvite.add_argument("--output-dir", type=pathlib.Path, default=None, help="一時パスワードのファイルの置き場")
    command("check-waf", "ChangePassword が WAF で止まることを確かめる", email=False)
    return parser


COMMANDS = {
    "create-admin": create_admin,
    "show-admin": show_admin,
    "enable-passkey-mfa": enable_passkey_mfa,
    "activate-admin": activate_admin,
    "emergency-reinvite": emergency_reinvite,
    "check-waf": check_waf,
}


def main(argv: list[str] | None = None, *, clients: Clients | None = None, confirm=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        clients = clients or make_clients(args.region)
        config = resolve_config(clients, args.env, args.region)
        return COMMANDS[args.command](clients, config, args, confirm or default_confirm)
    except OpsError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
    except ClientError as exc:
        error = exc.response.get("Error", {})
        print(f"AWS のエラー: {error.get('Code')} ({exc.operation_name})", file=sys.stderr)
    except BotoCoreError as exc:
        print(f"AWS への要求が失敗しました: {type(exc).__name__}", file=sys.stderr)
    except OSError as exc:
        print(f"ファイルの操作に失敗しました: {exc.strerror}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
