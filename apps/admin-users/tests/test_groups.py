"""グループの API (ADR D13、admin-users 設計書の「グループと access」)。

木は 3 段まで。名前の重なりは同じ親の下 (根どうしを含む) の稼働中のグループで見る。付け替えは無い。
終えられるのは、稼働中の子・所属・終わっていない割り当てが無いときだけ。行は消さない。
"""

import logging

import pytest

from service_platform import ledger

UNKNOWN = "01JBZ00000000000000000NONE"


def error(result, status, code):
    assert result[0] == status, result
    assert result[1]["error"]["code"] == code


def row(aws, pk, sk):
    return aws.ledger.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")


GROUP_ROUTES = [
    ("GET", "/groups"),
    ("POST", "/groups"),
    ("GET", f"/groups/{UNKNOWN}"),
    ("PUT", f"/groups/{UNKNOWN}"),
    ("POST", f"/groups/{UNKNOWN}/end"),
    ("POST", f"/groups/{UNKNOWN}/members"),
    ("POST", f"/groups/{UNKNOWN}/members/{UNKNOWN}/remove"),
    ("PUT", f"/device-periods/{UNKNOWN}/access"),
]


@pytest.mark.parametrize("method,path", GROUP_ROUTES)
@pytest.mark.parametrize("state,code", [("user", "ADMIN_NOT_ACTIVE"), ("stale", "ADMIN_SESSION_STALE")])
def test_every_new_route_requires_an_active_admin(aws, call, actor, method, path, state, code):
    kwargs = {}
    caller = actor
    if state == "user":
        caller = aws.make_user("user@example.com")
    else:
        kwargs["auth_time"] = aws.mfa_enabled_at
    error(call(method, path, caller=caller, body={}, **kwargs), 403, code)
    assert aws.dynamodb.calls == []


# =============================================================================
# 作成と経路
# =============================================================================


def test_root_child_and_grandchild_carry_the_path(aws, group_api, actor):
    root = group_api.create("会社A", note="控え")
    child = group_api.create("部署B", parent=root)
    grand = group_api.create("課C", parent=child)

    assert root["parent_group_id"] is None and root["depth"] == 1 and root["path"] == [root["group_id"]]
    assert child["parent_group_id"] == root["group_id"] and child["depth"] == 2
    assert grand["path"] == [root["group_id"], child["group_id"], grand["group_id"]]
    assert grand["depth"] == 3
    assert grand["path_names"] == ["会社A", "部署B", "課C"]
    assert grand["status"] == "active" and grand["created_by"] == actor.user_id
    profile = row(aws, ledger.group_pk(grand["group_id"]), ledger.GROUP_PROFILE_SK)
    assert profile["GSI1PK"] == ledger.GROUP_GSI1PK
    assert profile["GSI1SK"] == "#".join(grand["path"])
    assert profile["version"] == 1 and profile["last_op_id"]
    child_row = row(aws, ledger.group_pk(child["group_id"]), ledger.child_sk(grand["group_id"]))
    assert child_row["name"] == "課C" and child_row["status"] == "active"
    root_row = row(aws, ledger.GROUP_ROOT_PK, ledger.child_sk(root["group_id"]))
    assert root_row["name"] == "会社A"
    assert row(aws, ledger.GROUP_ROOT_PK, ledger.GROUP_META_SK)["version"] == 1
    assert row(aws, ledger.group_pk(child["group_id"]), ledger.GROUP_PROFILE_SK)["version"] == 2
    created = aws.audit_items("group.create")
    assert [item["detail"]["depth"] for item in created] == [1, 2, 3]
    assert created[2]["detail"] == {
        "group_id": grand["group_id"],
        "parent_group_id": child["group_id"],
        "depth": 3,
    }
    assert created[0]["billing"] is False and "ttl" in created[0]


def test_a_fourth_level_is_409(aws, call, group_api):
    root = group_api.create("会社A")
    child = group_api.create("部署B", parent=root)
    grand = group_api.create("課C", parent=child)
    before = len(aws.audit_items())

    result = call("POST", "/groups", body={"name": "係D", "parent_group_id": grand["group_id"]})

    error(result, 409, "GROUP_DEPTH_EXCEEDED")
    assert len(aws.audit_items()) == before


def test_the_same_name_under_the_same_parent_is_409_including_roots(call, group_api):
    root = group_api.create("会社A")
    group_api.create("部署B", parent=root)

    error(call("POST", "/groups", body={"name": "会社A"}), 409, "GROUP_NAME_TAKEN")
    error(
        call("POST", "/groups", body={"name": " 部署B ", "parent_group_id": root["group_id"]}), 409, "GROUP_NAME_TAKEN"
    )


