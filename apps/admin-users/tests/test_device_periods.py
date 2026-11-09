"""利用期間 API の認可・一括終了・監査・障害時の原子性を検査する。"""

import logging

import pytest
from botocore.exceptions import ReadTimeoutError

from service_platform import ledger

UNKNOWN = "01JBZ00000000000000000NONE"
ROUTES = [
    ("GET", "/device-periods"),
    ("POST", "/device-periods"),
    ("GET", f"/device-periods/{UNKNOWN}"),
    ("POST", f"/device-periods/{UNKNOWN}/end"),
    ("POST", f"/device-periods/{UNKNOWN}/assignments"),
    ("POST", f"/device-periods/{UNKNOWN}/assignments/{UNKNOWN}/end"),
    ("POST", f"/device-periods/{UNKNOWN}/grants"),
    ("POST", f"/device-periods/{UNKNOWN}/grants/{UNKNOWN}/end"),
]


def error(result, status, code):
    assert result[0] == status, result
    assert result[1]["error"]["code"] == code


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize(
    "state,code",
    [
        ("user", "ADMIN_NOT_ACTIVE"),
        ("pending", "ADMIN_NOT_ACTIVE"),
        ("suspended", "USER_SUSPENDED"),
        ("unregistered", "USER_NOT_REGISTERED"),
        ("stale", "ADMIN_SESSION_STALE"),
        ("no_totp", "ADMIN_MFA_MISSING"),
        ("no_group", "ADMIN_NOT_ACTIVE"),
        ("disabled", "ADMIN_NOT_ACTIVE"),
    ],
)
def test_every_route_requires_an_active_admin(aws, call, actor, method, path, state, code):
    caller = actor
    kwargs = {}
    if state == "user":
        caller = aws.make_user("user@example.com")
    elif state == "pending":
        caller = aws.make_admin("pending@example.com", admin_state="pending")
    elif state == "suspended":
        caller = aws.make_admin("stopped@example.com", status="suspended")
    elif state == "unregistered":
        caller = aws.make_admin("absent@example.com", ledger_row=False)
    elif state == "stale":
        kwargs["auth_time"] = aws.mfa_enabled_at
    elif state == "no_totp":
        aws.cognito.totp.discard(actor.username)
    elif state == "no_group":
        aws.cognito._client.admin_remove_user_from_group(
            UserPoolId=aws.pool_id,
            Username=actor.username,
            GroupName="admin",
        )
    elif state == "disabled":
        aws.cognito._client.admin_disable_user(UserPoolId=aws.pool_id, Username=actor.username)
    error(call(method, path, caller=caller, body={}, **kwargs), 403, code)
    assert aws.dynamodb.calls == []


def test_start_writes_current_period_and_billing_audit(aws, call, period_api, actor):
    period = period_api.start(note="控え")
    pid = period["device_period_id"]
    row = period_api.row(ledger.device_pk(period["account"]), ledger.dperiod_sk(pid))
    assert row["status"] == "active"
    assert row["note"] == "控え"
    assert row["started_by"] == actor.user_id
    assert row["last_op_id"]
    current = period_api.row(ledger.device_pk(period["account"]), ledger.CURRENT_SK)
    assert current["device_period_id"] == pid
    assert current["GSI1PK"] == ledger.DEVICE_GSI1PK
    (audit,) = aws.audit_items("dperiod.start")
    assert audit["billing"] is True and "ttl" not in audit
    assert audit["account"] == period["account"] and audit["device_period_id"] == pid
    assert call("GET", f"/device-periods/{pid}")[1]["data"]["assignments"] == []


@pytest.mark.parametrize(
    "fields",
    [
        {"account": "1234567"},
        {"account": "123456789"},
        {"account": 12345678},
        {"account": "１２３４５６７８"},
        {"account": "1234x678"},
        {"org_name": " "},
        {"org_name": "a" * 101},
        {"org_name": 1},
        {"note": "a" * 501},
        {"note": []},
    ],
)
def test_start_validates_payload(aws, call, fields):
    payload = {"account": "12345678", "org_name": "組織A", **fields}
    error(call("POST", "/device-periods", body=payload), 400, "VALIDATION_ERROR")
    assert aws.audit_items() == []


