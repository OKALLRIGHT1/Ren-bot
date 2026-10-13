import asyncio

import pytest

from modules.live2d import (
    get_current_costume_model_path,
    is_same_costume_model_path,
    normalize_costume_model_path,
    update_current_costume_config,
)


def test_normalize_costume_model_path_is_stable_on_windows_style_paths():
    left = normalize_costume_model_path(r"C:\models\suzu\casual\model.model3.json")
    right = normalize_costume_model_path("C:/models/suzu/casual/model.model3.json")
    assert left
    assert left == right


def test_is_same_costume_model_path_compares_identity():
    assert is_same_costume_model_path(
        r"C:\models\suzu\casual\model.model3.json",
        "C:/models/suzu/casual/model.model3.json",
    )
    assert not is_same_costume_model_path(
        "C:/models/suzu/casual/model.model3.json",
        "C:/models/suzu/school/model.model3.json",
    )


def test_update_current_costume_config_tracks_model_path():
    update_current_costume_config({"emotion_map": {}}, model_path="C:/models/a/model.model3.json")
    assert is_same_costume_model_path(
        get_current_costume_model_path(),
        "C:/models/a/model.model3.json",
    )


class _FakeGuiWs:
    def __init__(self, capabilities):
        self._capabilities = set(capabilities or [])

    def client_has_capability(self, ws, capability):
        del ws
        return str(capability) in self._capabilities


def test_hello_forces_live2d_costume_resync_for_capable_client():
    import asyncio
    from core.application import Live2DApplication

    app = Live2DApplication.__new__(Live2DApplication)
    app.gui_ws_server = _FakeGuiWs({"gui.v1", "live2d.protocol.v1"})
    app.logger = None
    calls = []

    async def _send_gui_snapshot(ws=None):
        del ws
        calls.append("snapshot")

    app._send_gui_snapshot = _send_gui_snapshot
    app.sync_active_character_live2d = lambda force=False: calls.append(("sync", force)) or True

    asyncio.run(
        Live2DApplication._on_gui_ws_message(
            app,
            {
                "type": "hello",
                "client": "live2d-enhanced",
                "protocol_version": 1,
                "capabilities": ["gui.v1", "live2d.protocol.v1"],
            },
            object(),
        )
    )

    assert calls == ["snapshot", ("sync", True)]


def test_hello_skips_live2d_costume_resync_for_legacy_client():
    import asyncio
    from core.application import Live2DApplication

    app = Live2DApplication.__new__(Live2DApplication)
    app.gui_ws_server = _FakeGuiWs({"gui.v1"})
    app.logger = None
    calls = []

    async def _send_gui_snapshot(ws=None):
        del ws
        calls.append("snapshot")

    app._send_gui_snapshot = _send_gui_snapshot
    app.sync_active_character_live2d = lambda force=False: calls.append(("sync", force)) or True

    asyncio.run(
        Live2DApplication._on_gui_ws_message(
            app,
            {
                "type": "hello",
                "client": "legacy",
                "protocol_version": 1,
                "capabilities": ["gui.v1"],
            },
            object(),
        )
    )

    assert calls == ["snapshot"]


def test_sync_active_character_live2d_skips_when_paths_match(monkeypatch):
    from core.application import Live2DApplication
    import modules.live2d as live2d_mod

    app = Live2DApplication.__new__(Live2DApplication)
    app.logger = None
    applied = []

    class FakeCharacterManager:
        data = {"active_id": "suzu"}

        def get_character(self, char_id):
            assert char_id == "suzu"
            return {
                "costumes": {
                    "casual": {"path": "C:/models/suzu/casual/model.model3.json"},
                }
            }

        def get_current_costume_name(self, char_id):
            assert char_id == "suzu"
            return "casual"

        def get_costume_runtime_config(self, char_id, costume_name):
            assert char_id == "suzu"
            assert costume_name == "casual"
            return {"emotion_map": {}}

    monkeypatch.setattr(
        "modules.character_manager.character_manager",
        FakeCharacterManager(),
        raising=False,
    )
    monkeypatch.setattr(
        live2d_mod,
        "get_current_costume_model_path",
        lambda: "C:/models/suzu/casual/model.model3.json",
    )
    app.on_gui_change_costume = lambda path, config: applied.append((path, config))

    assert app.sync_active_character_live2d(force=False) is True
    assert applied == []
    assert app.sync_active_character_live2d(force=True) is True
    assert applied and applied[0][0].replace("\\", "/").endswith(
        "models/suzu/casual/model.model3.json"
    )


