"""土台の台帳のキーと読み取り（計画 2.1、ADR-001 D3 / D4）。

台帳のキー (PK / SK / GSI1) の組み立ての唯一の置き場。書き込みの関数は書き手
(admin-users・base・scripts) の側に置き、このモジュールは読み取りとキーだけを持つ。
認可の判断に使う読み取りは、すべて強い整合 (`ConsistentRead=True`) で、GSI を使わない。
"""

import os
from dataclasses import dataclass
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from service_platform.auth import ForbiddenError

LEDGER_TABLE_ENV = "LEDGER_TABLE"
GSI1 = "GSI1"

PROFILE_SK = "PROFILE"
SUB_SK = "USER"
CURRENT_SK = "CURRENT"
PINFAIL_SK = "PINFAIL"
REFRESH_SK = "REFRESH"
ASSIGN_PREFIX = "ASSIGN#"

USER_GSI1PK = "USER"
DEVICE_GSI1PK = "DEVICE"
DPERIOD_GSI1PK = "DPERIOD"

# グループと所属 (ADR D13)。根の控えは GROUP#ROOT の下 (group_id は ULID なので ROOT と紛れない)
GROUP_GSI1PK = "GROUP"
GROUP_ROOT_PK = "GROUP#ROOT"
GROUP_PROFILE_SK = "PROFILE"
GROUP_META_SK = "META"
CHILD_PREFIX = "CHILD#"
MEMBER_PREFIX = "MEMBER#"
GROUP_ASSIGNMENT_PREFIX = "GROUP#"
SERIAL_PREFIX = "SERIAL#"
USER_PINFAIL_SK = "PINFAIL"

# 通信機の利用期間の access (ADR D14)。属性が無い行は everyone
ACCESS_EVERYONE = "everyone"
ACCESS_ASSIGNED = "assigned"
ACCESS_CHOICES = (ACCESS_EVERYONE, ACCESS_ASSIGNED)
TARGET_USER = "user"
TARGET_GROUP = "group"

# 有効な admin の user_id の文字列集合 (属性 members)。最後の admin を残す条件を書き込みと一体にする
ADMIN_SET_PK = "ADMIN#SET"
ADMIN_SET_SK = "ACTIVE"
ADMIN_MEMBERS = "members"

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
KIND_CUSTOMER = "customer"
KIND_EMPLOYEE = "employee"
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ADMIN_PENDING = "pending"
ADMIN_AWAITING = "awaiting"
ADMIN_ACTIVE = "active"
RESTORE_REINVITE = "reinvite"


class UserNotRegisteredError(ForbiddenError):
    """sub が台帳に無い、無効の印が付いている、または今の sub ではない（403）。"""

    code = "USER_NOT_REGISTERED"

    def __init__(self, message: str = "利用者が登録されていません") -> None:
        super().__init__(message)


class UserSuspendedError(ForbiddenError):
    """台帳の利用者が停止中（403）。"""

    code = "USER_SUSPENDED"

    def __init__(self, message: str = "利用が停止されています") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class UserContext:
    """要求ごとに確定した利用者。"""

    user_id: str
    sub: str
    email: str
    status: str
    kind: str | None
    role: str | None
    admin_state: str | None
    mfa_enabled_at: int | None
    cognito_username: str | None = None

    @property
    def is_suspended(self) -> bool:
        return self.status == STATUS_SUSPENDED


