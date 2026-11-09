"""`GET /me` と `POST /me/totp-registered` のテスト（計画 2.6 / 4.2、ADR-001 D9 の例外 1・2）。"""

import time

import pytest
from botocore.exceptions import ClientError

ADMIN = ("admin",)
VIEW_KEYS = {
    "user_id",
    "email",
    "status",
    "restore_requires",
    "kind",
    "org_name",
    "is_admin",
    "admin_state",
    "mfa_registered",
}


# =============================================================================
# GET /me (例外 2: 自分の状態を読む)
# =============================================================================


def test_me_returns_only_the_callers_own_summary(aws, call):
    aws.put_user()

    status, body = call("GET", "/me")

    assert status == 200
    assert body["data"] == {
        "user_id": aws.ids.user_id,
        "email": aws.ids.email,
        "status": "active",
        "restore_requires": None,
        "kind": "customer",
        "org_name": "組織A",
        "is_admin": False,
        "admin_state": None,
        "mfa_registered": False,
    }


def test_me_does_not_leak_the_contact_note_or_sub(aws, call):
    """連絡先は詳細 (admin) だけに出す。ほかの利用者のことも返さない。"""
    aws.put_user()

    _status, body = call("GET", "/me")

    assert set(body["data"]) == VIEW_KEYS
    text = str(body)
    assert "連絡先の控え" not in text
    assert "メモ" not in text
    assert aws.ids.sub not in text
    assert aws.ids.username not in text


def test_me_works_while_suspended(aws, call):
    """停止中でも呼べる (例外 2)。画面が「停止中」を出すため。"""
    aws.put_user(status="suspended", restore_requires="reinvite")

    status, body = call("GET", "/me")

    assert status == 200
    assert body["data"]["status"] == "suspended"


@pytest.mark.parametrize("user_status", ["active", "suspended"])
@pytest.mark.parametrize("restore_requires", [None, "reinvite", "other"])
def test_me_returns_restore_requires_from_the_ledger(aws, call, user_status, restore_requires):
    aws.put_user(status=user_status, restore_requires=restore_requires)

    status, body = call("GET", "/me")

    assert status == 200
    assert body["data"]["restore_requires"] == restore_requires


def test_me_reports_the_admin_state_and_only_whether_mfa_is_registered(aws, call):
    aws.put_admin(admin_state="awaiting", mfa_enabled_at=1_762_000_000)

    status, body = call("GET", "/me", groups=ADMIN)

    assert status == 200
    assert body["data"]["is_admin"] is True
    assert body["data"]["admin_state"] == "awaiting"
    assert body["data"]["mfa_registered"] is True
    assert "1762000000" not in str(body)


def test_me_with_a_revoked_sub_is_403(aws, call):
    """再招待の前のトークンは例外 2 でも使えない (ADR D9、判断 16)。"""
    aws.put_user(status="suspended", revoked_sub=aws.ids.old_sub)

    status, body = call("GET", "/me", sub=aws.ids.old_sub)

    assert status == 403
    assert body["error"]["code"] == "USER_NOT_REGISTERED"


def test_me_with_an_unknown_sub_is_403(aws, call):
    status, body = call("GET", "/me")

    assert status == 403
    assert body["error"]["code"] == "USER_NOT_REGISTERED"


def test_me_without_claims_is_401(aws, event):
    from base_api.app import handler

    request = event("GET", "/me")
    request["requestContext"].pop("authorizer")

    assert handler(request, None)["statusCode"] == 401


# =============================================================================
# POST /me/totp-registered (例外 1: TOTP の登録を終えた)
# =============================================================================


@pytest.fixture
def pending_admin(aws):
    aws.put_admin(admin_state="pending")
    aws.cognito.add()
    return aws


def test_totp_registered_moves_a_pending_admin_to_awaiting(pending_admin, call):
    aws = pending_admin
    before = int(time.time())

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 200
    assert body["data"] == {"admin_state": "awaiting"}
    row = aws.profile()
    assert row["admin_state"] == "awaiting"
    assert before <= int(row["mfa_enabled_at"]) <= int(time.time())
    assert row["version"] == 2
    assert row["updated_by"] == aws.ids.user_id
    assert row["status"] == "active"
    assert aws.cognito.called("admin_user_global_sign_out") == [
        {"UserPoolId": "ap-northeast-1_EXMPLpool1", "Username": aws.ids.username}
    ]


