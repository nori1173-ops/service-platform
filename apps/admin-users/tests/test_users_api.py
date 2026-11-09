"""`admin_users.app` の利用者の管理の API のテスト（計画 2.6 / 4.2、ADR-001 D3 / D8 / D9 / D12）。

台帳・監査・Cognito (moto + 薄い代理) に実際に読み書きし、JWT クレーム入りのイベントでハンドラを
一気通貫に呼ぶ。自分自身への操作・最後の admin・admin の守りはサーバ側で必ず落ちることを確かめる。
"""

import logging

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

UNKNOWN_ID = "01JBZ00000000000000000NONE"

ADMIN_ROUTES = [
    ("GET", "/health", None),
    ("GET", "/users", None),
    ("POST", "/users", {"email": "x@example.com", "kind": "employee", "role": "user"}),
    ("GET", f"/users/{UNKNOWN_ID}", None),
    ("PUT", f"/users/{UNKNOWN_ID}/profile", {"note": "x"}),
    ("PUT", f"/users/{UNKNOWN_ID}/email", {"email": "y@example.com"}),
    ("POST", f"/users/{UNKNOWN_ID}/suspend", {}),
    ("POST", f"/users/{UNKNOWN_ID}/restore", {"verified_method": "phone"}),
    ("POST", f"/users/{UNKNOWN_ID}/reinvite", {"reason": "x"}),
    ("POST", f"/users/{UNKNOWN_ID}/admin-activation", {"method": "phone"}),
    ("POST", f"/users/{UNKNOWN_ID}/temporary-password", None),
    ("PUT", f"/users/{UNKNOWN_ID}/admin", {"admin": False}),
]


def assert_error(result, status, code):
    actual_status, body = result
    assert actual_status == status, body
    assert body["error"]["code"] == code, body


# =============================================================================
# admin の守り (ADR D9)
# =============================================================================


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_a_user_without_the_admin_claim_is_rejected_everywhere(aws, call, method, path, body):
    user = aws.make_user("user@example.com")

    assert_error(call(method, path, body=body, caller=user), 403, "ADMIN_NOT_ACTIVE")


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_a_pending_admin_is_rejected_everywhere(aws, call, method, path, body):
    """最初の admin は scripts で作られた直後は準備中。admin の API は 403 (dev の確認と同じ)。"""
    pending = aws.make_admin("pending@example.com", admin_state="pending", totp=False, mfa_enabled_at=None)

    assert_error(call(method, path, body=body, caller=pending), 403, "ADMIN_NOT_ACTIVE")


def test_an_unregistered_caller_is_rejected(aws, call):
    stranger = aws.make_user("stranger@example.com", ledger_row=False, role="admin", in_group=True)

    assert_error(call("GET", "/users", caller=stranger), 403, "USER_NOT_REGISTERED")


def test_a_session_from_before_the_totp_registration_is_rejected(aws, call):
    assert_error(call("GET", "/users", auth_time=aws.mfa_enabled_at), 403, "ADMIN_SESSION_STALE")


def test_a_caller_removed_from_the_group_is_rejected(aws, call, actor):
    aws.cognito._client.admin_remove_user_from_group(UserPoolId=aws.pool_id, Username=actor.username, GroupName="admin")

    assert_error(call("GET", "/users"), 403, "ADMIN_NOT_ACTIVE")


def test_a_caller_without_totp_in_cognito_is_rejected(aws, call, actor):
    aws.cognito.totp.discard(actor.username)

    assert_error(call("GET", "/users"), 403, "ADMIN_MFA_MISSING")


def test_a_disabled_caller_is_rejected(aws, call, actor):
    aws.cognito._client.admin_disable_user(UserPoolId=aws.pool_id, Username=actor.username)

    assert_error(call("GET", "/users"), 403, "ADMIN_NOT_ACTIVE")


def test_health_for_an_active_admin(call):
    status, body = call("GET", "/health")

    assert status == 200
    assert body["data"] == {"status": "ok"}


@pytest.mark.parametrize(
    "method,path", [("POST", f"/users/{UNKNOWN_ID}/password-reset"), ("DELETE", f"/users/{UNKNOWN_ID}")]
)
def test_removed_routes_are_gone(call, method, path):
    """お客様はパスワードを持たない。利用をやめるのは停止 (計画 6 章)。"""
    assert call(method, path)[0] == 404


# =============================================================================
# 招待 POST /users
# =============================================================================

CUSTOMER = {
    "email": "Customer@Example.com",
    "kind": "customer",
    "role": "user",
    "org_name": "組織B",
    "contact": "窓口の電話 000",
    "note": "初回",
}


def test_invite_a_customer(aws, call, actor):
    status, body = call("POST", "/users", body=CUSTOMER)

    assert status == 201, body
    data = body["data"]
    assert "temporary_password" not in data
    assert data["email"] == "customer@example.com"
    row = aws.profile(data["user_id"])
    assert row["status"] == "active"
    assert row["kind"] == "customer"
    assert row["role"] == "user"
    assert row["org_name"] == "組織B"
    assert row["contact"] == "窓口の電話 000"
    assert row["note"] == "初回"
    assert row["created_by"] == actor.user_id
    assert row["version"] == 1
    assert "admin_state" not in row
    assert row["GSI1SK"] == f"customer@example.com#{data['user_id']}"
    user = aws.cognito.user(row["cognito_username"])
    assert aws.attributes(user)["sub"] == row["sub"]
    assert aws.sub_row(row["sub"])["user_id"] == data["user_id"]
    (request,) = aws.cognito.called("admin_create_user")
    assert request["DesiredDeliveryMediums"] == ["EMAIL"]
    assert "TemporaryPassword" not in request


def test_invite_writes_an_audit_row_without_the_contact(aws, call, actor):
    _status, body = call("POST", "/users", body=CUSTOMER)

    (item,) = aws.audit_items("user.invite")
    assert item["PK"] == f"ACTOR#{actor.user_id}"
    assert item["target_user_id"] == body["data"]["user_id"]
    assert item["service"] == "admin-users"
    assert "窓口の電話" not in str(item)


def test_invite_an_admin_returns_the_temporary_password_once(aws, call, caplog):
    caplog.set_level(logging.DEBUG)

    status, body = call(
        "POST", "/users", body={"email": "admin2@example.com", "kind": "employee", "role": "admin", "note": "社員"}
    )

    assert status == 201, body
    password = body["data"]["temporary_password"]
    assert len(password) >= 12
    row = aws.profile(body["data"]["user_id"])
    assert row["role"] == "admin"
    assert row["admin_state"] == "pending"
    assert "mfa_enabled_at" not in row
    assert aws.cognito.groups(row["cognito_username"]) == ["admin"]
    (request,) = aws.cognito.called("admin_create_user")
    assert request["MessageAction"] == "SUPPRESS"
    assert password not in caplog.text
    assert password not in str(aws.audit_items())
    assert password not in str(aws.ledger.scan()["Items"])


def test_admins_must_be_employees(call):
    assert_error(
        call("POST", "/users", body={"email": "a@example.com", "kind": "customer", "role": "admin", "org_name": "x"}),
        400,
        "VALIDATION_ERROR",
    )


