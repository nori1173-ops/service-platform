"""`service_platform.access` の根拠 (ADR D13 / D14、計画 4.12 の shared のテスト 2〜17)。

条件 (2) は「利用者が P を扱う根拠がある」: (a) 本人宛の割り当て、(b) グループ宛の割り当て (本人の所属の経路に
そのグループがある)、(c) P の access が everyone (属性が無いときを含む)。条件 (3) はログイン記録の根拠
(種類・ID・世代) が今の根拠の集合に含まれるかで確かめ、時刻 (秒) は比べない。
"""

from datetime import UTC, datetime

import pytest

from service_platform import access, ledger
from service_platform.auth import ForbiddenError

ACCOUNT = "12345678"
USER_ID = "01JBZ0000000000000000USER1"
PERIOD_ID = "01JBZ00000000000000PERIOD"
OLD_PERIOD_ID = "01JBZ0000000000000OLDPERD"
ASSIGNMENT_ID = "01JBZ0000000000000ASSIGN1"
NEW_ASSIGNMENT_ID = "01JBZ0000000000000ASSIGN2"
GROUP_ASSIGNMENT_ID = "01JBZ000000000000GASSIGN1"
NEW_GROUP_ASSIGNMENT_ID = "01JBZ000000000000GASSIGN2"
ROOT = "01JBZ0000000000000GROUPRT"
CHILD = "01JBZ0000000000000GROUPCH"
GRAND = "01JBZ0000000000000GROUPGC"
OTHER = "01JBZ0000000000000GROUPOT"
SIBLING = "01JBZ0000000000000GROUPSB"
M_CHILD = "01JBZ000000000000MEMBERCH"
M_GRAND = "01JBZ000000000000MEMBERGC"
M_SIBLING = "01JBZ000000000000MEMBERSB"
M_AGAIN = "01JBZ000000000000MEMBERAG"
GEN_1 = "01JBZ000000000000000GEN01"
GEN_2 = "01JBZ000000000000000GEN02"