def test_totp_registered_writes_one_audit_row(pending_admin, call):
    aws = pending_admin

    call("POST", "/me/totp-registered", groups=ADMIN)

    (item,) = aws.audit_items()
    assert item["action"] == "admin.totp_registered"
    assert item["PK"] == f"ACTOR#{aws.ids.user_id}"
    assert item["service"] == "base"
    assert item["result"] == "ok"
    assert item["target_user_id"] == aws.ids.user_id
    assert item["source_ip"] == "203.0.113.1"
    assert item["detail"]["mfa_enabled_at"] == aws.profile()["mfa_enabled_at"]


def test_totp_registered_checks_the_cognito_state_with_the_ledger_username(pending_admin, call):
    aws = pending_admin

    call("POST", "/me/totp-registered", groups=ADMIN)

    assert aws.cognito.called("admin_get_user")[0]["Username"] == aws.ids.username
    assert aws.cognito.called("admin_list_groups_for_user")[0]["Username"] == aws.ids.username


def test_totp_registered_writes_only_the_callers_profile(pending_admin, call):
    """自分の台帳の行だけを書く (例外 1 は ほかの行を書かない)。"""
    aws = pending_admin
    before = {(item["PK"], item["SK"]): item for item in aws.ledger.scan()["Items"]}

    call("POST", "/me/totp-registered", groups=ADMIN)

    after = {(item["PK"], item["SK"]): item for item in aws.ledger.scan()["Items"]}
    assert set(after) == set(before)
    changed = {key for key in after if after[key] != before[key]}
    assert changed == {(f"USER#{aws.ids.user_id}", "PROFILE")}


def test_totp_registered_works_while_suspended_after_a_reinvite(aws, call):
    """再招待の後 (台帳は停止のまま) でも呼べる (例外 1)。台帳の停止はそのまま。"""
    aws.put_admin(admin_state="pending", status="suspended", restore_requires="reinvite")
    aws.cognito.add()

    status, _body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 200
    row = aws.profile()
    assert row["admin_state"] == "awaiting"
    assert row["status"] == "suspended"
    assert row["restore_requires"] == "reinvite"


def assert_rejected(aws, status, body, code):
    assert status == 403
    assert body["error"]["code"] == code
    assert aws.profile().get("admin_state") == "pending"
    assert "mfa_enabled_at" not in aws.profile()
    assert aws.cognito.called("admin_user_global_sign_out") == []
    assert aws.audit_items() == []


def test_totp_registered_requires_the_admin_claim(pending_admin, call):
    status, body = call("POST", "/me/totp-registered", groups=())

    assert_rejected(pending_admin, status, body, "NOT_ADMIN")


def test_totp_registered_rejects_a_non_admin_user(aws, call):
    aws.put_user()
    aws.cognito.add(groups=())

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 403
    assert body["error"]["code"] in ("NOT_ADMIN", "ADMIN_NOT_PENDING")
    assert aws.audit_items() == []


@pytest.mark.parametrize("state", ["awaiting", "active"])
def test_totp_registered_requires_the_pending_state(aws, call, state):
    aws.put_admin(admin_state=state, mfa_enabled_at=1_762_000_000)
    aws.cognito.add()

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 403
    assert body["error"]["code"] == "ADMIN_NOT_PENDING"
    assert aws.profile()["admin_state"] == state
    assert aws.profile()["mfa_enabled_at"] == 1_762_000_000
    assert aws.cognito.called("admin_user_global_sign_out") == []
    assert aws.audit_items() == []


def test_totp_registered_requires_totp_in_cognito(aws, call):
    aws.put_admin(admin_state="pending")
    aws.cognito.add(totp=False)

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert_rejected(aws, status, body, "ADMIN_MFA_MISSING")


@pytest.mark.parametrize(
    "cognito_user",
    [{"enabled": False}, {"groups": ()}, {"sub": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}],
    ids=["disabled", "not-in-group", "other-sub"],
)
def test_totp_registered_requires_an_enabled_cognito_admin(aws, call, cognito_user):
    aws.put_admin(admin_state="pending")
    aws.cognito.add(**cognito_user)

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert_rejected(aws, status, body, "NOT_ADMIN")


def test_totp_registered_with_a_revoked_sub_is_403(aws, call):
    aws.put_admin(admin_state="pending", status="suspended", revoked_sub=aws.ids.old_sub)
    aws.cognito.add()

    status, body = call("POST", "/me/totp-registered", groups=ADMIN, sub=aws.ids.old_sub)

    assert status == 403
    assert body["error"]["code"] == "USER_NOT_REGISTERED"
    assert aws.audit_items() == []