def test_the_same_name_is_allowed_under_another_parent_or_after_the_old_one_ended(call, group_api):
    first = group_api.create("会社A")
    second = group_api.create("会社B")
    group_api.create("部署", parent=first)
    group_api.create("部署", parent=second)
    assert call("POST", f"/groups/{first['group_id']}/end")[0] == 409

    lone = group_api.create("一時の名前")
    assert call("POST", f"/groups/{lone['group_id']}/end")[0] == 200
    assert group_api.create("一時の名前")["group_id"] != lone["group_id"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": " "},
        {"name": "a" * 101},
        {"name": 1},
        {"name": "会社", "note": "a" * 501},
        {"name": "会社", "parent_group_id": "x"},
        {"name": "会社", "parent_group_id": 1},
        {"name": "会社", "level_name": "会社"},
    ],
)
def test_create_validates_the_body(aws, call, body):
    result = call("POST", "/groups", body=body)
    if body.get("parent_group_id") == "x":
        error(result, 404, "GROUP_NOT_FOUND")
    else:
        error(result, 400, "VALIDATION_ERROR")
    assert aws.audit_items("group.create") == []


def test_create_under_a_missing_or_ended_parent(call, group_api):
    error(call("POST", "/groups", body={"name": "x", "parent_group_id": UNKNOWN}), 404, "GROUP_NOT_FOUND")
    parent = group_api.create("会社A")
    assert call("POST", f"/groups/{parent['group_id']}/end")[0] == 200
    error(call("POST", "/groups", body={"name": "x", "parent_group_id": parent["group_id"]}), 409, "GROUP_ENDED")


def test_a_create_after_another_create_under_the_same_parent_was_read_is_409(aws, call, group_api, monkeypatch):
    """名前の重なりは CHILD の控えで確かめ、親の version で競合を落とす。"""
    from admin_users import ledger_writes

    parent = group_api.create("会社A")
    real_write = ledger_writes.write
    raced = []

    def bump_parent(items, **kwargs):
        if not raced:
            raced.append(True)
            aws.ledger.update_item(
                Key={"PK": ledger.group_pk(parent["group_id"]), "SK": ledger.GROUP_PROFILE_SK},
                UpdateExpression="SET version = version + :one",
                ExpressionAttributeValues={":one": 1},
            )
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", bump_parent)
    error(call("POST", "/groups", body={"name": "部署B", "parent_group_id": parent["group_id"]}), 409, "CONFLICT")


# =============================================================================
# 一覧と詳細
# =============================================================================


def test_list_is_in_tree_order_and_filters_by_status(call, group_api):
    root_b = group_api.create("会社B")
    root_a = group_api.create("会社A")
    child = group_api.create("部署", parent=root_b)
    ended = group_api.create("終える")
    assert call("POST", f"/groups/{ended['group_id']}/end")[0] == 200

    active = call("GET", "/groups")[1]["data"]["groups"]
    everything = call("GET", "/groups?status=all")[1]["data"]["groups"]

    assert [group["group_id"] for group in active] == [root_b["group_id"], child["group_id"], root_a["group_id"]]
    assert {group["group_id"] for group in everything} == {
        root_b["group_id"],
        child["group_id"],
        root_a["group_id"],
        ended["group_id"],
    }
    assert {group["status"] for group in everything} == {"active", "ended"}
    error(call("GET", "/groups?status=bad"), 400, "VALIDATION_ERROR")


def test_detail_lists_path_children_members_and_assignments(aws, call, group_api, period_api, period):
    root = group_api.create("会社A")
    child = group_api.create("部署B", parent=root)
    member = aws.make_user("member@example.com", org_name="組織M")
    group_api.add_member(child, member.user_id)
    assigned = group_api.assign(period, root)

    detail = group_api.detail(root)
    lower = group_api.detail(child)

    assert detail["path_names"] == ["会社A"]
    assert [item["group_id"] for item in detail["children"]] == [child["group_id"]]
    assert detail["children"][0]["name"] == "部署B"
    assert detail["members"] == []
    (assignment,) = detail["assignments"]
    assert assignment["assignment_id"] == assigned["assignment_id"]
    assert assignment["account"] == period["account"] and assignment["org_name"] == period["org_name"]
    assert assignment["state"] == "current"
    (listed,) = lower["members"]
    assert listed["user_id"] == member.user_id and listed["email"] == "member@example.com"
    assert listed["membership_id"] and listed["added_at"]
    error(call("GET", f"/groups/{UNKNOWN}"), 404, "GROUP_NOT_FOUND")
    error(call("GET", "/groups/bad"), 404, "GROUP_NOT_FOUND")


