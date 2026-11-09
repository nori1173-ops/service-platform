"""割り当ての期間検査・逆引き・LOGIN の条件付き削除を検査する。"""

import pytest

from service_platform import ledger

UNKNOWN = "01JBZ00000000000000000NONE"


def error(result, status, code):
    assert result[0] == status, result
    assert result[1]["error"]["code"] == code


@pytest.mark.parametrize("target_status", ["active", "suspended"])
def test_assignment_and_reverse_are_atomic_and_visible_in_user_detail(aws, call, period_api, period, target_status):
    target = aws.make_user("target@example.com", status=target_status)
    row = period_api.assign(period, target.user_id)
    pid, aid = period["device_period_id"], row["assignment_id"]
    stored = period_api.row(ledger.dperiod_pk(pid), ledger.assignment_sk(target.user_id, aid))
    reverse = period_api.row(ledger.user_pk(target.user_id), ledger.assign_sk(pid, aid))
    assert stored["valid_to"] is None and stored["role"] == "user"
    assert reverse["valid_from"] == stored["valid_from"]
    assert reverse["org_name"] == period["org_name"]
    detail = call("GET", f"/users/{target.user_id}")[1]["data"]
    assert detail["assignments"][0]["assignment_id"] == aid
    (audit,) = aws.audit_items("assignment.add")
    assert audit["target_user_id"] == target.user_id and audit["assignment_id"] == aid
    assert audit["billing"] is True and "ttl" not in audit


def test_admin_can_assign_self_and_end_then_reassign_with_new_id(aws, call, period_api, period, actor):
    row = period_api.assign(period, actor.user_id)
    period_api.login(period, actor.user_id, row["assignment_id"])
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    pid, aid = period["device_period_id"], row["assignment_id"]
    assert call("POST", f"/device-periods/{pid}/assignments/{aid}/end")[0] == 200
    assert period_api.row(ledger.user_pk(actor.user_id), ledger.login_sk(period["account"])) is None
    reverse = period_api.row(ledger.user_pk(actor.user_id), ledger.assign_sk(pid, aid))
    assert reverse["valid_to"] == period_api.clock[0]
    replacement = period_api.assign(period, actor.user_id, valid_from=period_api.clock[0])
    assert replacement["assignment_id"] != aid
    assert len(call("GET", f"/device-periods/{pid}")[1]["data"]["assignments"]) == 2
    (audit,) = aws.audit_items("assignment.end")
    assert audit["billing"] is True and "ttl" not in audit
    error(call("POST", f"/device-periods/{pid}/assignments/{aid}/end"), 409, "ASSIGNMENT_ENDED")


@pytest.mark.parametrize(
    "fields",
    [
        {"valid_from": "2026-11-04T00:59:59Z"},
        {"valid_from": "2026-11-05T00:00:00Z", "valid_to": "2026-11-05T00:00:00Z"},
        {"valid_to": "2026-11-04T00:00:00Z"},
        {"valid_from": None},
        {"valid_from": "2026-02-30T00:00:00Z"},
        {"valid_from": "2026-11-04"},
        {"valid_from": "2026-11-04T01:00:00"},
        {"valid_to": 1},
    ],
)
def test_assignment_period_validation(call, period, actor, fields):
    payload = {"user_id": actor.user_id, "valid_from": period["started_at"], **fields}
    error(
        call("POST", f"/device-periods/{period['device_period_id']}/assignments", body=payload), 400, "VALIDATION_ERROR"
    )


def test_assignment_requires_a_registered_user(call, period):
    error(
        call(
            "POST",
            f"/device-periods/{period['device_period_id']}/assignments",
            body={"user_id": UNKNOWN, "valid_from": period["started_at"]},
        ),
        404,
        "USER_NOT_FOUND",
    )


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-11-04T01:00:00Z", None),
        ("2026-11-04T01:30:00Z", None),
        ("2026-11-04T01:00:00Z", "2026-11-04T01:30:00Z"),
        ("2026-11-04T01:30:00Z", "2026-11-04T03:00:00Z"),
    ],
)
def test_overlapping_assignments_are_rejected(call, period_api, period, actor, start, end):
    period_api.assign(period, actor.user_id, valid_to="2026-11-04T02:00:00Z")
    error(
        call(
            "POST",
            f"/device-periods/{period['device_period_id']}/assignments",
            body={
                "user_id": actor.user_id,
                "valid_from": start,
                "valid_to": end,
            },
        ),
        409,
        "ASSIGNMENT_OVERLAP",
    )


