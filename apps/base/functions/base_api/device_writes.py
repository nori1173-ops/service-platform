"""通信機ログインで base が書く台帳の行 (ログイン記録と製造番号。計画 2.1 / 4.12、ADR D13 / D14)。

ログイン記録・製造番号 (まだ無いときだけ)・監査を 1 つのトランザクションで書く。条件は、device_backend の照会の
後、書く直前に取り直した時刻で作る (照会の間に終わった根拠に書かない)。トランザクションには選んだ根拠の行の
条件を入れ、書いた時点で根拠があることを守る。

- 人宛 (user): 割り当ての行が今有効 (開始 <= 今 < 終了)。製造番号はこの行に書く (まだ空のときだけ)。
  親の利用期間は稼働中で読んだ version のまま (製造番号を書くときは version を上げる)
- グループ宛 (group): グループ宛の割り当ての行が今有効、記録する所属の行それぞれが同じ membership_id のまま、
  親は稼働中で読んだ version のまま
- everyone: 親が稼働中で、読んだ version のまま、access が everyone (access の変更は version を上げるので、
  読んだ後の世代の替わりもここで落ちる)
- 製造番号の控え (group と everyone): 本人の `SERIAL#{device_period_id}` を、無いときだけ Put (親の version は
  上げない)
- ログイン記録: 初めに読んだ記録の版 (`last_op_id`。読んだときに無ければ行が無いこと) を条件に置き換える。
  根拠の始まりの大小では縛らない (前の根拠が無くなった後の、別の有効な根拠への入り直しはいつでもできる)

根拠の行か親の条件に落ちたら、書いた後の時刻で条件 (1)(2) を確かめ直し、根拠が無ければ 403
`DEVICE_NOT_ASSIGNED`、あれば 409 `CONFLICT`。ログイン記録か控えで落ちたら 409 `CONFLICT` (並行するログインか、
読んだ後に admin が記録を消した)。落ちた項目は位置ではなく項目の種類で見る (根拠で項目の数が変わるため)。
"""

from boto3.dynamodb.types import TypeSerializer

from base_api import clients, clock, errors
from service_platform import access, audit, ledger, transactions
from service_platform.access import DeviceAccess
from service_platform.ids import new_ulid
from service_platform.ledger import UserContext
from service_platform.logging import get_logger

SERVICE = "base"
LOGIN_TTL_SECONDS = 12 * 60 * 60
TTL_MARGIN_SECONDS = 24 * 60 * 60
# 割り当てが今有効 (開始を含み、終了を含まない。終了の null は利用期間の終わりまで)
ASSIGNMENT_VALID = (
    "attribute_exists(PK) AND #c_vf <= :c_now AND "
    "(attribute_not_exists(#c_vt) OR attribute_type(#c_vt, :c_null) OR #c_vt > :c_now)"
)
ASSIGNMENT_NAMES = {"#c_vf": "valid_from", "#c_vt": "valid_to"}
BASIS_ITEM = "basis"
LOGIN_ITEM = "login"
SERIAL_ITEM = "serial"
AUDIT_ITEM = "audit"

logger = get_logger()
_serializer = TypeSerializer()


def _serialize(values: dict) -> dict:
    return {key: _serializer.serialize(value) for key, value in values.items()}


def _check(pk: str, sk: str, condition: str, names: dict | None = None, values: dict | None = None) -> dict:
    check = {"TableName": ledger.ledger_table_name(), "Key": _serialize({"PK": pk, "SK": sk})}
    check["ConditionExpression"] = condition
    if names:
        check["ExpressionAttributeNames"] = names
    if values:
        check["ExpressionAttributeValues"] = _serialize(values)
    return {"ConditionCheck": check}


def _assignment_item(pk: str, sk: str, *, at_iso: str, op_id: str, serial: str | None) -> dict:
    values = {":c_now": at_iso, ":c_null": "NULL"}
    if serial is None:
        return _check(pk, sk, ASSIGNMENT_VALID, ASSIGNMENT_NAMES, values)
    return {
        "Update": {
            "TableName": ledger.ledger_table_name(),
            "Key": _serialize({"PK": pk, "SK": sk}),
            "UpdateExpression": "SET #serial = :serial, #op = :op",
            "ConditionExpression": f"{ASSIGNMENT_VALID} AND attribute_not_exists(#serial)",
            "ExpressionAttributeNames": {**ASSIGNMENT_NAMES, "#serial": "serial", "#op": "last_op_id"},
            "ExpressionAttributeValues": _serialize({**values, ":serial": serial, ":op": op_id}),
        }
    }