# =============================================================================
# 名前とメモの変更
# =============================================================================


def test_rename_changes_the_child_copy_and_keeps_the_path(aws, call, group_api):
    root = group_api.create("会社A")
    child = group_api.create("部署B", parent=root)

    status, body = call("PUT", f"/groups/{child['group_id']}", body={"name": "部署C", "note": "新しいメモ"})

    assert status == 200, body
    assert body["data"]["name"] == "部署C" and body["data"]["path"] == child["path"]
    assert row(aws, ledger.group_pk(root["group_id"]), ledger.child_sk(child["group_id"]))["name"] == "部署C"
    (audit,) = aws.audit_items("group.update")
    assert audit["detail"] == {"group_id": child["group_id"], "changed": ["name", "note"]}
    error(call("PUT", f"/groups/{child['group_id']}", body={}), 400, "VALIDATION_ERROR")


def test_rename_to_a_sibling_name_is_409_and_a_root_rename_updates_the_root_copy(aws, call, group_api):
    first = group_api.create("会社A")
    group_api.create("会社B")

    error(call("PUT", f"/groups/{first['group_id']}", body={"name": "会社B"}), 409, "GROUP_NAME_TAKEN")
    assert call("PUT", f"/groups/{first['group_id']}", body={"name": "会社A"})[0] == 200
    assert call("PUT", f"/groups/{first['group_id']}", body={"name": "会社Z"})[0] == 200
    assert row(aws, ledger.GROUP_ROOT_PK, ledger.child_sk(first["group_id"]))["name"] == "会社Z"


def test_an_unchanged_update_does_not_write(aws, call, group_api):
    group = group_api.create("会社A", note="メモ")
    aws.dynamodb.calls.clear()

    assert call("PUT", f"/groups/{group['group_id']}", body={"name": "会社A", "note": "メモ"})[0] == 200
    assert aws.dynamodb.calls == []


# =============================================================================
# 終了
# =============================================================================


def test_end_only_when_empty(aws, call, group_api, period_api, period):
    root = group_api.create("会社A")
    child = group_api.create("部署B", parent=root)
    error(call("POST", f"/groups/{root['group_id']}/end"), 409, "GROUP_IN_USE")

    member = aws.make_user("member@example.com")
    group_api.add_member(child, member.user_id)
    error(call("POST", f"/groups/{child['group_id']}/end"), 409, "GROUP_IN_USE")
    assert group_api.remove_member(child, member.user_id)[0] == 200

    assigned = group_api.assign(period, child)
    error(call("POST", f"/groups/{child['group_id']}/end"), 409, "GROUP_IN_USE")
    pid = period["device_period_id"]
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    assert call("POST", f"/device-periods/{pid}/assignments/{assigned['assignment_id']}/end")[0] == 200

    status, body = call("POST", f"/groups/{child['group_id']}/end")
    assert status == 200, body
    assert body["data"]["status"] == "ended" and body["data"]["ended_at"] == "2026-11-04T02:00:00Z"
    assert row(aws, ledger.group_pk(root["group_id"]), ledger.child_sk(child["group_id"]))["status"] == "ended"
    assert call("POST", f"/groups/{root['group_id']}/end")[0] == 200
    assert row(aws, ledger.group_pk(child["group_id"]), ledger.GROUP_PROFILE_SK) is not None
    (audit, *_rest) = aws.audit_items("group.end")
    assert audit["detail"] == {"group_id": child["group_id"]}


def test_an_assignment_that_starts_later_also_keeps_the_group_in_use(call, group_api, period):
    group = group_api.create("会社A")
    group_api.assign(period, group, valid_from="2026-12-01T00:00:00Z", valid_to="2026-12-02T00:00:00Z")

    error(call("POST", f"/groups/{group['group_id']}/end"), 409, "GROUP_IN_USE")


