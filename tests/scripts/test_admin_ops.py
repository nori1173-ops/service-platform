"""`scripts/admin_ops.py` のテスト（ADR-001 D9 の最初の admin と緊急の復旧、計画 4.1）。"""

import base64
import json
import os
import pathlib
import stat

import admin_ops
import pytest
import reinvite_state
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber

from service_platform import ledger
from service_platform.ids import ULID_PATTERN

EMAIL = "first-admin@example.com"
CLIENT_ID = "exampleclientid"
WAF_BLOCK_MESSAGE = "Request not allowed due to WAF block."
# moto の STS の主体は arn:aws:sts::210000000001:user/moto
SCRIPT_ACTOR = "script:moto"


def run(aws, *argv, confirm=None, output_dir=True):
    args = list(argv)
    if output_dir and args[0] in ("create-admin", "emergency-reinvite"):
        args += ["--output-dir", str(aws.output_dir)]
    return admin_ops.main(args, clients=aws.clients, confirm=confirm or (lambda prompt: True))


def unverified_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def secret_files(aws) -> list:
    return sorted(aws.output_dir.glob("temporary-password-*")) if aws.output_dir.exists() else []


def state_files(aws) -> list:
    return sorted(aws.output_dir.glob("emergency-reinvite-*")) if aws.output_dir.exists() else []


def profile_of(aws, email=EMAIL) -> dict:
    rows = [row for row in aws.ledger.scan()["Items"] if row["SK"] == ledger.PROFILE_SK and row.get("email") == email]
    assert len(rows) == 1
    return rows[0]


def sub_rows(aws) -> dict:
    return {row["PK"]: row for row in aws.ledger.scan()["Items"] if row["PK"].startswith("SUB#")}


def audit_rows(aws, action=None) -> list[dict]:
    rows = aws.audit.scan()["Items"]
    return [row for row in rows if action is None or row["action"] == action]


def cognito_user(aws, email=EMAIL) -> dict:
    return aws.raw_cognito.admin_get_user(UserPoolId=aws.pool_id, Username=email)


def groups_of(aws, email=EMAIL) -> list[str]:
    groups = aws.raw_cognito.admin_list_groups_for_user(UserPoolId=aws.pool_id, Username=email)["Groups"]
    return [group["GroupName"] for group in groups]


def client_error(code: str, message: str = "error", status: int | None = None) -> ClientError:
    response = {"Error": {"Code": code, "Message": message}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status}
    return ClientError(response, "Operation")


def canceled() -> ClientError:
    """条件に合わず取り消された (成否が確定した失敗)。"""
    return client_error("TransactionCanceledException", "Transaction cancelled", 400)


def in_progress() -> ClientError:
    """同じ ClientRequestToken の要求がまだ処理中。"""
    return client_error("TransactionInProgressException", "in progress", 400)


def reinvite_attempts(aws) -> list[dict]:
    """再招待の段 (SUB の Put を含む) の TransactWriteItems の呼び出し。"""
    return [
        call
        for call in aws.dynamodb.called("transact_write_items")
        if any("Put" in item and item["Put"]["Item"]["PK"]["S"].startswith("SUB#") for item in call["TransactItems"])
        and any(
            "Update" in item and "revoked_at" in item["Update"]["UpdateExpression"] for item in call["TransactItems"]
        )
    ]


def lost_response() -> ReadTimeoutError:
    """応答を失った (AWS 側で処理されたかどうか分からない) 失敗。"""
    return ReadTimeoutError(endpoint_url="https://example.invalid")


def reinvite_rows(aws, step: str) -> list[dict]:
    return [row for row in audit_rows(aws, "script.emergency_reinvite") if row["detail"].get("step") == step]


def sub_of(user: dict) -> str:
    return next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")


def set_awaiting(aws, email=EMAIL, mfa_enabled_at=1_762_000_000):
    """本人が TOTP の登録を終えた状態 (例外 1 の API の後) にする。"""
    profile = profile_of(aws, email)
    aws.ledger.update_item(
        Key={"PK": profile["PK"], "SK": profile["SK"]},
        UpdateExpression="SET admin_state = :awaiting, mfa_enabled_at = :at",
        ExpressionAttributeValues={":awaiting": "awaiting", ":at": mfa_enabled_at},
    )
    aws.cognito.totp_users.add(cognito_user(aws, email)["Username"])


# =============================================================================
# 一時パスワード
# =============================================================================


def test_temporary_password_meets_the_pool_policy():
    """12 文字以上、英大小・数字を含む（core の PasswordPolicy）。"""
    for _ in range(200):
        password = admin_ops.generate_temporary_password()
        assert len(password) >= 12
        assert any(c.islower() for c in password)
        assert any(c.isupper() for c in password)
        assert any(c.isdigit() for c in password)
        assert password.isalnum()


def test_temporary_passwords_differ():
    assert len({admin_ops.generate_temporary_password() for _ in range(100)}) == 100


# =============================================================================
# create-admin
# =============================================================================


def test_create_admin_writes_cognito_ledger_and_audit(aws):
    assert run(aws, "create-admin", "dev", EMAIL) == 0

    user = cognito_user(aws)
    sub = next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")
    assert groups_of(aws) == ["admin"]

    profile = profile_of(aws)
    assert ULID_PATTERN.fullmatch(profile["user_id"])
    assert profile["PK"] == ledger.user_pk(profile["user_id"])
    assert profile["GSI1PK"] == ledger.USER_GSI1PK
    assert profile["GSI1SK"] == ledger.user_gsi1sk(EMAIL, profile["user_id"])
    assert profile["sub"] == sub
    assert profile["cognito_username"] == user["Username"]
    assert (profile["status"], profile["role"], profile["kind"], profile["admin_state"]) == (
        "active",
        "admin",
        "employee",
        "pending",
    )
    assert "mfa_enabled_at" not in profile
    assert profile["created_by"] == SCRIPT_ACTOR
    assert profile["updated_by"] == SCRIPT_ACTOR
    assert profile["version"] == 1

    assert sub_rows(aws)[ledger.sub_pk(sub)]["user_id"] == profile["user_id"]
    assert ledger.resolve_user(sub).user_id == profile["user_id"]

    rows = audit_rows(aws, "script.create_admin")
    assert len(rows) == 1
    assert rows[0]["PK"] == "ACTOR#script"
    assert rows[0]["service"] == "scripts"
    assert rows[0]["target_user_id"] == profile["user_id"]
    assert rows[0]["detail"]["caller_arn"].startswith("arn:aws:sts::")


def test_create_admin_sends_no_mail_and_verifies_the_email(aws):
    """招待メールを送らず (SUPPRESS)、email_verified=true で作る（ADR D9）。"""
    run(aws, "create-admin", "dev", EMAIL)

    (kwargs,) = aws.cognito.called("admin_create_user")
    assert kwargs["MessageAction"] == "SUPPRESS"
    assert {"Name": "email_verified", "Value": "true"} in kwargs["UserAttributes"]
    assert {"Name": "email", "Value": EMAIL} in kwargs["UserAttributes"]
    assert "DesiredDeliveryMediums" not in kwargs


