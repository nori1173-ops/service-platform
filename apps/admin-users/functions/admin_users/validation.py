"""要求の本文の検査（計画 4.2 の招待の本文の上限）。"""

import json
import re

from admin_users import errors
from service_platform import ledger

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254
ORG_NAME_MAX = 100
CONTACT_MAX = 200
NOTE_MAX = 500
REASON_MAX = 500
KINDS = (ledger.KIND_CUSTOMER, ledger.KIND_EMPLOYEE)
ROLES = (ledger.ROLE_USER, ledger.ROLE_ADMIN)
VERIFY_METHODS = ("phone", "in_person", "video", "other")
GROUP_NAME_MAX = 100
ACCESS_CHOICES = ledger.ACCESS_CHOICES
TARGET_TYPES = (ledger.TARGET_USER, ledger.TARGET_GROUP)


def body(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise errors.validation("リクエスト本文が JSON ではありません") from exc
    if not isinstance(parsed, dict):
        raise errors.validation("リクエスト本文は JSON オブジェクトで指定してください")
    return parsed


def email(value: object) -> str:
    if not isinstance(value, str):
        raise errors.validation("email を指定してください")
    normalized = ledger.normalize_email(value)
    if len(normalized) > EMAIL_MAX or not EMAIL_PATTERN.fullmatch(normalized):
        raise errors.validation("email の形が正しくありません")
    return normalized


def text(value: object, name: str, limit: int, *, required: bool = False) -> str | None:
    """文字列の項目。空 (空白だけ) は `None`。"""
    if value is None:
        stripped = ""
    elif isinstance(value, str):
        stripped = value.strip()
    else:
        raise errors.validation(f"{name} は文字列で指定してください")
    if len(stripped) > limit:
        raise errors.validation(f"{name} は {limit} 文字以内で指定してください")
    if required and not stripped:
        raise errors.validation(f"{name} を指定してください")
    return stripped or None


def choice(value: object, name: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise errors.validation(f"{name} は {' / '.join(choices)} のどれかです")
    return value


def verify_method(value: object, name: str = "method") -> str:
    return choice(value, name, VERIFY_METHODS)


def boolean(value: object, name: str) -> bool:
    """真偽値。文字列 `"false"` のような truthy な値は受け付けない。"""
    if not isinstance(value, bool):
        raise errors.validation(f"{name} は真偽値で指定してください")
    return value