def test_operations_on_an_ended_group_are_409(aws, call, group_api, period):
    group = group_api.create("会社A")
    assert call("POST", f"/groups/{group['group_id']}/end")[0] == 200
    member = aws.make_user("member@example.com")
    gid = group["group_id"]
    pid = period["device_period_id"]

    error(call("POST", f"/groups/{gid}/end"), 409, "GROUP_ENDED")
    error(call("PUT", f"/groups/{gid}", body={"name": "x"}), 409, "GROUP_ENDED")
    error(call("POST", f"/groups/{gid}/members", body={"user_id": member.user_id}), 409, "GROUP_ENDED")
    error(
        call(
            "POST",
            f"/device-periods/{pid}/assignments",
            body={"target_type": "group", "group_id": gid, "valid_from": period["started_at"]},
        ),
        409,
        "GROUP_ENDED",
    )


def test_a_member_added_after_the_end_check_was_read_makes_the_end_409(aws, call, group_api, monkeypatch):
    from admin_users import ledger_writes

    group = group_api.create("会社A")
    member = aws.make_user("member@example.com")
    real_write = ledger_writes.write
    raced = []

    def add_member_first(items, **kwargs):
        if not raced:
            raced.append(True)
            group_api.add_member(group, member.user_id)
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", add_member_first)
    error(call("POST", f"/groups/{group['group_id']}/end"), 409, "CONFLICT")
    assert row(aws, ledger.group_pk(group["group_id"]), ledger.GROUP_PROFILE_SK)["status"] == "active"


def test_names_and_notes_never_enter_audit_or_logs(aws, call, group_api, caplog):
    caplog.set_level(logging.INFO)
    group = group_api.create("非公開の会社", note="非公開のメモ")
    call("PUT", f"/groups/{group['group_id']}", body={"name": "非公開の部署"})
    call("POST", f"/groups/{group['group_id']}/end")

    assert "非公開" not in str(aws.audit_items())
    assert "非公開" not in caplog.text


# =============================================================================
# 呼び出し元の検査 (ADR D9): 新しい書き込みの全部
# =============================================================================


@pytest.mark.parametrize(
    "operation",
    [
        "group_create",
        "group_update",
        "group_end",
        "member_add",
        "member_add_self",
        "member_remove",
        "group_assignment_add",
        "group_assignment_end",
        "period_start_access",
        "access_change",
    ],
)
@pytest.mark.parametrize("change", ["status", "admin_state"])
def test_a_caller_stopped_after_authorization_cannot_write(
    aws, call, actor, group_api, period_api, period, monkeypatch, operation, change
):
    from admin_users import ledger_writes

    group = group_api.create("会社A")
    member = aws.make_user("member@example.com")
    gid, pid = group["group_id"], period["device_period_id"]
    method, path, payload = "POST", f"/groups/{gid}/members", {"user_id": member.user_id}
    if operation == "group_create":
        path, payload = "/groups", {"name": "会社B"}
    elif operation == "group_update":
        method, path, payload = "PUT", f"/groups/{gid}", {"name": "会社C"}
    elif operation == "group_end":
        path, payload = f"/groups/{gid}/end", None
    elif operation == "member_add_self":
        payload = {"user_id": actor.user_id}
    elif operation == "member_remove":
        group_api.add_member(group, member.user_id)
        path, payload = f"/groups/{gid}/members/{member.user_id}/remove", None
    elif operation == "group_assignment_add":
        path = f"/device-periods/{pid}/assignments"
        payload = {"target_type": "group", "group_id": gid, "valid_from": period["started_at"]}
    elif operation == "group_assignment_end":
        assigned = group_api.assign(period, group)
        path, payload = f"/device-periods/{pid}/assignments/{assigned['assignment_id']}/end", None
    elif operation == "period_start_access":
        path, payload = "/device-periods", {"account": "87654321", "org_name": "組織B", "access": "assigned"}
    elif operation == "access_change":
        method, path, payload = "PUT", f"/device-periods/{pid}/access", {"access": "assigned"}
    real_write = ledger_writes.write
    value = "suspended" if change == "status" else "pending"

    def stop_then_write(items, **kwargs):
        aws.ledger.update_item(
            Key={"PK": ledger.user_pk(actor.user_id), "SK": ledger.PROFILE_SK},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": change},
            ExpressionAttributeValues={":s": value},
        )
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", stop_then_write)
    caller_key = (ledger.user_pk(actor.user_id), ledger.PROFILE_SK)

    def snapshot():
        items = [item for item in aws.ledger.scan()["Items"] if (item["PK"], item["SK"]) != caller_key]
        return sorted(items, key=lambda item: (item["PK"], item["SK"]))

    before = snapshot()
    audits = len(aws.audit_items())

    error(call(method, path, body=payload), 403, "ADMIN_NOT_ACTIVE")

    assert snapshot() == before
    assert len(aws.audit_items()) == audits
