"""所属の API (ADR D13)。所属の ID (membership_id) は使い回さない。

外して付け直せば新しい ID になり、条件 (3) はこの ID で前の所属と見分ける (ADR D14)。外してもログイン記録は消さない。
"""

import pytest

from service_platform import ledger

UNKNOWN = "01JBZ00000000000000000NONE"


def error(result, status, code):
    assert result[0] == status, result
    assert result[1]["error"]["code"] == code


def row(aws, pk, sk):
    return aws.ledger.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")


@pytest.fixture
def tree(group_api):
    root = group_api.create("会社A")
    child = group_api.create("部署B", parent=root)
    return root, child


@pytest.mark.parametrize("target_status", ["active", "suspended"])
def test_add_copies_the_path_and_writes_the_same_membership_id_twice(aws, actor, group_api, tree, target_status):
    root, child = tree
    member = aws.make_user("member@example.com", status=target_status)

    added = group_api.add_member(child, member.user_id)

    assert added["user_id"] == member.user_id and added["group_id"] == child["group_id"]
    assert added["path"] == [root["group_id"], child["group_id"]]
    assert added["added_by"] == actor.user_id and added["added_at"]
    own = row(aws, ledger.user_pk(member.user_id), ledger.member_sk(child["group_id"]))
    reverse = row(aws, ledger.group_pk(child["group_id"]), ledger.group_member_sk(member.user_id))
    assert own["membership_id"] == reverse["membership_id"] == added["membership_id"]
    assert own["path"] == added["path"]
    assert reverse["user_id"] == member.user_id
    (audit,) = aws.audit_items("group.member_add")
    assert audit["target_user_id"] == member.user_id
    assert audit["detail"] == {"group_id": child["group_id"], "membership_id": added["membership_id"]}
    assert audit["billing"] is True and "ttl" not in audit


def test_remove_and_add_again_gives_a_new_membership_id(aws, call, group_api, tree):
    _root, child = tree
    member = aws.make_user("member@example.com")
    first = group_api.add_member(child, member.user_id)

    status, body = group_api.remove_member(child, member.user_id)
    assert status == 200, body
    assert body["data"]["membership_id"] == first["membership_id"]
    assert row(aws, ledger.user_pk(member.user_id), ledger.member_sk(child["group_id"])) is None
    assert row(aws, ledger.group_pk(child["group_id"]), ledger.group_member_sk(member.user_id)) is None

    second = group_api.add_member(child, member.user_id)
    assert second["membership_id"] != first["membership_id"]
    (removed,) = aws.audit_items("group.member_remove")
    assert removed["detail"] == {"group_id": child["group_id"], "membership_id": first["membership_id"]}
    assert removed["billing"] is True


def test_adding_twice_is_409_and_a_missing_user_is_404(aws, call, group_api, tree):
    _root, child = tree
    member = aws.make_user("member@example.com")
    group_api.add_member(child, member.user_id)
    gid = child["group_id"]

    error(call("POST", f"/groups/{gid}/members", body={"user_id": member.user_id}), 409, "MEMBER_EXISTS")
    error(call("POST", f"/groups/{gid}/members", body={"user_id": UNKNOWN}), 404, "USER_NOT_FOUND")
    error(call("POST", f"/groups/{UNKNOWN}/members", body={"user_id": member.user_id}), 404, "GROUP_NOT_FOUND")
    error(call("POST", f"/groups/{gid}/members/{UNKNOWN}/remove"), 404, "MEMBER_NOT_FOUND")


def test_the_same_person_may_belong_to_a_parent_and_a_child(aws, group_api, tree):
    root, child = tree
    member = aws.make_user("member@example.com")

    assert group_api.add_member(root, member.user_id)["path"] == [root["group_id"]]
    assert group_api.add_member(child, member.user_id)["path"] == [root["group_id"], child["group_id"]]


def test_adding_the_caller_itself_puts_one_operation_per_item(aws, actor, group_api, tree):
    """同じ行への操作が 2 つあると DynamoDB がトランザクション全体を断る。呼び出し元の検査 1 つにまとめる。"""
    _root, child = tree
    aws.dynamodb.calls.clear()

    added = group_api.add_member(child, actor.user_id)

    assert added["user_id"] == actor.user_id
    (sent,) = aws.dynamodb.calls
    keys = []
    for item in sent["TransactItems"]:
        (operation,) = item.values()
        key = operation.get("Key") or {name: operation["Item"][name] for name in ("PK", "SK")}
        keys.append((operation["TableName"], key["PK"]["S"], key["SK"]["S"]))
    assert len(keys) == len(set(keys))


def test_removing_keeps_the_login_record(aws, call, group_api, tree):
    _root, child = tree
    member = aws.make_user("member@example.com")
    group_api.add_member(child, member.user_id)
    login = {"PK": ledger.user_pk(member.user_id), "SK": ledger.login_sk("12345678"), "basis": "group"}
    aws.ledger.put_item(Item=login)

    assert group_api.remove_member(child, member.user_id)[0] == 200
    assert row(aws, login["PK"], login["SK"]) is not None


def test_a_membership_replaced_after_it_was_read_makes_the_remove_409(aws, call, group_api, tree, monkeypatch):
    from admin_users import ledger_writes

    _root, child = tree
    member = aws.make_user("member@example.com")
    group_api.add_member(child, member.user_id)
    real_write = ledger_writes.write
    raced = []

    def replace_first(items, **kwargs):
        if not raced:
            raced.append(True)
            aws.ledger.update_item(
                Key={"PK": ledger.user_pk(member.user_id), "SK": ledger.member_sk(child["group_id"])},
                UpdateExpression="SET membership_id = :m",
                ExpressionAttributeValues={":m": "01JBZ000000000000MEMBERNEW"},
            )
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", replace_first)
    error(group_api.remove_member(child, member.user_id), 409, "CONFLICT")
    assert row(aws, ledger.group_pk(child["group_id"]), ledger.group_member_sk(member.user_id)) is not None


def test_the_user_detail_lists_memberships_with_the_path_names(aws, call, group_api, tree):
    root, child = tree
    member = aws.make_user("member@example.com")
    added = group_api.add_member(child, member.user_id)

    detail = call("GET", f"/users/{member.user_id}")[1]["data"]

    assert detail["memberships"] == [
        {
            "membership_id": added["membership_id"],
            "group_id": child["group_id"],
            "path": [root["group_id"], child["group_id"]],
            "path_names": ["会社A", "部署B"],
            "added_at": added["added_at"],
        }
    ]
    assert call("GET", f"/users/{aws.make_user('other@example.com').user_id}")[1]["data"]["memberships"] == []


def test_the_group_detail_lists_members_after_add(call, aws, group_api, tree):
    _root, child = tree
    member = aws.make_user("member@example.com", org_name="組織M")
    group_api.add_member(child, member.user_id)

    (listed,) = group_api.detail(child)["members"]

    assert listed["org_name"] == "組織M" and listed["kind"] == "customer"