def test_totp_registered_loses_a_race_without_writing(pending_admin, call):
    """確かめた後に台帳が変わったら (条件付きの更新が通らない) 書かずに 403。"""
    aws = pending_admin
    aws.cognito.on_sign_out = lambda: aws.ledger.update_item(
        Key={"PK": f"USER#{aws.ids.user_id}", "SK": "PROFILE"},
        UpdateExpression="SET admin_state = :s",
        ExpressionAttributeValues={":s": "awaiting"},
    )

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 403
    assert body["error"]["code"] == "ADMIN_NOT_PENDING"
    assert "mfa_enabled_at" not in aws.profile()
    assert aws.audit_items() == []


def client_error(code: str, status: int) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "x")


def test_totp_registered_does_not_write_when_the_sign_out_fails(pending_admin, call):
    """失効が失敗したら台帳を書かない。本人は呼び直せる。"""
    aws = pending_admin
    aws.cognito.sign_out_error = client_error("InternalErrorException", 500)

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert (status, body["error"]["code"]) == (503, "TEMPORARILY_UNAVAILABLE")
    assert aws.profile()["admin_state"] == "pending"
    assert "mfa_enabled_at" not in aws.profile()
    assert aws.dynamodb.calls == []
    assert aws.audit_items() == []


@pytest.mark.parametrize(
    "failure",
    [client_error("ValidationException", 400), client_error("InternalServerError", 500)],
    ids=["definite", "unknown"],
)
def test_totp_registered_can_be_retried_after_the_ledger_fails(pending_admin, call, failure):
    """失効の後で台帳が失敗しても 503 で、呼び直すと確認待ちになる。"""
    aws = pending_admin
    aws.dynamodb.plan = [failure] * 4

    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert (status, body["error"]["code"]) == (503, "TEMPORARILY_UNAVAILABLE")
    assert aws.profile()["admin_state"] == "pending"
    assert aws.audit_items() == []

    aws.dynamodb.plan = []
    status, body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 200
    assert aws.profile()["admin_state"] == "awaiting"
    assert len(aws.cognito.called("admin_user_global_sign_out")) == 2
    assert len(aws.audit_items()) == 1


def test_totp_registered_retries_a_transaction_in_progress(pending_admin, call):
    aws = pending_admin
    aws.dynamodb.plan = [client_error("TransactionInProgressException", 400)]

    status, _body = call("POST", "/me/totp-registered", groups=ADMIN)

    assert status == 200
    first, second = aws.dynamodb.calls
    assert first["ClientRequestToken"] == second["ClientRequestToken"]


def test_totp_registered_records_the_operation_id(pending_admin, call):
    """反映の確かめは状態ではなく操作 ID で行う (台帳の last_op_id と監査の行が同じトランザクション)。"""
    from service_platform.ids import ULID_PATTERN

    aws = pending_admin

    assert call("POST", "/me/totp-registered", groups=ADMIN)[0] == 200

    assert ULID_PATTERN.fullmatch(aws.profile()["last_op_id"])


def test_totp_registered_checks_state_without_changing_mfa_settings(pending_admin, call, monkeypatch):
    from unittest.mock import Mock

    aws = pending_admin
    preference = Mock(side_effect=AssertionError("MFA settings must not be changed"))
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    assert call("POST", "/me/totp-registered", groups=ADMIN)[0] == 200
    preference.assert_not_called()
    assert len(aws.cognito.called("admin_user_global_sign_out")) == 1
    assert aws.profile()["admin_state"] == "awaiting"


@pytest.fixture
def active_admin(aws):
    aws.put_admin(admin_state="active", mfa_enabled_at=1_762_000_000)
    aws.cognito.add()
    return aws


def test_passkey_mfa_is_enabled_only_after_registration_with_audit(active_admin, call, monkeypatch):
    from unittest.mock import Mock

    aws = active_admin
    before = aws.profile()

    def enable(**kwargs):
        assert len(aws.audit_items()) == 1
        assert kwargs == {
            "UserPoolId": "ap-northeast-1_EXMPLpool1",
            "Username": aws.ids.username,
            "SoftwareTokenMfaSettings": {"Enabled": True, "PreferredMfa": True},
            "WebAuthnMfaSettings": {"Enabled": True},
        }
        return {}

    preference = Mock(side_effect=enable)
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    status, body = call("POST", "/me/passkey-mfa", groups=ADMIN)
    assert (status, body["data"]) == (200, {"passkey_mfa_enabled": True})
    preference.assert_called_once()
    assert aws.profile() == before
    records = {row["action"]: row for row in aws.audit_items()}
    assert set(records) == {"admin.passkey_mfa_requested", "admin.passkey_mfa_enabled"}
    assert all(row["target_user_id"] == aws.ids.user_id for row in records.values())
    assert records["admin.passkey_mfa_enabled"]["result"] == "ok"
    assert "code" not in records["admin.passkey_mfa_enabled"]