def test_create_admin_keeps_the_temporary_password_out_of_stdout(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)

    (path,) = secret_files(aws)
    password = path.read_text().strip()
    (kwargs,) = aws.cognito.called("admin_create_user")
    assert kwargs["TemporaryPassword"] == password
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    out, err = capsys.readouterr()
    assert password not in out
    assert password not in err
    assert str(path) in out
    assert password not in json.dumps(audit_rows(aws), default=str)
    assert password not in json.dumps(aws.ledger.scan()["Items"], default=str)


def test_create_admin_normalizes_the_email(aws):
    run(aws, "create-admin", "dev", "  First-Admin@Example.COM ")

    assert profile_of(aws)["email"] == EMAIL
    assert cognito_user(aws)["Username"]


def test_create_admin_refuses_an_output_dir_inside_the_repository(aws):
    inside = admin_ops.REPO_ROOT / "tmp-secrets-should-not-exist"

    assert (
        admin_ops.main(
            ["create-admin", "dev", EMAIL, "--output-dir", str(inside)], clients=aws.clients, confirm=lambda p: True
        )
        == 1
    )
    assert not inside.exists()
    assert aws.cognito.called("admin_create_user") == []


def test_create_admin_default_output_dir_is_private(aws, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(admin_ops.tempfile, "tempdir", str(tmp_path))
    assert run(aws, "create-admin", "dev", EMAIL, output_dir=False) == 0

    (directory,) = [p for p in tmp_path.iterdir() if p.name.startswith("service-platform-admin-")]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    (path,) = list(directory.iterdir())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_create_admin_refuses_an_existing_cognito_user(aws):
    aws.raw_cognito.admin_create_user(UserPoolId=aws.pool_id, Username=EMAIL, MessageAction="SUPPRESS")

    assert run(aws, "create-admin", "dev", EMAIL) == 1
    assert aws.ledger.scan()["Items"] == []
    assert secret_files(aws) == []


def test_create_admin_refuses_an_email_already_in_the_ledger(aws):
    aws.ledger.put_item(
        Item={
            "PK": ledger.user_pk("01JBZ0000000000000000USER9"),
            "SK": ledger.PROFILE_SK,
            "GSI1PK": ledger.USER_GSI1PK,
            "GSI1SK": ledger.user_gsi1sk(EMAIL, "01JBZ0000000000000000USER9"),
            "email": EMAIL,
        }
    )

    assert run(aws, "create-admin", "dev", EMAIL) == 1
    assert aws.cognito.called("admin_create_user") == []


def test_create_admin_rolls_back_when_the_ledger_fails(aws, monkeypatch):
    """台帳 (と監査) の書き込みが失敗したら Cognito の利用者を消して戻す。"""
    monkeypatch.setattr(admin_ops, "LEDGER_CONDITION", "attribute_exists(PK)")

    assert run(aws, "create-admin", "dev", EMAIL) == 1

    with pytest.raises(aws.raw_cognito.exceptions.UserNotFoundException):
        cognito_user(aws)
    assert aws.ledger.scan()["Items"] == []
    assert audit_rows(aws) == []
    assert secret_files(aws) == []


def test_create_admin_rolls_back_when_the_group_fails(aws):
    aws.cognito.failures["admin_add_user_to_group"] = client_error("InternalErrorException")

    assert run(aws, "create-admin", "dev", EMAIL) == 1

    with pytest.raises(aws.raw_cognito.exceptions.UserNotFoundException):
        cognito_user(aws)
    assert aws.ledger.scan()["Items"] == []
    assert secret_files(aws) == []


def test_create_admin_checks_the_output_dir_before_creating(aws, tmp_path):
    output_dir = tmp_path / "read-only"
    output_dir.mkdir(mode=0o500)

    code = admin_ops.main(
        ["create-admin", "dev", EMAIL, "--output-dir", str(output_dir)], clients=aws.clients, confirm=lambda p: True
    )

    assert code == 1
    assert aws.cognito.called("admin_create_user") == []
    output_dir.chmod(0o700)


def test_create_admin_treats_a_lost_ledger_response_as_success(aws):
    aws.dynamodb.inject("transact_write_items", lost_response(), after=True)

    assert run(aws, "create-admin", "dev", EMAIL) == 0

    assert aws.cognito.called("admin_delete_user") == []
    profile = profile_of(aws)
    assert ledger.resolve_user(profile["sub"]).user_id == profile["user_id"]
    assert len(secret_files(aws)) == 1


def test_create_admin_keeps_everything_when_retries_stay_unknown(aws, retry_sleeps):
    aws.dynamodb.inject("transact_write_items", lost_response(), after=True)
    for _ in range(3):
        aws.dynamodb.inject("transact_write_items", lost_response())

    assert run(aws, "create-admin", "dev", EMAIL) == 1

    assert retry_sleeps == [2, 5, 10]
    assert aws.cognito.called("admin_delete_user") == []
    (path,) = secret_files(aws)
    assert path.read_text().strip()


def test_create_admin_keeps_the_secret_when_creation_is_unknown(aws, capsys):
    aws.cognito.inject("admin_create_user", lost_response(), after=True)

    assert run(aws, "create-admin", "dev", EMAIL) == 1

    assert "結果が分かりません" in capsys.readouterr().err
    assert len(secret_files(aws)) == 1
    assert aws.cognito.called("admin_delete_user") == []


def test_create_admin_in_prod_asks_first(aws):
    prompts = []

    assert run(aws, "create-admin", "prod", EMAIL, confirm=lambda p: prompts.append(p) or False) == 1
    assert prompts and "prod" in prompts[0]
    assert aws.cognito.called("admin_create_user") == []

    assert run(aws, "create-admin", "prod", EMAIL, confirm=lambda p: True) == 0
    assert profile_of(aws)["admin_state"] == "pending"


def test_dev_does_not_ask(aws):
    def refuse(prompt):
        raise AssertionError("dev では確認しない")

    assert run(aws, "create-admin", "dev", EMAIL, confirm=refuse) == 0


@pytest.mark.parametrize("argv", [["create-admin", "stg", EMAIL], ["create-admin", "dev", "not-an-email"]])
def test_rejects_bad_arguments(aws, argv):
    with pytest.raises(SystemExit) as excinfo:
        admin_ops.main(argv, clients=aws.clients, confirm=lambda p: True)
    assert excinfo.value.code != 0


# =============================================================================
# activate-admin
# =============================================================================


def test_activate_admin_stops_without_totp(aws, capsys):
    """最初の admin を作った直後は TOTP が無いので止まる（計画 4.1 の完了条件）。"""
    run(aws, "create-admin", "dev", EMAIL)
    capsys.readouterr()

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1

    assert "TOTP" in capsys.readouterr().err
    assert profile_of(aws)["admin_state"] == "pending"
    assert audit_rows(aws, "script.activate_admin") == []


def test_activate_admin_requires_the_awaiting_state(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    aws.cognito.totp_users.add(cognito_user(aws)["Username"])
    capsys.readouterr()

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1
    assert "awaiting" in capsys.readouterr().err
    assert profile_of(aws)["admin_state"] == "pending"


def test_activate_admin_marks_the_admin_active(aws):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone", "--note", "内線で本人の声を確認") == 0

    profile = profile_of(aws)
    assert profile["admin_state"] == "active"
    assert profile["status"] == "active"
    assert profile["admin_verified_by"] == SCRIPT_ACTOR
    assert profile["updated_by"] == SCRIPT_ACTOR
    assert profile["admin_verified_method"] == "phone"
    assert profile["admin_verified_note"] == "内線で本人の声を確認"
    assert profile["admin_verified_at"].endswith("Z")
    assert profile["mfa_enabled_at"] == 1_762_000_000
    assert profile["version"] == 2

    (row,) = audit_rows(aws, "script.activate_admin")
    assert row["target_user_id"] == profile["user_id"]
    assert row["detail"]["method"] == "phone"
    assert row["detail"]["restored"] is False
    assert "内線" not in json.dumps(row, default=str)


def test_activate_admin_requires_mfa_enabled_at(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)
    profile = profile_of(aws)
    aws.ledger.update_item(Key={"PK": profile["PK"], "SK": profile["SK"]}, UpdateExpression="REMOVE mfa_enabled_at")
    capsys.readouterr()

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1
    assert "mfa_enabled_at" in capsys.readouterr().err


def test_activate_admin_requires_the_admin_group(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)
    aws.raw_cognito.admin_remove_user_from_group(UserPoolId=aws.pool_id, Username=EMAIL, GroupName="admin")
    capsys.readouterr()

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1
    assert "admin グループ" in capsys.readouterr().err


def test_activate_admin_requires_an_enabled_user(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)
    aws.raw_cognito.admin_disable_user(UserPoolId=aws.pool_id, Username=EMAIL)
    capsys.readouterr()

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1
    assert "無効" in capsys.readouterr().err


def test_activate_admin_requires_a_known_method(aws):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)

    with pytest.raises(SystemExit):
        admin_ops.main(
            ["activate-admin", "dev", EMAIL, "--method", "email"], clients=aws.clients, confirm=lambda p: True
        )


def test_activate_admin_in_prod_asks_first(aws):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)

    assert run(aws, "activate-admin", "prod", EMAIL, "--method", "phone", confirm=lambda p: False) == 1
    assert profile_of(aws)["admin_state"] == "awaiting"