def _part(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "#" in value:
        raise ValueError(f"{name} は '#' を含まない空でない文字列である必要があります")
    return value


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_pk(user_id: str) -> str:
    return f"USER#{_part(user_id, 'user_id')}"


def sub_pk(sub: str) -> str:
    return f"SUB#{_part(sub, 'sub')}"


def device_pk(account: str) -> str:
    return f"DEVICE#{_part(account, 'account')}"


def dperiod_pk(device_period_id: str) -> str:
    return f"DPERIOD#{_part(device_period_id, 'device_period_id')}"


def dperiod_sk(device_period_id: str) -> str:
    return dperiod_pk(device_period_id)


def assignment_prefix(user_id: str) -> str:
    return f"USER#{_part(user_id, 'user_id')}#"


def assignment_sk(user_id: str, assignment_id: str) -> str:
    return f"{assignment_prefix(user_id)}{_part(assignment_id, 'assignment_id')}"


def assign_sk(device_period_id: str, assignment_id: str) -> str:
    return f"{ASSIGN_PREFIX}{_part(device_period_id, 'device_period_id')}#{_part(assignment_id, 'assignment_id')}"


def grant_prefix(service: str) -> str:
    return f"GRANT#{_part(service, 'service')}#"


def grant_sk(service: str, grant_id: str) -> str:
    return f"{grant_prefix(service)}{_part(grant_id, 'grant_id')}"


def login_sk(account: str) -> str:
    return f"LOGIN#{_part(account, 'account')}"


def group_pk(group_id: str) -> str:
    return f"GROUP#{_part(group_id, 'group_id')}"


def child_sk(group_id: str) -> str:
    return f"{CHILD_PREFIX}{_part(group_id, 'group_id')}"


def member_sk(group_id: str) -> str:
    """利用者の下の所属 (`USER#{user_id}` / `MEMBER#{group_id}`)。"""
    return f"{MEMBER_PREFIX}{_part(group_id, 'group_id')}"


def group_member_sk(user_id: str) -> str:
    """グループの下の所属の逆引き (`GROUP#{group_id}` / `MEMBER#{user_id}`)。"""
    return f"{MEMBER_PREFIX}{_part(user_id, 'user_id')}"


def group_assignment_prefix(group_id: str) -> str:
    return f"{GROUP_ASSIGNMENT_PREFIX}{_part(group_id, 'group_id')}#"


def group_assignment_sk(group_id: str, assignment_id: str) -> str:
    return f"{group_assignment_prefix(group_id)}{_part(assignment_id, 'assignment_id')}"


def group_assign_sk(device_period_id: str, assignment_id: str) -> str:
    """グループの下のグループ宛の割り当ての逆引き (人宛の `assign_sk` と同じ形)。"""
    return assign_sk(device_period_id, assignment_id)


def serial_sk(device_period_id: str) -> str:
    """利用者の下の製造番号の控え (グループ宛と everyone のログインで書く)。"""
    return f"{SERIAL_PREFIX}{_part(device_period_id, 'device_period_id')}"


def group_gsi1sk(path: list[str]) -> str:
    if not path:
        raise ValueError("path は空でない group_id の並びである必要があります")
    return "#".join(_part(group_id, "group_id") for group_id in path)


def period_access(row: dict) -> str:
    """利用期間 (か CURRENT) の access。属性が無いか everyone なら everyone (ADR D14 の移行)。

    知らない値は assigned と読む (fail-closed。書き手の条件も知らない値を everyone と認めない)。
    """
    return ACCESS_EVERYONE if row.get("access") in (None, ACCESS_EVERYONE) else ACCESS_ASSIGNED


def period_access_gen(row: dict) -> str:
    """everyone の世代。属性の無い既存の行は `START#{started_at}` (読み取りで行に書き足さない)。"""
    value = row.get("access_gen")
    return value if isinstance(value, str) and value else f"START#{row.get('started_at')}"


def access_changed_at(row: dict) -> str | None:
    return row.get("access_changed_at") or row.get("started_at")


def access_changed_by(row: dict) -> str | None:
    return row.get("access_changed_by") or None


def user_gsi1sk(email: str, user_id: str) -> str:
    return f"{normalize_email(email)}#{_part(user_id, 'user_id')}"


def device_gsi1sk(account: str) -> str:
    return _part(account, "account")


def dperiod_gsi1sk(account: str, device_period_id: str) -> str:
    return f"{_part(account, 'account')}#{_part(device_period_id, 'device_period_id')}"


def ledger_table_name() -> str:
    """台帳のテーブル名（環境変数 `LEDGER_TABLE`）。"""
    name = os.environ.get(LEDGER_TABLE_ENV) or ""
    if not name:
        raise RuntimeError(f"環境変数 {LEDGER_TABLE_ENV} が設定されていません")
    return name


_resource_cache = None


def reset_clients() -> None:
    """boto3 のキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _resource_cache
    _resource_cache = None


def _table():
    global _resource_cache
    if _resource_cache is None:
        _resource_cache = boto3.resource("dynamodb")
    return _resource_cache.Table(ledger_table_name())


def _get(pk: str, sk: str) -> dict | None:
    return _table().get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")


def _as_int(value: object) -> int | None:
    if isinstance(value, Decimal | int) and not isinstance(value, bool):
        return int(value)
    return None


def get_profile(user_id: str) -> dict | None:
    """利用者の行（強い整合）。"""
    return _get(user_pk(user_id), PROFILE_SK)


def resolve_user(sub: str, *, allow_suspended: bool = False) -> UserContext:
    """トークンの sub から利用者を確定する（計画 2.1 の「要求ごとの利用者の確定」）。

    引き当ての行が無い・無効の印 (`revoked_at`) がある・利用者の行の今の sub と違うときは
    `UserNotRegisteredError`。停止中は `allow_suspended` (ADR D9 の例外 1・2) でなければ
    `UserSuspendedError`。状態が有効でも停止でもなければ通さない。
    """
    mapping = _get(sub_pk(sub), SUB_SK)
    if not mapping or mapping.get("revoked_at") or not mapping.get("user_id"):
        raise UserNotRegisteredError()

    profile = get_profile(str(mapping["user_id"]))
    if not profile or profile.get("sub") != sub:
        raise UserNotRegisteredError()

    status = profile.get("status")
    if status == STATUS_SUSPENDED:
        if not allow_suspended:
            raise UserSuspendedError()
    elif status != STATUS_ACTIVE:
        raise UserSuspendedError()

    return UserContext(
        user_id=str(profile["user_id"]),
        sub=sub,
        email=str(profile.get("email") or ""),
        status=status,
        kind=profile.get("kind"),
        role=profile.get("role"),
        admin_state=profile.get("admin_state"),
        mfa_enabled_at=_as_int(profile.get("mfa_enabled_at")),
        cognito_username=profile.get("cognito_username"),
    )


def query_prefix(pk: str, prefix: str) -> list[dict]:
    """PK の下で SK が prefix で始まる行を全部返す（強い整合、全ページ）。"""
    table = _table()
    query = {
        "KeyConditionExpression": Key("PK").eq(pk) & Key("SK").begins_with(prefix),
        "ConsistentRead": True,
    }
    rows: list[dict] = []
    while True:
        page = table.query(**query)
        rows.extend(page.get("Items") or [])
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            return rows
        query = {**query, "ExclusiveStartKey": last_key}


def list_memberships(user_id: str) -> list[dict]:
    """利用者の所属の行 (`MEMBER#...`。経路の写しを持つ) を全部返す（強い整合）。"""
    return query_prefix(user_pk(user_id), MEMBER_PREFIX)


def list_user_assignments(user_id: str) -> list[dict]:
    """利用者の割り当ての逆引きの行 (`ASSIGN#...`) を全部返す（強い整合）。"""
    table = _table()
    query = {
        "KeyConditionExpression": Key("PK").eq(user_pk(user_id)) & Key("SK").begins_with(ASSIGN_PREFIX),
        "ConsistentRead": True,
    }
    rows: list[dict] = []
    while True:
        page = table.query(**query)
        rows.extend(page.get("Items") or [])
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            return rows
        query = {**query, "ExclusiveStartKey": last_key}
