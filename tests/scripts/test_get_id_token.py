"""`scripts/get_id_token.py` のテスト（計画 4.2 のリスク、判断 6）。

USER_AUTH の PASSWORD で始め、NEW_PASSWORD_REQUIRED (一時パスワード → 本パスワード) と
SOFTWARE_TOKEN_MFA (TOTP) の応答を返す。moto は USER_AUTH を持たないので、Cognito は偽物で包む。
標準出力に出るのは ID トークンだけで、パスワードを出さない。
"""

import io
import os

import get_id_token
import pytest

POOL = "ap-northeast-1_EXMPLpool1"
CLIENT = "exampleclientid"
EMAIL = "admin@example.com"
TEMPORARY = "Temporary1234567890"
PERMANENT = "Permanent1234567890Xy"


class FakeSsm:
    def get_parameter(self, Name):  # noqa: N803 (boto3 の引数名)
        values = {"/service-platform/dev/cognito/user-pool-id": POOL, "/service-platform/dev/cognito/client-id": CLIENT}
        return {"Parameter": {"Value": values[Name]}}


class FakeCognito:
    """`steps` の順に応答する。最後は ID トークン。"""

    def __init__(self, *challenges):
        self.challenges = list(challenges)
        self.calls = []

    def _next(self):
        if self.challenges:
            name = self.challenges.pop(0)
            return {"ChallengeName": name, "Session": f"session-{name}", "ChallengeParameters": {}}
        return {
            "AuthenticationResult": {"IdToken": "id.token.value", "AccessToken": "access", "RefreshToken": "refresh"}
        }

    def initiate_auth(self, **kwargs):
        self.calls.append(("initiate_auth", kwargs))
        return self._next()

    def respond_to_auth_challenge(self, **kwargs):
        self.calls.append(("respond_to_auth_challenge", kwargs))
        return self._next()


@pytest.fixture
def run(monkeypatch, capsys, tmp_path):
    def _run(cognito, *args, password=TEMPORARY, new_password=None):
        monkeypatch.setattr(get_id_token, "make_clients", lambda region: (FakeSsm(), cognito))
        password_file = tmp_path / "password.txt"
        password_file.write_text(f"{password}\n", encoding="utf-8")
        os.chmod(password_file, 0o600)
        argv = ["dev", EMAIL, "--password-file", str(password_file), *args]
        if new_password is not None:
            new_file = tmp_path / "new-password.txt"
            new_file.write_text(new_password, encoding="utf-8")
            os.chmod(new_file, 0o600)
            argv += ["--new-password-file", str(new_file)]
        code = get_id_token.main(argv)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def test_password_sign_in_prints_only_the_id_token(run):
    cognito = FakeCognito()

    code, out, err = run(cognito)

    assert code == 0
    assert out == "id.token.value\n"
    ((name, request),) = cognito.calls
    assert name == "initiate_auth"
    assert request["AuthFlow"] == "USER_AUTH"
    assert request["ClientId"] == CLIENT
    assert request["AuthParameters"] == {"USERNAME": EMAIL, "PREFERRED_CHALLENGE": "PASSWORD", "PASSWORD": TEMPORARY}
    assert TEMPORARY not in out + err
    assert "access" not in out


def test_new_password_required_sets_the_permanent_password(run):
    cognito = FakeCognito("NEW_PASSWORD_REQUIRED")

    code, out, err = run(cognito, new_password=PERMANENT)

    assert code == 0
    assert out == "id.token.value\n"
    _, respond = cognito.calls[1]
    assert respond["ChallengeName"] == "NEW_PASSWORD_REQUIRED"
    assert respond["Session"] == "session-NEW_PASSWORD_REQUIRED"
    assert respond["ChallengeResponses"] == {"USERNAME": EMAIL, "NEW_PASSWORD": PERMANENT}
    assert PERMANENT not in out + err


def test_new_password_required_without_a_new_password_fails(run):
    cognito = FakeCognito("NEW_PASSWORD_REQUIRED")

    code, out, err = run(cognito)

    assert code == 1
    assert out == ""
    assert "--new-password-file" in err


def test_totp_challenge_uses_the_code(run):
    cognito = FakeCognito("SOFTWARE_TOKEN_MFA")

    code, out, _err = run(cognito, "--totp-code", "123456")

    assert code == 0
    assert out == "id.token.value\n"
    _, respond = cognito.calls[1]
    assert respond["ChallengeResponses"] == {"USERNAME": EMAIL, "SOFTWARE_TOKEN_MFA_CODE": "123456"}


def test_totp_prompt_goes_to_stderr_so_stdout_has_only_the_token(run, monkeypatch):
    cognito = FakeCognito("SOFTWARE_TOKEN_MFA")
    monkeypatch.setattr("sys.stdin", io.StringIO("246810\n"))

    code, out, err = run(cognito)

    assert code == 0
    assert out == "id.token.value\n"
    assert "TOTP code: " in err
    _, respond = cognito.calls[1]
    assert respond["ChallengeResponses"] == {"USERNAME": EMAIL, "SOFTWARE_TOKEN_MFA_CODE": "246810"}


def test_new_password_then_totp(run):
    cognito = FakeCognito("NEW_PASSWORD_REQUIRED", "SOFTWARE_TOKEN_MFA")

    code, out, _err = run(cognito, "--totp-code", "654321", new_password=PERMANENT)

    assert code == 0
    assert out == "id.token.value\n"
    assert [kwargs["ChallengeName"] for name, kwargs in cognito.calls[1:]] == [
        "NEW_PASSWORD_REQUIRED",
        "SOFTWARE_TOKEN_MFA",
    ]


def test_select_challenge_answers_with_the_password(run):
    cognito = FakeCognito("SELECT_CHALLENGE")

    code, _out, _err = run(cognito)

    assert code == 0
    _, respond = cognito.calls[1]
    assert respond["ChallengeResponses"] == {"USERNAME": EMAIL, "ANSWER": "PASSWORD", "PASSWORD": TEMPORARY}


def test_unexpected_challenge_fails(run):
    cognito = FakeCognito("EMAIL_OTP")

    code, out, err = run(cognito)

    assert code == 1
    assert out == ""
    assert "EMAIL_OTP" in err


def test_unknown_env_fails(run, capsys):
    assert get_id_token.main(["staging", EMAIL]) == 1