@pytest.mark.parametrize("state", ["pending", "awaiting"])
def test_passkey_mfa_rejects_inactive_admins(aws, call, state):
    aws.put_admin(admin_state=state, mfa_enabled_at=1_762_000_000)
    aws.cognito.add()
    status, body = call("POST", "/me/passkey-mfa", groups=ADMIN)
    assert (status, body["error"]["code"]) == (403, "ADMIN_NOT_ACTIVE")
    assert aws.cognito.called("admin_set_user_mfa_preference") == []
    assert aws.audit_items() == []


@pytest.mark.parametrize(
    "invalid", ["suspended", "no_claim", "no_totp", "old_session", "no_group", "disabled", "other_sub"]
)
def test_passkey_mfa_requires_the_existing_admin_guard(aws, call, invalid):
    aws.put_admin(
        admin_state="active", mfa_enabled_at=1_762_000_000, status="suspended" if invalid == "suspended" else "active"
    )
    aws.cognito.add(
        totp=invalid != "no_totp",
        groups=() if invalid == "no_group" else ADMIN,
        enabled=invalid != "disabled",
        **({"sub": "other-sub"} if invalid == "other_sub" else {}),
    )
    status, _ = call(
        "POST",
        "/me/passkey-mfa",
        groups=() if invalid == "no_claim" else ADMIN,
        auth_time=1_762_000_000 if invalid == "old_session" else 1_800_000_000,
    )
    assert status == 403
    assert aws.cognito.called("admin_set_user_mfa_preference") == []
    assert aws.audit_items() == []


@pytest.mark.parametrize("failure_at", ["audit", "cognito"])
def test_passkey_mfa_can_be_retried_without_changing_the_ledger(active_admin, call, monkeypatch, failure_at):
    from unittest.mock import Mock

    aws = active_admin
    before = aws.profile()
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    if failure_at == "audit":
        aws.dynamodb.plan = [client_error("InternalServerError", 500)]
    else:
        preference.side_effect = client_error("InternalErrorException", 500)
    status, body = call("POST", "/me/passkey-mfa", groups=ADMIN)
    assert (status, body["error"]["code"]) == (503, "TEMPORARILY_UNAVAILABLE")
    assert aws.profile() == before
    if failure_at == "audit":
        preference.assert_not_called()
    preference.side_effect = None
    assert call("POST", "/me/passkey-mfa", groups=ADMIN)[0] == 200
    assert aws.profile() == before


@pytest.mark.parametrize(
    "failure, status, code, message",
    [
        (client_error("InvalidParameterException", 400), 409, "PASSKEY_REQUIRED", "先にパスキーを登録してください"),
        (client_error("InternalErrorException", 500), 503, "TEMPORARILY_UNAVAILABLE", None),
    ],
)
def test_passkey_mfa_records_cognito_failure(active_admin, call, monkeypatch, failure, status, code, message):
    from unittest.mock import Mock

    aws = active_admin
    before = aws.profile()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", Mock(side_effect=failure))
    actual_status, body = call("POST", "/me/passkey-mfa", groups=ADMIN)
    assert (actual_status, body["error"]["code"]) == (status, code)
    if message is not None:
        assert body["error"]["message"] == message
    else:
        from base_api.me import TemporarilyUnavailableError

        assert body["error"]["message"] == str(TemporarilyUnavailableError())
    results = [row for row in aws.audit_items() if row["action"] == "admin.passkey_mfa_enabled"]
    assert len(results) == 1
    assert results[0]["result"] == "failed"
    assert results[0]["code"] == code
    assert aws.profile() == before


@pytest.mark.parametrize("failure", [None, client_error("InternalErrorException", 500)])
def test_passkey_mfa_result_audit_failure_does_not_change_response(active_admin, call, monkeypatch, failure):
    from unittest.mock import Mock

    from service_platform import audit

    aws = active_admin
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", Mock(side_effect=failure))
    monkeypatch.setattr(audit._client(), "put_item", Mock(side_effect=client_error("InternalServerError", 500)))
    status, body = call("POST", "/me/passkey-mfa", groups=ADMIN)
    assert status == (503 if failure else 200)
    if failure:
        assert body["error"]["code"] == "TEMPORARILY_UNAVAILABLE"
    assert [row["action"] for row in aws.audit_items()] == ["admin.passkey_mfa_requested"]