def _period_item(found: DeviceAccess, *, op_id: str, bump: bool, everyone: bool) -> dict:
    names = {"#c_status": "status", "#version": "version"}
    values: dict = {":c_active": access.STATUS_ACTIVE}
    if found.period_version is None:
        condition = "#c_status = :c_active AND attribute_not_exists(#version)"
    else:
        condition = "#c_status = :c_active AND #version = :c_version"
        values[":c_version"] = found.period_version
    if everyone:
        condition += " AND (attribute_not_exists(#c_access) OR #c_access = :c_everyone)"
        names["#c_access"] = "access"
        values[":c_everyone"] = ledger.ACCESS_EVERYONE
    pk, sk = ledger.device_pk(found.account), ledger.dperiod_sk(found.device_period_id)
    if not bump:
        return _check(pk, sk, condition, names, values)
    return {
        "Update": {
            "TableName": ledger.ledger_table_name(),
            "Key": _serialize({"PK": pk, "SK": sk}),
            "UpdateExpression": "SET #version = if_not_exists(#version, :zero) + :one, #op = :op",
            "ConditionExpression": condition,
            "ExpressionAttributeNames": {**names, "#op": "last_op_id"},
            "ExpressionAttributeValues": _serialize({**values, ":zero": 0, ":one": 1, ":op": op_id}),
        }
    }


def saved_serial(user: UserContext, found: DeviceAccess) -> str | None:
    """今の根拠で控えてある製造番号 (人宛は割り当ての行、グループ宛と everyone は本人の控え)。"""
    if found.basis is not None and found.basis.kind == access.BASIS_USER:
        return found.serial
    row = ledger._get(ledger.user_pk(user.user_id), ledger.serial_sk(found.device_period_id)) or {}
    return row.get("serial") or None


def _basis_items(user: UserContext, found: DeviceAccess, *, at_iso: str, op_id: str, serial: str | None) -> list:
    """根拠の行と親の条件。人宛で製造番号をまだ控えていなければ割り当ての行に書き、親の version を上げる。"""
    basis = found.basis
    if basis.kind == access.BASIS_USER:
        pk = ledger.dperiod_pk(found.device_period_id)
        sk = ledger.assignment_sk(user.user_id, basis.assignment_id)
        return [
            _assignment_item(pk, sk, at_iso=at_iso, op_id=op_id, serial=serial),
            _period_item(found, op_id=op_id, bump=serial is not None, everyone=False),
        ]
    if basis.kind == access.BASIS_GROUP:
        pk = ledger.dperiod_pk(found.device_period_id)
        sk = ledger.group_assignment_sk(basis.group_id, basis.assignment_id)
        items = [_assignment_item(pk, sk, at_iso=at_iso, op_id=op_id, serial=None)]
        for membership_id, group_id in zip(basis.membership_ids, basis.membership_groups, strict=True):
            items.append(
                _check(
                    ledger.user_pk(user.user_id),
                    ledger.member_sk(group_id),
                    "attribute_exists(PK) AND #c_m = :c_m",
                    {"#c_m": "membership_id"},
                    {":c_m": membership_id},
                )
            )
        return [*items, _period_item(found, op_id=op_id, bump=False, everyone=False)]
    return [_period_item(found, op_id=op_id, bump=False, everyone=True)]


def _login_condition(previous: dict | None) -> tuple[str, dict, dict]:
    if previous is None:
        return "attribute_not_exists(PK)", {}, {}
    if previous.get("last_op_id"):
        return "#c_op = :c_op", {"#c_op": "last_op_id"}, {":c_op": previous["last_op_id"]}
    return "attribute_exists(PK) AND attribute_not_exists(#c_op)", {"#c_op": "last_op_id"}, {}


def _login_row(user: UserContext, found: DeviceAccess, *, serial: str, mode: str, summary: dict, at, op_id) -> dict:
    basis = found.basis
    expires_at = int(at.timestamp()) + LOGIN_TTL_SECONDS
    row = {
        "PK": ledger.user_pk(user.user_id),
        "SK": ledger.login_sk(found.account),
        "account": found.account,
        "basis": basis.kind,
        "device_period_id": found.device_period_id,
        "logged_in_at": access.iso(at),
        "expires_at": expires_at,
        "ttl": expires_at + TTL_MARGIN_SECONDS,
        "method": mode,
        "serial": serial,
        "summary": summary,
        "last_op_id": op_id,
    }
    if basis.kind == access.BASIS_EVERYONE:
        row["access_gen"] = basis.access_gen
    else:
        row["assignment_id"] = basis.assignment_id
    if basis.kind == access.BASIS_GROUP:
        row["group_id"] = basis.group_id
        row["membership_ids"] = list(basis.membership_ids)
    return row