@pytest.mark.parametrize(
    "body",
    [
        {**CUSTOMER, "org_name": None},
        {**CUSTOMER, "org_name": "  "},
        {**CUSTOMER, "org_name": "あ" * 101},
        {**CUSTOMER, "email": "not-an-email"},
        {**CUSTOMER, "email": "a" * 250 + "@example.com"},
        {**CUSTOMER, "kind": "partner"},
        {**CUSTOMER, "role": "owner"},
        {**CUSTOMER, "contact": "x" * 201},
        {**CUSTOMER, "note": "x" * 501},
        {**CUSTOMER, "note": 1},
    ],
    ids=["no-org", "blank-org", "long-org", "bad-email", "long-email", "kind", "role", "contact", "note", "note-type"],
)
def test_invite_validates_the_body(aws, call, body):
    assert_error(call("POST", "/users", body=body), 400, "VALIDATION_ERROR")
    assert aws.cognito.called("admin_create_user") == []


def test_invite_rejects_a_duplicate_email_in_cognito(aws, call):
    aws.make_user("customer@example.com", ledger_row=False)

    assert_error(call("POST", "/users", body=CUSTOMER), 409, "USER_EXISTS")
    assert aws.cognito.called("admin_create_user") == []


def test_invite_rejects_a_duplicate_email_in_the_ledger(aws, call):
    existing = aws.make_user("customer@example.com")
    aws.cognito._client.admin_delete_user(UserPoolId=aws.pool_id, Username=existing.username)

    assert_error(call("POST", "/users", body=CUSTOMER), 409, "USER_EXISTS")


INVITE_ADMIN = {"email": "admin3@example.com", "kind": "employee", "role": "admin"}
KNOWN_PASSWORD = "KnownTemporary12345678"


@pytest.fixture
def known_password(monkeypatch):
    """失敗の経路でも一時パスワードが漏れないことを縛るため、決まった値にする。"""
    from admin_users import passwords

    monkeypatch.setattr(passwords, "generate", lambda: KNOWN_PASSWORD)
    return KNOWN_PASSWORD


def assert_secret_kept(password, caplog, capsys, *texts):
    out, err = capsys.readouterr()
    for text in (caplog.text, out, err, *texts):
        assert password not in text