def test_adjacent_and_different_user_intervals_are_allowed(aws, period_api, period, actor):
    period_api.assign(period, actor.user_id, valid_to="2026-11-04T02:00:00Z")
    period_api.assign(period, actor.user_id, valid_from="2026-11-04T11:00:00+09:00")
    target = aws.make_user("other@example.com")
    period_api.assign(period, target.user_id)


def test_end_keeps_login_for_a_different_assignment(call, period_api, period, actor):
    row = period_api.assign(period, actor.user_id)
    period_api.login(period, actor.user_id, UNKNOWN)
    assert (
        call("POST", f"/device-periods/{period['device_period_id']}/assignments/{row['assignment_id']}/end")[0] == 200
    )
    assert period_api.row(ledger.user_pk(actor.user_id), ledger.login_sk(period["account"]))["assignment_id"] == UNKNOWN


def test_assignments_cannot_be_added_to_an_ended_period(call, period_api, period, actor):
    pid = period["device_period_id"]
    assert call("POST", f"/device-periods/{pid}/end")[0] == 200
    error(
        call(
            "POST",
            f"/device-periods/{pid}/assignments",
            body={"user_id": actor.user_id, "valid_from": period["started_at"]},
        ),
        409,
        "DEVICE_PERIOD_ENDED",
    )


def test_end_assignment_belongs_to_this_period(call, period_api, period, actor):
    row = period_api.assign(period, actor.user_id)
    other = period_api.start(account="87654321")
    error(
        call("POST", f"/device-periods/{other['device_period_id']}/assignments/{row['assignment_id']}/end"),
        404,
        "ASSIGNMENT_NOT_FOUND",
    )


@pytest.mark.parametrize("value", ["2026-11-04T04:00:00+00:99", "2026-11-04T04:00:00+01:60"])
def test_invalid_timezone_offsets_are_rejected(call, period, actor, value):
    error(
        call(
            "POST",
            f"/device-periods/{period['device_period_id']}/assignments",
            body={
                "user_id": actor.user_id,
                "valid_from": value,
            },
        ),
        400,
        "VALIDATION_ERROR",
    )


# =============================================================================
# グループ宛の割り当て (ADR D13)
# =============================================================================


def test_a_group_assignment_and_its_reverse_row(aws, call, group_api, period):
    group = group_api.create("会社A")
    before = period_api_row(aws, period)["version"]

    assigned = group_api.assign(period, group)

    pid, gid, aid = period["device_period_id"], group["group_id"], assigned["assignment_id"]
    assert assigned["target_type"] == "group" and assigned["group_id"] == gid
    assert assigned.get("user_id") is None
    stored = aws.ledger.get_item(Key={"PK": ledger.dperiod_pk(pid), "SK": ledger.group_assignment_sk(gid, aid)})["Item"]
    assert stored["target_type"] == "group" and stored["role"] == "user" and "serial" not in stored
    reverse = aws.ledger.get_item(Key={"PK": ledger.group_pk(gid), "SK": ledger.group_assign_sk(pid, aid)})["Item"]
    assert reverse["account"] == period["account"] and reverse["org_name"] == period["org_name"]
    assert period_api_row(aws, period)["version"] == before + 1
    group_row = aws.ledger.get_item(Key={"PK": ledger.group_pk(gid), "SK": ledger.GROUP_PROFILE_SK})["Item"]
    assert group_row["version"] == 2
    (audit,) = aws.audit_items("assignment.add")
    assert audit["detail"] == {"target_type": "group", "group_id": gid}
    assert "target_user_id" not in audit and audit["assignment_id"] == aid
    (listed,) = call("GET", f"/device-periods/{pid}")[1]["data"]["assignments"]
    assert listed["target_type"] == "group" and listed["group_path_names"] == ["会社A"]