# =============================================================================
# emergency-reinvite（ADR D9 の緊急の復旧の手順 2）
# =============================================================================


def make_active_admin(aws):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)
    run(aws, "activate-admin", "dev", EMAIL, "--method", "in_person")
    for path in secret_files(aws):
        path.unlink()
    return profile_of(aws)


def test_emergency_reinvite_recreates_the_admin(aws, capsys):
    before = make_active_admin(aws)
    old_sub = before["sub"]
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "TOTP の端末を紛失") == 0

    user = cognito_user(aws)
    new_sub = next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")
    assert new_sub != old_sub
    assert groups_of(aws) == ["admin"]
    for name in ("admin_disable_user", "admin_user_global_sign_out", "admin_delete_user"):
        (kwargs,) = aws.cognito.called(name)
        assert kwargs["Username"] == before["cognito_username"]
    kwargs = aws.cognito.called("admin_create_user")[-1]
    assert kwargs["MessageAction"] == "SUPPRESS"

    after = profile_of(aws)
    assert after["user_id"] == before["user_id"]
    assert after["sub"] == new_sub
    assert after["cognito_username"] == user["Username"]
    assert after["status"] == "suspended"
    assert after["restore_requires"] == "reinvite"
    assert after["admin_state"] == "pending"
    for removed in ("mfa_enabled_at", "admin_verified_by", "admin_verified_at", "admin_verified_method"):
        assert removed not in after
    assert after["suspended_by"] == SCRIPT_ACTOR
    assert after["updated_by"] == SCRIPT_ACTOR

    subs = sub_rows(aws)
    assert subs[ledger.sub_pk(old_sub)]["revoked_at"]
    assert subs[ledger.sub_pk(old_sub)]["user_id"] == before["user_id"]
    assert subs[ledger.sub_pk(new_sub)]["user_id"] == before["user_id"]
    with pytest.raises(ledger.UserNotRegisteredError):
        ledger.resolve_user(old_sub, allow_suspended=True)
    assert ledger.resolve_user(new_sub, allow_suspended=True).admin_state == "pending"

    for step in ("suspend", "reinvite"):
        (row,) = reinvite_rows(aws, step)
        assert row["PK"] == "ACTOR#script"
        assert row["target_user_id"] == before["user_id"]
        assert row["detail"]["reason"] == "TOTP の端末を紛失"
        assert row["detail"]["caller_arn"].startswith("arn:aws:sts::")

    (path,) = secret_files(aws)
    password = path.read_text().strip()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out, err = capsys.readouterr()
    assert password not in out + err
    assert str(path) in out


def test_activate_after_emergency_reinvite_restores_the_user(aws):
    """再招待の後の「有効にする」は、利用者の台帳と admin の状態を一緒に有効に戻す（ADR D8 / D9）。"""
    make_active_admin(aws)
    run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "TOTP の端末を紛失")
    set_awaiting(aws, mfa_enabled_at=1_762_100_000)

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 0

    profile = profile_of(aws)
    assert (profile["status"], profile["admin_state"]) == ("active", "active")
    assert "restore_requires" not in profile
    (row,) = audit_rows(aws, "script.activate_admin")[-1:]
    assert row["detail"]["restored"] is True


def admin_set(aws) -> set[str]:
    key = {"PK": ledger.ADMIN_SET_PK, "SK": ledger.ADMIN_SET_SK}
    return set((aws.ledger.get_item(Key=key, ConsistentRead=True).get("Item") or {}).get("members") or ())


def test_activate_admin_joins_the_active_admin_set(aws):
    """有効な admin の集合 (ADMIN#SET) に入れる。admin-users が最後の admin を残す条件に使う 。"""
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 0

    assert admin_set(aws) == {profile_of(aws)["user_id"]}


def test_emergency_reinvite_leaves_the_active_admin_set_and_counts_the_reinvite(aws):
    before = make_active_admin(aws)
    assert admin_set(aws) == {before["user_id"]}

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "TOTP の端末を紛失") == 0

    after = profile_of(aws)
    assert admin_set(aws) == set()
    assert after["reinvite_count"] == 1
    assert after["previous_sub"] == before["sub"]
    assert "reinvite_in_progress" not in after

    for path in secret_files(aws):
        path.unlink()
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "もう一度") == 0
    assert profile_of(aws)["reinvite_count"] == 2


def test_emergency_reinvite_marks_the_reinvite_in_progress_until_linked(aws, capsys):
    """古い利用者を消す前に止まったら、再招待の途中の印が残り、activate-admin は断る。"""
    make_active_admin(aws)
    aws.cognito.failures["admin_disable_user"] = client_error("InternalErrorException")

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    profile = profile_of(aws)
    assert profile["reinvite_in_progress"] is True
    assert admin_set(aws) == set()
    set_awaiting(aws)
    capsys.readouterr()
    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1
    assert "再招待" in capsys.readouterr().err
    assert profile_of(aws)["admin_state"] == "awaiting"


