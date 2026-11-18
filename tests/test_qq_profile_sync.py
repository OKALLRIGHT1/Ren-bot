import logging

import pytest

from core.application import (
    Live2DApplication,
    load_qq_profile_sync,
    napcat_watchdog_delay,
    qq_profile_sync_fingerprint,
    save_qq_profile_sync,
)


def test_watchdog_slows_down_once_napcat_is_connected():
    assert (
        napcat_watchdog_delay(
            enabled=True, listening=True, ws_connected=True, previous_delay=20
        )
        == 120
    )
    assert (
        napcat_watchdog_delay(
            enabled=True, listening=True, ws_connected=False, previous_delay=120
        )
        == 45
    )
    assert (
        napcat_watchdog_delay(
            enabled=True, listening=False, ws_connected=False, previous_delay=120
        )
        == 20
    )
    assert (
        napcat_watchdog_delay(
            enabled=True, listening=False, ws_connected=False, previous_delay=20
        )
        == 40
    )


@pytest.mark.asyncio
async def test_unchanged_qq_profile_is_not_sent_again(tmp_path, monkeypatch):
    monkeypatch.setenv("SUZU_DATA_DIR", str(tmp_path))
    avatar = tmp_path / "face.jpg"
    avatar.write_bytes(b"same")
    app = Live2DApplication.__new__(Live2DApplication)
    app.logger = logging.getLogger("qq-profile-sync-test")
    calls = []

    async def call_action(action, params=None, timeout=8.0):
        calls.append(action)
        if action == "get_login_info":
            return {"ok": True, "data": {"nickname": "灯"}}
        return {"ok": True}

    app._call_napcat_action = call_action
    app._napcat_reverse_ws_connected = lambda: True
    profile = {"nickname": "灯", "avatar": str(avatar)}

    await app._sync_character_qq_profile("char", profile)
    await app._sync_character_qq_profile("char", profile)

    assert calls == ["get_login_info", "set_qq_avatar"]
    stored = load_qq_profile_sync()
    assert stored["nickname"] == "灯"
    assert stored["file"]["size"] == 4


@pytest.mark.asyncio
async def test_qq_profile_sync_waits_for_napcat_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("SUZU_DATA_DIR", str(tmp_path))
    app = Live2DApplication.__new__(Live2DApplication)
    app.logger = logging.getLogger("qq-profile-sync-test")
    calls = []

    async def call_action(action, params=None, timeout=8.0):
        calls.append(action)
        return {"ok": False, "reason": "should-not-run"}

    app._call_napcat_action = call_action
    app._napcat_reverse_ws_connected = lambda: False
    await app._sync_character_qq_profile("char", {"nickname": "灯"})
    assert calls == []
    assert load_qq_profile_sync() == {}


def test_avatar_change_is_a_different_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("SUZU_DATA_DIR", str(tmp_path))
    avatar = tmp_path / "face.jpg"
    avatar.write_bytes(b"a")
    first = qq_profile_sync_fingerprint("char", "灯", str(avatar))
    avatar.write_bytes(b"ab")
    second = qq_profile_sync_fingerprint("char", "灯", str(avatar))
    assert first != second
    save_qq_profile_sync(first)
    assert load_qq_profile_sync() == first
