"""通知メッセージスキーマのテスト用フィクスチャ。"""

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "shared" / "schemas" / "notify.v1.json"


@pytest.fixture(scope="session")
def notify_schema() -> dict:
    """`shared/schemas/notify.v1.json` を dict で返す。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def valid_message() -> dict:
    """の実例に沿った完全なメッセージ。

    各テストが自由に書き換えられるよう、呼び出しごとに新しい dict を返す。
    """
    return {
        "schema": "service-platform.notify.v1",
        "message_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "app": "sample-report",
        "channels": ["email", "slack"],
        "email": {
            "to": ["devA@example.co.jp"],
            "cc": ["devteam@example.co.jp"],
            "reply_to": ["devA@example.co.jp"],
            "subject": "【サンプルサービス】月額ライセンス料金一覧 2026年10月分（月末締め）",
            "text": "2026年10月分の月額課金レポートを送付します。",
            "html": "<p>2026年10月分の月額課金レポートを送付します。</p>",
        },
        "attachments": [
            {
                "bucket": "service-platform-core-attachments-dev",
                "key": "sample-report/production/2026/10/sample_report_2026-10.xlsx",
                "filename": "sample_report_2026-10.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ],
        "slack": {
            "channel": "C0123ABCD",
            "mention": "<!here>",
            "text": "2026-10 の サンプルサービス 月額課金レポートを送付しました。",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "2026-10 分"}}],
        },
        "meta": {"run_id": "RUN#production/2026-10", "target_month": "2026-10"},
    }