def test_start_accepts_text_boundaries(period_api):
    period = period_api.start(account="00000001", org_name="あ" * 100, note="い" * 500)
    assert period["org_name"] == "あ" * 100


def test_current_prevents_a_second_active_period(aws, call, period):
    error(
        call("POST", "/device-periods", body={"account": period["account"], "org_name": "別組織"}),
        409,
        "DEVICE_PERIOD_ACTIVE",
    )
    assert len(aws.audit_items("dperiod.start")) == 1


def test_lists_active_and_all_after_handover(call, period_api, period):
    pid = period["device_period_id"]
    assert call("POST", f"/device-periods/{pid}/end")[0] == 200
    current = period_api.start()
    for path in ("/device-periods", "/device-periods?status=active"):
        rows = call("GET", path)[1]["data"]["device_periods"]
        assert [row["device_period_id"] for row in rows] == [current["device_period_id"]]
    rows = call("GET", "/device-periods?status=all")[1]["data"]["device_periods"]
    assert {row["status"] for row in rows} == {"active", "ended"}
    error(call("GET", "/device-periods?status=ended"), 400, "VALIDATION_ERROR")
    assert call("GET", f"/device-periods/{pid}")[1]["data"]["status"] == "ended"


@pytest.mark.parametrize("pid", [UNKNOWN, "invalid"])
def test_missing_period(call, pid):
    error(call("GET", f"/device-periods/{pid}"), 404, "DEVICE_PERIOD_NOT_FOUND")
    error(call("POST", f"/device-periods/{pid}/end"), 404, "DEVICE_PERIOD_NOT_FOUND")


def test_end_closes_children_and_logins_without_erasing_history(aws, call, period_api, period, actor):
    target = aws.make_user("customer@example.com", status="suspended")
    old = period_api.assign(period, target.user_id, valid_to="2026-11-04T01:10:00Z")
    current = period_api.assign(period, target.user_id, valid_from="2026-11-04T01:10:00Z")
    future = period_api.assign(period, actor.user_id, valid_from="2026-11-05T00:00:00Z")
    grant = period_api.grant(period)
    period_api.login(period, target.user_id, current["assignment_id"])
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    pid = period["device_period_id"]
    status, body = call("POST", f"/device-periods/{pid}/end")
    assert status == 200, body
    assert period_api.row(ledger.device_pk(period["account"]), ledger.CURRENT_SK) is None
    detail = call("GET", f"/device-periods/{pid}")[1]["data"]
    assert detail["ended_at"] == period_api.clock[0] and detail["status"] == "ended"
    rows = {row["assignment_id"]: row for row in detail["assignments"]}
    assert rows[old["assignment_id"]]["valid_to"] == old["valid_to"]
    for assignment, user_id in ((current, target.user_id), (future, actor.user_id)):
        assert rows[assignment["assignment_id"]]["valid_to"] == period_api.clock[0]
        reverse = period_api.row(ledger.user_pk(user_id), ledger.assign_sk(pid, assignment["assignment_id"]))
        assert reverse["valid_to"] == period_api.clock[0]
    assert detail["grants"][0]["grant_id"] == grant["grant_id"]
    assert detail["grants"][0]["ends_at"] == period_api.clock[0]
    assert detail["grants"][0]["status"] == "ended"
    assert period_api.row(ledger.user_pk(target.user_id), ledger.login_sk(period["account"])) is None
    (audit,) = aws.audit_items("dperiod.end")
    assert audit["billing"] is True and "ttl" not in audit
    error(call("POST", f"/device-periods/{pid}/end"), 409, "DEVICE_PERIOD_ENDED")


def test_period_end_retains_a_login_for_another_period(call, period_api, period, actor):
    assignment = period_api.assign(period, actor.user_id)
    period_api.login({**period, "device_period_id": UNKNOWN}, actor.user_id, assignment["assignment_id"])
    assert call("POST", f"/device-periods/{period['device_period_id']}/end")[0] == 200
    assert period_api.row(ledger.user_pk(actor.user_id), ledger.login_sk(period["account"])) is not None