def test_invite_rolls_back_the_cognito_user_when_the_ledger_fails(aws, call, known_password, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    aws.dynamodb.plan = [aws.client_error("ValidationException", 400, "TransactWriteItems")]

    status, body = call("POST", "/users", body=INVITE_ADMIN)

    assert status == 500
    assert body["error"]["code"] == "LEDGER_WRITE_FAILED"
    assert aws.cognito.called("admin_delete_user")
    assert aws.cognito.user("admin3@example.com") is None
    assert len(aws.dynamodb.calls) == 1
    assert_secret_kept(known_password, caplog, capsys, str(body))


def test_invite_does_not_roll_back_when_the_outcome_is_unknown(aws, call, known_password, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    aws.dynamodb.plan = [aws.client_error("InternalServerError", 500, "TransactWriteItems")] * 4

    status, body = call("POST", "/users", body=INVITE_ADMIN)

    assert (status, body["error"]["code"]) == (503, "OUTCOME_UNKNOWN")
    assert aws.cognito.called("admin_delete_user") == []
    assert aws.cognito.user("admin3@example.com") is not None
    assert len({call["ClientRequestToken"] for call in aws.dynamodb.calls}) == 1
    assert len(aws.dynamodb.calls) == 4
    assert_secret_kept(known_password, caplog, capsys, str(body))


@pytest.mark.parametrize("code", ["TransactionInProgressException", "ThrottlingException"])
def test_invite_retries_with_the_same_token(aws, call, code):
    aws.dynamodb.plan = [aws.client_error(code, 400, "TransactWriteItems")]

    status, body = call("POST", "/users", body=CUSTOMER)

    assert status == 201, body
    first, second = aws.dynamodb.calls
    assert first["ClientRequestToken"] == second["ClientRequestToken"]
    assert aws.cognito.called("admin_delete_user") == []
    assert aws.sub_row(aws.profile(body["data"]["user_id"])["sub"])


def test_invite_succeeds_when_only_the_response_was_lost(aws, call, known_password, caplog, capsys):
    """台帳はコミット済みで応答だけを失った。補償せず、強い整合で読み直して成功にする (一時パスワードを失わない)。"""
    caplog.set_level(logging.DEBUG)
    aws.dynamodb.plan = [("after", ReadTimeoutError(endpoint_url="https://dynamodb"))]

    status, body = call("POST", "/users", body=INVITE_ADMIN)

    assert status == 201, body
    assert body["data"]["temporary_password"] == known_password
    assert aws.cognito.called("admin_delete_user") == []
    row = aws.profile(body["data"]["user_id"])
    assert aws.sub_row(row["sub"])["user_id"] == row["user_id"]
    assert_secret_kept(known_password, caplog, capsys)


def test_invite_is_outcome_unknown_when_the_read_back_fails(aws, call, monkeypatch, known_password, caplog, capsys):
    from admin_users import users

    def broken(*_args):
        raise aws.client_error("InternalServerError", 500, "GetItem")

    caplog.set_level(logging.DEBUG)
    aws.dynamodb.plan = [aws.client_error("ValidationException", 400, "TransactWriteItems")]
    monkeypatch.setattr(users, "maps", broken)

    status, body = call("POST", "/users", body=INVITE_ADMIN)

    assert (status, body["error"]["code"]) == (503, "OUTCOME_UNKNOWN")
    assert aws.cognito.called("admin_delete_user") == []
    assert_secret_kept(known_password, caplog, capsys, str(body))


def test_invite_reports_a_failed_compensation(aws, call, known_password, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    aws.dynamodb.plan = [aws.client_error("ValidationException", 400, "TransactWriteItems")]
    aws.cognito.failures["admin_delete_user"] = aws.client_error("InternalErrorException", 500, "AdminDeleteUser")

    status, body = call("POST", "/users", body=INVITE_ADMIN)

    assert (status, body["error"]["code"]) == (500, "LEDGER_WRITE_FAILED")
    assert "残っている可能性" in body["error"]["message"]
    assert "compensation failed" in caplog.text
    assert_secret_kept(known_password, caplog, capsys, str(body))


# =============================================================================
# 一覧と詳細
# =============================================================================


def test_list_joins_the_ledger_and_cognito(aws, call, actor):
    customer = aws.make_user("customer@example.com", contact="秘密の連絡先")
    aws.make_user("orphan@example.com", ledger_row=False)

    status, body = call("GET", "/users")

    assert status == 200
    users = {user["email"]: user for user in body["data"]["users"]}
    assert set(users) == {"actor@example.com", "customer@example.com", "orphan@example.com"}
    row = users["customer@example.com"]
    assert row["user_id"] == customer.user_id
    assert row["in_ledger"] is True
    assert row["status"] == "active"
    assert row["kind"] == "customer"
    assert row["cognito_status"] == "CONFIRMED"
    assert row["cognito_enabled"] is True
    assert users["orphan@example.com"]["in_ledger"] is False
    assert users["orphan@example.com"]["user_id"] is None
    assert body["data"]["self_user_id"] == actor.user_id
    assert "秘密の連絡先" not in str(body)


def test_detail_shows_the_contact_factors_and_admin_state(aws, call):
    target = aws.make_admin("other@example.com", admin_state="awaiting", contact="連絡先", note="メモ")
    aws.cognito.factors[target.username] = ["PASSWORD", "WEB_AUTHN"]

    status, body = call("GET", f"/users/{target.user_id}")

    assert status == 200
    data = body["data"]
    assert data["user_id"] == target.user_id
    assert data["contact"] == "連絡先"
    assert data["note"] == "メモ"
    assert data["factors"] == {"password": True, "web_authn": True, "email_otp": False}
    assert data["totp"] is True
    assert data["is_admin"] is True
    assert data["admin_state"] == "awaiting"
    assert data["mfa_enabled_at"] == "2023-11-14T22:13:20Z"
    assert data["assignments"] == []
    assert data["reinvite_in_progress"] is False
    assert data["email_pending"] is None
    assert data["cognito_enabled"] is True


@pytest.mark.parametrize("user_id", [UNKNOWN_ID, "not-a-ulid", "01JBZ0000000000000000USER%23"])
def test_detail_of_an_unknown_user_is_404(call, user_id):
    assert_error(call("GET", f"/users/{user_id}"), 404, "USER_NOT_FOUND")


# =============================================================================
# 台帳の項目と email の変更
# =============================================================================


def test_update_profile(aws, call, actor):
    target = aws.make_user("customer@example.com", contact="古い", note="古いメモ")

    status, body = call(
        "PUT", f"/users/{target.user_id}/profile", body={"org_name": "組織C", "contact": "新しい", "note": ""}
    )

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert row["org_name"] == "組織C"
    assert row["contact"] == "新しい"
    assert "note" not in row
    assert row["version"] == 2
    assert row["updated_by"] == actor.user_id
    (item,) = aws.audit_items("user.profile_update")
    assert item["detail"]["fields"] == ["contact", "note", "org_name"]
    assert "新しい" not in str(item)


def test_update_profile_keeps_the_org_name_of_a_customer(aws, call):
    target = aws.make_user("customer@example.com")

    assert_error(call("PUT", f"/users/{target.user_id}/profile", body={"org_name": ""}), 400, "VALIDATION_ERROR")
    assert_error(call("PUT", f"/users/{target.user_id}/profile", body={}), 400, "VALIDATION_ERROR")


def test_change_email(aws, call):
    target = aws.make_user("old@example.com")

    status, body = call("PUT", f"/users/{target.user_id}/email", body={"email": "New@Example.com"})

    assert status == 200, body
    attributes = aws.attributes(aws.cognito.user(target.username))
    assert attributes["email"] == "new@example.com"
    assert attributes["email_verified"] == "true"
    row = aws.profile(target.user_id)
    assert row["email"] == "new@example.com"
    assert row["GSI1SK"] == f"new@example.com#{target.user_id}"
    assert len(aws.audit_items("user.email_change")) == 1


def test_change_email_rejects_an_address_in_use(aws, call):
    target = aws.make_user("old@example.com")
    aws.make_user("taken@example.com")

    assert_error(call("PUT", f"/users/{target.user_id}/email", body={"email": "taken@example.com"}), 409, "USER_EXISTS")
    assert aws.profile(target.user_id)["email"] == "old@example.com"


# =============================================================================
# 停止と有効に戻す
# =============================================================================


def test_suspend(aws, call):
    target = aws.make_user("customer@example.com")

    status, body = call("POST", f"/users/{target.user_id}/suspend", body={"reason": "端末の紛失"})

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert row["status"] == "suspended"
    assert row["suspend_reason"] == "端末の紛失"
    assert aws.cognito.user(target.username)["Enabled"] is False
    assert aws.cognito.called("admin_user_global_sign_out")[0]["Username"] == target.username
    (item,) = aws.audit_items("user.suspend")
    assert item["detail"]["reason"] == "端末の紛失"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/suspend", {}),
        ("POST", "/reinvite", {"reason": "x"}),
        ("PUT", "/admin", {"admin": False}),
        ("POST", "/admin-activation", {"method": "phone"}),
        ("POST", "/temporary-password", None),
        ("POST", "/restore", {"verified_method": "phone"}),
    ],
)
def test_self_operations_are_forbidden(aws, call, actor, method, path, body):
    assert_error(call(method, f"/users/{actor.user_id}{path}", body=body), 403, "SELF_OPERATION_FORBIDDEN")
    assert aws.profile(actor.user_id)["status"] == "active"


def test_restore_after_a_suspension(aws, call):
    target = aws.make_user("customer@example.com", status="suspended")
    aws.cognito._client.admin_disable_user(UserPoolId=aws.pool_id, Username=target.username)

    status, body = call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone", "note": "窓口"})

    assert status == 200, body
    assert aws.profile(target.user_id)["status"] == "active"
    assert aws.cognito.user(target.username)["Enabled"] is True
    (item,) = aws.audit_items("user.restore")
    assert item["detail"]["verified_method"] == "phone"


def test_restore_after_a_reinvite_requires_a_passkey_on_the_new_user(aws, call):
    target = aws.make_user("customer@example.com")
    aws.cognito.factors[target.username] = ["EMAIL_OTP", "WEB_AUTHN"]
    assert call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "盗難"})[0] == 200

    restore = {"verified_method": "phone"}
    assert_error(call("POST", f"/users/{target.user_id}/restore", body=restore), 409, "PASSKEY_NOT_REGISTERED")

    aws.cognito.factors[aws.profile(target.user_id)["cognito_username"]] = ["EMAIL_OTP", "WEB_AUTHN"]
    status, _body = call("POST", f"/users/{target.user_id}/restore", body=restore)

    assert status == 200
    row = aws.profile(target.user_id)
    assert row["status"] == "active"
    assert "restore_requires" not in row


@pytest.mark.parametrize("step", ["admin_disable_user", "admin_user_global_sign_out", "admin_delete_user"])
def test_an_interrupted_reinvite_cannot_be_restored_with_the_old_passkey(aws, call, step):
    """古い sub とパスキーが残ったまま止まった再招待からは戻せない (ADR D8 手順 2)。やり直して完了すれば戻せる。"""
    target = aws.make_user("customer@example.com")
    aws.cognito.factors[target.username] = ["EMAIL_OTP", "WEB_AUTHN"]
    aws.cognito.failures[step] = aws.client_error("InternalErrorException", 500, step)

    with pytest.raises(ClientError):
        call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "盗難"})

    row = aws.profile(target.user_id)
    assert row["reinvite_in_progress"] is True
    assert row["sub"] == target.sub
    restore = {"verified_method": "phone"}
    assert_error(call("POST", f"/users/{target.user_id}/restore", body=restore), 409, "REINVITE_INCOMPLETE")
    assert aws.profile(target.user_id)["status"] == "suspended"

    del aws.cognito.failures[step]
    assert call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "盗難"})[0] == 200
    row = aws.profile(target.user_id)
    assert "reinvite_in_progress" not in row
    assert row["previous_sub"] == target.sub
    aws.cognito.factors[row["cognito_username"]] = ["EMAIL_OTP", "WEB_AUTHN"]
    assert call("POST", f"/users/{target.user_id}/restore", body=restore)[0] == 200


