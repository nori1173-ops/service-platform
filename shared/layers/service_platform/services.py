"""利用期間に許可できるサービスの定義（計画 4.5、ADR-001 D4 / D6）。

許可を出す admin-users と、通信機ログインの後にサービスの一覧を出す base が同じ表を使う。
起動先は同じタブで開くサービスの SPA (`/{id}/?account=...`)。サービスの API は台帳で確かめ直す。
"""

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    path: str


SERVICES: dict[str, Service] = {"camera": Service(id="camera", name="カメラ画像判定", path="/camera/")}


def launch_url(service: str, account: str) -> str:
    """サービスの起動先。定義に無いサービスは `KeyError`。"""
    return f"{SERVICES[service].path}?account={quote(account, safe='')}"
