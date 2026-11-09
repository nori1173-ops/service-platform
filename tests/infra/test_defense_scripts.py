"""配備せずに CLI を差し替え、リージョンとパラメータの受け渡しを確かめる。"""

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
STUB = r"""
import json
import os
import pathlib
import sys
import tomllib

args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
call = {"tool": name, "args": args, "cwd": os.getcwd()}
if name == "sam" and args[0] == "deploy" and "--config-file" in args:
    path = args[args.index("--config-file") + 1]
    config_env = args[args.index("--config-env") + 1]
    with open(path, "rb") as f:
        call["overrides"] = tomllib.load(f)[config_env]["deploy"]["parameters"]["parameter_overrides"]
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps(call) + "\n")
if name == "aws" and args[:2] == ["ssm", "get-parameter"]:
    parameter = args[args.index("--name") + 1]
    if parameter.endswith("origin-verify-secret"):
        print("test-origin-secret")
    elif parameter.endswith("api-domain"):
        print("api.example.invalid")
    elif parameter.endswith("cloudfront-web-acl-arn"):
        state = os.environ["WAF_STATE"]
        if state in ("missing", "denied", "denied-with-output"):
            code = "ParameterNotFound" if state == "missing" else "AccessDeniedException"
            if state == "denied-with-output":
                print("(ParameterNotFound)")
            print(f"An error occurred ({code}) when calling GetParameter", file=sys.stderr)
            sys.exit(254)
        if state == "warning":
            print("CLI warning", file=sys.stderr)
        print("example-web-acl-arn" if state in ("present", "warning") else "" if state == "blank" else "None")
    else:
        raise AssertionError(parameter)
elif name == "aws":
    print("https://example.invalid")
"""


@pytest.fixture
def cli(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("aws", "sam", "uv"):
        path = bin_dir / tool
        path.write_text(f"#!{sys.executable}\n" + STUB, encoding="utf-8")
        path.chmod(0o700)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("CALLS", str(calls))
    monkeypatch.setenv("WAF_STATE", "present")
    monkeypatch.delenv("ALLOW_NO_WAF", raising=False)
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    yield calls
    assert not list(tmp_path.glob("service-platform-edge-*"))


def run_script(name, *args):
    return subprocess.run(  # noqa: S603
        [shutil.which("bash"), str(ROOT / "scripts" / name), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


def read_calls(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("env,config_env", [("dev", "default"), ("prod", "production")])
def test_waf_deploy_always_uses_us_east_1(cli, env, config_env):
    result = run_script("deploy-waf.sh", env)
    assert result.returncode == 0, result.stderr
    deploy = next(c for c in read_calls(cli) if c["tool"] == "sam")
    assert deploy["args"][0] == "deploy"
    assert deploy["args"][deploy["args"].index("--region") + 1] == "us-east-1"
    assert deploy["args"][deploy["args"].index("--config-env") + 1] == config_env
    assert pathlib.Path(deploy["cwd"]) == ROOT / "infra/waf"


@pytest.mark.parametrize("env", ["invalid", ""])
def test_waf_deploy_rejects_invalid_environment(cli, env):
    result = run_script("deploy-waf.sh", *([env] if env else []))
    assert result.returncode != 0
    assert not cli.exists()


@pytest.mark.parametrize("env", ["dev", "prod"])
@pytest.mark.parametrize("state", ["present", "warning", "missing", "empty", "blank"])
def test_edge_reads_us_east_1_and_passes_optional_acl(cli, monkeypatch, state, env):
    monkeypatch.setenv("WAF_STATE", state)
    if env == "prod" and state in ("missing", "empty", "blank"):
        monkeypatch.setenv("ALLOW_NO_WAF", "1")
    result = run_script("deploy-edge.sh", env)
    assert result.returncode == 0, result.stderr
    calls = read_calls(cli)
    fetch = next(c for c in calls if c["tool"] == "aws" and any("/waf/cloudfront-web-acl-arn" in a for a in c["args"]))
    assert fetch["args"][fetch["args"].index("--region") + 1] == "us-east-1"
    deploy = next(c for c in calls if c["tool"] == "sam")
    value = "example-web-acl-arn" if state in ("present", "warning") else ""
    assert f"WebAclArn={value}" in shlex.split(deploy["overrides"])
    assert "test-origin-secret" not in result.stdout + result.stderr
    if state in ("missing", "empty", "blank"):
        assert "WAF" in result.stdout and "未登録" in result.stdout
    if state == "warning":
        assert "CLI warning" in result.stderr


@pytest.mark.parametrize("env", ["dev", "prod"])
@pytest.mark.parametrize("allow_no_waf", ["0", "1"])
@pytest.mark.parametrize("state", ["denied", "denied-with-output"])
def test_edge_stops_on_waf_lookup_failure_except_missing(cli, monkeypatch, env, allow_no_waf, state):
    monkeypatch.setenv("WAF_STATE", state)
    monkeypatch.setenv("ALLOW_NO_WAF", allow_no_waf)
    result = run_script("deploy-edge.sh", env)
    assert result.returncode != 0
    assert "AccessDeniedException" in result.stderr
    assert not any(c["tool"] == "sam" for c in read_calls(cli))


def test_validation_uses_us_east_1_for_waf_only(cli):
    result = run_script("validate-templates.sh")
    assert result.returncode == 0, result.stderr
    calls = read_calls(cli)
    sam_calls = [c for c in calls if c["tool"] == "sam"]
    assert any(pathlib.Path(c["cwd"]).name == "waf" for c in sam_calls)
    for call in calls:
        expected = (
            "us-east-1"
            if "/infra/waf" in call["cwd"] or any("/infra/waf/" in a for a in call["args"])
            else "ap-northeast-1"
        )
        args = call["args"]
        option = "--region" if call["tool"] == "sam" else "--regions"
        assert args[args.index(option) + 1] == expected


def test_deploy_all_runs_waf_between_core_and_apps():
    script = (ROOT / "scripts/deploy-all.sh").read_text()
    assert (
        script.index('deploy_stack "core"') < script.index('/scripts/deploy-waf.sh"') < script.index("for app_dir in")
    )


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_edge_quotes_empty_acl_for_the_sam_parameter_parser(cli, monkeypatch, state):
    monkeypatch.setenv("WAF_STATE", state)
    result = run_script("deploy-edge.sh", "dev")
    assert result.returncode == 0, result.stderr
    deploy = next(c for c in read_calls(cli) if c["tool"] == "sam")
    assert 'WebAclArn=""' in deploy["overrides"]


@pytest.mark.parametrize("state", ["missing", "empty", "blank"])
@pytest.mark.parametrize("allow_no_waf", [None, "0", "true"])
def test_edge_requires_waf_in_prod_unless_explicitly_allowed(cli, monkeypatch, state, allow_no_waf):
    monkeypatch.setenv("WAF_STATE", state)
    if allow_no_waf is not None:
        monkeypatch.setenv("ALLOW_NO_WAF", allow_no_waf)
    result = run_script("deploy-edge.sh", "prod")
    assert result.returncode != 0
    assert "WAF" in result.stderr and "ALLOW_NO_WAF=1" in result.stderr
    assert not any(c["tool"] == "sam" for c in read_calls(cli))