NOW = datetime(2026, 11, 4, 3, 0, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
STARTED = "2026-11-01T00:00:00Z"
EARLIER = "2026-11-02T00:00:00Z"
NOW_ISO = "2026-11-04T03:00:00Z"

PATHS = {ROOT: [ROOT], CHILD: [ROOT, CHILD], GRAND: [ROOT, CHILD, GRAND], OTHER: [OTHER], SIBLING: [ROOT, SIBLING]}


@pytest.fixture
def table(ledger_table):
    return ledger_table


def put_period(table, *, period_id=PERIOD_ID, status="active", access_mode=None, access_gen=None, current=True):
    item = {
        "PK": ledger.device_pk(ACCOUNT),
        "SK": ledger.dperiod_sk(period_id),
        "device_period_id": period_id,
        "account": ACCOUNT,
        "org_name": "組織A",
        "status": status,
        "started_at": STARTED,
        "version": 1,
    }
    if access_mode is not None:
        item["access"] = access_mode
    if access_gen is not None:
        item["access_gen"] = access_gen
    table.put_item(Item=item)
    if current:
        table.put_item(
            Item={
                "PK": ledger.device_pk(ACCOUNT),
                "SK": ledger.CURRENT_SK,
                "device_period_id": period_id,
                "org_name": "組織A",
                "started_at": STARTED,
            }
        )


def set_access(table, access_mode, access_gen=None):
    """access の切り替え (admin-users の set_access と同じく、everyone にするときは世代を替える)。"""
    names = {"#a": "access", "#v": "version"}
    values = {":a": access_mode, ":one": 1}
    expression = "SET #a = :a, #v = #v + :one"
    if access_gen is not None:
        names["#g"] = "access_gen"
        values[":g"] = access_gen
        expression += ", #g = :g"
    table.update_item(
        Key={"PK": ledger.device_pk(ACCOUNT), "SK": ledger.dperiod_sk(PERIOD_ID)},
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def put_assignment(table, *, assignment_id=ASSIGNMENT_ID, period_id=PERIOD_ID, valid_from=STARTED, valid_to=None):
    table.put_item(
        Item={
            "PK": ledger.dperiod_pk(period_id),
            "SK": ledger.assignment_sk(USER_ID, assignment_id),
            "assignment_id": assignment_id,
            "device_period_id": period_id,
            "account": ACCOUNT,
            "user_id": USER_ID,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "role": "user",
            "serial": "87654321",
        }
    )


def put_group_assignment(
    table, group_id, *, assignment_id=GROUP_ASSIGNMENT_ID, period_id=PERIOD_ID, valid_from=STARTED, valid_to=None
):
    table.put_item(
        Item={
            "PK": ledger.dperiod_pk(period_id),
            "SK": ledger.group_assignment_sk(group_id, assignment_id),
            "assignment_id": assignment_id,
            "device_period_id": period_id,
            "account": ACCOUNT,
            "target_type": "group",
            "group_id": group_id,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "role": "user",
        }
    )


def put_membership(table, group_id, membership_id):
    table.put_item(
        Item={
            "PK": ledger.user_pk(USER_ID),
            "SK": ledger.member_sk(group_id),
            "membership_id": membership_id,
            "group_id": group_id,
            "path": PATHS[group_id],
            "added_at": NOW_ISO,
        }
    )


def remove_membership(table, group_id):
    table.delete_item(Key={"PK": ledger.user_pk(USER_ID), "SK": ledger.member_sk(group_id)})


def put_login(table, *, period_id=PERIOD_ID, expires_at=NOW_EPOCH + 3600, **fields):
    table.put_item(
        Item={
            "PK": ledger.user_pk(USER_ID),
            "SK": ledger.login_sk(ACCOUNT),
            "device_period_id": period_id,
            "logged_in_at": NOW_ISO,
            "expires_at": expires_at,
            "method": "previous",
            "summary": {"location_name": "現場の名前", "observation": {}, "devices": []},
            **fields,
        }
    )


def user_login(table, assignment_id=ASSIGNMENT_ID, **fields):
    put_login(table, basis="user", assignment_id=assignment_id, **fields)


def group_login(table, membership_ids, *, group_id=ROOT, assignment_id=GROUP_ASSIGNMENT_ID, **fields):
    put_login(
        table, basis="group", assignment_id=assignment_id, group_id=group_id, membership_ids=membership_ids, **fields
    )


def everyone_login(table, access_gen, **fields):
    put_login(table, basis="everyone", access_gen=access_gen, **fields)


def assert_denied(error_type, code, func, *args, **kwargs):
    with pytest.raises(error_type) as info:
        func(*args, **kwargs)
    assert isinstance(info.value, ForbiddenError)
    assert info.value.code == code


def not_assigned():
    assert_denied(access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_basis, USER_ID, ACCOUNT, now=NOW)


def login_required():
    assert_denied(
        access.DeviceLoginRequiredError, "DEVICE_LOGIN_REQUIRED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


def device():
    return access.require_device(USER_ID, ACCOUNT, now=NOW)


# =============================================================================
# 条件 (2): 根拠 (a)(b)(c)
# =============================================================================


def test_2_a_period_without_access_lets_a_user_without_an_assignment_in_as_everyone(table):
    put_period(table)

    found = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert found.access == "everyone"
    assert found.basis == access.Basis(kind="everyone", access_gen=f"START#{STARTED}")
    assert found.bases == (found.basis,)
    assert found.assignment_id is None
    assert found.valid_from is None and found.valid_to is None
    assert found.device_period_id == PERIOD_ID
    assert found.period_version == 1


def test_2_an_everyone_period_uses_its_access_gen(table):
    put_period(table, access_mode="everyone", access_gen=GEN_1)

    assert access.require_basis(USER_ID, ACCOUNT, now=NOW).basis.access_gen == GEN_1


def test_3_an_assigned_period_rejects_a_user_without_an_assignment(table):
    put_period(table, access_mode="assigned")

    not_assigned()


def test_3_an_assigned_period_still_accepts_the_users_own_assignment(table):
    put_period(table, access_mode="assigned")
    put_assignment(table)

    found = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert found.access == "assigned"
    assert found.basis.kind == "user"
    assert found.basis.serial == "87654321"
    assert found.serial == "87654321"
    assert found.assignment_id == ASSIGNMENT_ID
    assert found.valid_from == STARTED


def test_4_a_member_of_a_child_group_is_covered_by_an_assignment_to_the_root(table):
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)

    found = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert found.basis == access.Basis(
        kind="group",
        assignment_id=GROUP_ASSIGNMENT_ID,
        group_id=ROOT,
        membership_ids=(M_CHILD,),
        valid_from=STARTED,
        valid_to=None,
    )
    assert found.assignment_id == GROUP_ASSIGNMENT_ID
    assert found.serial is None


@pytest.mark.parametrize("valid_to", [EARLIER, NOW_ISO])
def test_5_another_branch_or_an_ended_group_assignment_does_not_count(table, valid_to):
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, OTHER)
    put_group_assignment(table, ROOT, assignment_id=NEW_GROUP_ASSIGNMENT_ID, valid_to=valid_to)

    not_assigned()


def test_5_an_assignment_to_a_lower_group_does_not_cover_a_member_of_the_parent(table):
    put_period(table, access_mode="assigned")
    put_membership(table, ROOT, M_CHILD)
    put_group_assignment(table, CHILD)

    not_assigned()


@pytest.mark.parametrize(
    ("group_id", "expected"),
    [(ROOT, (M_CHILD, M_GRAND)), (CHILD, (M_CHILD, M_GRAND)), (GRAND, (M_GRAND,))],
)
def test_6_every_membership_with_the_group_on_its_path_is_collected(table, group_id, expected):
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_membership(table, GRAND, M_GRAND)
    put_group_assignment(table, group_id)

    assert access.require_basis(USER_ID, ACCOUNT, now=NOW).basis.membership_ids == expected


def test_13_the_primary_basis_is_user_then_group_then_everyone_and_the_newest_id(table):
    put_period(table)
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    put_group_assignment(table, CHILD, assignment_id=NEW_GROUP_ASSIGNMENT_ID)
    put_assignment(table)
    put_assignment(table, assignment_id=NEW_ASSIGNMENT_ID, valid_from=EARLIER)

    found = access.require_basis(USER_ID, ACCOUNT, now=NOW)

    assert [(basis.kind, basis.assignment_id) for basis in found.bases] == [
        ("user", NEW_ASSIGNMENT_ID),
        ("user", ASSIGNMENT_ID),
        ("group", NEW_GROUP_ASSIGNMENT_ID),
        ("group", GROUP_ASSIGNMENT_ID),
        ("everyone", None),
    ]
    assert found.basis == found.bases[0]
    assert found.assignment_id == NEW_ASSIGNMENT_ID


def test_13_require_device_returns_the_basis_of_the_login_record(table):
    put_period(table)
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    put_assignment(table)
    group_login(table, [M_CHILD])

    found = device()

    assert found.basis.kind == "group"
    assert found.assignment_id == GROUP_ASSIGNMENT_ID
    assert found.login["basis"] == "group"


def test_12_a_group_assignment_of_the_previous_period_falls_at_condition_2(table):
    put_period(table, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT, period_id=OLD_PERIOD_ID)

    not_assigned()


def test_12_a_group_login_of_the_previous_period_falls_at_condition_3(table):
    put_period(table, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_period(table)
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    group_login(table, [M_CHILD], period_id=OLD_PERIOD_ID)

    login_required()


def test_12_an_everyone_login_of_the_previous_period_falls_at_condition_3(table):
    put_period(table, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_period(table)
    everyone_login(table, f"START#{STARTED}", period_id=OLD_PERIOD_ID)

    login_required()


# =============================================================================
# 条件 (3): 根拠の ID (世代) の照合
# =============================================================================


def test_an_everyone_login_passes_with_the_same_generation(table):
    put_period(table, access_mode="everyone", access_gen=GEN_1)
    everyone_login(table, GEN_1)

    found = device()

    assert found.basis.kind == "everyone"
    assert found.assignment_id is None
    assert found.login["access_gen"] == GEN_1


def test_an_expired_everyone_login_is_not_used(table):
    put_period(table, access_mode="everyone", access_gen=GEN_1)
    everyone_login(table, GEN_1, expires_at=NOW_EPOCH)

    login_required()


def test_7_switching_to_assigned_drops_the_everyone_login_but_not_the_assignment_login(table):
    put_period(table, access_mode="everyone", access_gen=GEN_1)
    everyone_login(table, GEN_1)
    set_access(table, "assigned")

    not_assigned()

    put_assignment(table)
    login_required()

    user_login(table)
    assert device().basis.kind == "user"


def test_8_everyone_assigned_everyone_changes_the_generation(table):
    put_period(table, access_mode="everyone", access_gen=GEN_1)
    everyone_login(table, GEN_1)
    set_access(table, "assigned")
    set_access(table, "everyone", GEN_2)

    login_required()

    everyone_login(table, GEN_2)
    assert device().basis.access_gen == GEN_2


def test_9_removing_and_adding_the_membership_again_changes_the_membership_id(table):
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    group_login(table, [M_CHILD])
    assert device().basis.kind == "group"

    remove_membership(table, CHILD)
    put_membership(table, CHILD, M_AGAIN)

    login_required()


def test_10_an_everyone_login_survives_a_new_assignment(table):
    put_period(table, access_mode="everyone", access_gen=GEN_1)
    everyone_login(table, GEN_1)
    put_assignment(table)

    found = device()

    assert found.basis.kind == "everyone"
    assert found.assignment_id is None
    assert found.bases[0].kind == "user"


def test_11_an_old_login_without_a_basis_is_matched_by_the_assignment_id_only(table):
    put_period(table, access_mode="assigned")
    put_assignment(table)
    put_login(table, assignment_id=ASSIGNMENT_ID, assignment_valid_from="2026-12-31T00:00:00Z")

    found = device()

    assert found.basis.kind == "user"
    assert found.assignment_id == ASSIGNMENT_ID


def test_11_an_old_login_with_another_assignment_id_falls(table):
    put_period(table)
    put_assignment(table, assignment_id=NEW_ASSIGNMENT_ID)
    put_login(table, assignment_id=ASSIGNMENT_ID)

    login_required()


def test_17_a_group_login_needs_a_membership_id_that_still_makes_the_basis(table):
    put_period(table)
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    group_login(table, [M_GRAND, M_SIBLING])

    login_required()


def test_a_group_login_needs_the_same_assignment_id(table):
    put_period(table)
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT, assignment_id=NEW_GROUP_ASSIGNMENT_ID)
    group_login(table, [M_CHILD], assignment_id=GROUP_ASSIGNMENT_ID)

    login_required()


def test_a_user_login_is_not_matched_by_a_group_basis_with_the_same_id(table):
    """種類が違えば同じ assignment_id でも含まれない。"""
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT, assignment_id=ASSIGNMENT_ID)
    user_login(table)

    login_required()


@pytest.mark.parametrize("kind", ["user", "group", "everyone"])
def test_14_a_revoke_and_regrant_in_the_same_second_drops_the_old_login(table, kind):
    """時刻を 1 つの秒に止める。前の記録は落ち、新しい根拠で入り直せば通る。"""
    if kind == "user":
        put_period(table, access_mode="assigned")
        put_assignment(table, valid_to=NOW_ISO)
        put_assignment(table, assignment_id=NEW_ASSIGNMENT_ID, valid_from=NOW_ISO)
        user_login(table)
        login_required()
        user_login(table, NEW_ASSIGNMENT_ID)
    elif kind == "group":
        put_period(table, access_mode="assigned")
        put_group_assignment(table, ROOT)
        put_membership(table, CHILD, M_CHILD)
        group_login(table, [M_CHILD])
        remove_membership(table, CHILD)
        put_membership(table, CHILD, M_AGAIN)
        login_required()
        group_login(table, [M_AGAIN])
    else:
        put_period(table, access_mode="everyone", access_gen=GEN_1)
        everyone_login(table, GEN_1)
        set_access(table, "assigned")
        set_access(table, "everyone", GEN_2)
        login_required()
        everyone_login(table, GEN_2)

    assert device().device_period_id == PERIOD_ID


def test_15_a_group_login_continues_while_one_of_its_memberships_remains(table):
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_membership(table, GRAND, M_GRAND)
    put_group_assignment(table, ROOT)
    group_login(table, [M_CHILD, M_GRAND])

    remove_membership(table, CHILD)
    assert device().basis.membership_ids == (M_GRAND,)

    remove_membership(table, GRAND)
    assert_denied(
        access.DeviceNotAssignedError, "DEVICE_NOT_ASSIGNED", access.require_device, USER_ID, ACCOUNT, now=NOW
    )


def test_15_a_membership_added_after_the_login_does_not_carry_it(table):
    put_period(table, access_mode="assigned")
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    group_login(table, [M_CHILD])
    put_membership(table, SIBLING, M_SIBLING)
    remove_membership(table, CHILD)

    login_required()


def test_16_a_period_without_access_gen_accepts_the_start_generation(table):
    put_period(table)
    everyone_login(table, f"START#{STARTED}")

    assert device().basis.access_gen == f"START#{STARTED}"

    set_access(table, "assigned")
    set_access(table, "everyone", GEN_1)
    login_required()


# =============================================================================
# 読み方 (認可の読み取りは強い整合で、GSI を使わない)
# =============================================================================


class _Recorder:
    def __init__(self, table):
        self._table = table
        self.calls: list[dict] = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return self._table.get_item(**kwargs)

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self._table.query(**kwargs)


def test_the_bases_are_read_strongly_consistent_without_the_index(table, monkeypatch):
    put_period(table)
    put_membership(table, CHILD, M_CHILD)
    put_group_assignment(table, ROOT)
    group_login(table, [M_CHILD])
    recorder = _Recorder(table)
    monkeypatch.setattr(ledger, "_table", lambda: recorder)

    device()

    assert len(recorder.calls) >= 5
    for kwargs in recorder.calls:
        assert kwargs.get("ConsistentRead") is True
        assert "IndexName" not in kwargs


def test_without_memberships_the_group_assignments_are_not_queried(table, monkeypatch):
    put_period(table)
    recorder = _Recorder(table)
    monkeypatch.setattr(ledger, "_table", lambda: recorder)

    access.require_basis(USER_ID, ACCOUNT, now=NOW)

    queries = [kwargs for kwargs in recorder.calls if "KeyConditionExpression" in kwargs]
    assert len(queries) == 2
