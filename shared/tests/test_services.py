"""`service_platform.services` のテスト（ADR-001 D4: サービス許可）。"""

import pytest

from service_platform import services


def test_camera_is_the_only_service_for_now():
    assert list(services.SERVICES) == ["camera"]
    camera = services.SERVICES["camera"]
    assert camera.id == "camera"
    assert camera.name == "カメラ画像判定"


def test_launch_url_opens_the_service_for_the_account():
    """起動は同じタブで `/camera/?account=...` (ADR D6)。カメラの API は台帳で確かめ直す。"""
    assert services.launch_url("camera", "12345678") == "/camera/?account=12345678"


def test_launch_url_rejects_an_unknown_service():
    with pytest.raises(KeyError):
        services.launch_url("unknown", "12345678")


def test_the_definition_matches_admin_users():
    """許可を出す側 (admin-users) と、一覧を出す側 (base) で同じ id と表示名を使う。"""
    from admin_users import services as admin_services

    assert {key: value["name"] for key, value in admin_services.SERVICES.items()} == {
        key: value.name for key, value in services.SERVICES.items()
    }