def test_current_delete_is_conditional_and_everything_rolls_back(aws, call, period_api, period, actor):
    assignment = period_api.assign(period, actor.user_id)
    period_api.grant(period)
    period_api.login(period, actor.user_id, assignment["assignment_id"])
    key = {"PK": ledger.device_pk(period["account"]), "SK": ledger.CURRENT_SK}
    aws.ledger.update_item(
        Key=key, UpdateExpression="SET device_period_id = :p", ExpressionAttributeValues={":p": UNKNOWN}
    )
    error(call("POST", f"/device-periods/{period['device_period_id']}/end"), 409, "CONFLICT")
    detail = call("GET", f"/device-periods/{period['device_period_id']}")[1]["data"]
    assert detail["status"] == "active" and detail["assignments"][0]["valid_to"] is None
    assert detail["grants"][0]["ends_at"] is None
    assert aws.audit_items("dperiod.end") == []
    assert period_api.row(ledger.user_pk(actor.user_id), ledger.login_sk(period["account"])) is not None


def test_audit_failure_rolls_back_start(aws, call):
    aws.audit.delete()
    error(
        call("POST", "/device-periods", body={"account": "12345678", "org_name": "組織A"}), 500, "LEDGER_WRITE_FAILED"
    )
    assert aws.ledger.scan()["Items"] == [
        item for item in aws.ledger.scan()["Items"] if not item["PK"].startswith(("DEVICE#", "DPERIOD#"))
    ]


def test_notes_and_names_never_enter_audit_or_logs(aws, call, period_api, caplog):
    caplog.set_level(logging.INFO)
    period_api.start(org_name="非公開組織", note="非公開の本文")
    assert "非公開" not in str(aws.audit_items())
    assert "非公開" not in caplog.text


@pytest.mark.parametrize("operation", ["start", "end", "assignment", "assignment_end", "grant", "grant_end"])
@pytest.mark.parametrize("failure", ["timeout_before", "timeout_after", "denied"])
def test_transactions_retry_and_confirm_this_operation(aws, call, period_api, period, actor, operation, failure):
    pid = period["device_period_id"]
    path, payload = f"/device-periods/{pid}/end", None
    if operation == "start":
        path, payload = "/device-periods", {"account": "87654321", "org_name": "組織B"}
    elif operation == "assignment":
        path, payload = (
            f"/device-periods/{pid}/assignments",
            {"user_id": actor.user_id, "valid_from": period["started_at"]},
        )
    elif operation == "assignment_end":
        assignment = period_api.assign(period, actor.user_id)
        path = f"/device-periods/{pid}/assignments/{assignment['assignment_id']}/end"
    elif operation == "grant":
        path, payload = f"/device-periods/{pid}/grants", {"service": "camera", "starts_at": period["started_at"]}
    elif operation == "grant_end":
        grant = period_api.grant(period)
        path = f"/device-periods/{pid}/grants/{grant['grant_id']}/end"
    before = len(aws.audit_items())
    aws.dynamodb.calls.clear()
    if failure == "denied":
        aws.dynamodb.plan = [aws.client_error("AccessDeniedException")]
    else:
        timeout = ReadTimeoutError(endpoint_url="https://dynamodb.invalid")
        aws.dynamodb.plan = [timeout] * 4
        if failure == "timeout_after":
            aws.dynamodb.plan[0] = ("after", timeout)
    result = call("POST", path, body=payload)
    if failure == "timeout_after":
        assert result[0] in (200, 201), result
        assert len(aws.audit_items()) == before + 1
    else:
        error(
            result,
            503 if failure == "timeout_before" else 500,
            "OUTCOME_UNKNOWN" if failure == "timeout_before" else "LEDGER_WRITE_FAILED",
        )
        assert len(aws.audit_items()) == before
    calls = aws.dynamodb.calls
    assert all(item == calls[0] for item in calls)
    assert len(calls) == (1 if failure == "denied" else 4)


