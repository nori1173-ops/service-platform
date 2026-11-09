"""`GET /me/assignments` のテスト（計画 2.6 / 4.5、ADR-001 D5 のアカウント単位: 割り当てと許可の一覧）。"""

from datetime import timedelta

from device_data import (
    ACCOUNT,
    ASSIGNMENT_ID,
    NOW,
    OLD_PERIOD_ID,
    OTHER_USER_ID,
    PERIOD_ID,
    SERIAL,
    iso,
    put_assignment,
    put_grant,
    put_period,
)

OTHER_ACCOUNT = "23456789"
OTHER_PERIOD_ID = "01JBZ0000000000000PERIOD2"
UPCOMING_ID = "01JBZ000000000000UPCOMING"
OLD_ASSIGNMENT_ID = "01JBZ000000000000OLDASSGN"


def test_assignments_lists_current_upcoming_and_ended(aws, clock, call):
    aws.put_user()
    put_period(aws)
    put_assignment(aws, serial=SERIAL)
    put_grant(aws)
    put_grant(aws, grant_id="01JBZ00000000000000GRANT2", starts_at=iso(NOW + timedelta(days=1)))
    put_grant(aws, grant_id="01JBZ00000000000000GRANT3", status="ended", ends_at=iso(NOW - timedelta(hours=1)))
    put_period(aws, period_id=OTHER_PERIOD_ID, account=OTHER_ACCOUNT)
    put_assignment(
        aws,
        period_id=OTHER_PERIOD_ID,
        account=OTHER_ACCOUNT,
        assignment_id=UPCOMING_ID,
        valid_from=iso(NOW + timedelta(days=1)),
        org_name="組織B",
    )
    put_period(aws, period_id=OLD_PERIOD_ID, status="ended", current=False)
    put_assignment(
        aws,
        period_id=OLD_PERIOD_ID,
        assignment_id=OLD_ASSIGNMENT_ID,
        valid_from=iso(NOW - timedelta(days=30)),
        valid_to=iso(NOW - timedelta(days=10)),
        serial="11112222",
    )
    put_grant(aws, period_id=OLD_PERIOD_ID, grant_id="01JBZ00000000000000GRANT9")

    status, body = call("GET", "/me/assignments")

    assert status == 200
    rows = body["data"]["assignments"]
    assert [(row["assignment_id"], row["state"]) for row in rows] == [
        (ASSIGNMENT_ID, "current"),
        (UPCOMING_ID, "upcoming"),
        (OLD_ASSIGNMENT_ID, "ended"),
    ]
    current = rows[0]
    assert current == {
        "assignment_id": ASSIGNMENT_ID,
        "device_period_id": PERIOD_ID,
        "account": ACCOUNT,
        "org_name": "組織A",
        "valid_from": iso(NOW - timedelta(days=2)),
        "valid_to": None,
        "target_type": "user",
        "group_id": None,
        "group_label": None,
        "serial": SERIAL,
        "state": "current",
        "grants": [
            {
                "service": "camera",
                "name": "カメラ画像判定",
                "starts_at": iso(NOW - timedelta(days=2)),
                "ends_at": None,
                "current": True,
            },
            {
                "service": "camera",
                "name": "カメラ画像判定",
                "starts_at": iso(NOW + timedelta(days=1)),
                "ends_at": None,
                "current": False,
            },
        ],
    }
    assert rows[1]["org_name"] == "組織B"
    assert rows[1]["grants"] == []
    assert rows[2]["grants"] == []
    assert rows[2]["serial"] is None


def test_assignments_in_an_ended_period_are_ended_even_if_left_open(aws, clock, call):
    """利用期間が終わっていれば、終え損ねた割り当ても「終わったもの」として出す。"""
    aws.put_user()
    put_period(aws, status="ended", current=False)
    put_assignment(aws)
    put_grant(aws)

    _status, body = call("GET", "/me/assignments")

    (row,) = body["data"]["assignments"]
    assert row["state"] == "ended"
    assert row["grants"] == []


def test_assignments_of_other_users_are_not_listed(aws, clock, call):
    aws.put_user()
    put_period(aws)
    put_assignment(aws, user_id=OTHER_USER_ID, serial=SERIAL)

    status, body = call("GET", "/me/assignments")

    assert status == 200
    assert body["data"] == {"assignments": []}
    assert SERIAL not in str(body)


def test_assignments_require_an_active_user(aws, clock, call):
    aws.put_user(status="suspended")

    status, body = call("GET", "/me/assignments")

    assert status == 403
    assert body["error"]["code"] == "USER_SUSPENDED"