def test_restore_after_a_reinvite_without_the_revoked_old_sub_is_refused(aws, call):
    """再招待の後の印だけがあって、古い sub の無効の印が確かめられなければ戻さない (fail-closed)。"""
    target = aws.make_user("customer@example.com", status="suspended", restore_requires="reinvite")
    aws.cognito.factors[target.username] = ["EMAIL_OTP", "WEB_AUTHN"]

    assert_error(
        call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"}), 409, "REINVITE_INCOMPLETE"
    )


def test_restore_a_suspended_admin(aws, call):
    """通常の停止 (再招待なし) の admin は、別の admin の restore で戻せる。admin の状態と mfa_enabled_at は保つ。"""
    target = aws.make_admin("other@example.com")
    assert call("POST", f"/users/{target.user_id}/suspend", body={"reason": "端末の一時紛失"})[0] == 200
    assert target.user_id not in aws.admin_set()

    status, body = call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"})

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert (row["status"], row["admin_state"]) == ("active", "active")
    assert row["mfa_enabled_at"] == aws.mfa_enabled_at
    assert target.user_id in aws.admin_set()
    assert aws.cognito.user(target.username)["Enabled"] is True
    (item,) = aws.audit_items("user.restore")
    assert item["detail"]["admin"] is True
    assert call("GET", "/users", caller=target)[0] == 200


def test_restore_a_suspended_admin_requires_totp(aws, call):
    target = aws.make_admin("other@example.com", status="suspended")
    aws.cognito.totp.discard(target.username)

    assert_error(
        call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"}), 409, "ADMIN_NOT_READY"
    )
    assert aws.profile(target.user_id)["status"] == "suspended"


def test_restore_of_a_reinvited_admin_uses_the_activation(aws, call):
    target = aws.make_admin("other@example.com")
    assert call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "TOTP の紛失"})[0] == 200

    assert_error(
        call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"}), 409, "USE_ADMIN_ACTIVATION"
    )


def test_restore_requires_a_suspended_user(aws, call):
    target = aws.make_user("customer@example.com")

    assert_error(
        call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"}), 409, "USER_NOT_SUSPENDED"
    )


def test_restore_validates_the_method(aws, call):
    target = aws.make_user("customer@example.com", status="suspended")

    assert_error(
        call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "email"}), 400, "VALIDATION_ERROR"
    )


# =============================================================================
# 再招待 (ADR D8 手順 2 / 4)
# =============================================================================


def test_reinvite_a_customer_keeps_the_user_id_and_revokes_the_old_sub(aws, call):
    from service_platform import ledger

    target = aws.make_user("customer@example.com")

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "パスキーの盗難"})

    assert status == 200, body
    assert "temporary_password" not in body["data"]
    assert aws.cognito.user(target.username) is None
    row = aws.profile(target.user_id)
    assert row["sub"] != target.sub
    assert row["cognito_username"] != target.username
    assert row["status"] == "suspended"
    assert row["restore_requires"] == "reinvite"
    assert row["reinvite_count"] == 1
    assert aws.sub_row(row["sub"])["user_id"] == target.user_id
    assert aws.sub_row(target.sub)["revoked_at"]
    with pytest.raises(ledger.UserNotRegisteredError):
        ledger.resolve_user(target.sub, allow_suspended=True)
    assert ledger.resolve_user(row["sub"], allow_suspended=True).user_id == target.user_id
    created = aws.cognito.called("admin_create_user")[-1]
    assert created["DesiredDeliveryMediums"] == ["EMAIL"]
    steps = [item["detail"]["step"] for item in aws.audit_items("user.reinvite")]
    assert steps == ["suspend", "reinvite"]
    assert aws.audit_items("user.reinvite")[-1]["detail"]["reason"] == "パスキーの盗難"


def test_reinvite_an_admin_resets_the_admin_state(aws, call, caplog):
    caplog.set_level(logging.DEBUG)
    target = aws.make_admin("other@example.com", admin_verified_by="someone", admin_verified_method="phone")

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "TOTP の紛失"})

    assert status == 200, body
    password = body["data"]["temporary_password"]
    row = aws.profile(target.user_id)
    assert row["admin_state"] == "pending"
    assert "mfa_enabled_at" not in row
    assert "admin_verified_by" not in row
    assert "admin_verified_method" not in row
    assert aws.cognito.groups(row["cognito_username"]) == ["admin"]
    assert aws.cognito.called("admin_create_user")[-1]["MessageAction"] == "SUPPRESS"
    assert password not in caplog.text
    assert password not in str(aws.audit_items())
    assert password not in str(aws.ledger.scan()["Items"])


def test_reinvite_to_a_new_address_records_the_prior_verification(aws, call):
    target = aws.make_user("old@example.com")

    status, body = call(
        "POST",
        f"/users/{target.user_id}/reinvite",
        body={
            "reason": "受信箱の乗っ取り",
            "email": "new@example.com",
            "verified_before": {"method": "phone", "note": "窓口"},
        },
    )

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert row["email"] == "new@example.com"
    assert row["GSI1SK"] == f"new@example.com#{target.user_id}"
    assert aws.attributes(aws.cognito.user(row["cognito_username"]))["email"] == "new@example.com"
    assert row["identity_verified_by"]
    assert row["identity_verified_method"] == "phone"
    (item,) = aws.audit_items("user.identity_verified")
    assert item["detail"]["method"] == "phone"


def test_reinvite_rejects_an_address_of_another_user(aws, call):
    target = aws.make_user("old@example.com")
    aws.make_user("taken@example.com")

    result = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "x", "email": "taken@example.com"})

    assert_error(result, 409, "USER_EXISTS")
    assert aws.profile(target.user_id)["status"] == "active"
    assert aws.cognito.user(target.username) is not None


def test_reinvite_replaces_an_orphan_left_by_an_earlier_attempt(aws, call):
    """前の回が古い利用者を消した後で止まり、台帳に無い利用者が残っていても続きから進める。"""
    target = aws.make_user(
        "customer@example.com", status="suspended", restore_requires="reinvite", reinvite_in_progress=True
    )
    aws.cognito._client.admin_delete_user(UserPoolId=aws.pool_id, Username=target.username)
    orphan = aws.make_user("customer@example.com", ledger_row=False)

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "やり直し"})

    assert status == 200, body
    assert aws.cognito.user(orphan.username) is None
    row = aws.profile(target.user_id)
    assert aws.sub_row(row["sub"])["user_id"] == target.user_id
    assert aws.sub_row(target.sub)["revoked_at"]
    assert "reinvite_in_progress" not in row
    assert [item["detail"]["step"] for item in aws.audit_items("user.reinvite")] == ["reinvite"]