@pytest.mark.parametrize("operation", ["start", "end", "assignment", "assignment_end", "grant", "grant_end"])
def test_caller_suspended_after_authorization_cannot_write(
    aws, call, period_api, period, actor, monkeypatch, operation
):
    from admin_users import ledger_writes

    pid = period["device_period_id"]
    path, payload = f"/device-periods/{pid}/end", None
    if operation == "start":
        path, payload = "/device-periods", {"account": "87654321", "org_name": "組織B"}
    elif operation == "assignment":
        path, payload = (
            f"/device-periods/{pid}/assignments",
            {"user_id": actor.user_id, "valid_from": period["started_at"]},
        )
    elif operation == "assignment_end":
        row = period_api.assign(period, actor.user_id)
        path = f"/device-periods/{pid}/assignments/{row['assignment_id']}/end"
    elif operation == "grant":
        path, payload = f"/device-periods/{pid}/grants", {"service": "camera", "starts_at": period["started_at"]}
    elif operation == "grant_end":
        row = period_api.grant(period)
        path = f"/device-periods/{pid}/grants/{row['grant_id']}/end"
    real_write = ledger_writes.write

    def stop_then_write(items, **kwargs):
        aws.ledger.update_item(
            Key={"PK": ledger.user_pk(actor.user_id), "SK": ledger.PROFILE_SK},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "suspended"},
        )
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", stop_then_write)
    before = len(aws.audit_items())
    error(call("POST", path, body=payload), 403, "ADMIN_NOT_ACTIVE")
    assert len(aws.audit_items()) == before


@pytest.mark.parametrize("operation", ["detail", "assignment", "grant", "end"])
def test_detail_and_writes_never_query_a_gsi(aws, call, period_api, period, actor, monkeypatch, operation):
    table = aws.ledger
    calls = []

    class TableProxy:
        def __getattr__(self, name):
            return getattr(table, name)

        def get_item(self, **kwargs):
            assert kwargs.get("ConsistentRead") is True
            return table.get_item(**kwargs)

        def query(self, **kwargs):
            calls.append(kwargs)
            assert "IndexName" not in kwargs
            assert kwargs.get("ConsistentRead") is True
            return table.query(**kwargs)

    monkeypatch.setattr(ledger, "_table", lambda: TableProxy())
    if operation == "detail":
        assert call("GET", f"/device-periods/{period['device_period_id']}")[0] == 200
    elif operation == "assignment":
        period_api.assign(period, actor.user_id)
    elif operation == "grant":
        period_api.grant(period)
    else:
        assert call("POST", f"/device-periods/{period['device_period_id']}/end")[0] == 200


def test_queries_read_every_page(aws, call, period_api, period, actor, monkeypatch):
    period_api.assign(period, actor.user_id, valid_to="2026-11-04T02:00:00Z")
    period_api.assign(period, actor.user_id, valid_from="2026-11-04T02:00:00Z")
    period_api.grant(period)
    other = period_api.start(account="87654321")
    table = aws.ledger

    class Paged:
        def __getattr__(self, name):
            return getattr(table, name)

        def query(self, **kwargs):
            return table.query(**kwargs, Limit=1)

    monkeypatch.setattr(ledger, "_table", lambda: Paged())
    pid = period["device_period_id"]
    assert len(call("GET", f"/device-periods/{pid}")[1]["data"]["assignments"]) == 2
    assert len(call("GET", f"/users/{actor.user_id}")[1]["data"]["assignments"]) == 2
    for status in ("active", "all"):
        rows = call("GET", f"/device-periods?status={status}")[1]["data"]["device_periods"]
        assert {row["device_period_id"] for row in rows} == {pid, other["device_period_id"]}
    assert call("POST", f"/device-periods/{pid}/end")[0] == 200