def audit_detail(found: DeviceAccess, **detail) -> dict:
    """監査の detail に根拠の種類 (と、グループ宛のときは group_id) を足す。"""
    basis = found.basis
    if basis is None:
        return detail
    extra = {"basis": basis.kind}
    if basis.kind == access.BASIS_GROUP:
        extra["group_id"] = basis.group_id
    return {**detail, **extra}


def _write_failed(user, found: DeviceAccess, exc: transactions.WriteFailed, kinds: list[str], *, mode, request):
    failed = {kinds[index] for index in range(len(kinds)) if exc.failed_at(index)}
    if BASIS_ITEM in failed:
        try:
            access.require_basis(user.user_id, found.account, clock.now())
        except access.DeviceNotAssignedError as error:
            audit.emit(
                "device.login",
                actor=user,
                service=SERVICE,
                result="denied",
                code=error.code,
                account=found.account,
                device_period_id=found.device_period_id,
                assignment_id=found.assignment_id,
                request=request,
                detail=audit_detail(found, method=mode, stage="write"),
            )
            raise error from exc
        logger.info("device.login conflict", {"user_id": user.user_id, "account": found.account, "stage": "write"})
        raise errors.conflict() from exc
    if failed & {LOGIN_ITEM, SERIAL_ITEM}:
        logger.info("device.login superseded", {"user_id": user.user_id, "account": found.account})
        raise errors.conflict() from exc
    logger.error("device.login ledger failed", {"user_id": user.user_id, "error": type(exc).__name__})
    raise errors.temporarily_unavailable("ログインを記録できませんでした。もう一度お試しください") from exc


def write_login(
    user: UserContext,
    found: DeviceAccess,
    *,
    serial: str,
    mode: str,
    summary: dict,
    request: dict,
    previous: dict | None,
) -> dict:
    """ログイン記録 (12 時間)・製造番号 (まだ無いときだけ)・監査を書く。

    根拠が無くなっていれば `DeviceNotAssignedError`、利用期間の version や根拠の ID が変わっていれば
    409 `CONFLICT`、読んだ後にログイン記録が書き換わっていれば 409 `CONFLICT`。
    """
    now = clock.now()
    at_iso = access.iso(now)
    op_id = new_ulid()
    basis = found.basis
    save_serial = saved_serial(user, found) is None
    row = _login_row(user, found, serial=serial, mode=mode, summary=summary, at=now, op_id=op_id)
    basis_items = _basis_items(
        user,
        found,
        at_iso=at_iso,
        op_id=op_id,
        serial=serial if save_serial and basis.kind == access.BASIS_USER else None,
    )
    kinds = [BASIS_ITEM] * len(basis_items)
    items = list(basis_items)
    if save_serial and basis.kind != access.BASIS_USER:
        control = {
            "PK": ledger.user_pk(user.user_id),
            "SK": ledger.serial_sk(found.device_period_id),
            "account": found.account,
            "device_period_id": found.device_period_id,
            "serial": serial,
            "saved_at": at_iso,
        }
        items.append(
            {
                "Put": {
                    "TableName": ledger.ledger_table_name(),
                    "Item": _serialize(control),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
        )
        kinds.append(SERIAL_ITEM)
    condition, names, values = _login_condition(previous)
    put = {"TableName": ledger.ledger_table_name(), "Item": _serialize(row), "ConditionExpression": condition}
    if names:
        put["ExpressionAttributeNames"] = names
    if values:
        put["ExpressionAttributeValues"] = _serialize(values)
    items.append({"Put": put})
    kinds.append(LOGIN_ITEM)
    items.append(
        audit.put_item_for_transaction(
            "device.login",
            actor=user,
            service=SERVICE,
            account=found.account,
            device_period_id=found.device_period_id,
            assignment_id=found.assignment_id,
            request=request,
            detail=audit_detail(found, method=mode, device_count=len(summary["devices"])),
        )
    )
    kinds.append(AUDIT_ITEM)

    def applied() -> bool:
        return (ledger._get(row["PK"], row["SK"]) or {}).get("last_op_id") == op_id

    try:
        transactions.write(clients.dynamodb(), items, applied=applied)
    except transactions.WriteFailed as exc:
        _write_failed(user, found, exc, kinds, mode=mode, request=request)
    except transactions.OutcomeUnknown as exc:
        logger.error("device.login ledger unknown", {"user_id": user.user_id, "error": type(exc).__name__})
        raise errors.temporarily_unavailable("ログインを記録できませんでした。もう一度お試しください") from exc
    return row
