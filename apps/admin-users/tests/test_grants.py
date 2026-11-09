"""サービスの許可の期間・重複・終了と監査を検査する。"""

import pytest

UNKNOWN = "01JBZ00000000000000000NONE"


def error(result, status, code):
    assert result[0] == status, result
    assert result[1]["error"]["code"] == code


def test_camera_definition_and_grant_lifecycle(aws, call, period_api, period):
    from admin_users import services

    assert services.SERVICES["camera"]["name"] == "カメラ画像判定"
    row = period_api.grant(period)
    pid, gid = period["device_period_id"], row["grant_id"]
    (audit,) = aws.audit_items("grant.start")
    assert audit["billing"] is True and "ttl" not in audit
    assert audit["detail"]["service"] == "camera"
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    assert call("POST", f"/device-periods/{pid}/grants/{gid}/end")[0] == 200
    detail = call("GET", f"/device-periods/{pid}")[1]["data"]["grants"][0]
    assert detail["status"] == "ended" and detail["ends_at"] == period_api.clock[0]
    (audit,) = aws.audit_items("grant.end")
    assert audit["billing"] is True and "ttl" not in audit
    error(call("POST", f"/device-periods/{pid}/grants/{gid}/end"), 409, "GRANT_ENDED")
    assert period_api.grant(period, starts_at=period_api.clock[0])["grant_id"] != gid


@pytest.mark.parametrize(
    "fields",
    [
        {"service": "unknown"},
        {"service": []},
        {"starts_at": None},
        {"starts_at": "2026-11-04T00:59:59Z"},
        {"starts_at": "2026-11-04T01:00:00"},
        {"ends_at": "2026-11-04T01:00:00Z"},
        {"ends_at": "2026-11-04T00:00:00Z"},
        {"ends_at": "bad"},
        {"ends_at": False},
    ],
)
def test_grant_validation(call, period, fields):
    payload = {"service": "camera", "starts_at": period["started_at"], **fields}
    error(call("POST", f"/device-periods/{period['device_period_id']}/grants", body=payload), 400, "VALIDATION_ERROR")


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-11-04T01:00:00Z", None),
        ("2026-11-04T01:30:00Z", None),
        ("2026-11-04T01:00:00Z", "2026-11-04T01:30:00Z"),
        ("2026-11-04T01:30:00Z", "2026-11-04T03:00:00Z"),
    ],
)
def test_overlapping_active_grants_are_rejected(call, period_api, period, start, end):
    period_api.grant(period, ends_at="2026-11-04T02:00:00Z")
    error(
        call(
            "POST",
            f"/device-periods/{period['device_period_id']}/grants",
            body={
                "service": "camera",
                "starts_at": start,
                "ends_at": end,
            },
        ),
        409,
        "GRANT_OVERLAP",
    )


def test_adjacent_grants_and_natural_expiry(call, period_api, period):
    old = period_api.grant(period, ends_at="2026-11-04T02:00:00Z")
    period_api.grant(period, starts_at="2026-11-04T11:00:00+09:00")
    period_api.clock[0] = "2026-11-04T02:00:00Z"
    error(
        call("POST", f"/device-periods/{period['device_period_id']}/grants/{old['grant_id']}/end"), 409, "GRANT_ENDED"
    )


def test_grants_cannot_be_added_to_an_ended_period(call, period):
    pid = period["device_period_id"]
    assert call("POST", f"/device-periods/{pid}/end")[0] == 200
    error(
        call("POST", f"/device-periods/{pid}/grants", body={"service": "camera", "starts_at": period["started_at"]}),
        409,
        "DEVICE_PERIOD_ENDED",
    )


def test_grant_id_must_belong_to_the_period(call, period_api, period):
    row = period_api.grant(period)
    other = period_api.start(account="87654321")
    error(
        call("POST", f"/device-periods/{other['device_period_id']}/grants/{row['grant_id']}/end"),
        404,
        "GRANT_NOT_FOUND",
    )
    error(call("POST", f"/device-periods/{period['device_period_id']}/grants/{UNKNOWN}/end"), 404, "GRANT_NOT_FOUND")