def test_end_rejects_an_oversized_transaction_without_partial_writes(aws, call, period_api, period, actor):
    pid = period["device_period_id"]
    for index in range(34):
        aid = f"01JBZ{index:021d}"
        row = {
            "PK": ledger.dperiod_pk(pid),
            "SK": ledger.assignment_sk(actor.user_id, aid),
            "assignment_id": aid,
            "device_period_id": pid,
            "account": period["account"],
            "user_id": actor.user_id,
            "valid_from": period["started_at"],
            "valid_to": None,
        }
        aws.ledger.put_item(Item=row)
        aws.ledger.put_item(Item={**row, "PK": ledger.user_pk(actor.user_id), "SK": ledger.assign_sk(pid, aid)})
    # 利用者ごとに LOGIN は一度だけ消す。100 件を超えるため、利用者の異なる行も加える。
    for index in range(34):
        user_id = f"01JBX{index:021d}"
        aid = f"01JBW{index:021d}"
        row = {
            "PK": ledger.dperiod_pk(pid),
            "SK": ledger.assignment_sk(user_id, aid),
            "assignment_id": aid,
            "device_period_id": pid,
            "account": period["account"],
            "user_id": user_id,
            "valid_from": period["started_at"],
            "valid_to": None,
        }
        aws.ledger.put_item(Item=row)
        aws.ledger.put_item(Item={**row, "PK": ledger.user_pk(user_id), "SK": ledger.assign_sk(pid, aid)})
    calls = len(aws.dynamodb.calls)
    error(call("POST", f"/device-periods/{pid}/end"), 409, "TRANSACTION_LIMIT_EXCEEDED")
    assert len(aws.dynamodb.calls) == calls
    assert call("GET", f"/device-periods/{pid}")[1]["data"]["status"] == "active"
    assert aws.audit_items("dperiod.end") == []


@pytest.mark.parametrize("operation", ["assignment", "grant"])
def test_an_end_with_a_stale_child_snapshot_rolls_back(aws, call, period_api, period, actor, monkeypatch, operation):
    from admin_users import ledger_writes

    pid = period["device_period_id"]
    real_write = ledger_writes.write
    intervened = False

    def change_children_then_write(items, **kwargs):
        nonlocal intervened
        if not intervened:
            intervened = True
            if operation == "assignment":
                period_api.assign(period, actor.user_id)
            else:
                period_api.grant(period)
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", change_children_then_write)
    error(call("POST", f"/device-periods/{pid}/end"), 409, "CONFLICT")
    detail = call("GET", f"/device-periods/{pid}")[1]["data"]
    assert detail["status"] == "active"
    assert len(detail["assignments"] if operation == "assignment" else detail["grants"]) == 1
    assert aws.audit_items("dperiod.end") == []


def test_end_assignment_checks_login_id_at_commit_time(aws, call, period_api, period, actor, monkeypatch):
    from admin_users import ledger_writes

    assignment = period_api.assign(period, actor.user_id)
    period_api.login(period, actor.user_id, assignment["assignment_id"])
    real_write = ledger_writes.write

    def replace_login_then_write(items, **kwargs):
        period_api.login(period, actor.user_id, UNKNOWN)
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", replace_login_then_write)
    error(
        call("POST", f"/device-periods/{period['device_period_id']}/assignments/{assignment['assignment_id']}/end"),
        409,
        "CONFLICT",
    )
    detail = call("GET", f"/device-periods/{period['device_period_id']}")[1]["data"]
    assert detail["assignments"][0]["valid_to"] is None
    assert aws.audit_items("assignment.end") == []
    assert period_api.row(ledger.user_pk(actor.user_id), ledger.login_sk(period["account"]))["assignment_id"] == UNKNOWN


def test_an_old_operation_marker_cannot_prove_a_failed_end_succeeded(aws, call, period_api, period, monkeypatch):
    from admin_users import device_periods

    real_load = device_periods.load_active

    def stale_snapshot_then_end(pid):
        snapshot = real_load(pid)
        # 別の操作が先に終了した後も、この要求は開始時の読み取りを持っている。
        monkeypatch.setattr(device_periods, "load_active", real_load)
        assert call("POST", f"/device-periods/{pid}/end")[0] == 200
        return snapshot

    monkeypatch.setattr(device_periods, "load_active", stale_snapshot_then_end)
    error(call("POST", f"/device-periods/{period['device_period_id']}/end"), 409, "CONFLICT")
    assert len(aws.audit_items("dperiod.end")) == 1