def test_reinvite_requires_a_reason(aws, call):
    target = aws.make_user("customer@example.com")

    assert_error(call("POST", f"/users/{target.user_id}/reinvite", body={}), 400, "VALIDATION_ERROR")


def test_reinvite_rolls_back_the_new_cognito_user_when_the_link_fails(aws, call):
    target = aws.make_user("customer@example.com")
    aws.dynamodb.plan = [None, aws.client_error("ValidationException", 400, "TransactWriteItems")]

    assert_error(call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "x"}), 500, "LEDGER_WRITE_FAILED")
    row = aws.profile(target.user_id)
    assert row["sub"] == target.sub
    assert row["status"] == "suspended"
    assert row["reinvite_in_progress"] is True
    assert aws.cognito.user("customer@example.com") is None

    status, _body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "x"})

    assert status == 200
    assert aws.sub_row(aws.profile(target.user_id)["sub"])["user_id"] == target.user_id


def test_reinvite_of_an_admin_keeps_the_temporary_password_out_of_failures(aws, call, known_password, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    target = aws.make_admin("other@example.com")
    aws.dynamodb.plan = [None, aws.client_error("ValidationException", 400, "TransactWriteItems")]

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "TOTP の紛失"})

    assert status == 500
    assert_secret_kept(known_password, caplog, capsys, str(body))


# =============================================================================
# admin の有効化・一時パスワード・admin を外す
# =============================================================================


def test_activate_an_awaiting_admin(aws, call, actor):
    target = aws.make_admin("other@example.com", admin_state="awaiting", mfa_enabled_at=1_750_000_000)

    status, body = call(
        "POST", f"/users/{target.user_id}/admin-activation", body={"method": "in_person", "note": "対面で確認"}
    )

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert row["admin_state"] == "active"
    assert row["admin_verified_by"] == actor.user_id
    assert row["admin_verified_method"] == "in_person"
    assert row["admin_verified_note"] == "対面で確認"
    assert aws.admin_set() == {actor.user_id, target.user_id}
    (item,) = aws.audit_items("admin.activate")
    assert item["detail"] == {"method": "in_person", "restored": False}

    signed_in_later = call("GET", "/users", caller=target, auth_time=1_750_000_001)
    assert signed_in_later[0] == 200


def register_totp(aws, user_id: str, at: int = 1_750_000_000) -> None:
    """本人が TOTP を登録して「登録を終えた」を呼んだ後の状態 (base の例外 1) を作る。"""
    from service_platform import ledger

    row = aws.profile(user_id)
    aws.cognito.totp.add(row["cognito_username"])
    aws.ledger.update_item(
        Key={"PK": ledger.user_pk(user_id), "SK": ledger.PROFILE_SK},
        UpdateExpression="SET admin_state = :a, mfa_enabled_at = :t, version = version + :one",
        ExpressionAttributeValues={":a": "awaiting", ":t": at, ":one": 1},
    )


def test_activation_after_a_reinvite_restores_the_user(aws, call):
    target = aws.make_admin("other@example.com")
    assert call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "TOTP の紛失"})[0] == 200
    register_totp(aws, target.user_id)

    status, _body = call("POST", f"/users/{target.user_id}/admin-activation", body={"method": "phone"})

    assert status == 200
    row = aws.profile(target.user_id)
    assert row["status"] == "active"
    assert "restore_requires" not in row
    assert target.user_id in aws.admin_set()
    assert aws.audit_items("admin.activate")[0]["detail"]["restored"] is True


def test_activation_refuses_an_interrupted_reinvite(aws, call):
    target = aws.make_admin(
        "other@example.com",
        admin_state="awaiting",
        status="suspended",
        restore_requires="reinvite",
        reinvite_in_progress=True,
    )

    assert_error(
        call("POST", f"/users/{target.user_id}/admin-activation", body={"method": "phone"}), 409, "REINVITE_INCOMPLETE"
    )


@pytest.mark.parametrize("state", ["pending", "active"])
def test_activation_requires_the_awaiting_state(aws, call, state):
    target = aws.make_admin("other@example.com", admin_state=state)

    assert_error(
        call("POST", f"/users/{target.user_id}/admin-activation", body={"method": "phone"}), 409, "ADMIN_NOT_AWAITING"
    )


def test_activation_requires_totp_in_cognito(aws, call):
    target = aws.make_admin("other@example.com", admin_state="awaiting", totp=False)

    assert_error(
        call("POST", f"/users/{target.user_id}/admin-activation", body={"method": "phone"}), 409, "ADMIN_NOT_READY"
    )
    assert aws.profile(target.user_id)["admin_state"] == "awaiting"


def test_issue_a_temporary_password_for_an_admin(aws, call, caplog):
    caplog.set_level(logging.DEBUG)
    target = aws.make_admin("other@example.com")

    status, body = call("POST", f"/users/{target.user_id}/temporary-password")

    assert status == 200, body
    password = body["data"]["temporary_password"]
    assert aws.cognito.called("admin_set_user_password")[-1]["Permanent"] is False
    assert aws.cognito.user(target.username)["UserStatus"] == "FORCE_CHANGE_PASSWORD"
    (item,) = aws.audit_items("admin.temporary_password")
    assert item["target_user_id"] == target.user_id
    assert password not in caplog.text
    assert password not in str(aws.audit_items())


def test_temporary_password_is_only_for_admins(aws, call):
    target = aws.make_user("customer@example.com")

    assert_error(call("POST", f"/users/{target.user_id}/temporary-password"), 409, "TARGET_NOT_ADMIN")
    assert aws.cognito.called("admin_set_user_password") == []


def test_remove_admin(aws, call):
    target = aws.make_admin("other@example.com")

    status, body = call("PUT", f"/users/{target.user_id}/admin", body={"admin": False})

    assert status == 200, body
    assert aws.cognito.groups(target.username) == []
    row = aws.profile(target.user_id)
    assert row["role"] == "user"
    assert "admin_state" not in row
    assert "mfa_enabled_at" not in row
    assert len(aws.audit_items("admin.revoke")) == 1


def test_granting_admin_is_not_done_here(aws, call):
    target = aws.make_user("employee@example.com", kind="employee")

    assert_error(call("PUT", f"/users/{target.user_id}/admin", body={"admin": True}), 400, "VALIDATION_ERROR")
    assert_error(call("PUT", f"/users/{target.user_id}/admin", body={"admin": "false"}), 400, "VALIDATION_ERROR")


def test_remove_admin_from_a_non_admin_is_409(aws, call):
    target = aws.make_user("customer@example.com")

    assert_error(call("PUT", f"/users/{target.user_id}/admin", body={"admin": False}), 409, "TARGET_NOT_ADMIN")


