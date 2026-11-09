"""利用者の一覧と詳細の表示（計画 4.2）。台帳と Cognito の状態を突き合わせる。連絡先は詳細だけに出す。"""

from datetime import UTC, datetime
from decimal import Decimal

from admin_users import cognito, groups
from admin_users.users import IN_PROGRESS, list_profiles, load
from service_platform import admin_guard, ledger

ASSIGNMENT_FIELDS = ("account", "device_period_id", "assignment_id", "valid_from", "valid_to", "org_name")


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, Decimal | int) and not isinstance(value, bool):
        return datetime.fromtimestamp(int(value), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _cognito_fields(user: dict | None) -> dict:
    return {
        "cognito_status": user.get("UserStatus") if user else None,
        "cognito_enabled": bool(user.get("Enabled")) if user else False,
    }


def summary(profile: dict, cognito_user: dict | None) -> dict:
    return {
        "user_id": profile["user_id"],
        "email": profile.get("email"),
        "status": profile.get("status"),
        "kind": profile.get("kind"),
        "role": profile.get("role"),
        "org_name": profile.get("org_name"),
        "admin_state": profile.get("admin_state"),
        **_cognito_fields(cognito_user),
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
        "in_ledger": True,
    }


def _unregistered(user: dict) -> dict:
    """Cognito にいて台帳に無い利用者 (招待・再招待の途中の失敗の見つけ口)。"""
    return {
        "user_id": None,
        "email": cognito.attribute(user, cognito.EMAIL_ATTRIBUTE),
        "status": None,
        "kind": None,
        "role": None,
        "org_name": None,
        "admin_state": None,
        **_cognito_fields(user),
        "created_at": _iso(user.get("UserCreateDate")),
        "updated_at": _iso(user.get("UserLastModifiedDate")),
        "in_ledger": False,
    }


def list_users() -> list[dict]:
    by_username = {user["Username"]: user for user in cognito.list_users()}
    rows = [summary(profile, by_username.pop(profile.get("cognito_username"), None)) for profile in list_profiles()]
    rows.extend(_unregistered(user) for user in by_username.values())
    return sorted(rows, key=lambda row: (row["email"] or "", row["user_id"] or ""))


def detail(user_id: str) -> dict:
    profile = load(user_id)
    username = profile.get("cognito_username")
    user = cognito.get_user(username) if username else None
    groups = cognito.groups_of(username) if user else set()
    assignments = [
        {field: row.get(field) for field in ASSIGNMENT_FIELDS} for row in ledger.list_user_assignments(user_id)
    ]
    return {
        **summary(profile, user),
        "contact": profile.get("contact"),
        "note": profile.get("note"),
        "is_admin": cognito.ADMIN_GROUP in groups,
        "totp": admin_guard.TOTP in ((user or {}).get("UserMFASettingList") or []),
        "factors": cognito.auth_factors(username) if user else None,
        "mfa_enabled_at": _iso(profile.get("mfa_enabled_at")),
        "admin_verified_at": profile.get("admin_verified_at"),
        "admin_verified_method": profile.get("admin_verified_method"),
        "restore_requires": profile.get("restore_requires"),
        "reinvite_in_progress": bool(profile.get(IN_PROGRESS)),
        "email_pending": (profile.get("email_pending") or {}).get("email"),
        # admin-users と scripts (emergency-reinvite) の再招待の回数
        "reinvite_count": int(profile.get("reinvite_count") or 0),
        "suspended_at": profile.get("suspended_at"),
        "suspend_reason": profile.get("suspend_reason"),
        "assignments": assignments,
        "memberships": memberships(user_id),
    }


def memberships(user_id: str) -> list[dict]:
    """利用者の所属 (経路と経路の名前)。"""
    cache: dict = {}
    rows = []
    for row in ledger.list_memberships(user_id):
        path = [str(item) for item in row.get("path") or []]
        rows.append(
            {
                "membership_id": row.get("membership_id"),
                "group_id": row.get("group_id"),
                "path": path,
                "path_names": groups.path_names(path, cache),
                "added_at": row.get("added_at"),
            }
        )
    return sorted(rows, key=lambda item: [name or "" for name in item["path_names"]])
