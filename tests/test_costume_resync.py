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
