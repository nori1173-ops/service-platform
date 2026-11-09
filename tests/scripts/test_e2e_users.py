"""dev 専用 E2E 利用者の送信抑止・秘密保護・片付け。"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import admin_ops
import pytest
from botocore.exceptions import ClientError

SPEC = importlib.util.spec_from_file_location("e2e_users", Path(__file__).parents[2] / "scripts/e2e-users.py")
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)


def test_prod_is_rejected_before_creating_clients(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(e2e, "make_clients", factory)
    with pytest.raises(SystemExit):
        e2e.main(["create", "prod"])
    factory.assert_not_called()


@pytest.mark.parametrize(
    "email", ["person@example.co.jp", "e2e@sub.example.com", "a@example.com.evil", "bad", "a\n@example.com"]
)
def test_only_example_com_is_accepted(email):
    with pytest.raises(e2e.E2EError):
        e2e.validate_email(email)


def test_suppress_permanent_password_and_ledger_cleanup(aws, capsys):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    state = json.loads(manifest.read_text())
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert manifest.parent.stat().st_mode & 0o777 == 0o700
    assert len(state["users"]) == 3
    calls = aws.cognito.called("admin_create_user")
    assert len(calls) == 3
    assert all(c["MessageAction"] == "SUPPRESS" for c in calls)
    assert all(c["Username"].endswith("@example.com") for c in calls)
    assert all(c["Permanent"] is True for c in aws.cognito.called("admin_set_user_password"))
    assert aws.ledger.scan()["Count"] == 6
    output = capsys.readouterr()
    for user in state["users"].values():
        assert user["password"] not in output.out + output.err
    e2e.delete_suite(aws.clients, config, manifest)
    assert not aws.raw_cognito.list_users(UserPoolId=aws.pool_id)["Users"]
    assert aws.ledger.scan()["Count"] == 0
    assert json.loads(manifest.read_text())["deleted"] is True
    e2e.delete_suite(aws.clients, config, manifest)


def test_existing_manifest_is_not_overwritten(aws):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    before = manifest.read_bytes()
    with pytest.raises(e2e.E2EError):
        e2e.create_suite(aws.clients, config, manifest, enroll=False)
    assert manifest.read_bytes() == before


def test_aws_error_does_not_print_secret(aws, capsys):
    secret = "SecretNotForOutput123"
    aws.cognito.failures["admin_create_user"] = ClientError(
        {"Error": {"Code": "InternalErrorException", "Message": secret}}, "AdminCreateUser"
    )
    assert e2e.main(["create", "dev", "--secrets-dir", str(aws.output_dir)], clients=aws.clients) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert (aws.output_dir / "e2e-users.json").exists()


def test_manifest_inside_repository_is_rejected(aws):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    with pytest.raises(e2e.E2EError):
        e2e.create_suite(aws.clients, config, Path(__file__).parents[2] / "secrets" / "e2e-users.json", enroll=False)
    assert not aws.cognito.calls


def test_enrollment_uses_otplib_and_original_session(aws, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    user = {"email": "e2e@example.com"}
    tokens = {"AccessToken": "access-not-logged", "IdToken": "id-not-logged"}
    signin = Mock(return_value=tokens)
    monkeypatch.setattr(e2e, "password_sign_in", signin)
    monkeypatch.setattr(aws.cognito, "associate_software_token", Mock(return_value={"SecretCode": "TOTP-SECRET"}))
    verify = Mock(return_value={"Status": "SUCCESS"})
    monkeypatch.setattr(aws.cognito, "verify_software_token", verify)
    preference = Mock()
    monkeypatch.setattr(aws.cognito, "set_user_mfa_preference", preference)
    clock = [29]
    monkeypatch.setattr(e2e.time, "time", lambda: clock[0])

    def process_result(*args, **kwargs):
        clock[0] = 31
        return SimpleNamespace(stdout="123456", returncode=0)

    processes = Mock(side_effect=process_result)
    monkeypatch.setattr(e2e.subprocess, "run", processes)
    response = MagicMock()
    response.__enter__.return_value.status = 200
    monkeypatch.setattr(e2e.urllib.request, "urlopen", Mock(return_value=response))
    monkeypatch.setattr(e2e.time, "sleep", Mock())
    persist = Mock()
    e2e.enroll_admin(aws.clients, config, user, persist)
    signin.assert_called_once()
    verify.assert_called_once_with(AccessToken=tokens["AccessToken"], UserCode="123456")
    preference.assert_called_once_with(
        AccessToken=tokens["AccessToken"], SoftwareTokenMfaSettings={"Enabled": True, "PreferredMfa": True}
    )
    assert persist.call_count == 2
    assert user["totp_secret"] == "TOTP-SECRET"
    assert user["totp_last_step"] == 1
    assert "otplib" in processes.call_args_list[0].args[0][-1]
    assert processes.call_args_list[0].kwargs["input"] == "TOTP-SECRET"
    assert "activate-admin" in processes.call_args_list[1].args[0]


def test_cleanup_rejects_other_users_before_mutating(aws):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    state = json.loads(manifest.read_text())
    state["users"]["customer"]["email"] = "someone@example.com"
    e2e.save(manifest, state)
    with pytest.raises(e2e.E2EError):
        e2e.delete_suite(aws.clients, config, manifest)
    assert not aws.cognito.called("admin_delete_user")
    assert aws.ledger.scan()["Count"] == 6


def test_cleanup_recovers_partial_create(aws):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    aws.cognito.inject(
        "admin_set_user_password", ClientError({"Error": {"Code": "InternalErrorException"}}, "AdminSetUserPassword")
    )
    with pytest.raises(ClientError):
        e2e.create_suite(aws.clients, config, manifest, enroll=False)
    e2e.delete_suite(aws.clients, config, manifest)
    assert not aws.raw_cognito.list_users(UserPoolId=aws.pool_id)["Users"]


def test_symlink_secret_path_is_rejected(aws, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    with pytest.raises(e2e.E2EError):
        e2e.create_suite(aws.clients, config, link / "e2e-users.json", enroll=False)
    assert not aws.cognito.calls


def test_only_ended_e2e_assignments_can_be_deleted(aws):
    from service_platform import ledger

    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    user = json.loads(manifest.read_text())["users"]["customer"]
    period_id, assignment_id = e2e.new_ulid(), e2e.new_ulid()
    reverse = {
        "PK": ledger.user_pk(user["user_id"]),
        "SK": ledger.assign_sk(period_id, assignment_id),
        "device_period_id": period_id,
        "assignment_id": assignment_id,
        "valid_to": None,
    }
    forward = {
        "PK": ledger.dperiod_pk(period_id),
        "SK": ledger.assignment_sk(user["user_id"], assignment_id),
        "user_id": user["user_id"],
        "valid_to": None,
    }
    aws.ledger.put_item(Item=reverse)
    aws.ledger.put_item(Item=forward)
    with pytest.raises(e2e.E2EError):
        e2e.delete_suite(aws.clients, config, manifest)
    assert not aws.cognito.called("admin_delete_user")
    for row in (reverse, forward):
        row.update(valid_to="2026-01-01T00:00:00Z", ended_by="e2e")
        aws.ledger.put_item(Item=row)
    e2e.delete_suite(aws.clients, config, manifest)
    assert aws.ledger.scan()["Count"] == 0


def test_secrets_dir_is_required_before_creating_clients(monkeypatch, capsys):
    factory = Mock()
    monkeypatch.setattr(e2e, "make_clients", factory)
    with pytest.raises(SystemExit) as error:
        e2e.main(["create", "dev"])
    assert error.value.code == 2
    assert "--secrets-dir" in capsys.readouterr().err
    factory.assert_not_called()


def test_mnt_secrets_dir_is_rejected_before_creating_clients(monkeypatch, capsys):
    factory = Mock()
    monkeypatch.setattr(e2e, "make_clients", factory)
    assert e2e.main(["create", "dev", "--secrets-dir", "/mnt/c/e2e-secrets"]) == 1
    assert "/mnt/" in capsys.readouterr().err
    factory.assert_not_called()


@pytest.mark.parametrize("mode", [0o755, 0o1777])
def test_existing_directory_permissions_are_preserved_and_rejected(aws, mode):
    aws.output_dir.mkdir(mode=mode)
    aws.output_dir.chmod(mode)
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    with pytest.raises(e2e.E2EError, match="700"):
        e2e.create_suite(aws.clients, config, aws.output_dir / "e2e-users.json", enroll=False)
    assert aws.output_dir.stat().st_mode & 0o7777 == mode
    assert not aws.cognito.called("admin_create_user")


def test_only_new_directories_get_private_permissions(tmp_path):
    tmp_path.chmod(0o755)
    manifest = tmp_path / "new-parent" / "new-secrets" / "e2e-users.json"
    e2e.secret_path(manifest)
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert manifest.parent.stat().st_mode & 0o777 == 0o700
    assert manifest.parent.parent.stat().st_mode & 0o777 == 0o700


def test_ineffective_chmod_stops_before_cognito_changes(aws, monkeypatch):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    original_open = e2e.os.open
    original_fchmod = e2e.os.fchmod

    def public_open(*args):
        fd = original_open(*args)
        original_fchmod(fd, 0o644)
        return fd

    monkeypatch.setattr(e2e.os, "open", public_open)
    monkeypatch.setattr(e2e.os, "fchmod", lambda *args: None)
    with pytest.raises(e2e.E2EError, match="600"):
        e2e.create_suite(aws.clients, config, aws.output_dir / "e2e-users.json", enroll=False)
    assert not aws.cognito.called("admin_create_user")
    assert aws.ledger.scan()["Count"] == 0


def test_profile_sub_mismatch_is_rejected_even_without_cognito_user(aws):
    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    state = json.loads(manifest.read_text())
    customer = state["users"]["customer"]
    aws.raw_cognito.admin_delete_user(UserPoolId=aws.pool_id, Username=customer["username"])
    customer["sub"] = "different-sub"
    e2e.save(manifest, state)
    with pytest.raises(e2e.E2EError):
        e2e.delete_suite(aws.clients, config, manifest)
    assert not aws.cognito.called("admin_delete_user")
    assert aws.ledger.scan()["Count"] == 6


@pytest.mark.parametrize("mapping_exists", [False, True])
def test_sub_mapping_not_owned_by_e2e_user_is_preserved(aws, mapping_exists):
    from service_platform import ledger

    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    customer = json.loads(manifest.read_text())["users"]["customer"]
    key = {"PK": ledger.sub_pk(customer["sub"]), "SK": ledger.SUB_SK}
    aws.ledger.delete_item(Key=key)
    if mapping_exists:
        aws.ledger.put_item(Item={**key, "user_id": "other-user"})
    e2e.delete_suite(aws.clients, config, manifest)
    remaining = aws.ledger.get_item(Key=key, ConsistentRead=True).get("Item")
    assert remaining == ({**key, "user_id": "other-user"} if mapping_exists else None)
    assert aws.ledger.scan()["Count"] == int(mapping_exists)
    assert not aws.raw_cognito.list_users(UserPoolId=aws.pool_id)["Users"]


def test_sub_mapping_delete_does_not_ignore_unexpected_errors(aws, monkeypatch):
    from service_platform import ledger

    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    delete = aws.ledger.delete_item

    def delete_item(**kwargs):
        if kwargs["Key"]["PK"].startswith("SUB#"):
            raise ClientError({"Error": {"Code": "InternalServerError"}}, "DeleteItem")
        return delete(**kwargs)

    monkeypatch.setattr(aws.ledger, "delete_item", delete_item)
    monkeypatch.setattr(ledger, "_table", lambda: aws.ledger)
    with pytest.raises(ClientError):
        e2e.delete_suite(aws.clients, config, manifest)
    assert not json.loads(manifest.read_text())["deleted"]


def test_cleanup_removes_memberships_and_their_reverse_rows_but_keeps_groups(aws):
    """: E2E の利用者の所属 (`MEMBER#`) と逆引きを消す。グループの行は終えるだけで消さない。"""
    from service_platform import ledger

    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    user = json.loads(manifest.read_text())["users"]["customer"]
    group_id, membership_id = e2e.new_ulid(), e2e.new_ulid()
    group = {"PK": ledger.group_pk(group_id), "SK": ledger.GROUP_PROFILE_SK, "group_id": group_id, "status": "ended"}
    own = {
        "PK": ledger.user_pk(user["user_id"]),
        "SK": ledger.member_sk(group_id),
        "group_id": group_id,
        "membership_id": membership_id,
    }
    reverse = {
        "PK": ledger.group_pk(group_id),
        "SK": ledger.group_member_sk(user["user_id"]),
        "user_id": user["user_id"],
        "membership_id": membership_id,
    }
    other = {**reverse, "SK": ledger.group_member_sk(e2e.new_ulid()), "membership_id": e2e.new_ulid()}
    for row in (group, own, reverse, other):
        aws.ledger.put_item(Item=row)

    e2e.delete_suite(aws.clients, config, manifest)

    keys = {(item["PK"], item["SK"]) for item in aws.ledger.scan()["Items"]}
    assert keys == {(group["PK"], group["SK"]), (other["PK"], other["SK"])}


def test_cleanup_removes_the_member_reverse_before_the_users_rows(aws, monkeypatch):
    """逆引きを先に消す。利用者の行の削除の途中で落ちても、流し直せば逆引きが残らない (グループを終えられる)。"""
    from service_platform import ledger

    config = admin_ops.resolve_config(aws.clients, "dev", "ap-northeast-1")
    manifest = aws.output_dir / "e2e-users.json"
    e2e.create_suite(aws.clients, config, manifest, enroll=False)
    user = json.loads(manifest.read_text())["users"]["customer"]
    group_id, membership_id = e2e.new_ulid(), e2e.new_ulid()
    own = {
        "PK": ledger.user_pk(user["user_id"]),
        "SK": ledger.member_sk(group_id),
        "group_id": group_id,
        "membership_id": membership_id,
    }
    reverse = {
        "PK": ledger.group_pk(group_id),
        "SK": ledger.group_member_sk(user["user_id"]),
        "user_id": user["user_id"],
        "membership_id": membership_id,
    }
    for row in (own, reverse):
        aws.ledger.put_item(Item=row)
    delete = aws.ledger.delete_item
    failed = []

    def delete_item(**kwargs):
        key = kwargs["Key"]
        if not failed and key == {"PK": ledger.user_pk(user["user_id"]), "SK": ledger.PROFILE_SK}:
            failed.append(key)
            raise ClientError({"Error": {"Code": "InternalServerError"}}, "DeleteItem")
        return delete(**kwargs)

    monkeypatch.setattr(aws.ledger, "delete_item", delete_item)
    monkeypatch.setattr(ledger, "_table", lambda: aws.ledger)
    with pytest.raises(ClientError):
        e2e.delete_suite(aws.clients, config, manifest)
    assert failed

    e2e.delete_suite(aws.clients, config, manifest)

    keys = {(item["PK"], item["SK"]) for item in aws.ledger.scan()["Items"]}
    assert (reverse["PK"], reverse["SK"]) not in keys
    assert (own["PK"], own["SK"]) not in keys