def test_two_admins_suspending_each_other_keep_the_last_one(aws, call, actor):
    """A と B が互いに停止する要求を、どちらも認可を済ませてから送っても、最後の 1 人は残る。"""
    from admin_users import errors, users

    other = aws.make_admin("other@example.com")
    stale_other = aws.context(other)
    assert call("POST", f"/users/{other.user_id}/suspend", body={})[0] == 200
    assert aws.admin_set() == {actor.user_id}

    with pytest.raises(errors.ApiError) as excinfo:
        users.suspend(stale_other, actor.user_id, {}, aws.request)

    assert (excinfo.value.status, excinfo.value.code) == (409, "LAST_ADMIN")
    assert aws.profile(actor.user_id)["status"] == "active"
    assert aws.admin_set() == {actor.user_id}
    assert aws.cognito.user(actor.username)["Enabled"] is True


@pytest.mark.parametrize("operation", ["revoke", "reinvite"])
def test_the_last_admin_survives_a_stale_revoke_or_reinvite(aws, call, actor, operation):
    from admin_users import errors, users

    other = aws.make_admin("other@example.com")
    stale_other = aws.context(other)
    if operation == "revoke":
        assert call("PUT", f"/users/{other.user_id}/admin", body={"admin": False})[0] == 200
        attempt = lambda: users.revoke_admin(stale_other, actor.user_id, {"admin": False}, aws.request)  # noqa: E731
    else:
        assert call("POST", f"/users/{other.user_id}/reinvite", body={"reason": "x"})[0] == 200
        attempt = lambda: users.reinvite(stale_other, actor.user_id, {"reason": "x"}, aws.request)  # noqa: E731

    with pytest.raises(errors.ApiError) as excinfo:
        attempt()

    assert (excinfo.value.status, excinfo.value.code) == (409, "LAST_ADMIN")
    row = aws.profile(actor.user_id)
    assert (row["status"], row["role"], row["admin_state"]) == ("active", "admin", "active")
    assert aws.admin_set() == {actor.user_id}
    assert aws.cognito.groups(actor.username) == ["admin"]
    assert aws.cognito.user(actor.username) is not None


def test_a_caller_suspended_after_authorization_cannot_write(aws, call, actor):
    """書き込みと同じトランザクションで、呼び出し元がまだ有効な admin であることを確かめる。"""
    from admin_users import errors, users

    other = aws.make_admin("other@example.com")
    customer = aws.make_user("customer@example.com")
    stale_actor = aws.context(actor)
    assert call("POST", f"/users/{actor.user_id}/suspend", body={}, caller=other)[0] == 200

    with pytest.raises(errors.ApiError) as excinfo:
        users.suspend(stale_actor, customer.user_id, {}, aws.request)

    assert (excinfo.value.status, excinfo.value.code) == (403, "ADMIN_NOT_ACTIVE")
    assert aws.profile(customer.user_id)["status"] == "active"
    assert aws.cognito.user(customer.username)["Enabled"] is True


def test_a_stale_profile_update_is_a_conflict(aws, call, monkeypatch):
    """読んだ version を条件に書く (楽観ロック)。間に別の更新があれば 409。"""
    from admin_users import users

    target = aws.make_user("customer@example.com")
    stale = aws.profile(target.user_id)
    assert call("PUT", f"/users/{target.user_id}/profile", body={"note": "1 回目"})[0] == 200
    monkeypatch.setattr(users, "load", lambda _user_id: stale)

    assert_error(call("PUT", f"/users/{target.user_id}/profile", body={"note": "2 回目"}), 409, "CONFLICT")
    assert aws.profile(target.user_id)["note"] == "1 回目"


def test_a_stale_suspend_is_a_conflict(aws, call, monkeypatch):
    from admin_users import users

    target = aws.make_user("customer@example.com")
    stale = aws.profile(target.user_id)
    assert call("PUT", f"/users/{target.user_id}/profile", body={"note": "間の更新"})[0] == 200
    monkeypatch.setattr(users, "load", lambda _user_id: stale)

    assert_error(call("POST", f"/users/{target.user_id}/suspend", body={}), 409, "CONFLICT")
    assert aws.profile(target.user_id)["status"] == "active"
    assert aws.cognito.user(target.username)["Enabled"] is True


def test_change_email_restores_the_ledger_when_cognito_fails(aws, call):
    """台帳に変更の途中の印 (email_pending) を書いてから Cognito を変える。Cognito が失敗したら印を消す。"""
    target = aws.make_user("old@example.com")
    aws.cognito.failures["admin_update_user_attributes"] = aws.client_error("InternalErrorException", 500, "x")

    with pytest.raises(ClientError):
        call("PUT", f"/users/{target.user_id}/email", body={"email": "new@example.com"})

    row = aws.profile(target.user_id)
    assert row["email"] == "old@example.com"
    assert row["GSI1SK"] == f"old@example.com#{target.user_id}"
    assert "email_pending" not in row
    assert aws.attributes(aws.cognito.user(target.username))["email"] == "old@example.com"
    assert [item["result"] for item in aws.audit_items("user.email_change")] == ["failed"]


def test_overlapping_email_changes_are_serialized(aws, call, monkeypatch):
    """1 つ目の要求が Cognito を変えている間の 2 つ目の要求は 409。台帳と Cognito は 1 つ目の宛先で揃う。"""
    from admin_users import cognito

    target = aws.make_user("old@example.com")
    original = cognito.update_email
    second = {}

    def update_email(username, email):
        if not second:
            second["result"] = call("PUT", f"/users/{target.user_id}/email", body={"email": "b@example.com"})
        original(username, email)

    monkeypatch.setattr(cognito, "update_email", update_email)

    status, _body = call("PUT", f"/users/{target.user_id}/email", body={"email": "a@example.com"})

    assert status == 200
    assert_error(second["result"], 409, "CONFLICT")
    row = aws.profile(target.user_id)
    assert row["email"] == "a@example.com"
    assert "email_pending" not in row
    assert aws.attributes(aws.cognito.user(target.username))["email"] == "a@example.com"


def test_an_interrupted_email_change_resumes_with_the_same_address(aws, call):
    """確定の段が落ちて印が残っても、同じ宛先でやり直すと続きから終わる。別の宛先は 409。"""
    from service_platform import ledger

    target = aws.make_user("old@example.com")
    aws.ledger.update_item(
        Key={"PK": ledger.user_pk(target.user_id), "SK": ledger.PROFILE_SK},
        UpdateExpression="SET email_pending = :p",
        ExpressionAttributeValues={":p": {"email": "new@example.com", "op_id": "01JBZ000000000000000000OP1"}},
    )

    assert_error(call("PUT", f"/users/{target.user_id}/email", body={"email": "other@example.com"}), 409, "CONFLICT")
    status, _body = call("PUT", f"/users/{target.user_id}/email", body={"email": "new@example.com"})

    assert status == 200
    row = aws.profile(target.user_id)
    assert row["email"] == "new@example.com"
    assert "email_pending" not in row
    assert aws.attributes(aws.cognito.user(target.username))["email"] == "new@example.com"


def test_change_email_restores_the_ledger_when_the_address_is_taken_in_cognito(aws, call):
    target = aws.make_user("old@example.com")
    aws.cognito.failures["admin_update_user_attributes"] = aws.client_error("AliasExistsException", 400, "x")

    assert_error(call("PUT", f"/users/{target.user_id}/email", body={"email": "new@example.com"}), 409, "USER_EXISTS")
    assert aws.profile(target.user_id)["email"] == "old@example.com"