def test_a_second_emergency_reinvite_marks_the_reinvite_in_progress(aws, capsys):
    """初回の完了 → 本人の TOTP の登録 (awaiting) → 2 回目で AdminDisableUser が失敗しても、途中の印が立つ。

    印が無いと、初回の previous_sub の無効の印で完了の確認が通り、今回失効させる利用者で有効にできてしまう。
    """
    from admin_users import errors as api_errors
    from admin_users import users as api_users

    from service_platform.ledger import UserContext

    make_active_admin(aws)
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "1 回目") == 0
    set_awaiting(aws, mfa_enabled_at=1_762_100_000)
    for path in secret_files(aws):
        path.unlink()
    aws.cognito.failures["admin_disable_user"] = client_error("InternalErrorException")

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "2 回目") == 1

    profile = profile_of(aws)
    assert profile["reinvite_in_progress"] is True
    assert profile["admin_state"] == "awaiting"
    capsys.readouterr()
    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 1
    other = UserContext("01JBZ0000000000000000OTHER", "x", "o@example.com", "active", "employee", "admin", "active", 1)
    with pytest.raises(api_errors.ApiError) as excinfo:
        api_users.activate_admin(other, profile["user_id"], {"method": "phone"}, {})
    assert (excinfo.value.status, excinfo.value.code) == (409, "REINVITE_INCOMPLETE")


def test_emergency_reinvite_refuses_a_stale_profile(aws, monkeypatch):
    """読んだ version を条件に停止する。読んだ後に別の更新 (API の有効化など) があれば何も変えずに止める。"""
    make_active_admin(aws)
    located = admin_ops.locate_admin

    def stale(*args):
        target = located(*args)
        profile_row = target.profile
        aws.ledger.update_item(
            Key={"PK": profile_row["PK"], "SK": profile_row["SK"]},
            UpdateExpression="SET version = version + :one",
            ExpressionAttributeValues={":one": 1},
        )
        return target

    monkeypatch.setattr(admin_ops, "locate_admin", stale)

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    profile = profile_of(aws)
    assert profile["status"] == "active"
    assert "reinvite_in_progress" not in profile
    assert cognito_user(aws)["Enabled"] is True


def test_emergency_reinvite_link_always_leaves_the_admin_set(aws):
    """付け替えのトランザクションに ADMIN#SET からの DELETE を必ず入れる (間に有効化されても集合に残さない)。"""
    make_active_admin(aws)

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    (attempt,) = reinvite_attempts(aws)
    deletes = [
        item
        for item in attempt["TransactItems"]
        if "Update" in item
        and item["Update"]["Key"]["PK"]["S"] == ledger.ADMIN_SET_PK
        and item["Update"]["UpdateExpression"].startswith("DELETE")
    ]
    assert len(deletes) == 1
    assert admin_set(aws) == set()


def test_scripts_record_the_operation_id(aws):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone") == 0

    assert ULID_PATTERN.fullmatch(profile_of(aws)["last_op_id"])


def test_emergency_reinvite_suspends_before_touching_cognito(aws):
    """台帳で停止してから Cognito を止める。途中で落ちても API は 403 のまま。"""
    make_active_admin(aws)
    aws.cognito.failures["admin_disable_user"] = client_error("InternalErrorException")

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    profile = profile_of(aws)
    assert profile["status"] == "suspended"
    assert profile["restore_requires"] == "reinvite"
    assert cognito_user(aws)["Enabled"] is True
    # 停止したのに監査が無い、という状態にしない
    (row,) = reinvite_rows(aws, "suspend")
    assert row["detail"]["reason"] == "紛失"
    assert reinvite_rows(aws, "reinvite") == []


def test_emergency_reinvite_suspends_in_the_same_transaction_as_its_audit(aws):
    """停止 (台帳の UpdateItem) と監査の Put は同じ TransactWriteItems で書く（ADR D12）。"""
    make_active_admin(aws)
    aws.cognito.failures["admin_disable_user"] = client_error("InternalErrorException")

    run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失")

    first = aws.dynamodb.called("transact_write_items")[-1]["TransactItems"]
    tables = sorted({next(iter(item.values()))["TableName"] for item in first})
    assert tables == ["service-platform-core-audit-dev", "service-platform-core-ledger-dev"]
    assert "Update" in first[0]
    assert aws.dynamodb.called("update_item") == []


def test_emergency_reinvite_resumes_after_the_old_user_was_deleted(aws):
    """古い利用者を消した後で落ちても、もう一度流せば続きから進む。"""
    before = make_active_admin(aws)
    aws.cognito.failures["admin_create_user"] = client_error("InternalErrorException")
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    with pytest.raises(aws.raw_cognito.exceptions.UserNotFoundException):
        cognito_user(aws)
    assert secret_files(aws) == []

    del aws.cognito.failures["admin_create_user"]
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    after = profile_of(aws)
    assert after["user_id"] == before["user_id"]
    assert after["sub"] != before["sub"]
    assert sub_rows(aws)[ledger.sub_pk(before["sub"])]["revoked_at"]
    # 2 回目は停止済みなので、停止を書き直さない
    assert len(reinvite_rows(aws, "suspend")) == 1
    assert len(reinvite_rows(aws, "reinvite")) == 1


def test_emergency_reinvite_removes_the_new_user_when_the_ledger_fails(aws, monkeypatch):
    make_active_admin(aws)
    monkeypatch.setattr(admin_ops, "LEDGER_CONDITION", "attribute_exists(PK)")

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    with pytest.raises(aws.raw_cognito.exceptions.UserNotFoundException):
        cognito_user(aws)
    assert secret_files(aws) == []
    assert reinvite_rows(aws, "reinvite") == []


def test_emergency_reinvite_resumes_after_a_lost_create_user_response(aws, capsys):
    """AdminCreateUser は通ったが応答を失った。秘密のファイルを残し、もう一度流すと作り直さずに続きから進む。"""
    before = make_active_admin(aws)
    aws.cognito.inject("admin_create_user", lost_response(), after=True)

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    assert "結果が分かりません" in capsys.readouterr().err
    (kept,) = secret_files(aws)
    assert kept.read_text().strip()
    recreated = cognito_user(aws)
    assert sub_of(recreated) != before["sub"]
    assert profile_of(aws)["sub"] == before["sub"]

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    assert len(aws.cognito.called("admin_create_user")) == 2  # create-admin の 1 回 + 再招待の 1 回
    assert len(aws.cognito.called("admin_delete_user")) == 1
    (reset,) = aws.cognito.called("admin_set_user_password")
    assert reset["Username"] == recreated["Username"]
    assert reset["Permanent"] is False
    after = profile_of(aws)
    assert after["sub"] == sub_of(cognito_user(aws)) == sub_of(recreated)
    assert after["admin_state"] == "pending"
    assert sub_rows(aws)[ledger.sub_pk(before["sub"])]["revoked_at"]
    assert len(reinvite_rows(aws, "suspend")) == 1
    assert len(reinvite_rows(aws, "reinvite")) == 1
    newest = max(secret_files(aws), key=lambda path: path.stat().st_mtime_ns)
    assert newest.read_text().strip() == reset["Password"]


def test_emergency_reinvite_does_not_resume_onto_a_used_cognito_user(aws, capsys):
    """台帳に無い sub の利用者が既にサインイン済み (FORCE_CHANGE_PASSWORD でない) なら、続きとみなさない。"""
    make_active_admin(aws)
    aws.cognito.inject("admin_create_user", lost_response(), after=True)
    run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失")
    aws.raw_cognito.admin_set_user_password(
        UserPoolId=aws.pool_id, Username=EMAIL, Password="Confirmed-Password-1", Permanent=True
    )
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    assert "FORCE_CHANGE_PASSWORD" in capsys.readouterr().err
    assert len(reinvite_rows(aws, "reinvite")) == 0


