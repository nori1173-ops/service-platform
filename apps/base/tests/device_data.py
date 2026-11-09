"""通信機ログインのテストの値と台帳の行の組み立て 。

conftest が tests のディレクトリを sys.path に足すので、テストから `from device_data import ...` で読める
(pytest の `--import-mode=importlib` では conftest を import できないため)。値はすべて架空。
"""

from datetime import UTC, datetime, timedelta

from service_platform import ledger

USER_ID = "01JBZ0000000000000000USER1"

NOW = datetime(2026, 11, 4, 3, 0, 0, tzinfo=UTC)
ACCOUNT = "12345678"
SERIAL = "87654321"
# 明らかな架空の値。応答・ログ・監査に出ないことを、この文字列が無いことで確かめる
PIN = "9999"
PERIOD_ID = "01JBZ00000000000000PERIOD"
OLD_PERIOD_ID = "01JBZ0000000000000OLDPERD"
ASSIGNMENT_ID = "01JBZ0000000000000ASSIGN1"
OTHER_USER_ID = "01JBZ0000000000000000USER2"
GRANT_ID = "01JBZ00000000000000GRANT1"

# device-backend の GET settings の 200 本文 (形は本物、値は架空)。暗証番号と通知先を含む
SETTINGS_PAYLOAD = {
    "location": {"kanji": "見本の現場", "kana": "ﾐﾎﾝ"},
    "pin": PIN,
    "devices": [
        {"address": "#05", "site": "ｶﾒﾗ", "model": "CAM-UNIT-1", "action": "L"},
        {"address": "#12", "site": "ｽｲｲ", "model": "LOGGER-UNIT-A", "action": "S"},
        {"address": "bad", "site": "x", "model": "CAM-UNIT-1", "action": "L"},
    ],
    "email": {
        "data": {
            "subscriber": ["notify@example.jp"],
            "devproto": {"enabled": True, "interval": {"long": "10M", "short": "5M"}, "offset": 0},
        }
    },
}
SETTINGS_PATH = f"/api/v2/accounts/{ACCOUNT}/settings"
STATUS_PATH = f"/api/v2/accounts/{ACCOUNT}/status"


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def put_period(
    aws,
    *,
    period_id=PERIOD_ID,
    account=ACCOUNT,
    status="active",
    current=True,
    started_at=None,
    access="assigned",
    access_gen=None,
    org_name="組織A",
):
    """利用期間の正本・META・CURRENT。`access=None` は  の前の属性の無い行 (everyone と読む)。

    の前からのテストは割り当てが条件の通信機を前提にしているので、既定は assigned。
    """
    started = started_at or iso(NOW - timedelta(days=3))
    item = {
        "PK": ledger.device_pk(account),
        "SK": ledger.dperiod_sk(period_id),
        "device_period_id": period_id,
        "account": account,
        "org_name": org_name,
        "status": status,
        "started_at": started,
        "version": 1,
    }
    if access is not None:
        item["access"] = access
    if access_gen is not None:
        item["access_gen"] = access_gen
    aws.ledger.put_item(Item=item)
    aws.ledger.put_item(
        Item={"PK": ledger.dperiod_pk(period_id), "SK": "META", "account": account, "device_period_id": period_id}
    )
    if current:
        pointer = {
            "PK": ledger.device_pk(account),
            "SK": ledger.CURRENT_SK,
            "GSI1PK": ledger.DEVICE_GSI1PK,
            "GSI1SK": account,
            "device_period_id": period_id,
            "org_name": org_name,
            "started_at": started,
        }
        if access is not None:
            pointer["access"] = access
        aws.ledger.put_item(Item=pointer)


def put_assignment(
    aws,
    *,
    period_id=PERIOD_ID,
    user_id=USER_ID,
    assignment_id=ASSIGNMENT_ID,
    account=ACCOUNT,
    valid_from=None,
    valid_to=None,
    serial=None,
    org_name="組織A",
):
    start = valid_from or iso(NOW - timedelta(days=2))
    row = {
        "PK": ledger.dperiod_pk(period_id),
        "SK": ledger.assignment_sk(user_id, assignment_id),
        "assignment_id": assignment_id,
        "device_period_id": period_id,
        "account": account,
        "user_id": user_id,
        "valid_from": start,
        "valid_to": valid_to,
        "role": "user",
        "assigned_by": "01JBZ000000000000000ADMIN1",
        "assigned_at": start,
    }
    if serial is not None:
        row["serial"] = serial
    aws.ledger.put_item(Item=row)
    aws.ledger.put_item(
        Item={
            "PK": ledger.user_pk(user_id),
            "SK": ledger.assign_sk(period_id, assignment_id),
            "account": account,
            "device_period_id": period_id,
            "assignment_id": assignment_id,
            "valid_from": start,
            "valid_to": valid_to,
            "org_name": org_name,
        }
    )


