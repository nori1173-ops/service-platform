"""`service_platform.audit` のテスト（計画 2.2、ADR-001 D12）。"""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from service_platform import audit
from service_platform.ids import ULID_PATTERN
from service_platform.ledger import UserContext

REGION = "ap-northeast-1"
AUDIT_TABLE = "service-platform-core-audit-test"
OTHER_TABLE = "service-platform-core-ledger-test"
NOW = datetime(2026, 11, 4, 1, 23, 45, 678000, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
YEAR_SECONDS = 365 * 24 * 60 * 60
SK_PATTERN = re.compile(r"^2026-11-04T01:23:45Z#(?P<ulid>[0-9A-Z]{26})$")

USER = UserContext(
    user_id="01JBZ0000000000000000USER1",
    sub="11111111-2222-3333-4444-210000000001",
    email="admin@example.com",
    status="active",
    kind="employee",
    role="admin",
    admin_state="active",
    mfa_enabled_at=1_762_000_000,
)
HTTP_EVENT = {
    "requestContext": {
        "http": {"method": "POST", "path": "/api/x", "sourceIp": "198.51.100.7", "userAgent": "Mozilla/5.0 test"}
    },
    "headers": {"authorization": "Bearer secret-token"},
    "body": '{"pin": "1234"}',
}


def _create_table(client, name, gsis):
    attributes = ["PK", "SK"] + [f"{gsi}{suffix}" for gsi in gsis for suffix in ("PK", "SK")]
    client.create_table(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attributes],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": gsi,
                "KeySchema": [
                    {"AttributeName": f"{gsi}PK", "KeyType": "HASH"},
                    {"AttributeName": f"{gsi}SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
            for gsi in gsis
        ],
    )


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        _create_table(client, AUDIT_TABLE, ["GSI1", "GSI2"])
        _create_table(client, OTHER_TABLE, ["GSI1"])
        monkeypatch.setenv("AUDIT_TABLE", AUDIT_TABLE)
        audit.reset_clients()
        table = boto3.resource("dynamodb", region_name=REGION).Table(AUDIT_TABLE)
        yield SimpleNamespace(client=client, table=table)
    audit.reset_clients()


def all_rows(aws) -> list[dict]:
    return aws.table.scan()["Items"]


def build(action="user.invite", **overrides) -> dict:
    fields = {"actor": USER, "service": "admin-users", "now": NOW}
    fields.update(overrides)
    return audit.build_item(action, **fields)


# =============================================================================
# 行の組み立て（計画 2.2 の表）
# =============================================================================


def test_item_keys_for_a_user():
    item = build(target_user_id="01JBZ0000000000000000USER2")

    assert item["PK"] == f"ACTOR#{USER.user_id}"
    match = SK_PATTERN.fullmatch(item["SK"])
    assert match and ULID_PATTERN.fullmatch(match["ulid"])
    assert item["GSI2PK"] == "DAY#2026-11-04"
    assert item["GSI2SK"] == item["SK"]
    assert "GSI1PK" not in item
    assert item["occurred_at"] == "2026-11-04T01:23:45Z"
    assert item["user_id"] == USER.user_id
    assert item["email"] == USER.email
    assert item["role"] == USER.role
    assert item["target_user_id"] == "01JBZ0000000000000000USER2"
    assert item["action"] == "user.invite"
    assert item["service"] == "admin-users"
    assert item["result"] == "ok"


def test_sk_ulid_carries_the_occurrence_time():
    """同じ秒の中の順は ULID の時刻 (ミリ秒) で決まる。"""
    first = build(now=NOW)
    second = build(now=NOW.replace(microsecond=999000))

    assert first["SK"] < second["SK"]


def test_device_rows_are_reachable_by_account():
    item = build(
        "device.login",
        service="base",
        result="denied",
        code="PIN_MISMATCH",
        account="acc001",
        device_period_id="D1",
        assignment_id="A1",
    )

    assert item["GSI1PK"] == "DEVICE#acc001"
    assert item["GSI1SK"] == item["SK"]
    assert (item["account"], item["device_period_id"], item["assignment_id"]) == ("acc001", "D1", "A1")
    assert (item["result"], item["code"]) == ("denied", "PIN_MISMATCH")


def test_camera_fields():
    item = build("period.start", service="camera", account="acc001", camera_address="01", period_id="P1", billing=True)

    assert (item["camera_address"], item["period_id"]) == ("01", "P1")


@pytest.mark.parametrize(
    "actor,pk",
    [("script", "ACTOR#script"), ("system:judge-worker", "ACTOR#system:judge-worker")],
)
def test_actors_without_a_person(actor, pk):
    item = build("script.create_admin", actor=actor, service="scripts")

    assert item["PK"] == pk
    assert "user_id" not in item
    assert "email" not in item


@pytest.mark.parametrize("actor", ["", "admin", "system:", "system:a#b", None, 1])
def test_rejects_unknown_actors(actor):
    with pytest.raises(ValueError):
        build(actor=actor)


@pytest.mark.parametrize("service", ["", "admin_users", "notifier"])
def test_rejects_unknown_services(service):
    with pytest.raises(ValueError):
        build(service=service)


@pytest.mark.parametrize("result", ["", "error", "OK"])
def test_rejects_unknown_results(result):
    with pytest.raises(ValueError):
        build(result=result)


@pytest.mark.parametrize("action", ["", "invite", "User.invite", "user.", "user.invite#x"])
def test_rejects_malformed_actions(action):
    with pytest.raises(ValueError):
        build(action)


def test_operation_records_expire_after_365_days():
    item = build()

    assert item["billing"] is False
    assert item["ttl"] == NOW_EPOCH + YEAR_SECONDS


def test_billing_records_never_expire():
    item = build("dperiod.start", billing=True, account="acc001")

    assert item["billing"] is True
    assert "ttl" not in item


def test_request_gives_source_ip_and_user_agent_only():
    item = build(request=HTTP_EVENT)

    assert item["source_ip"] == "198.51.100.7"
    assert item["user_agent"] == "Mozilla/5.0 test"
    text = json.dumps(item, default=str)
    assert "secret-token" not in text
    assert "1234" not in text


def test_secret_fields_in_detail_are_dropped():
    """暗証番号・トークン・一時パスワード・連絡先は監査に入れない（ADR D12）。"""
    item = build(
        detail={
            "method": "phone",
            "pin": "4321",
            "temporary_password": "Tmp-Secret-1",
            "contact": "090-0000-0000",
            "nested": {"access_token": "eyJ-access", "count": 2, "items": [{"refresh_token": "eyJ-refresh"}]},
            "client_secret": "s3cr3t",
        }
    )

    text = json.dumps(item, default=str)
    for value in ("4321", "Tmp-Secret-1", "090-0000-0000", "eyJ-access", "eyJ-refresh", "s3cr3t"):
        assert value not in text
    assert item["detail"]["method"] == "phone"
    assert item["detail"]["nested"]["count"] == 2
    assert item["detail_redacted"] == [
        "client_secret",
        "contact",
        "nested.access_token",
        "nested.items.refresh_token",
        "pin",
        "temporary_password",
    ]


def test_detail_without_secrets_has_no_redaction_marker():
    item = build(detail={"method": "phone", "ratio": 0.5})

    assert "detail_redacted" not in item
    assert item["detail"] == {"method": "phone", "ratio": Decimal("0.5")}


def test_none_fields_are_left_out():
    item = build()

    for name in ("code", "account", "device_period_id", "assignment_id", "camera_address", "period_id", "detail"):
        assert name not in item


# =============================================================================
# 書き込み（ADR D12 の追記だけの仕組み）
# =============================================================================


def test_emit_writes_one_row(aws):
    audit.emit("user.invite", actor=USER, service="admin-users", target_user_id="U2", now=NOW)

    rows = all_rows(aws)
    assert len(rows) == 1
    assert rows[0]["PK"] == f"ACTOR#{USER.user_id}"
    assert rows[0]["ttl"] == NOW_EPOCH + YEAR_SECONDS


def test_emit_writes_distinct_rows_for_the_same_moment(aws):
    audit.emit("user.invite", actor=USER, service="admin-users", now=NOW)
    audit.emit("user.invite", actor=USER, service="admin-users", now=NOW)

    assert len({row["SK"] for row in all_rows(aws)}) == 2


def test_emit_never_overwrites_an_existing_row(aws, monkeypatch, capsys):
    """PutItem は同じキーの行を置き換えられるので、条件付きで書く。"""
    monkeypatch.setattr(audit, "new_ulid", lambda timestamp_ms=None: "01JBZ00000000000000000FIXD")
    audit.emit("user.invite", actor=USER, service="admin-users", now=NOW, detail={"n": 1})
    audit.emit("user.suspend", actor=USER, service="admin-users", now=NOW, detail={"n": 2})

    rows = all_rows(aws)
    assert len(rows) == 1
    assert rows[0]["action"] == "user.invite"


def test_emit_does_not_raise_when_the_write_fails(aws, monkeypatch):
    monkeypatch.setenv("AUDIT_TABLE", "no-such-table")

    assert audit.emit("user.invite", actor=USER, service="admin-users", now=NOW) is None


def test_emit_does_not_raise_on_a_bad_record(aws):
    assert audit.emit("bad action", actor=USER, service="admin-users", now=NOW) is None
    assert all_rows(aws) == []


def test_emit_does_not_raise_without_configuration(monkeypatch):
    monkeypatch.delenv("AUDIT_TABLE", raising=False)
    audit.reset_clients()

    assert audit.emit("user.invite", actor=USER, service="admin-users", now=NOW) is None


def test_emit_failure_log_has_no_record_values(aws, monkeypatch):
    messages = []
    monkeypatch.setattr(audit.logger, "error", lambda msg, details=None: messages.append((msg, details)))
    monkeypatch.setenv("AUDIT_TABLE", "no-such-table")

    audit.emit("user.invite", actor=USER, service="admin-users", now=NOW, detail={"method": "phone-x"})

    assert len(messages) == 1
    text = json.dumps(messages, default=str)
    assert "user.invite" in text
    assert USER.email not in text
    assert "phone-x" not in text


def test_transaction_item_is_conditional(aws):
    put = audit.put_item_for_transaction("script.create_admin", actor="script", service="scripts", now=NOW)

    assert set(put) == {"Put"}
    assert put["Put"]["TableName"] == AUDIT_TABLE
    assert put["Put"]["ConditionExpression"] == "attribute_not_exists(PK)"
    assert put["Put"]["Item"]["PK"] == {"S": "ACTOR#script"}


def test_transaction_item_goes_with_ledger_writes(aws):
    """監査の Put を台帳の書き込みと同じトランザクションに入れられる（計画 2.1）。"""
    aws.client.transact_write_items(
        TransactItems=[
            {"Put": {"TableName": OTHER_TABLE, "Item": {"PK": {"S": "USER#U1"}, "SK": {"S": "PROFILE"}}}},
            audit.put_item_for_transaction(
                "script.create_admin", actor="script", service="scripts", target_user_id="U1", now=NOW
            ),
        ]
    )

    rows = all_rows(aws)
    assert len(rows) == 1
    assert rows[0]["target_user_id"] == "U1"


def test_transaction_item_raises_on_a_bad_record(aws):
    """トランザクションの版は、組み立てに失敗したら投げる (台帳の書き込みを止める)。"""
    with pytest.raises(ValueError):
        audit.put_item_for_transaction("bad", actor="script", service="scripts", now=NOW)


def test_table_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUDIT_TABLE", "service-platform-core-audit-dev")
    assert audit.audit_table_name() == "service-platform-core-audit-dev"

    monkeypatch.delenv("AUDIT_TABLE")
    with pytest.raises(RuntimeError, match="AUDIT_TABLE"):
        audit.audit_table_name()
