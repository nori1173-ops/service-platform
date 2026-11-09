"""`service_platform.ledger` のテスト（計画 2.1、ADR-001 D3 / D4）。"""

from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from service_platform import ledger
from service_platform.auth import ForbiddenError
from service_platform.ledger import UserContext, UserNotRegisteredError, UserSuspendedError

REGION = "ap-northeast-1"
TABLE = "service-platform-core-ledger-test"
USER_ID = "01JBZ0000000000000000USER1"
SUB = "11111111-2222-3333-4444-210000000001"
OLD_SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
EMAIL = "Admin@Example.com"


@pytest.fixture
def aws(monkeypatch):
    """moto の台帳テーブルを作り、環境変数 `LEDGER_TABLE` を差し替える。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        table = dynamodb.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"} for name in ("PK", "SK", "GSI1PK", "GSI1SK")
            ],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        monkeypatch.setenv("LEDGER_TABLE", TABLE)
        ledger.reset_clients()
        yield SimpleNamespace(table=table)
    ledger.reset_clients()


def put_user(aws, *, status="active", sub=SUB, profile_sub=None, revoked=False, **attributes):
    aws.table.put_item(
        Item={
            "PK": ledger.user_pk(USER_ID),
            "SK": ledger.PROFILE_SK,
            "GSI1PK": ledger.USER_GSI1PK,
            "GSI1SK": ledger.user_gsi1sk(EMAIL, USER_ID),
            "user_id": USER_ID,
            "sub": profile_sub or sub,
            "email": EMAIL.lower(),
            "status": status,
            "kind": "employee",
            "role": "admin",
            **attributes,
        }
    )
    mapping = {"PK": ledger.sub_pk(sub), "SK": ledger.SUB_SK, "user_id": USER_ID, "created_at": "2026-11-04T00:00:00Z"}
    if revoked:
        mapping["revoked_at"] = "2026-11-04T01:00:00Z"
    aws.table.put_item(Item=mapping)


# =============================================================================
# キー（計画 2.1 の表）
# =============================================================================


def test_keys_follow_the_data_model():
    assert ledger.user_pk("U1") == "USER#U1"
    assert ledger.PROFILE_SK == "PROFILE"
    assert ledger.sub_pk("s-1") == "SUB#s-1"
    assert ledger.SUB_SK == "USER"
    assert ledger.device_pk("acc1") == "DEVICE#acc1"
    assert ledger.CURRENT_SK == "CURRENT"
    assert ledger.dperiod_sk("D1") == "DPERIOD#D1"
    assert ledger.dperiod_pk("D1") == "DPERIOD#D1"
    assert ledger.assignment_sk("U1", "A1") == "USER#U1#A1"
    assert ledger.assignment_prefix("U1") == "USER#U1#"
    assert ledger.assign_sk("D1", "A1") == "ASSIGN#D1#A1"
    assert ledger.ASSIGN_PREFIX == "ASSIGN#"
    assert ledger.grant_sk("camera", "G1") == "GRANT#camera#G1"
    assert ledger.grant_prefix("camera") == "GRANT#camera#"
    assert ledger.login_sk("acc1") == "LOGIN#acc1"
    assert ledger.PINFAIL_SK == "PINFAIL"
    assert ledger.REFRESH_SK == "REFRESH"


def test_gsi1_keys_follow_the_data_model():
    assert ledger.GSI1 == "GSI1"
    assert ledger.USER_GSI1PK == "USER"
    assert ledger.user_gsi1sk(" Admin@Example.COM ", "U1") == "admin@example.com#U1"
    assert ledger.DEVICE_GSI1PK == "DEVICE"
    assert ledger.device_gsi1sk("acc1") == "acc1"
    assert ledger.DPERIOD_GSI1PK == "DPERIOD"
    assert ledger.dperiod_gsi1sk("acc1", "D1") == "acc1#D1"


def test_group_keys_follow_the_data_model():
    """(計画 4.12 の台帳の行): グループ・所属・グループ宛の割り当て・製造番号の控え・利用者ごとの失敗回数。"""
    assert ledger.group_pk("G1") == "GROUP#G1"
    assert ledger.GROUP_ROOT_PK == "GROUP#ROOT"
    assert ledger.GROUP_PROFILE_SK == "PROFILE"
    assert ledger.GROUP_META_SK == "META"
    assert ledger.child_sk("G2") == "CHILD#G2"
    assert ledger.CHILD_PREFIX == "CHILD#"
    assert ledger.member_sk("G1") == "MEMBER#G1"
    assert ledger.group_member_sk("U1") == "MEMBER#U1"
    assert ledger.MEMBER_PREFIX == "MEMBER#"
    assert ledger.group_assignment_prefix("G1") == "GROUP#G1#"
    assert ledger.GROUP_ASSIGNMENT_PREFIX == "GROUP#"
    assert ledger.group_assignment_sk("G1", "A1") == "GROUP#G1#A1"
    assert ledger.group_assign_sk("D1", "A1") == "ASSIGN#D1#A1"
    assert ledger.serial_sk("D1") == "SERIAL#D1"
    assert ledger.USER_PINFAIL_SK == "PINFAIL"
    assert ledger.GROUP_GSI1PK == "GROUP"
    assert ledger.group_gsi1sk(["G1", "G2"]) == "G1#G2"
    with pytest.raises(ValueError):
        ledger.group_pk("G#1")
    with pytest.raises(ValueError):
        ledger.group_gsi1sk([])


def test_period_access_reads_rows_without_the_attributes_as_everyone():
    """属性の無い既存の行は everyone、世代は `START#{started_at}`、変えた時刻は started_at、変えた人は None。"""
    old = {"started_at": "2026-11-01T00:00:00Z", "started_by": "U1"}
    assert ledger.ACCESS_EVERYONE == "everyone"
    assert ledger.ACCESS_ASSIGNED == "assigned"
    assert ledger.period_access(old) == "everyone"
    assert ledger.period_access_gen(old) == "START#2026-11-01T00:00:00Z"
    assert ledger.access_changed_at(old) == "2026-11-01T00:00:00Z"
    assert ledger.access_changed_by(old) is None
    new = {**old, "access": "assigned", "access_gen": "G1", "access_changed_at": "x", "access_changed_by": "U2"}
    assert ledger.period_access(new) == "assigned"
    assert ledger.period_access_gen(new) == "G1"
    assert ledger.access_changed_at(new) == "x"
    assert ledger.access_changed_by(new) == "U2"


@pytest.mark.parametrize("value", ["", "EVERYONE", "anyone", 1])
def test_period_access_reads_unknown_values_as_assigned(value):
    """属性が無いか everyone のときだけ everyone。知らない値は assigned と読む (fail-closed)。"""
    row = {"started_at": "2026-11-01T00:00:00Z", "access": value}
    assert ledger.period_access(row) == "assigned"
    assert ledger.period_access({"started_at": "2026-11-01T00:00:00Z", "access": "everyone"}) == "everyone"


def test_normalize_email():
    assert ledger.normalize_email("  A.B@Example.COM ") == "a.b@example.com"


@pytest.mark.parametrize("value", ["", "a#b", None, 1])
def test_key_parts_must_be_plain_strings(value):
    """空や `#` を含む値でキーを作ると、別の種類の行と紛れる。"""
    with pytest.raises(ValueError):
        ledger.user_pk(value)
    with pytest.raises(ValueError):
        ledger.assignment_sk("U1", value)


def test_table_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LEDGER_TABLE", "service-platform-core-ledger-dev")
    assert ledger.ledger_table_name() == "service-platform-core-ledger-dev"

    monkeypatch.delenv("LEDGER_TABLE")
    with pytest.raises(RuntimeError, match="LEDGER_TABLE"):
        ledger.ledger_table_name()


# =============================================================================
# 要求ごとの利用者の確定（計画 2.1 のアクセスのしかた、ADR D3 / D9）
# =============================================================================


def test_resolve_user_returns_the_context(aws):
    put_user(aws, admin_state="active", mfa_enabled_at=1_762_000_000)

    user = ledger.resolve_user(SUB)

    assert user == UserContext(
        user_id=USER_ID,
        sub=SUB,
        email=EMAIL.lower(),
        status="active",
        kind="employee",
        role="admin",
        admin_state="active",
        mfa_enabled_at=1_762_000_000,
    )
    assert isinstance(user.mfa_enabled_at, int)


def test_resolve_user_rejects_an_unknown_sub(aws):
    with pytest.raises(UserNotRegisteredError) as excinfo:
        ledger.resolve_user(SUB)
    assert excinfo.value.code == "USER_NOT_REGISTERED"


def test_resolve_user_rejects_a_revoked_sub(aws):
    """再招待で古くなった sub の行は消さずに無効の印を付ける。引き当てでは引けない扱い（判断 16）。"""
    put_user(aws, sub=OLD_SUB, profile_sub=SUB, revoked=True)

    with pytest.raises(UserNotRegisteredError):
        ledger.resolve_user(OLD_SUB)


def test_resolve_user_rejects_a_revoked_sub_even_when_suspension_is_allowed(aws):
    """例外 1・2 (停止中でも呼べる API) も無効の印の付いた sub は通さない（ADR D9）。"""
    put_user(aws, sub=OLD_SUB, profile_sub=SUB, revoked=True, status="suspended")

    with pytest.raises(UserNotRegisteredError):
        ledger.resolve_user(OLD_SUB, allow_suspended=True)


def test_resolve_user_rejects_a_sub_that_is_not_the_current_one(aws):
    """引き当ての行に印が無くても、利用者の行の今の sub と違えば引かない（書き込みの途中の状態など）。"""
    put_user(aws, sub=OLD_SUB, profile_sub=SUB)

    with pytest.raises(UserNotRegisteredError):
        ledger.resolve_user(OLD_SUB)


def test_resolve_user_rejects_a_mapping_without_profile(aws):
    aws.table.put_item(Item={"PK": ledger.sub_pk(SUB), "SK": ledger.SUB_SK, "user_id": USER_ID})

    with pytest.raises(UserNotRegisteredError):
        ledger.resolve_user(SUB)


def test_resolve_user_rejects_a_suspended_user(aws):
    put_user(aws, status="suspended")

    with pytest.raises(UserSuspendedError) as excinfo:
        ledger.resolve_user(SUB)
    assert excinfo.value.code == "USER_SUSPENDED"


def test_resolve_user_allows_a_suspended_user_when_asked(aws):
    put_user(aws, status="suspended", admin_state="pending")

    user = ledger.resolve_user(SUB, allow_suspended=True)

    assert user.status == "suspended"
    assert user.is_suspended
    assert user.admin_state == "pending"
    assert user.mfa_enabled_at is None


@pytest.mark.parametrize("status", ["", "deleted", None])
def test_resolve_user_rejects_unknown_statuses(aws, status):
    """状態が active か suspended のどちらでもなければ通さない (fail-closed)。"""
    put_user(aws, status=status)

    with pytest.raises(ForbiddenError):
        ledger.resolve_user(SUB, allow_suspended=True)


def test_ledger_errors_are_403(aws):
    assert issubclass(UserNotRegisteredError, ForbiddenError)
    assert issubclass(UserSuspendedError, ForbiddenError)


class RecordingTable:
    """台帳の読み取りの引数を記録する。"""

    def __init__(self, table):
        self._table = table
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self._table.get_item(**kwargs)

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self._table.query(**kwargs)


def test_authorization_reads_are_strongly_consistent_and_skip_the_gsi(aws, monkeypatch):
    """認可の判断に使う読み取りは強い整合で、GSI を使わない（計画 2.1）。"""
    put_user(aws)
    recorder = RecordingTable(aws.table)
    monkeypatch.setattr(ledger, "_table", lambda: recorder)

    ledger.resolve_user(SUB)
    ledger.get_profile(USER_ID)
    ledger.list_user_assignments(USER_ID)

    assert recorder.calls
    for _, kwargs in recorder.calls:
        assert kwargs.get("ConsistentRead") is True
        assert "IndexName" not in kwargs


def test_get_profile(aws):
    put_user(aws)

    assert ledger.get_profile(USER_ID)["email"] == EMAIL.lower()
    assert ledger.get_profile("01JBZ0000000000000000NOONE") is None


def test_list_user_assignments_returns_only_the_reverse_rows(aws):
    put_user(aws)
    for index in range(3):
        aws.table.put_item(
            Item={
                "PK": ledger.user_pk(USER_ID),
                "SK": ledger.assign_sk(f"D{index}", f"A{index}"),
                "account": f"acc{index}",
                "device_period_id": f"D{index}",
                "assignment_id": f"A{index}",
            }
        )
    aws.table.put_item(Item={"PK": ledger.user_pk(USER_ID), "SK": ledger.login_sk("acc0")})
    aws.table.put_item(Item={"PK": ledger.user_pk("OTHER"), "SK": ledger.assign_sk("D9", "A9")})

    rows = ledger.list_user_assignments(USER_ID)

    assert [row["assignment_id"] for row in rows] == ["A0", "A1", "A2"]


def test_list_user_assignments_follows_pagination(aws, monkeypatch):
    pages = [
        {"Items": [{"assignment_id": "A0"}], "LastEvaluatedKey": {"PK": "x", "SK": "y"}},
        {"Items": [{"assignment_id": "A1"}]},
    ]
    calls = []

    class PagedTable:
        def query(self, **kwargs):
            calls.append(kwargs)
            return pages[len(calls) - 1]

    monkeypatch.setattr(ledger, "_table", lambda: PagedTable())

    assert [row["assignment_id"] for row in ledger.list_user_assignments(USER_ID)] == ["A0", "A1"]
    assert calls[1]["ExclusiveStartKey"] == {"PK": "x", "SK": "y"}


def test_resolve_user_carries_the_cognito_username(aws):
    """admin の守りは Cognito の Username を台帳から引く（計画 4.2。Filter で sub から引かない）。"""
    put_user(aws, cognito_username="cognito-user-1")

    assert ledger.resolve_user(SUB).cognito_username == "cognito-user-1"


def test_admin_set_key():
    """有効な admin の user_id の集合の行 (最後の admin の保護を書き込みと一体にする)。人数の判定に GSI を使わない。"""
    assert ledger.ADMIN_SET_PK == "ADMIN#SET"
    assert ledger.ADMIN_SET_SK == "ACTIVE"
    assert ledger.ADMIN_MEMBERS == "members"