def test_detail_requires_both_the_locator_and_the_original_row(aws, call, period):
    from service_platform.ids import new_ulid

    error(call("GET", f"/device-periods/{new_ulid()}"), 404, "DEVICE_PERIOD_NOT_FOUND")
    pid = period["device_period_id"]
    aws.ledger.delete_item(Key={"PK": ledger.device_pk(period["account"]), "SK": ledger.dperiod_sk(pid)})
    error(call("GET", f"/device-periods/{pid}"), 404, "DEVICE_PERIOD_NOT_FOUND")


def test_duplicate_status_query_is_invalid(call):
    error(call("GET", "/device-periods?status=active&status=all"), 400, "VALIDATION_ERROR")


@pytest.mark.parametrize("grant_count,expected_status", [(0, 200), (1, 409)])
def test_end_transaction_boundary_with_absent_and_expired_logins(
    aws, call, period_api, period, grant_count, expected_status
):
    from datetime import datetime

    pid = period["device_period_id"]
    expired = int(datetime.fromisoformat(period["started_at"]).timestamp())
    for index in range(48):
        uid = f"01JBX{index:021d}"
        aid = f"01JBW{index:021d}"
        row = {
            "PK": ledger.dperiod_pk(pid),
            "SK": ledger.assignment_sk(uid, aid),
            "assignment_id": aid,
            "device_period_id": pid,
            "account": period["account"],
            "user_id": uid,
            "valid_from": period["started_at"],
            "valid_to": None,
        }
        aws.ledger.put_item(Item=row)
        aws.ledger.put_item(Item={**row, "PK": ledger.user_pk(uid), "SK": ledger.assign_sk(pid, aid)})
        if index % 2:
            period_api.login(period, uid, aid)
            aws.ledger.update_item(
                Key={"PK": ledger.user_pk(uid), "SK": ledger.login_sk(period["account"])},
                UpdateExpression="SET expires_at = :e",
                ExpressionAttributeValues={":e": expired},
            )
    if grant_count:
        period_api.grant(period)
    before = len(aws.dynamodb.calls)
    result = call("POST", f"/device-periods/{pid}/end")
    assert result[0] == expected_status, result
    detail = call("GET", f"/device-periods/{pid}")[1]["data"]
    if expected_status == 200:
        assert len(aws.dynamodb.calls[-1]["TransactItems"]) == 100
        assert detail["status"] == "ended"
        assert all(row["valid_to"] == period["started_at"] for row in detail["assignments"])
        assert len(aws.audit_items("dperiod.end")) == 1
    else:
        error(result, 409, "TRANSACTION_LIMIT_EXCEEDED")
        assert len(aws.dynamodb.calls) == before
        assert detail["status"] == "active"
        assert all(row["valid_to"] is None for row in detail["assignments"])
        assert aws.audit_items("dperiod.end") == []


def test_end_many_historical_assignees_without_logins(aws, call, period):
    pid = period["device_period_id"]
    for index in range(97):
        uid, aid = f"01JBX{index:021d}", f"01JBW{index:021d}"
        aws.ledger.put_item(
            Item={
                "PK": ledger.dperiod_pk(pid),
                "SK": ledger.assignment_sk(uid, aid),
                "assignment_id": aid,
                "device_period_id": pid,
                "account": period["account"],
                "user_id": uid,
                "valid_from": period["started_at"],
                "valid_to": period["started_at"],
            }
        )
    assert call("POST", f"/device-periods/{pid}/end")[0] == 200
    assert len(aws.dynamodb.calls[-1]["TransactItems"]) == 4


@pytest.mark.parametrize("operation", ["period", "assignment"])
@pytest.mark.parametrize("expires_delta,deleted", [(-1, False), (0, False), (1, True)])
def test_end_skips_only_expired_logins(aws, call, period_api, period, actor, operation, expires_delta, deleted):
    from datetime import datetime

    assignment = period_api.assign(period, actor.user_id)
    period_api.login(period, actor.user_id, assignment["assignment_id"])
    key = {"PK": ledger.user_pk(actor.user_id), "SK": ledger.login_sk(period["account"])}
    expires = int(datetime.fromisoformat(period["started_at"]).timestamp()) + expires_delta
    aws.ledger.update_item(Key=key, UpdateExpression="SET expires_at = :e", ExpressionAttributeValues={":e": expires})
    suffix = "" if operation == "period" else f"/assignments/{assignment['assignment_id']}"
    assert call("POST", f"/device-periods/{period['device_period_id']}{suffix}/end")[0] == 200
    assert (period_api.row(key["PK"], key["SK"]) is None) is deleted


