import sys
import types
import importlib


class _Logger:
    def __getattr__(self, name):
        def _log(*args, **kwargs):
            return None

        return _log


def test_voice_sensor_disabled_does_not_import_voice_module(monkeypatch):
    import core.application as app_module

    sys.modules.pop("modules.voice_sensor", None)
    monkeypatch.setattr(app_module.config, "VOICE_SENSOR_ENABLED", False, raising=False)

    app = app_module.Live2DApplication()
    app.logger = _Logger()

    app._init_voice_sensor_if_configured()

    assert app.voice_sensor is None
    assert "modules.voice_sensor" not in sys.modules


def test_voice_sensor_enabled_imports_and_creates_sensor(monkeypatch):
    import core.application as app_module

    created = {}
    fake_module = types.ModuleType("modules.voice_sensor")

    class FakeVoiceSensor:
        def __init__(self, chat_service, event_bus, config_path):
            created["args"] = (chat_service, event_bus, config_path)
            self.running = False

    fake_module.VoiceSensor = FakeVoiceSensor
    monkeypatch.setitem(sys.modules, "modules.voice_sensor", fake_module)
    monkeypatch.setattr(app_module.config, "VOICE_SENSOR_ENABLED", True, raising=False)
    monkeypatch.setattr(app_module.config, "SHERPA_MODEL_CONFIG", {"tokens": "fake"}, raising=False)

    app = app_module.Live2DApplication()
    app.logger = _Logger()
    app.chat_service = object()

    app._init_voice_sensor_if_configured()

    assert isinstance(app.voice_sensor, FakeVoiceSensor)
    assert created["args"][0] is app.chat_service
    assert created["args"][1] is app.event_bus
    assert created["args"][2] == {"tokens": "fake"}


def test_vision_capture_import_does_not_import_cv2(monkeypatch):
    sys.modules.pop("modules.vision.capture", None)
    sys.modules.pop("cv2", None)

    importlib.import_module("modules.vision.capture")

    assert "cv2" not in sys.modules


def test_tts_router_edge_default_does_not_import_gptsovits(monkeypatch):
    sys.modules.pop("modules.tts.router", None)
    sys.modules.pop("modules.tts.gptsovits", None)

    router_module = importlib.import_module("modules.tts.router")

    edge_cfg = {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
        "enabled": True,
        "max_chars": 500,
        "use_live2d_player": True,
        "live2d_channel": 0,
        "live2d_volume": 1.0,
        "enable_lip_sync": False,
        "rhubarb_path": "",
        "lip_sync_smooth_window": 3,
    }
    router = router_module.TTSRouter(edge_cfg=edge_cfg, verbose=False)

    assert router._active == "edge"
    assert "modules.tts.gptsovits" not in sys.modules


def test_gui_app_import_does_not_import_heavy_dialogs(monkeypatch):
    heavy_modules = {
        "modules.gui.dialogs.settings",
        "modules.gui.dialogs.knowledge_manager",
        "modules.gui.dialogs.status_screen_manager",
        "modules.gui.dialogs.codex_assistant",
        "modules.gui.dialogs.console_log",
        "modules.gui.dialogs.expression_library_manager",
        "modules.gui.dialogs.meme_manager",
        "modules.gui.dialogs.memory_editor",
    }
    sys.modules.pop("modules.gui.app", None)
    for name in heavy_modules:
        sys.modules.pop(name, None)

    importlib.import_module("modules.gui.app")

    loaded = heavy_modules.intersection(sys.modules)
    assert loaded == set()


def test_settings_dialog_import_does_not_import_memory_editor(monkeypatch):
    sys.modules.pop("modules.gui.dialogs.settings", None)
    sys.modules.pop("modules.gui.dialogs.memory_editor", None)

    importlib.import_module("modules.gui.dialogs.settings")

    assert "modules.gui.dialogs.memory_editor" not in sys.modules