def test_emergency_reinvite_treats_a_lost_ledger_response_as_success(aws):
    """台帳の書き込みは通ったが応答を失った。読み直して載っていれば成功とし、新しい利用者を消さない。"""
    before = make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), after=True, skip=1)

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    assert len(aws.cognito.called("admin_delete_user")) == 1
    user = cognito_user(aws)
    after = profile_of(aws)
    assert after["sub"] == sub_of(user) != before["sub"]
    assert len(reinvite_rows(aws, "reinvite")) == 1
    (path,) = secret_files(aws)
    assert path.read_text().strip()


def test_emergency_reinvite_compensates_when_a_lost_ledger_write_did_not_apply(aws, retry_sleeps):
    """応答を失い、同じ token の再試行が確定した失敗で、読み直しても台帳に無ければ、新しい利用者を消す。"""
    make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), after=False, skip=1)
    aws.dynamodb.inject("transact_write_items", canceled())

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    assert retry_sleeps == [2]
    assert len(aws.cognito.called("admin_delete_user")) == 2
    assert state_files(aws) == []
    with pytest.raises(aws.raw_cognito.exceptions.UserNotFoundException):
        cognito_user(aws)
    assert secret_files(aws) == []
    assert reinvite_rows(aws, "reinvite") == []


def test_emergency_reinvite_retries_a_transaction_in_progress_with_the_same_token(aws, retry_sleeps):
    """TransactionInProgressException は処理中。同じ token で同じ要求を送り直す。"""
    make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", in_progress(), skip=1)

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    first, second = reinvite_attempts(aws)
    assert first["ClientRequestToken"] == second["ClientRequestToken"]
    assert first["TransactItems"] == second["TransactItems"]
    assert retry_sleeps == [2]
    assert len(aws.cognito.called("admin_delete_user")) == 1
    assert profile_of(aws)["sub"] == sub_of(cognito_user(aws))
    assert state_files(aws) == []


def test_every_ledger_transaction_carries_a_client_request_token(aws):
    make_active_admin(aws)
    run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失")

    tokens = [call.get("ClientRequestToken") for call in aws.dynamodb.called("transact_write_items")]
    assert tokens and all(tokens)
    assert all(len(token) <= 36 for token in tokens)
    assert len(set(tokens)) == len(tokens)


def test_emergency_reinvite_keeps_everything_when_retries_stay_unknown(aws, retry_sleeps, capsys):
    """再試行も全部結果不明なら、補償も一時パスワードのファイルの削除もしない。"""
    make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), after=True, skip=1)
    for _ in range(3):
        aws.dynamodb.inject("transact_write_items", lost_response())
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    attempts = reinvite_attempts(aws)
    assert len(attempts) == 4
    assert len({call["ClientRequestToken"] for call in attempts}) == 1
    assert retry_sleeps == [2, 5, 10]
    assert len(aws.cognito.called("admin_delete_user")) == 1
    user = cognito_user(aws)
    assert user["UserStatus"] == "FORCE_CHANGE_PASSWORD"
    (path,) = secret_files(aws)
    password = path.read_text().strip()
    assert password
    err = capsys.readouterr().err
    assert "結果が分かりません" in err
    assert "--output-dir" in err

    (state,) = state_files(aws)
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert password not in state.read_text()


def test_emergency_reinvite_does_not_compensate_when_the_check_read_fails(aws, monkeypatch, capsys):
    """書き込みは済んで応答を失い、照合の読み取りも失敗した。結果不明として、補償しない。"""
    before = make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), after=True, skip=1)
    aws.dynamodb.inject("transact_write_items", canceled())
    original = ledger.resolve_user

    def flaky(sub, **kwargs):
        if sub != before["sub"]:
            raise client_error("ThrottlingException", "Rate exceeded", 400)
        return original(sub, **kwargs)

    monkeypatch.setattr(ledger, "resolve_user", flaky)
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    assert len(aws.cognito.called("admin_delete_user")) == 1
    assert profile_of(aws)["sub"] == sub_of(cognito_user(aws)) != before["sub"]
    (path,) = secret_files(aws)
    assert path.read_text().strip()
    assert "結果が分かりません" in capsys.readouterr().err


def test_emergency_reinvite_keeps_the_secret_when_compensation_is_unknown(aws, capsys):
    """補償の AdminDeleteUser が結果不明なら、ファイルを残し、利用者が残っている可能性を出して終える。"""
    make_active_admin(aws)
    aws.cognito.inject("admin_add_user_to_group", client_error("InternalErrorException", status=400))
    aws.cognito.inject("admin_delete_user", lost_response(), after=False, skip=1)
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1

    assert len(aws.cognito.called("admin_delete_user")) == 2
    assert "Cognito に利用者が残っている可能性" in capsys.readouterr().err
    (path,) = secret_files(aws)
    assert path.read_text().strip()
    assert cognito_user(aws)["UserStatus"] == "FORCE_CHANGE_PASSWORD"


def test_emergency_reinvite_rerun_finishes_when_the_previous_run_committed(aws, capsys):
    """前の回は台帳の書き込みが済んだのに結果不明で止まった。同じ --output-dir で流し直すと、作り直さずに終える。"""
    make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), after=True, skip=1)
    for _ in range(3):
        aws.dynamodb.inject("transact_write_items", lost_response())
    run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失")
    (first_secret,) = secret_files(aws)
    user = cognito_user(aws)
    creations = len(aws.cognito.called("admin_create_user"))
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    out = capsys.readouterr().out
    assert "前の回で済んでいました" in out
    assert str(first_secret) in out
    assert len(aws.cognito.called("admin_create_user")) == creations
    assert len(aws.cognito.called("admin_delete_user")) == 1
    assert cognito_user(aws)["Username"] == user["Username"]
    assert len(reinvite_rows(aws, "reinvite")) == 1
    assert secret_files(aws) == [first_secret]
    assert state_files(aws) == []


def test_emergency_reinvite_rerun_resends_the_pending_request_with_the_same_token(aws):
    """前の回は台帳の書き込みが届かないまま止まった。流し直すと同じ token で同じ要求を送る。"""
    make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), skip=1)
    for _ in range(3):
        aws.dynamodb.inject("transact_write_items", lost_response())
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    first = reinvite_attempts(aws)[0]

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    last = reinvite_attempts(aws)[-1]
    assert last["ClientRequestToken"] == first["ClientRequestToken"]
    assert last["TransactItems"] == first["TransactItems"]
    assert profile_of(aws)["sub"] == sub_of(cognito_user(aws))
    assert len(reinvite_rows(aws, "reinvite")) == 1
    assert len(aws.cognito.called("admin_create_user")) == 2
    assert state_files(aws) == []