# =============================================================================
# access (ADR D14)
# =============================================================================


def period_row(aws, period):
    key = {"PK": ledger.device_pk(period["account"]), "SK": ledger.dperiod_sk(period["device_period_id"])}
    return aws.ledger.get_item(Key=key, ConsistentRead=True)["Item"]


def current_row(aws, period):
    key = {"PK": ledger.device_pk(period["account"]), "SK": ledger.CURRENT_SK}
    return aws.ledger.get_item(Key=key, ConsistentRead=True).get("Item")


@pytest.mark.parametrize("requested", [None, "everyone", "assigned"])
def test_start_records_the_access(aws, period_api, actor, requested):
    fields = {} if requested is None else {"access": requested}
    period = period_api.start(**fields)
    expected = requested or "everyone"

    assert period["access"] == expected
    assert period["access_changed_at"] == period["started_at"]
    assert period["access_changed_by"] == actor.user_id
    assert "access_gen" not in period
    stored = period_row(aws, period)
    current = current_row(aws, period)
    for item in (stored, current):
        assert item["access"] == expected
        assert item["access_changed_at"] == period["started_at"]
        assert item["access_changed_by"] == actor.user_id
    assert ("access_gen" in stored) is (expected == "everyone")
    assert "access_gen" not in current
    (audit,) = aws.audit_items("dperiod.start")
    assert audit["detail"] == {"access": expected}


@pytest.mark.parametrize("value", ["anyone", "", 1, None])
def test_start_rejects_an_unknown_access(aws, call, value):
    body = {"account": "12345678", "org_name": "組織A", "access": value}
    error(call("POST", "/device-periods", body=body), 400, "VALIDATION_ERROR")
    assert aws.audit_items() == []


def test_access_change_bumps_the_version_and_copies_to_current(aws, call, period_api, period, actor):
    pid = period["device_period_id"]
    first_gen = period_row(aws, period)["access_gen"]
    version = period_row(aws, period)["version"]
    period_api.clock[0] = "2026-11-04T02:00:00Z"

    status, body = call("PUT", f"/device-periods/{pid}/access", body={"access": "assigned"})

    assert status == 200, body
    assert body["data"]["access"] == "assigned"
    assert body["data"]["access_changed_at"] == "2026-11-04T02:00:00Z"
    assert body["data"]["access_changed_by"] == actor.user_id
    stored = period_row(aws, period)
    assert stored["version"] == version + 1 and stored["access_gen"] == first_gen
    assert current_row(aws, period)["access"] == "assigned"
    (listed,) = call("GET", "/device-periods")[1]["data"]["device_periods"]
    assert listed["access"] == "assigned" and listed["access_changed_at"] == "2026-11-04T02:00:00Z"
    assert listed["access_changed_by"] == actor.user_id
    (audit,) = aws.audit_items("dperiod.access")
    assert audit["detail"] == {"from": "everyone", "to": "assigned"}
    assert audit["account"] == period["account"] and audit["device_period_id"] == pid
    assert audit["billing"] is True and "ttl" not in audit

    period_api.clock[0] = "2026-11-04T03:00:00Z"
    assert call("PUT", f"/device-periods/{pid}/access", body={"access": "everyone"})[0] == 200
    second_gen = period_row(aws, period)["access_gen"]
    assert second_gen != first_gen
    call("PUT", f"/device-periods/{pid}/access", body={"access": "assigned"})
    call("PUT", f"/device-periods/{pid}/access", body={"access": "everyone"})
    assert period_row(aws, period)["access_gen"] not in (first_gen, second_gen)