def put_grant(
    aws, *, period_id=PERIOD_ID, service="camera", grant_id=GRANT_ID, starts_at=None, ends_at=None, status="active"
):
    aws.ledger.put_item(
        Item={
            "PK": ledger.dperiod_pk(period_id),
            "SK": ledger.grant_sk(service, grant_id),
            "grant_id": grant_id,
            "service": service,
            "account": ACCOUNT,
            "starts_at": starts_at or iso(NOW - timedelta(days=2)),
            "ends_at": ends_at,
            "status": status,
        }
    )


def put_camera(aws, address="05", *, account=ACCOUNT, last_seen=None, hours_ago=1.0):
    """外部のデバイス活動台帳の行 (ingest の upsert_camera と同じ属性)。`last_seen=""` で読めない行。"""
    seen = iso(NOW - timedelta(hours=hours_ago)) if last_seen is None else last_seen
    aws.cameras.put_item(
        Item={"account": account, "address": address, "site": "x", "first_seen": seen, "last_seen": seen}
    )


def ledger_row(aws, pk: str, sk: str) -> dict | None:
    return aws.ledger.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")


class Interleaver:
    """`ledger._table()` の差し替え。条件に合う最初の書き込みの直前に、登録した処理を 1 回だけ呼ぶ。"""

    def __init__(self, table):
        self._table = table
        self.hooks: list[tuple[str, object, object]] = []

    def before(self, operation: str, when, action) -> None:
        self.hooks.append((operation, when, action))

    def _run(self, operation: str, kwargs: dict) -> None:
        for index, (name, when, action) in enumerate(self.hooks):
            if name == operation and when(kwargs):
                del self.hooks[index]
                action()
                return

    def __getattr__(self, name):
        return getattr(self._table, name)

    def put_item(self, **kwargs):
        self._run("put_item", kwargs)
        return self._table.put_item(**kwargs)

    def update_item(self, **kwargs):
        self._run("update_item", kwargs)
        return self._table.update_item(**kwargs)

    def delete_item(self, **kwargs):
        self._run("delete_item", kwargs)
        return self._table.delete_item(**kwargs)


def put_membership(aws, group_id, membership_id, path, *, user_id=USER_ID):
    aws.ledger.put_item(
        Item={
            "PK": ledger.user_pk(user_id),
            "SK": ledger.member_sk(group_id),
            "membership_id": membership_id,
            "group_id": group_id,
            "path": path,
            "added_at": iso(NOW - timedelta(days=1)),
        }
    )


def put_group(aws, group_id, name, path):
    aws.ledger.put_item(
        Item={
            "PK": ledger.group_pk(group_id),
            "SK": ledger.GROUP_PROFILE_SK,
            "group_id": group_id,
            "name": name,
            "path": path,
            "depth": len(path),
            "status": "active",
            "version": 1,
        }
    )


def put_group_assignment(
    aws, group_id, *, assignment_id, period_id=PERIOD_ID, account=ACCOUNT, valid_from=None, valid_to=None
):
    start = valid_from or iso(NOW - timedelta(days=2))
    aws.ledger.put_item(
        Item={
            "PK": ledger.dperiod_pk(period_id),
            "SK": ledger.group_assignment_sk(group_id, assignment_id),
            "assignment_id": assignment_id,
            "device_period_id": period_id,
            "account": account,
            "target_type": "group",
            "group_id": group_id,
            "valid_from": start,
            "valid_to": valid_to,
            "role": "user",
        }
    )
    aws.ledger.put_item(
        Item={
            "PK": ledger.group_pk(group_id),
            "SK": ledger.group_assign_sk(period_id, assignment_id),
            "account": account,
            "device_period_id": period_id,
            "assignment_id": assignment_id,
            "valid_from": start,
            "valid_to": valid_to,
            "org_name": "組織A",
        }
    )
