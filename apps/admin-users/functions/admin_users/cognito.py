"""Cognito User Pool の管理 API を薄く包む（計画 4.2）。

Admin 系 API には台帳の `cognito_username` を渡す。User Pool は `UsernameAttributes: [email]` で作って
あり、Username は生成された ID (メールアドレスは別名)。画面から来た文字列を Username に使わず、
メールアドレスで引くのは招待・再招待・email の変更の重複の確認だけ。

boto3 クライアントはモジュールに 1 つ持つ。`reset_client()` は moto の `mock_aws` を張り直すテスト用。
"""

import os

import boto3
from botocore.exceptions import ClientError

from service_platform import cognito_extra

ADMIN_GROUP = "admin"
SUB_ATTRIBUTE = "sub"
EMAIL_ATTRIBUTE = "email"
USER_NOT_FOUND = "UserNotFoundException"

# ListUsers / ListUsersInGroup の Limit の上限（AWS API の仕様）。
PAGE_SIZE = 60

_client_cache = None


def reset_client() -> None:
    global _client_cache
    _client_cache = None


def client():
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("cognito-idp")
    return _client_cache


def user_pool_id() -> str:
    return os.environ["USER_POOL_ID"]


def is_not_found(exc: Exception) -> bool:
    return isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == USER_NOT_FOUND


def attribute(user: dict, name: str) -> str:
    """ユーザー属性の値。無ければ空文字。"""
    for item in user.get("Attributes") or user.get("UserAttributes") or []:
        if item.get("Name") == name:
            return item.get("Value") or ""
    return ""


def list_users() -> list[dict]:
    """User Pool の全ユーザー（ページング込み）。"""
    users: list[dict] = []
    token = None
    while True:
        page = client().list_users(
            UserPoolId=user_pool_id(), Limit=PAGE_SIZE, **({"PaginationToken": token} if token else {})
        )
        users.extend(page.get("Users") or [])
        token = page.get("PaginationToken")
        if not token:
            return users


def admin_usernames() -> set[str]:
    """`admin` グループの利用者の Username（ページング込み）。"""
    names: set[str] = set()
    token = None
    while True:
        page = client().list_users_in_group(
            UserPoolId=user_pool_id(),
            GroupName=ADMIN_GROUP,
            Limit=PAGE_SIZE,
            **({"NextToken": token} if token else {}),
        )
        names.update(user["Username"] for user in page.get("Users") or [])
        token = page.get("NextToken")
        if not token:
            return names


def get_user(username: str) -> dict | None:
    """AdminGetUser。いなければ `None`。"""
    try:
        return client().admin_get_user(UserPoolId=user_pool_id(), Username=username)
    except ClientError as exc:
        if is_not_found(exc):
            return None
        raise


def find_by_email(email: str) -> dict | None:
    """メールアドレス (別名) で引く。重複の確認のためだけに使う。"""
    return get_user(email)


def groups_of(username: str) -> set[str]:
    names: set[str] = set()
    token = None
    while True:
        page = client().admin_list_groups_for_user(
            UserPoolId=user_pool_id(), Username=username, Limit=PAGE_SIZE, **({"NextToken": token} if token else {})
        )
        names.update(group.get("GroupName") for group in page.get("Groups") or [])
        token = page.get("NextToken")
        if not token:
            return names


def create_user(email: str, *, temporary_password: str | None) -> dict:
    """利用者を作る。`{"username", "sub"}` を返す。

    一時パスワードが無いときはパスワード無しの招待で、招待のメールを送る (お客様と社員)。
    一時パスワードがあるときは招待のメールを送らない (admin。一時パスワードは社内の別経路で渡す)。
    """
    options = (
        {"TemporaryPassword": temporary_password, "MessageAction": "SUPPRESS"}
        if temporary_password
        else {"DesiredDeliveryMediums": ["EMAIL"]}
    )
    user = client().admin_create_user(
        UserPoolId=user_pool_id(),
        Username=email,
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        **options,
    )["User"]
    return {"username": user["Username"], "sub": attribute(user, SUB_ATTRIBUTE)}


def delete_user(username: str) -> None:
    try:
        client().admin_delete_user(UserPoolId=user_pool_id(), Username=username)
    except ClientError as exc:
        if not is_not_found(exc):
            raise


def disable(username: str) -> None:
    client().admin_disable_user(UserPoolId=user_pool_id(), Username=username)


def enable(username: str) -> None:
    client().admin_enable_user(UserPoolId=user_pool_id(), Username=username)


def global_sign_out(username: str) -> None:
    client().admin_user_global_sign_out(UserPoolId=user_pool_id(), Username=username)


def add_to_admin(username: str) -> None:
    client().admin_add_user_to_group(UserPoolId=user_pool_id(), Username=username, GroupName=ADMIN_GROUP)


def remove_from_admin(username: str) -> None:
    client().admin_remove_user_from_group(UserPoolId=user_pool_id(), Username=username, GroupName=ADMIN_GROUP)


def set_temporary_password(username: str, password: str) -> None:
    client().admin_set_user_password(UserPoolId=user_pool_id(), Username=username, Password=password, Permanent=False)


def update_email(username: str, email: str) -> None:
    """email を変え、確認済みにする (利用者は email を書き換えられない。ADR D2)。"""
    client().admin_update_user_attributes(
        UserPoolId=user_pool_id(),
        Username=username,
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
    )


def auth_factors(username: str) -> dict[str, bool] | None:
    """パスワード・パスキー・メールのコードの有無。利用者がいなければ `None`。"""
    try:
        response = cognito_extra.admin_get_user_auth_factors(client(), user_pool_id(), username)
    except ClientError as exc:
        if is_not_found(exc):
            return None
        raise
    return cognito_extra.factor_flags(response)
