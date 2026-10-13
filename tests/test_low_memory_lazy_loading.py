import os
import subprocess
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

    sys.modules.pop("modules.asr.adapter", None)
    monkeypatch.setattr(app_module.config, "VOICE_SENSOR_ENABLED", False, raising=False)

    app = app_module.Live2DApplication()
    app.logger = _Logger()

    app._init_voice_sensor_if_configured()

    assert app.voice_sensor is None
    assert "modules.asr.adapter" not in sys.modules


def test_voice_sensor_enabled_imports_and_creates_sensor(monkeypatch):
    import core.application as app_module

    created = {}
    fake_sensor = types.ModuleType("modules.asr.adapter")

    class FakeVoiceSensor:
        def __init__(self, *args, **kwargs):
            created["args"] = args
            created["kwargs"] = kwargs
            self.running = False

    def _fake_build_voice_sensor(chat_service):
        created["chat_service"] = chat_service
        sensor = FakeVoiceSensor()
        created["sensor"] = sensor
        return sensor

    fake_sensor.build_voice_sensor = _fake_build_voice_sensor
    monkeypatch.setitem(sys.modules, "modules.asr.adapter", fake_sensor)
    monkeypatch.setattr(app_module.config, "VOICE_SENSOR_ENABLED", True, raising=False)

    app = app_module.Live2DApplication()
    app.logger = _Logger()
    app.chat_service = object()

    app._init_voice_sensor_if_configured()

    assert isinstance(app.voice_sensor, FakeVoiceSensor)
    assert created["chat_service"] is app.chat_service


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


def test_tts_router_applies_role_config_before_rejecting_unready_gptsovits(monkeypatch):
    sys.modules.pop("modules.tts.router", None)
    router_module = importlib.import_module("modules.tts.router")
    applied = []

    class FakeGPTSoVITS:
        def __init__(self, **kwargs):
            self.ready = False

        def apply_runtime_config(self, cfg):
            applied.append(dict(cfg))
            self.ready = True

    monkeypatch.setattr(
        router_module,
        "_load_gptsovits_class",
        lambda verbose=False: FakeGPTSoVITS,
    )
    router = router_module.TTSRouter(
        edge_cfg={"voice": "zh-CN-XiaoxiaoNeural"},
        verbose=False,
    )
    role_cfg = {
        "enabled": True,
        "gpt_w": "G:/voice/model.ckpt",
        "sov_w": "G:/voice/model.pth",
        "ref_wav": "G:/voice/ref.wav",
        "prompt_lang": "ja",
        "prompt_text": "reference",
    }

    router.apply_role_tts_config(role_cfg)

    assert applied == [role_cfg]
    assert router.gpt is not None
    assert router.gpt.ready is True
    assert router._active == "gpt"


def test_gptsovits_defers_model_init_until_paths_are_configured(monkeypatch):
    import modules.tts.gptsovits as gptsovits_module

    init_calls = []
    monkeypatch.setattr(gptsovits_module, "GPT_W", "")
    monkeypatch.setattr(gptsovits_module, "SOV_W", "")
    monkeypatch.setattr(gptsovits_module, "REF_WAV", "")
    monkeypatch.setattr(
        gptsovits_module.GPTSoVITSTTS,
        "_init_model",
        lambda self: init_calls.append(True),
    )

    backend = gptsovits_module.GPTSoVITSTTS(verbose=False)

    assert init_calls == []
    assert backend.ready is False


def test_gui_app_import_does_not_import_heavy_dialogs(monkeypatch):
    heavy_modules = {
        "modules.gui.dialogs.settings",
        "modules.gui.dialogs.knowledge_manager",
        "modules.gui.dialogs.status_screen_manager",
        "modules.gui.dialogs.console_log",
        "modules.gui.dialogs.expression_library_manager",
        "modules.gui.dialogs.meme_manager",
        "modules.gui.dialogs.memory_editor",
    }
    # 在干净子进程里验证：pytest 收集阶段会 import 各测试文件（其中
    # test_settings_nav 顶部就 import 了 dialogs.settings），所以会话内
    # 无法观察 gui.app 的「首次 import」。也不能用 pop+reimport —— 那
    # 会造出新模块对象/类，把其他测试通过旧引用做的 monkeypatch 脱钩
    # （实测造成 test_settings_nav 顺序假失败）。
    probe = (
        "import sys, importlib;"
        "importlib.import_module('modules.gui.app');"
        "loaded = [m for m in {"
        + ",".join(repr(name) for name in sorted(heavy_modules))
        + "} if m in sys.modules];"
        "print('LOADED=' + ','.join(loaded))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode == 0, result.stderr
    loaded = [m for m in result.stdout.splitlines() if m.startswith("LOADED=")]
    assert loaded == ["LOADED="], f"gui.app 拉起了重对话框模块: {loaded}"


def test_settings_dialog_import_does_not_import_memory_editor(monkeypatch):
    # 同样在子进程验证（会话内 settings 大概率已被测试文件加载）。
    probe = (
        "import sys, importlib;"
        "importlib.import_module('modules.gui.dialogs.settings');"
        "print('MEMORY_EDITOR_LOADED=' + str('modules.gui.dialogs.memory_editor' in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode == 0, result.stderr
    assert "MEMORY_EDITOR_LOADED=False" in result.stdout