def test_emergency_reinvite_keeps_the_user_when_the_state_file_fails_after_the_ledger(aws, monkeypatch, capsys):
    """台帳の書き込みが通った後に状態のファイルを書き直せなくても、補償せずファイルも消さない。"""
    before = make_active_admin(aws)
    original_forget = reinvite_state.ReinviteState.forget

    def broken_forget(self):
        if self.pending and self.pending.get("step") == "reinvite":
            raise OSError(28, "No space left on device")
        original_forget(self)

    monkeypatch.setattr(reinvite_state.ReinviteState, "forget", broken_forget)
    capsys.readouterr()

    code = run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失")

    assert code == 0
    assert len(aws.cognito.called("admin_delete_user")) == 1
    user = cognito_user(aws)
    assert profile_of(aws)["sub"] == sub_of(user) != before["sub"]
    (path,) = secret_files(aws)
    assert path.read_text().strip()
    err = capsys.readouterr().err
    assert "状態のファイル" in err
    assert "手で消して" in err


def test_emergency_reinvite_finished_message_points_to_the_latest_secret(aws, capsys):
    """1 回目は未反映の結果不明、2 回目は一時パスワードを出し直して台帳は通ったが応答を失った。
    3 回目の「前の回で済んでいました」は、2 回目のファイルを案内する。"""
    make_active_admin(aws)
    aws.dynamodb.inject("transact_write_items", lost_response(), skip=1)
    for _ in range(3):
        aws.dynamodb.inject("transact_write_items", lost_response())
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    (first_secret,) = secret_files(aws)

    aws.dynamodb.inject("transact_write_items", lost_response(), after=True)
    for _ in range(3):
        aws.dynamodb.inject("transact_write_items", lost_response())
    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    (second_secret,) = [path for path in secret_files(aws) if path != first_secret]
    (reset,) = aws.cognito.called("admin_set_user_password")
    assert second_secret.read_text().strip() == reset["Password"]
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0

    out = capsys.readouterr().out
    assert "前の回で済んでいました" in out
    assert str(second_secret) in out
    assert str(first_secret) not in out
    assert profile_of(aws)["sub"] == sub_of(cognito_user(aws))
    assert len(reinvite_rows(aws, "reinvite")) == 1


def test_emergency_reinvite_keeps_the_secret_when_deletion_is_unknown(aws):
    """古い利用者の削除の結果が分からないときは止め、もう一度流すと続きから進む。"""
    before = make_active_admin(aws)
    aws.cognito.inject("admin_delete_user", lost_response(), after=True)

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    assert len(secret_files(aws)) == 1

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 0
    assert profile_of(aws)["sub"] != before["sub"]
    assert len(reinvite_rows(aws, "reinvite")) == 1


@pytest.mark.parametrize("where", ["inside_repo", "read_only"])
def test_emergency_reinvite_checks_the_output_dir_before_any_change(aws, tmp_path, where):
    """保存先が不正なら、台帳も Cognito も変えない。"""
    before = make_active_admin(aws)
    if where == "inside_repo":
        output_dir = admin_ops.REPO_ROOT / "tmp-secrets-should-not-exist"
    else:
        output_dir = tmp_path / "read-only"
        output_dir.mkdir(mode=0o500)
    transactions = len(aws.dynamodb.called("transact_write_items"))

    code = admin_ops.main(
        ["emergency-reinvite", "dev", EMAIL, "--reason", "紛失", "--output-dir", str(output_dir)],
        clients=aws.clients,
        confirm=lambda p: True,
    )

    assert code == 1
    after = profile_of(aws)
    assert (after["status"], after["version"]) == (before["status"], before["version"])
    assert len(aws.dynamodb.called("transact_write_items")) == transactions
    for name in ("admin_disable_user", "admin_user_global_sign_out", "admin_delete_user"):
        assert aws.cognito.called(name) == []
    assert reinvite_rows(aws, "suspend") == []
    assert not (admin_ops.REPO_ROOT / "tmp-secrets-should-not-exist").exists()
    if where == "read_only":
        output_dir.chmod(0o700)


def test_emergency_reinvite_is_only_for_admins(aws, capsys):
    make_active_admin(aws)
    profile = profile_of(aws)
    aws.ledger.update_item(
        Key={"PK": profile["PK"], "SK": profile["SK"]},
        UpdateExpression="SET #r = :user",
        ExpressionAttributeNames={"#r": "role"},
        ExpressionAttributeValues={":user": "user"},
    )
    capsys.readouterr()

    assert run(aws, "emergency-reinvite", "dev", EMAIL, "--reason", "紛失") == 1
    assert "admin" in capsys.readouterr().err
    assert aws.cognito.called("admin_delete_user") == []


def test_emergency_reinvite_requires_a_reason(aws):
    make_active_admin(aws)

    with pytest.raises(SystemExit):
        admin_ops.main(["emergency-reinvite", "dev", EMAIL], clients=aws.clients, confirm=lambda p: True)


def test_emergency_reinvite_in_prod_asks_first(aws):
    make_active_admin(aws)

    assert run(aws, "emergency-reinvite", "prod", EMAIL, "--reason", "紛失", confirm=lambda p: False) == 1
    assert aws.cognito.called("admin_delete_user") == []
    assert profile_of(aws)["status"] == "active"


# =============================================================================
# check-waf（計画 4.1 の配備の手順 4）
# =============================================================================


def web_acl(action: str) -> dict:
    return {
        "WebACL": {
            "Name": "service-platform-userpool-dev",
            "Id": "11111111-1111-1111-1111-210000000001",
            "ARN": "arn:aws:wafv2:ap-northeast-1:210000000001:regional/webacl/service-platform-userpool-dev/1111",
            "DefaultAction": {"Allow": {}},
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "service-platform-userpool-dev",
            },
            "Rules": [
                {
                    "Name": "change-password",
                    "Priority": 0,
                    "Statement": {
                        "ByteMatchStatement": {
                            "SearchString": b"AWSCognitoIdentityProviderService.ChangePassword",
                            "FieldToMatch": {"SingleHeader": {"Name": "x-amz-target"}},
                            "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                            "PositionalConstraint": "EXACTLY",
                        }
                    },
                    "Action": {action: {}},
                    "VisibilityConfig": {
                        "SampledRequestsEnabled": True,
                        "CloudWatchMetricsEnabled": True,
                        "MetricName": "change-password",
                    },
                },
                {
                    "Name": "rate-per-ip",
                    "Priority": 1,
                    "Statement": {"RateBasedStatement": {"Limit": 300, "AggregateKeyType": "IP"}},
                    "Action": {"Count": {}},
                    "VisibilityConfig": {
                        "SampledRequestsEnabled": True,
                        "CloudWatchMetricsEnabled": True,
                        "MetricName": "rate-per-ip",
                    },
                },
            ],
        }
    }


@pytest.fixture
def waf_stubs(aws):
    raw = aws.raw_cognito
    with Stubber(aws.clients.wafv2) as waf, Stubber(raw) as cognito:
        yield waf, cognito


def expect_web_acl(waf, aws, action="Block"):
    waf.add_response(
        "get_web_acl_for_resource",
        web_acl(action),
        {"ResourceArn": f"arn:aws:cognito-idp:ap-northeast-1:210000000001:userpool/{aws.pool_id}"},
    )


def test_check_waf_passes_when_change_password_is_blocked(aws, waf_stubs, capsys):
    waf, cognito = waf_stubs
    expect_web_acl(waf, aws)
    cognito.add_client_error(
        "change_password",
        service_error_code="ForbiddenException",
        service_message=WAF_BLOCK_MESSAGE,
        http_status_code=403,
    )

    assert run(aws, "check-waf", "dev") == 0

    out = capsys.readouterr().out
    assert "change-password" in out
    assert "OK" in out
    waf.assert_no_pending_responses()
    cognito.assert_no_pending_responses()


