"""通信機を伴う API の許可の条件 (1)〜(4)（計画 2.1 / 2.4 / 4.12、ADR-001 D4 / D5 / D13 / D14）。

(1) 通信機アカウントに稼働中の利用期間 P がある。
(2) 利用者が P を扱う根拠がある。根拠は (a) 本人宛の割り当て (P に属し今が有効期間内)、(b) グループ宛の割り当て
    (P に属し今が有効期間内で、そのグループが本人の所属の経路にある)、(c) P の access が everyone (属性が
    無いときを含む)。根拠ごとに使い回さない ID (世代) を持つ: (a) assignment_id、(b) assignment_id と、その
    グループを経路に含む所属の membership_id の全部、(c) P の access_gen (無ければ `START#{started_at}`)。
(3) ログイン記録が P に属し、期限内で、記録の根拠 (種類と ID と世代) が今の (2) の根拠の集合に含まれる。
    時刻 (秒) は比べない (同じ秒の失効と再付与を見分けられないため。ID は使い回さない)。
(4) サービスの許可が P に属し、今が期間内。どれか 1 つでも欠ければ 403。

(1) と (2) が欠けたときは区別せずに `DEVICE_NOT_ASSIGNED` (稼働中かどうかを第三者に知らせない)。
期間は開始を含み、終了を含まない。時刻の文字列は UTC の `%Y-%m-%dT%H:%M:%SZ` (台帳の書き手がそろえる) で、
文字列のまま比べる。ログイン記録の期限 `expires_at` は epoch 秒。読み取りはすべて強い整合で、GSI を使わない。
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from service_platform import ledger
from service_platform.auth import ForbiddenError

STATUS_ACTIVE = "active"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class DeviceNotAssignedError(ForbiddenError):
    """稼働中の利用期間が無い、または自分の割り当てが無い (403。区別して出さない)。"""

    code = "DEVICE_NOT_ASSIGNED"

    def __init__(self, message: str = "この通信機は割り当てられていません") -> None:
        super().__init__(message)


class DeviceLoginRequiredError(ForbiddenError):
    """ログイン記録が無い・期限切れ・別の割り当て (403)。"""

    code = "DEVICE_LOGIN_REQUIRED"

    def __init__(self, message: str = "通信機ログインをしてください") -> None:
        super().__init__(message)


class ServiceNotGrantedError(ForbiddenError):
    """サービスの許可が無い (403)。"""

    code = "SERVICE_NOT_GRANTED"

    def __init__(self, message: str = "このサービスは許可されていません") -> None:
        super().__init__(message)


BASIS_USER = "user"
BASIS_GROUP = "group"
BASIS_EVERYONE = "everyone"
_BASIS_ORDER = {BASIS_USER: 0, BASIS_GROUP: 1, BASIS_EVERYONE: 2}


@dataclass(frozen=True)
class Basis:
    """条件 (2) の根拠 1 つ。根拠の始まりの時刻は持たない (照合は ID と世代で行う)。"""

    kind: str
    assignment_id: str | None = None
    group_id: str | None = None
    membership_ids: tuple[str, ...] = ()
    access_gen: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    serial: str | None = None
    # membership_ids と同じ順の、所属の行のグループ (`USER#{user_id}` / `MEMBER#{group_id}`)。書き手の条件に使う
    membership_groups: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class DeviceAccess:
    """条件 (1)(2) (と、`require_device` では (3)) を満たした通信機の利用。

    `basis` は主の根拠 (`require_device` ではログイン記録に合った根拠)。`assignment_id`・`valid_from`・
    `valid_to` はその根拠のもので、everyone では None。`bases` は今の根拠の全部 (主の根拠の順)。
    """

    account: str
    device_period_id: str
    assignment_id: str | None
    valid_from: str | None
    valid_to: str | None
    org_name: str | None = None
    serial: str | None = None
    login: dict | None = None
    # 条件 (1) で読んだ利用期間の行の version。書き手が「読んだ version」を条件にする ( の追加の裁定)
    period_version: int | None = None
    basis: Basis | None = None
    bases: tuple[Basis, ...] = field(default=())
    access: str = ledger.ACCESS_EVERYONE


@dataclass(frozen=True)
class Grant:
    grant_id: str
    service: str
    starts_at: str
    ends_at: str | None


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def iso(now: datetime) -> str:
    return now.astimezone(UTC).strftime(TIME_FORMAT)


def _within(start: object, end: object, at: str) -> bool:
    return isinstance(start, str) and start <= at and (end is None or (isinstance(end, str) and at < end))


def _epoch(value: object) -> int | None:
    if isinstance(value, Decimal | int) and not isinstance(value, bool):
        return int(value)
    return None


def _query(pk: str, prefix: str) -> list[dict]:
    return ledger.query_prefix(pk, prefix)


def active_period(account: str) -> dict | None:
    """条件 (1): 通信機アカウントの稼働中の利用期間の行。無ければ None。"""
    try:
        pk = ledger.device_pk(account)
    except ValueError:
        return None
    current = ledger._get(pk, ledger.CURRENT_SK)
    period_id = (current or {}).get("device_period_id")
    if not isinstance(period_id, str) or not period_id or "#" in period_id:
        return None
    period = ledger._get(pk, ledger.dperiod_sk(period_id))
    if (
        not period
        or period.get("status") != STATUS_ACTIVE
        or period.get("device_period_id") != period_id
        or period.get("account") != account
    ):
        return None
    return period


def find_assignment(device_period_id: str, user_id: str, now: datetime | None = None) -> dict | None:
    """根拠 (a) の行: 利用期間の中の自分の割り当てのうち、今が有効期間内のもの (assignment_id の大きいもの)。"""
    rows = _user_assignments(device_period_id, user_id, iso(_now(now)))
    return max(rows, key=lambda row: row["assignment_id"]) if rows else None


def _user_assignments(device_period_id: str, user_id: str, at: str) -> list[dict]:
    rows = _query(ledger.dperiod_pk(device_period_id), ledger.assignment_prefix(user_id))
    return [
        row
        for row in rows
        if row.get("user_id") == user_id
        and isinstance(row.get("assignment_id"), str)
        and row.get("target_type", ledger.TARGET_USER) == ledger.TARGET_USER
        and _within(row.get("valid_from"), row.get("valid_to"), at)
    ]


def _memberships_by_group(user_id: str) -> dict[str, list[tuple[str, str]]]:
    """本人の所属の経路の group_id ごとに、その group_id を経路に含む所属の (membership_id, 所属の group_id)。"""
    collected: dict[str, set[tuple[str, str]]] = {}
    for row in ledger.list_memberships(user_id):
        membership_id, path, own_group = row.get("membership_id"), row.get("path"), row.get("group_id")
        if not isinstance(membership_id, str) or not membership_id or not isinstance(path, list):
            continue
        if not isinstance(own_group, str) or not own_group:
            continue
        for group_id in path:
            if isinstance(group_id, str) and group_id:
                collected.setdefault(group_id, set()).add((membership_id, own_group))
    return {group_id: sorted(pairs) for group_id, pairs in collected.items()}


def find_bases(period: dict, user_id: str, at: datetime | None = None) -> list[Basis]:
    """条件 (2) の根拠の全部を、主の根拠の順 (user → group → everyone、同じ型では assignment_id の大きいもの) で。"""
    at_iso = iso(_now(at))
    period_id = period["device_period_id"]
    bases = [
        Basis(
            kind=BASIS_USER,
            assignment_id=row["assignment_id"],
            valid_from=row["valid_from"],
            valid_to=row.get("valid_to"),
            serial=row.get("serial") or None,
        )
        for row in _user_assignments(period_id, user_id, at_iso)
    ]
    memberships = _memberships_by_group(user_id)
    if memberships:
        for row in _query(ledger.dperiod_pk(period_id), ledger.GROUP_ASSIGNMENT_PREFIX):
            group_id = row.get("group_id")
            if (
                isinstance(row.get("assignment_id"), str)
                and group_id in memberships
                and _within(row.get("valid_from"), row.get("valid_to"), at_iso)
            ):
                bases.append(
                    Basis(
                        kind=BASIS_GROUP,
                        assignment_id=row["assignment_id"],
                        group_id=group_id,
                        membership_ids=tuple(pair[0] for pair in memberships[group_id]),
                        membership_groups=tuple(pair[1] for pair in memberships[group_id]),
                        valid_from=row["valid_from"],
                        valid_to=row.get("valid_to"),
                    )
                )
    if ledger.period_access(period) == ledger.ACCESS_EVERYONE:
        bases.append(Basis(kind=BASIS_EVERYONE, access_gen=ledger.period_access_gen(period)))
    bases.sort(key=lambda basis: basis.assignment_id or "", reverse=True)
    bases.sort(key=lambda basis: _BASIS_ORDER[basis.kind])
    return bases


def _with_basis(found: DeviceAccess, basis: Basis) -> DeviceAccess:
    return replace(
        found,
        basis=basis,
        assignment_id=basis.assignment_id,
        valid_from=basis.valid_from,
        valid_to=basis.valid_to,
        serial=basis.serial,
    )


def require_basis(user_id: str, account: str, now: datetime | None = None) -> DeviceAccess:
    """条件 (1)(2)。根拠が無ければ `DeviceNotAssignedError`。戻り値の根拠は主の根拠。"""
    at = _now(now)
    period = active_period(account)
    if period is None:
        raise DeviceNotAssignedError()
    bases = find_bases(period, user_id, at)
    if not bases:
        raise DeviceNotAssignedError()
    found = DeviceAccess(
        account=account,
        device_period_id=period["device_period_id"],
        assignment_id=None,
        valid_from=None,
        valid_to=None,
        org_name=period.get("org_name"),
        period_version=_epoch(period.get("version")),
        bases=tuple(bases),
        access=ledger.period_access(period),
    )
    return _with_basis(found, bases[0])


def _membership_ids(row: dict) -> set[str]:
    value = row.get("membership_ids")
    return {item for item in value if isinstance(item, str)} if isinstance(value, list | tuple | set) else set()


def login_basis(found: DeviceAccess, row: dict) -> Basis | None:
    """ログイン記録の根拠に合う、今の根拠。無ければ None。`basis` が無い古い記録は user として読む。"""
    kind = row.get("basis") or BASIS_USER
    for basis in found.bases:
        if basis.kind != kind:
            continue
        if kind == BASIS_EVERYONE and basis.access_gen == row.get("access_gen"):
            return basis
        if kind == BASIS_USER and basis.assignment_id == row.get("assignment_id"):
            return basis
        if (
            kind == BASIS_GROUP
            and basis.assignment_id == row.get("assignment_id")
            and set(basis.membership_ids) & _membership_ids(row)
        ):
            return basis
    return None


def check_login(access: DeviceAccess, user_id: str, now: datetime | None = None) -> dict:
    """条件 (3): P に属し、期限内で、根拠が今の (2) の根拠の集合に含まれる記録。

    欠ければ `DeviceLoginRequiredError`。時刻 (logged_in_at・valid_from など) は比べない。
    """
    at = _now(now)
    row = ledger._get(ledger.user_pk(user_id), ledger.login_sk(access.account))
    expires_at = _epoch((row or {}).get("expires_at"))
    if (
        not row
        or row.get("device_period_id") != access.device_period_id
        or expires_at is None
        or expires_at <= int(at.timestamp())
        or login_basis(access, row) is None
    ):
        raise DeviceLoginRequiredError()
    return row


def require_device(user_id: str, account: str, now: datetime | None = None) -> DeviceAccess:
    """条件 (1)(2)(3)。戻り値の根拠はログイン記録に合った根拠で、`login` にログイン記録 (要約を含む) を入れる。"""
    at = _now(now)
    found = require_basis(user_id, account, at)
    login = check_login(found, user_id, at)
    return replace(_with_basis(found, login_basis(found, login)), login=login)


def active_grants(device_period_id: str, now: datetime | None = None, service: str | None = None) -> list[Grant]:
    """利用期間の許可のうち、今が期間内の稼働中のもの (サービス・grant_id の順)。"""
    at = iso(_now(now))
    prefix = ledger.grant_prefix(service) if service is not None else "GRANT#"
    grants = [
        Grant(grant_id=row["grant_id"], service=row["service"], starts_at=row["starts_at"], ends_at=row.get("ends_at"))
        for row in _query(ledger.dperiod_pk(device_period_id), prefix)
        if row.get("status") == STATUS_ACTIVE
        and isinstance(row.get("grant_id"), str)
        and isinstance(row.get("service"), str)
        and (service is None or row["service"] == service)
        and _within(row.get("starts_at"), row.get("ends_at"), at)
    ]
    return sorted(grants, key=lambda grant: (grant.service, grant.grant_id))


def require_grant(access: DeviceAccess, service: str, now: datetime | None = None) -> Grant:
    """条件 (4)。欠ければ `ServiceNotGrantedError`。"""
    grants = active_grants(access.device_period_id, now, service)
    if not grants:
        raise ServiceNotGrantedError()
    return grants[0]