def period_api_row(aws, period):
    key = {"PK": ledger.device_pk(period["account"]), "SK": ledger.dperiod_sk(period["device_period_id"])}
    return aws.ledger.get_item(Key=key, ConsistentRead=True)["Item"]


def test_a_user_assignment_now_records_its_target_type(aws, call, period_api, period, actor):
    assigned = period_api.assign(period, actor.user_id)

    assert assigned["target_type"] == "user"
    (audit,) = aws.audit_items("assignment.add")
    assert audit["detail"] == {"target_type": "user"}


@pytest.mark.parametrize("case", ["group_without_group", "group_with_user", "user_with_group", "unknown_type"])
def test_the_target_fields_must_match_the_target_type(aws, call, group_api, period, actor, case):
    group = group_api.create("会社A")
    body = {
        "group_without_group": {"target_type": "group"},
        "group_with_user": {"target_type": "group", "group_id": group["group_id"], "user_id": actor.user_id},
        "user_with_group": {"target_type": "user", "user_id": actor.user_id, "group_id": group["group_id"]},
        "unknown_type": {"target_type": "team", "user_id": actor.user_id},
    }[case]
    result = call(
        "POST",
        f"/device-periods/{period['device_period_id']}/assignments",
        body={"valid_from": period["started_at"], **body},
    )
    error(result, 400, "VALIDATION_ERROR")
    assert aws.audit_items("assignment.add") == []


def test_overlaps_are_checked_within_the_same_group(call, group_api, period):
    first = group_api.create("会社A")
    second = group_api.create("会社B")
    group_api.assign(period, first)
    group_api.assign(period, second)

    result = call(
        "POST",
        f"/device-periods/{period['device_period_id']}/assignments",
        body={"target_type": "group", "group_id": first["group_id"], "valid_from": "2026-11-10T00:00:00Z"},
    )
    error(result, 409, "ASSIGNMENT_OVERLAP")


def test_a_user_and_a_group_assignment_may_overlap_for_the_same_person(aws, group_api, period_api, period, actor):
    group = group_api.create("会社A")
    group_api.add_member(group, actor.user_id)

    group_api.assign(period, group)
    assert period_api.assign(period, actor.user_id)["target_type"] == "user"


def test_ending_a_group_assignment_keeps_logins_and_closes_the_reverse_row(aws, call, group_api, period_api, period):
    group = group_api.create("会社A")
    member = aws.make_user("member@example.com")
    group_api.add_member(group, member.user_id)
    assigned = group_api.assign(period, group)
    period_api.login(period, member.user_id, assigned["assignment_id"])
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    pid, gid, aid = period["device_period_id"], group["group_id"], assigned["assignment_id"]

    status, body = call("POST", f"/device-periods/{pid}/assignments/{aid}/end")

    assert status == 200, body
    assert body["data"]["valid_to"] == "2026-11-04T02:00:00Z" and body["data"]["target_type"] == "group"
    assert period_api.row(ledger.user_pk(member.user_id), ledger.login_sk(period["account"])) is not None
    reverse = period_api.row(ledger.group_pk(gid), ledger.group_assign_sk(pid, aid))
    assert reverse["valid_to"] == "2026-11-04T02:00:00Z"
    (audit,) = aws.audit_items("assignment.end")
    assert audit["detail"] == {"target_type": "group", "group_id": gid}
    error(call("POST", f"/device-periods/{pid}/assignments/{aid}/end"), 409, "ASSIGNMENT_ENDED")


def test_an_old_assignment_row_without_target_type_can_be_ended(aws, call, period_api, period, actor):
    assigned = period_api.assign(period, actor.user_id)
    pid, aid = period["device_period_id"], assigned["assignment_id"]
    aws.ledger.update_item(
        Key={"PK": ledger.dperiod_pk(pid), "SK": ledger.assignment_sk(actor.user_id, aid)},
        UpdateExpression="REMOVE target_type",
    )
    listed = call("GET", f"/device-periods/{pid}")[1]["data"]["assignments"]
    assert listed[0]["target_type"] == "user"
    period_api.clock[0] = "2026-11-04T02:00:00Z"

    assert call("POST", f"/device-periods/{pid}/assignments/{aid}/end")[0] == 200