def test_check_waf_in_prod_asks_before_calling_change_password(aws, waf_stubs):
    """prod では ChangePassword (状態を変えうる API) の前に確認を求め、拒めば呼ばない。"""
    waf, cognito = waf_stubs
    expect_web_acl(waf, aws)
    prompts = []

    assert run(aws, "check-waf", "prod", confirm=lambda p: prompts.append(p) or False) == 1

    assert prompts and "prod" in prompts[0]
    assert aws.cognito.called("change_password") == []
    waf.assert_no_pending_responses()


def test_check_waf_in_prod_runs_after_confirmation(aws, waf_stubs):
    waf, cognito = waf_stubs
    expect_web_acl(waf, aws)
    cognito.add_client_error(
        "change_password",
        service_error_code="ForbiddenException",
        service_message=WAF_BLOCK_MESSAGE,
        http_status_code=403,
    )

    assert run(aws, "check-waf", "prod", confirm=lambda p: True) == 0
    cognito.assert_no_pending_responses()


def test_check_waf_forged_token_names_the_pool_and_client(aws, waf_stubs):
    """WAF はプールに付くので、トークンからプールが分かる形にする (署名は無効)。"""
    waf, cognito = waf_stubs
    expect_web_acl(waf, aws)
    seen = {}

    def capture(params, **kwargs):
        seen.update(params)

    aws.raw_cognito.meta.events.register("provide-client-params.cognito-idp.ChangePassword", capture)
    cognito.add_client_error(
        "change_password",
        service_error_code="ForbiddenException",
        service_message=WAF_BLOCK_MESSAGE,
        http_status_code=403,
    )

    run(aws, "check-waf", "dev")

    claims = unverified_claims(seen["AccessToken"])
    assert claims["iss"] == f"https://cognito-idp.ap-northeast-1.amazonaws.com/{aws.pool_id}"
    assert claims["client_id"] == CLIENT_ID
    assert claims["token_use"] == "access"


def test_check_waf_uses_a_given_access_token_without_printing_it(aws, waf_stubs, monkeypatch, capsys):
    waf, cognito = waf_stubs
    token = "eyJ.real-test-user-token.sig"
    monkeypatch.setenv(admin_ops.ACCESS_TOKEN_ENV, token)
    expect_web_acl(waf, aws)
    cognito.add_client_error(
        "change_password",
        service_error_code="ForbiddenException",
        service_message=WAF_BLOCK_MESSAGE,
        http_status_code=403,
        expected_params={"AccessToken": token, "ProposedPassword": admin_ops.WAF_CHECK_PASSWORD},
    )

    assert run(aws, "check-waf", "dev") == 0
    out, err = capsys.readouterr()
    assert token not in out + err


def test_check_waf_fails_when_cognito_answers_first(aws, waf_stubs, capsys):
    """WAF より先に Cognito がトークンを拒んだら、止まったことを確かめられていない。"""
    waf, cognito = waf_stubs
    expect_web_acl(waf, aws)
    cognito.add_client_error("change_password", service_error_code="NotAuthorizedException", http_status_code=400)

    assert run(aws, "check-waf", "dev") == 1
    assert admin_ops.ACCESS_TOKEN_ENV in capsys.readouterr().err


def test_check_waf_fails_when_the_rule_only_counts(aws, waf_stubs):
    waf, cognito = waf_stubs
    expect_web_acl(waf, aws, action="Count")
    cognito.add_client_error(
        "change_password",
        service_error_code="ForbiddenException",
        service_message=WAF_BLOCK_MESSAGE,
        http_status_code=403,
    )

    assert run(aws, "check-waf", "dev") == 1


def test_check_waf_fails_without_a_web_acl(aws, waf_stubs):
    waf, _ = waf_stubs
    waf.add_response(
        "get_web_acl_for_resource",
        {},
        {"ResourceArn": f"arn:aws:cognito-idp:ap-northeast-1:210000000001:userpool/{aws.pool_id}"},
    )

    assert run(aws, "check-waf", "dev") == 1


def test_check_waf_warns_when_the_password_was_accepted(aws, waf_stubs, monkeypatch, capsys):
    waf, cognito = waf_stubs
    monkeypatch.setenv(admin_ops.ACCESS_TOKEN_ENV, "eyJ.real.sig")
    expect_web_acl(waf, aws)
    cognito.add_response("change_password", {})

    assert run(aws, "check-waf", "dev") == 1
    assert "パスワード" in capsys.readouterr().err


def test_main_reports_aws_errors_without_a_traceback(aws, capsys, monkeypatch):
    class DeniedSsm:
        def get_parameter(self, **kwargs):
            raise client_error("AccessDeniedException")

    monkeypatch.setattr(aws.clients, "ssm", DeniedSsm())

    assert run(aws, "create-admin", "dev", EMAIL) == 1
    assert "AccessDeniedException" in capsys.readouterr().err


def test_secret_file_is_prepared_empty_and_filled_later(tmp_path):
    path = admin_ops.prepare_secret(output_dir=tmp_path, env="dev")

    assert path.read_text() == ""
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert os.path.dirname(path) == str(tmp_path)

    admin_ops.fill_secret(path, "x")
    assert path.read_text() == "x\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secret_file_is_never_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_ops.secrets, "token_hex", lambda n: "fixed")
    admin_ops.prepare_secret(output_dir=tmp_path, env="dev")

    with pytest.raises(FileExistsError):
        admin_ops.prepare_secret(output_dir=tmp_path, env="dev")


@pytest.mark.parametrize(
    "arn,actor",
    [
        ("arn:aws:iam::210000000001:user/ops-user", "script:ops-user"),
        ("arn:aws:sts::210000000001:assumed-role/OpsRole/ops-session", "script:ops-session"),
        ("arn:aws:iam::210000000001:root", "script:root"),
    ],
)
def test_script_actor_names_the_iam_principal(arn, actor):
    assert admin_ops.script_actor(arn) == actor


def test_shell_entry_points_are_executable():
    """未コミットの作業ツリーを含め、シェルの入口は実行可能であること。"""
    root = pathlib.Path(admin_ops.REPO_ROOT)
    paths = list((root / "scripts").glob("*.sh"))
    assert root / "scripts/emergency-reinvite-admin.sh" in paths
    assert {str(p): oct(p.stat().st_mode & 0o777) for p in paths if not p.stat().st_mode & 0o111} == {}


def test_show_admin_displays_only_selected_state_in_jst(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws, mfa_enabled_at=0)
    profile = profile_of(aws)
    aws.ledger.update_item(
        Key={"PK": profile["PK"], "SK": profile["SK"]},
        UpdateExpression="SET contact = :secret, note = :secret, last_op_id = :op",
        ExpressionAttributeValues={":secret": "private-value", ":op": "operation-marker"},
    )
    capsys.readouterr()
    before = len(aws.dynamodb.called("transact_write_items"))

    assert run(aws, "show-admin", "prod", EMAIL, confirm=lambda p: pytest.fail(p)) == 0

    out = capsys.readouterr().out
    for value in (
        "status",
        "admin_state",
        "mfa_enabled_at",
        "reinvite_in_progress",
        "last_op_id",
        "1970-01-01 09:00:00 JST",
        "Enabled",
        "UserStatus",
        "UserMFASettingList",
        "SOFTWARE_TOKEN_MFA",
        "operation-marker",
    ):
        assert value in out
    assert "private-value" not in out
    assert len(aws.dynamodb.called("transact_write_items")) == before
    assert audit_rows(aws, "script.activate_admin") == []