def test_contact_is_never_logged(aws, call, caplog):
    caplog.set_level(logging.DEBUG)

    call("POST", "/users", body=CUSTOMER)
    target_id = aws.audit_items("user.invite")[0]["target_user_id"]
    call("GET", f"/users/{target_id}")
    call("PUT", f"/users/{target_id}/profile", body={"contact": "別の連絡先"})

    assert "窓口の電話" not in caplog.text
    assert "別の連絡先" not in caplog.text


# =============================================================================
# 修正ラウンド 2: 停止と復帰の競合、操作 ID、一時パスワードの失敗の経路
# =============================================================================


def test_restore_waits_until_the_suspension_reached_cognito(aws, call):
    """台帳は停止でも Cognito の無効化がまだなら戻さない (遅れた無効化が復帰の後に効くのを防ぐ)。

    停止を流し直すと戻せる。
    """
    target = aws.make_user("customer@example.com", status="suspended")
    restore = {"verified_method": "phone"}

    assert_error(call("POST", f"/users/{target.user_id}/restore", body=restore), 409, "SUSPEND_INCOMPLETE")
    assert aws.profile(target.user_id)["status"] == "suspended"

    assert call("POST", f"/users/{target.user_id}/suspend", body={})[0] == 200
    assert aws.cognito.user(target.username)["Enabled"] is False
    assert aws.cognito.called("admin_user_global_sign_out")[-1]["Username"] == target.username

    assert call("POST", f"/users/{target.user_id}/restore", body=restore)[0] == 200
    assert aws.cognito.user(target.username)["Enabled"] is True


def test_suspended_admin_cannot_be_restored_before_the_disable(aws, call, actor):
    """A が B を停止して Cognito の無効化の前で遅れている間に、A が B を戻せない。

    戻せると、遅れた無効化の後に使える admin が 0 人になる競合がある。
    """
    other = aws.make_admin("other@example.com", status="suspended")

    assert_error(
        call("POST", f"/users/{other.user_id}/restore", body={"verified_method": "phone"}), 409, "SUSPEND_INCOMPLETE"
    )
    assert other.user_id not in aws.admin_set()


def test_restore_re_enables_after_a_late_disable(aws, call):
    """台帳は有効なのに Cognito が無効 (遅れた無効化) なら、AdminEnableUser だけを行う。"""
    target = aws.make_user("customer@example.com")
    aws.cognito._client.admin_disable_user(UserPoolId=aws.pool_id, Username=target.username)

    status, body = call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"})

    assert status == 200, body
    assert aws.cognito.user(target.username)["Enabled"] is True
    assert aws.profile(target.user_id)["status"] == "active"
    (item,) = aws.audit_items("user.restore")
    assert item["detail"]["step"] == "cognito"


def test_writes_record_the_operation_id(aws, call):
    from service_platform.ids import ULID_PATTERN

    target = aws.make_user("customer@example.com")

    assert call("POST", f"/users/{target.user_id}/suspend", body={})[0] == 200

    first = aws.profile(target.user_id)["last_op_id"]
    assert ULID_PATTERN.fullmatch(first)
    assert call("PUT", f"/users/{target.user_id}/profile", body={"note": "x"})[0] == 200
    assert aws.profile(target.user_id)["last_op_id"] != first


def test_a_stale_caller_cannot_touch_cognito_through_an_already_suspended_target(aws, call, actor):
    """認可の後に停止された呼び出し元は、別の要求が先に停止していた対象についても 403 で、Cognito を呼ばない。"""
    from admin_users import errors, users

    other = aws.make_admin("other@example.com")
    customer = aws.make_user("customer@example.com")
    stale_actor = aws.context(actor)
    assert call("POST", f"/users/{actor.user_id}/suspend", body={}, caller=other)[0] == 200
    assert call("POST", f"/users/{customer.user_id}/suspend", body={}, caller=other)[0] == 200
    calls = len(aws.cognito.calls)

    with pytest.raises(errors.ApiError) as excinfo:
        users.suspend(stale_actor, customer.user_id, {}, aws.request)

    assert (excinfo.value.status, excinfo.value.code) == (403, "ADMIN_NOT_ACTIVE")
    assert aws.cognito.calls[calls:] == []


def test_a_condition_failure_is_not_success_just_because_the_state_matches(aws, call, monkeypatch):
    """別の要求が同じ状態を作っていても、このトランザクションが反映されていなければ成功にしない (操作 ID)。"""
    from admin_users import users

    target = aws.make_user("customer@example.com")
    stale = aws.profile(target.user_id)
    assert call("POST", f"/users/{target.user_id}/suspend", body={})[0] == 200
    monkeypatch.setattr(users, "load", lambda _user_id: stale)
    disables = len(aws.cognito.called("admin_disable_user"))

    assert_error(call("POST", f"/users/{target.user_id}/suspend", body={}), 409, "CONFLICT")
    assert len(aws.cognito.called("admin_disable_user")) == disables
    assert len(aws.audit_items("user.suspend")) == 1


