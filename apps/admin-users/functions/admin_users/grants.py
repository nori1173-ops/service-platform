"""利用期間に属するサービスの許可。終了した行も履歴として残す。"""

from admin_users import device_periods as periods
from admin_users import errors, ledger_writes, services
from admin_users.tx import Tx
from service_platform import ledger
from service_platform.ids import ULID_PATTERN, new_ulid
from service_platform.ledger import UserContext

FIELDS = ("grant_id", "service", "account", "starts_at", "ends_at", "status", "granted_by", "granted_at", "ended_by")


def view(row: dict) -> dict:
    return {field: row.get(field) for field in FIELDS}


def add(caller: UserContext, period_id: str, payload: dict, request: dict) -> dict:
    period = periods.load_active(period_id)
    service = payload.get("service")
    if not isinstance(service, str) or service not in services.SERVICES:
        raise errors.validation("service が定義にありません")
    start, end = periods.interval(payload, "starts_at", "ends_at", period)
    existing = periods.children(period_id, ledger.grant_prefix(service))
    if any(
        row.get("status") == "active" and periods.overlap(start, end, row["starts_at"], row.get("ends_at"))
        for row in existing
    ):
        raise errors.conflict("GRANT_OVERLAP", "同じサービスの許可の期間が重なっています")
    grant_id = new_ulid()
    tx = Tx()
    row = {
        "PK": ledger.dperiod_pk(period_id),
        "SK": ledger.grant_sk(service, grant_id),
        "grant_id": grant_id,
        "service": service,
        "account": period["account"],
        "starts_at": start,
        "ends_at": end,
        "status": "active",
        "granted_by": caller.user_id,
        "granted_at": ledger_writes.now_iso(),
        "last_op_id": tx.op_id,
    }
    tx.add(ledger_writes.put(row))
    periods.finish(tx, period, caller, "grant.start", request, detail={"service": service, "grant_id": grant_id})
    return view(row)


def close(tx: Tx, row: dict, *, by: str, at: str) -> None:
    tx.add(
        ledger_writes.update(
            row["PK"],
            row["SK"],
            set_={"ends_at": at, "status": "ended", "ended_by": by, "last_op_id": tx.op_id},
            increment=(),
            condition="#c_status = :c_status AND #c_end = :c_end",
            names={"#c_status": "status", "#c_end": "ends_at"},
            values={":c_status": "active", ":c_end": row.get("ends_at")},
        ),
        errors.stale,
    )


def end(caller: UserContext, period_id: str, grant_id: str, request: dict) -> dict:
    period = periods.load_active(period_id)
    row = None
    if ULID_PATTERN.fullmatch(grant_id):
        row = next((r for r in periods.children(period_id, "GRANT#") if r["grant_id"] == grant_id), None)
    if row is None:
        raise errors.ApiError(404, "GRANT_NOT_FOUND", "許可が見つかりません")
    at = ledger_writes.now_iso()
    if row.get("status") != "active" or not periods.is_open(row.get("ends_at"), at):
        raise errors.conflict("GRANT_ENDED", "許可は終了しています")
    tx = Tx()
    close(tx, row, by=caller.user_id, at=at)
    periods.finish(tx, period, caller, "grant.end", request, detail={"service": row["service"], "grant_id": grant_id})
    return view({**row, "ends_at": at, "status": "ended", "ended_by": caller.user_id})