def test_show_admin_can_read_the_ledger_after_cognito_was_deleted(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    profile = profile_of(aws)
    aws.raw_cognito.admin_delete_user(UserPoolId=aws.pool_id, Username=EMAIL)
    capsys.readouterr()

    assert run(aws, "show-admin", "dev", EMAIL) == 0

    out = capsys.readouterr().out
    assert profile["user_id"] in out
    assert "Cognito: 利用者なし" in out
    assert "pending" in out


def test_show_admin_reports_an_orphan_and_a_missing_user(aws, capsys):
    aws.raw_cognito.admin_create_user(UserPoolId=aws.pool_id, Username=EMAIL, MessageAction="SUPPRESS")

    assert run(aws, "show-admin", "dev", EMAIL) == 0
    out = capsys.readouterr().out
    assert "PROFILE: 利用者なし" in out
    assert "SUB: 対応なし" in out
    assert run(aws, "show-admin", "dev", "missing@example.com") == 0
    assert "Cognito: 利用者なし" in capsys.readouterr().out


def test_activate_admin_shows_registration_before_confirmation_and_can_cancel(aws, capsys):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws, mfa_enabled_at=0)
    capsys.readouterr()
    prompts = []

    def refuse(prompt):
        out = capsys.readouterr().out
        assert "1970-01-01 09:00:00 JST" in out
        assert "SOFTWARE_TOKEN_MFA" in out
        prompts.append(prompt)
        return False

    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "phone", confirm=refuse) == 1
    assert "本人に別経路で登録した時刻を確かめたか" in prompts[0]
    assert profile_of(aws)["admin_state"] == "awaiting"
    assert audit_rows(aws, "script.activate_admin") == []


@pytest.mark.parametrize("env,expected", [("dev", 0), ("prod", 1)])
def test_activate_admin_yes_only_skips_the_dev_confirmation(aws, env, expected):
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)
    prompts = []

    result = run(
        aws, "activate-admin", env, EMAIL, "--method", "phone", "--yes", confirm=lambda p: prompts.append(p) or False
    )

    assert result == expected
    assert bool(prompts) == (env == "prod")


def active_admin(aws, monkeypatch):
    from unittest.mock import Mock

    factors = Mock(return_value={"ConfiguredUserAuthFactors": ["PASSWORD", "WEB_AUTHN"]})
    monkeypatch.setattr(admin_ops.cognito_extra, "admin_get_user_auth_factors", factors)
    run(aws, "create-admin", "dev", EMAIL)
    set_awaiting(aws)
    assert run(aws, "activate-admin", "dev", EMAIL, "--method", "other", "--yes") == 0


def test_enable_passkey_mfa_preserves_totp_and_records_audit(aws, monkeypatch):
    from unittest.mock import Mock

    active_admin(aws, monkeypatch)
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    before = profile_of(aws)
    assert run(aws, "enable-passkey-mfa", "dev", EMAIL) == 0
    preference.assert_called_once_with(
        UserPoolId=aws.pool_id,
        Username=cognito_user(aws)["Username"],
        SoftwareTokenMfaSettings={"Enabled": True, "PreferredMfa": True},
        WebAuthnMfaSettings={"Enabled": True},
    )
    assert profile_of(aws) == before
    rows = audit_rows(aws, "script.enable_passkey_mfa")
    assert len(rows) == 2
    assert {row["detail"]["step"] for row in rows} == {"requested", "enabled"}
    assert all(row["target_user_id"] == before["user_id"] for row in rows)
    assert all(row["detail"]["caller_arn"] for row in rows)


@pytest.mark.parametrize("invalid", ["no_totp", "pending", "suspended", "disabled", "no_group"])
def test_enable_passkey_mfa_rejects_unready_admins(aws, monkeypatch, invalid):
    from unittest.mock import Mock

    active_admin(aws, monkeypatch)
    username = cognito_user(aws)["Username"]
    if invalid == "no_totp":
        aws.cognito.totp_users.clear()
    elif invalid in ("pending", "suspended"):
        field = "admin_state" if invalid == "pending" else "status"
        aws.ledger.update_item(
            Key={"PK": profile_of(aws)["PK"], "SK": "PROFILE"},
            UpdateExpression="SET #f = :v",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":v": invalid},
        )
    elif invalid == "disabled":
        aws.raw_cognito.admin_disable_user(UserPoolId=aws.pool_id, Username=username)
    else:
        aws.raw_cognito.admin_remove_user_from_group(UserPoolId=aws.pool_id, Username=username, GroupName="admin")
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    assert run(aws, "enable-passkey-mfa", "dev", EMAIL) == 1
    preference.assert_not_called()
    assert audit_rows(aws, "script.enable_passkey_mfa") == []


def test_enable_passkey_mfa_requires_audit_before_cognito(aws, monkeypatch):
    from unittest.mock import Mock

    active_admin(aws, monkeypatch)
    aws.dynamodb.failures["transact_write_items"] = client_error("AccessDeniedException")
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    assert run(aws, "enable-passkey-mfa", "dev", EMAIL) == 1
    preference.assert_not_called()


def test_enable_passkey_mfa_reports_cognito_failure_without_changing_profile(aws, monkeypatch):
    from unittest.mock import Mock

    active_admin(aws, monkeypatch)
    before = profile_of(aws)
    monkeypatch.setattr(
        aws.cognito, "admin_set_user_mfa_preference", Mock(side_effect=client_error("InternalErrorException"))
    )
    assert run(aws, "enable-passkey-mfa", "dev", EMAIL) == 1
    assert profile_of(aws) == before


def test_enable_passkey_mfa_in_prod_requires_confirmation(aws, monkeypatch):
    from unittest.mock import Mock

    active_admin(aws, monkeypatch)
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    assert run(aws, "enable-passkey-mfa", "prod", EMAIL, confirm=lambda prompt: False) == 1
    preference.assert_not_called()
    assert audit_rows(aws, "script.enable_passkey_mfa") == []


def test_enable_passkey_mfa_requires_registered_passkey_before_audit(aws, monkeypatch, capsys):
    from unittest.mock import Mock

    active_admin(aws, monkeypatch)
    factors = admin_ops.cognito_extra.admin_get_user_auth_factors
    factors.return_value = {"ConfiguredUserAuthFactors": ["PASSWORD"]}
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "admin_set_user_mfa_preference", preference)
    before = profile_of(aws)
    assert run(aws, "enable-passkey-mfa", "dev", EMAIL) == 1
    assert "パスキーが登録されていません (本人がアカウントの画面で登録してから流す)" in capsys.readouterr().err
    factors.assert_called_once_with(aws.cognito, aws.pool_id, cognito_user(aws)["Username"])
    preference.assert_not_called()
    assert audit_rows(aws, "script.enable_passkey_mfa") == []
    assert profile_of(aws) == before