def test_a_failed_temporary_password_never_leaks(aws, call, known_password, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    target = aws.make_admin("other@example.com")
    aws.cognito.failures["admin_set_user_password"] = aws.client_error("InternalErrorException", 500, "x")

    with pytest.raises(ClientError) as excinfo:
        call("POST", f"/users/{target.user_id}/temporary-password")

    assert_secret_kept(known_password, caplog, capsys, str(excinfo.value))
    assert sorted(item["result"] for item in aws.audit_items("admin.temporary_password")) == ["failed", "ok"]


# =============================================================================
# 修正ラウンド 3: restore は Cognito の有効化 → 台帳
# =============================================================================


def test_a_failed_cognito_enable_does_not_revive_the_callers_condition(aws, call, actor):
    """Cognito の有効化が失敗したら台帳を戻さない。待っていた B の要求は、ほかの admin を停止できない。"""
    from admin_users import errors, users

    other = aws.make_admin("other@example.com")
    third = aws.make_admin("third@example.com")
    stale_other = aws.context(other)
    assert call("POST", f"/users/{other.user_id}/suspend", body={})[0] == 200
    aws.cognito.failures["admin_enable_user"] = aws.client_error("InternalErrorException", 500, "AdminEnableUser")

    status, body = call("POST", f"/users/{other.user_id}/restore", body={"verified_method": "phone"})

    assert (status, body["error"]["code"]) == (503, "TEMPORARILY_UNAVAILABLE")
    assert aws.profile(other.user_id)["status"] == "suspended"
    assert aws.admin_set() == {actor.user_id, third.user_id}
    assert aws.audit_items("user.restore") == []
    with pytest.raises(errors.ApiError) as excinfo:
        users.suspend(stale_other, third.user_id, {}, aws.request)
    assert (excinfo.value.status, excinfo.value.code) == (403, "ADMIN_NOT_ACTIVE")
    assert aws.profile(third.user_id)["status"] == "active"

    del aws.cognito.failures["admin_enable_user"]
    assert call("POST", f"/users/{other.user_id}/restore", body={"verified_method": "phone"})[0] == 200
    assert aws.admin_set() == {actor.user_id, other.user_id, third.user_id}


def test_restore_enables_cognito_before_the_ledger(aws, call, monkeypatch):
    """台帳を戻す時点で、Cognito はもう有効になっている (順は Cognito → 台帳)。"""
    from admin_users import ledger_writes

    target = aws.make_user("customer@example.com")
    assert call("POST", f"/users/{target.user_id}/suspend", body={})[0] == 200
    original = ledger_writes.write
    seen = []

    def write(items, **kwargs):
        seen.append(aws.cognito.user(target.username)["Enabled"])
        return original(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", write)

    assert call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"})[0] == 200
    assert seen == [True]


def test_a_failed_ledger_write_after_the_enable_stays_closed(aws, call):
    """Cognito を有効にした後で台帳が失敗しても、台帳は停止のままなので API は 403 (認可は台帳で閉じる)。"""
    target = aws.make_user("customer@example.com")
    assert call("POST", f"/users/{target.user_id}/suspend", body={})[0] == 200
    aws.dynamodb.plan = [aws.client_error("ValidationException", 400, "TransactWriteItems")]

    status, _body = call("POST", f"/users/{target.user_id}/restore", body={"verified_method": "phone"})

    assert status == 500
    assert aws.cognito.user(target.username)["Enabled"] is True
    assert aws.profile(target.user_id)["status"] == "suspended"
    assert_error(call("GET", "/health", caller=target), 403, "USER_SUSPENDED")


@pytest.mark.parametrize("previous_role", ["user", "admin"])
def test_reinvite_as_admin_sets_the_selected_role(aws, call, previous_role):
    target = aws.make_user("employee@example.com", kind="employee", role=previous_role)

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "役割の変更", "role": "admin"})

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert row["role"] == "admin"
    assert row["admin_state"] == "pending"
    assert row["status"] == "suspended"
    assert "mfa_enabled_at" not in row
    assert aws.cognito.groups(row["cognito_username"]) == ["admin"]
    assert body["data"]["temporary_password"]
    assert aws.sub_row(target.sub)["revoked_at"]
    assert aws.audit_items("user.reinvite")[-1]["detail"]["role"] == "admin"


def test_reinvite_as_user_removes_admin_and_old_credentials(aws, call, actor):
    target = aws.make_admin("other@example.com")

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "役割の変更", "role": "user"})

    assert status == 200, body
    row = aws.profile(target.user_id)
    assert row["role"] == "user"
    assert row["status"] == "suspended"
    assert not {"admin_state", "mfa_enabled_at", "admin_verified_at"} & row.keys()
    assert aws.cognito.groups(row["cognito_username"]) == []
    assert aws.cognito.user(target.username) is None
    assert aws.admin_set() == {actor.user_id}
    assert "temporary_password" not in body["data"]
    assert "TemporaryPassword" not in aws.cognito.called("admin_create_user")[-1]
    assert aws.cognito.called("admin_create_user")[-1]["DesiredDeliveryMediums"] == ["EMAIL"]


@pytest.mark.parametrize("role", [None, "owner", 1, [], {}])
def test_reinvite_rejects_an_invalid_role_before_changing_anything(aws, call, role):
    target = aws.make_user("employee@example.com", kind="employee")

    assert_error(
        call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "x", "role": role}), 400, "VALIDATION_ERROR"
    )
    assert aws.profile(target.user_id)["status"] == "active"
    assert aws.cognito.called("admin_delete_user") == []


def test_reinvite_cannot_promote_a_customer_to_admin(aws, call):
    target = aws.make_user("customer@example.com")

    assert_error(
        call("POST", f"/users/{target.user_id}/reinvite", body={"reason": "x", "role": "admin"}),
        400,
        "VALIDATION_ERROR",
    )
    assert aws.profile(target.user_id)["status"] == "active"
    assert aws.cognito.called("admin_delete_user") == []


def test_reinvite_can_retry_a_role_change_after_a_failed_link(aws, call):
    target = aws.make_user("employee@example.com", kind="employee")
    aws.dynamodb.plan = [None, aws.client_error("TransactionCanceledException")]
    payload = {"reason": "役割の変更", "role": "admin"}
    assert_error(call("POST", f"/users/{target.user_id}/reinvite", body=payload), 500, "LEDGER_WRITE_FAILED")
    assert aws.profile(target.user_id)["reinvite_in_progress"] is True

    status, body = call("POST", f"/users/{target.user_id}/reinvite", body=payload)

    assert status == 200, body
    assert aws.profile(target.user_id)["role"] == "admin"
    assert body["data"]["temporary_password"]


def test_invite_checks_the_caller_at_write_time_and_compensates(aws, call, actor):
    from admin_users import errors, users

    other = aws.make_admin("other@example.com")
    stale = aws.context(actor)
    assert call("POST", f"/users/{actor.user_id}/suspend", body={}, caller=other)[0] == 200
    with pytest.raises(errors.ApiError) as caught:
        users.invite(stale, {"email": "new@example.com", "kind": "employee", "role": "admin"}, aws.request)

    assert (caught.value.status, caught.value.code) == (403, "ADMIN_NOT_ACTIVE")
    assert aws.cognito.user("new@example.com") is None
    assert aws.audit_items("user.invite") == []


def test_temporary_password_requires_a_conditional_audit_before_cognito(aws, call, monkeypatch):
    from admin_users import cognito

    target = aws.make_admin("other@example.com")
    original = cognito.set_temporary_password

    def set_password(username, password):
        (item,) = aws.audit_items("admin.temporary_password")
        assert item["detail"]["step"] == "requested"
        return original(username, password)

    monkeypatch.setattr(cognito, "set_temporary_password", set_password)
    assert call("POST", f"/users/{target.user_id}/temporary-password")[0] == 200


@pytest.mark.parametrize("code", ["AccessDeniedException", "ConditionalCheckFailedException"])
def test_temporary_password_does_not_call_cognito_when_audit_fails(aws, call, monkeypatch, code):
    from admin_users import ledger_writes

    target = aws.make_admin("other@example.com")

    def fail(**kwargs):
        assert kwargs["ConditionExpression"] == "attribute_not_exists(PK)"
        raise aws.client_error(code)

    monkeypatch.setattr(ledger_writes._client(), "put_item", fail)
    assert_error(call("POST", f"/users/{target.user_id}/temporary-password"), 503, "TEMPORARILY_UNAVAILABLE")
    assert aws.cognito.called("admin_set_user_password") == []


def test_temporary_password_keeps_the_cognito_error_when_failure_audit_also_fails(aws, call, monkeypatch):
    from service_platform import audit

    target = aws.make_admin("other@example.com")
    aws.cognito.failures["admin_set_user_password"] = aws.client_error("InternalErrorException", 500)
    monkeypatch.setattr(
        audit._client(), "put_item", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable"))
    )

    with pytest.raises(ClientError):
        call("POST", f"/users/{target.user_id}/temporary-password")
    assert len(aws.audit_items("admin.temporary_password")) == 1