def test_the_same_access_is_not_written(aws, call, period):
    aws.dynamodb.calls.clear()

    status, body = call("PUT", f"/device-periods/{period['device_period_id']}/access", body={"access": "everyone"})

    assert status == 200 and body["data"]["access"] == "everyone"
    assert aws.dynamodb.calls == []
    assert aws.audit_items("dperiod.access") == []


def test_access_change_on_an_ended_or_missing_period(call, period):
    pid = period["device_period_id"]
    error(call("PUT", f"/device-periods/{pid}/access", body={"access": "bad"}), 400, "VALIDATION_ERROR")
    assert call("POST", f"/device-periods/{pid}/end")[0] == 200
    error(call("PUT", f"/device-periods/{pid}/access", body={"access": "assigned"}), 409, "DEVICE_PERIOD_ENDED")
    error(call("PUT", f"/device-periods/{UNKNOWN}/access", body={"access": "assigned"}), 404, "DEVICE_PERIOD_NOT_FOUND")


def test_a_period_changed_after_it_was_read_makes_the_access_change_409(aws, call, period, monkeypatch):
    from admin_users import ledger_writes

    real_write = ledger_writes.write
    raced = []

    def bump_first(items, **kwargs):
        if not raced:
            raced.append(True)
            aws.ledger.update_item(
                Key={"PK": ledger.device_pk(period["account"]), "SK": ledger.dperiod_sk(period["device_period_id"])},
                UpdateExpression="SET version = version + :one",
                ExpressionAttributeValues={":one": 1},
            )
        return real_write(items, **kwargs)

    monkeypatch.setattr(ledger_writes, "write", bump_first)
    path = f"/device-periods/{period['device_period_id']}/access"
    error(call("PUT", path, body={"access": "assigned"}), 409, "CONFLICT")
    assert period_row(aws, period).get("access") == "everyone"


def test_rows_without_the_access_attributes_read_as_everyone(aws, call, period):
    for key in (
        {"PK": ledger.device_pk(period["account"]), "SK": ledger.dperiod_sk(period["device_period_id"])},
        {"PK": ledger.device_pk(period["account"]), "SK": ledger.CURRENT_SK},
    ):
        aws.ledger.update_item(
            Key=key, UpdateExpression="REMOVE access, access_gen, access_changed_at, access_changed_by"
        )
    expected = {"access": "everyone", "access_changed_at": period["started_at"], "access_changed_by": None}

    (active,) = call("GET", "/device-periods")[1]["data"]["device_periods"]
    (everything,) = call("GET", "/device-periods?status=all")[1]["data"]["device_periods"]
    detail = call("GET", f"/device-periods/{period['device_period_id']}")[1]["data"]

    for view in (active, everything, detail):
        assert {name: view[name] for name in expected} == expected
    status, body = call("PUT", f"/device-periods/{period['device_period_id']}/access", body={"access": "assigned"})
    assert status == 200, body
    assert aws.audit_items("dperiod.access")[0]["detail"] == {"from": "everyone", "to": "assigned"}


def test_end_closes_group_assignments_and_their_reverse_rows(aws, call, group_api, period_api, period):
    group = group_api.create("会社A")
    member = aws.make_user("member@example.com")
    group_api.add_member(group, member.user_id)
    assigned = group_api.assign(period, group)
    period_api.login(period, member.user_id, assigned["assignment_id"])
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    pid, gid, aid = period["device_period_id"], group["group_id"], assigned["assignment_id"]

    assert call("POST", f"/device-periods/{pid}/end")[0] == 200

    stored = period_api.row(ledger.dperiod_pk(pid), ledger.group_assignment_sk(gid, aid))
    reverse = period_api.row(ledger.group_pk(gid), ledger.group_assign_sk(pid, aid))
    assert stored["valid_to"] == reverse["valid_to"] == "2026-11-04T02:00:00Z"
    assert period_api.row(ledger.user_pk(member.user_id), ledger.login_sk(period["account"])) is not None
    assert call("POST", f"/groups/{gid}/members/{member.user_id}/remove")[0] == 200
    assert call("POST", f"/groups/{gid}/end")[0] == 200