@pytest.mark.asyncio
async def test_preview_motion_sends_once_after_bounded_wait(monkeypatch):
    import modules.live2d as live2d_mod

    sent = []
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    async def fake_play_motion(mtn, motion_type=0, **kwargs):
        sent.append((mtn, motion_type, kwargs))

    monkeypatch.setattr(live2d_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(live2d_mod, "play_motion", fake_play_motion)
    monkeypatch.setattr(live2d_mod, "_COSTUME_GENERATION", 10)
    monkeypatch.setattr(live2d_mod, "_PREVIEW_TASK", None)

    await live2d_mod.preview_motion("wave", motion_type=2)

    assert sleeps == [live2d_mod.PREVIEW_SETTLE_DELAY]
    assert sent == [("wave", 2, {"source": "preview"})]
    assert live2d_mod._PREVIEW_TASK is None


@pytest.mark.asyncio
async def test_new_costume_invalidates_pending_preview(monkeypatch):
    import modules.live2d as live2d_mod

    real_sleep = asyncio.sleep
    released = asyncio.Event()
    sent = []

    async def blocked_sleep(delay):
        del delay
        await released.wait()

    async def fake_send_to_models(**kwargs):
        sent.append(kwargs["msg"])

    monkeypatch.setattr(live2d_mod.asyncio, "sleep", blocked_sleep)
    monkeypatch.setattr(live2d_mod, "_send_to_models", fake_send_to_models)
    monkeypatch.setattr(live2d_mod, "has_performance_client", lambda: True)
    monkeypatch.setattr(live2d_mod, "_COSTUME_GENERATION", 20)
    monkeypatch.setattr(live2d_mod, "_PREVIEW_TASK", None)

    preview = asyncio.create_task(live2d_mod.preview_motion("wave"))
    await real_sleep(0)
    await live2d_mod.change_costume("C:/models/new/model.model3.json", {})
    released.set()

    with pytest.raises(asyncio.CancelledError):
        await preview

    assert sent == [12000]
    assert live2d_mod._COSTUME_GENERATION == 21


@pytest.mark.asyncio
async def test_old_costume_idle_task_is_generation_guarded(monkeypatch):
    import modules.live2d as live2d_mod

    real_sleep = asyncio.sleep
    pending = []
    sent = []

    async def fake_sleep(delay):
        del delay
        await real_sleep(0)

    async def fake_send_to_models(**kwargs):
        sent.append(kwargs["msg"])

    def capture_task(coro):
        task = asyncio.get_running_loop().create_task(coro)
        pending.append(task)
        return task

    monkeypatch.setattr(live2d_mod, "_send_to_models", fake_send_to_models)
    monkeypatch.setattr(live2d_mod, "has_performance_client", lambda: False)
    monkeypatch.setattr(live2d_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(live2d_mod.asyncio, "create_task", capture_task)
    monkeypatch.setattr(live2d_mod, "_COSTUME_GENERATION", 30)
    monkeypatch.setattr(live2d_mod, "_PREVIEW_TASK", None)

    await live2d_mod.change_costume("C:/models/a/model.model3.json", {})
    await live2d_mod.change_costume("C:/models/b/model.model3.json", {})
    await asyncio.gather(*pending)

    assert sent.count(12000) == 2
    assert live2d_mod._COSTUME_GENERATION == 32
