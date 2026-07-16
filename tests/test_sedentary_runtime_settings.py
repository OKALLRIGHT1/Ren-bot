from pathlib import Path
import json

import config
import core.application as application_module
from core.application import Live2DApplication


class DummyScreenSensor:
    def __init__(self):
        self.sedentary_interval_sec = 0
        self.sedentary_cooldown_sec = 0
        self.next_sedentary_alert_time = 0


class DummyGuiHttpServer:
    host = "127.0.0.1"
    port = 8097
    path_prefix = "/gui"
    access_token = "gui-token"

    @staticmethod
    def activity_ingest_url():
        return "http://127.0.0.1:8097/gui/activity-ingest"


def _make_app(tmp_path):
    app = Live2DApplication.__new__(Live2DApplication)
    app.runtime_settings_path = Path(tmp_path) / "runtime_settings.json"
    app.skill_manager = None
    app.plugin_manager = None
    app.mcp_bridge = None
    app.chat_gateway = None
    app.chat_gateway_server = None
    app.chat_service = None
    app.loop = None
    app.logger = None
    app.screen_sensor = DummyScreenSensor()
    return app


def test_normalize_runtime_settings_reuses_existing_napcat_token(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    monkeypatch.setattr(
        application_module, "_read_existing_napcat_token", lambda: "existing-token"
    )

    result = app._normalize_external_runtime_settings({})

    assert result["napcat_access_token"] == "existing-token"
    saved = app._load_runtime_settings()
    assert saved["napcat_access_token"] == "existing-token"


def test_normalize_runtime_settings_keeps_runtime_napcat_token(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    called = False

    def fake_read_existing_token():
        nonlocal called
        called = True
        return "existing-token"

    monkeypatch.setattr(
        application_module, "_read_existing_napcat_token", fake_read_existing_token
    )

    result = app._normalize_external_runtime_settings(
        {"napcat_access_token": "runtime-token"}
    )

    assert result["napcat_access_token"] == "runtime-token"
    assert called is False


def test_normalize_runtime_settings_exposes_gui_activity_endpoint(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    monkeypatch.setattr(
        application_module, "_read_existing_napcat_token", lambda: "existing-token"
    )

    result = app._normalize_external_runtime_settings({})

    assert (
        result["gui_activity_endpoint"]
        == "http://127.0.0.1:8097/gui/activity-ingest"
    )


def test_publish_gui_activity_endpoint_syncs_live2d_appdata(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    app.gui_http_server = DummyGuiHttpServer()
    app._save_runtime_settings(
        {
            "gui_access_token": "gui-token",
            "sedentary_reminder_minutes": 45,
            "sedentary_break_minutes": 8,
            "sedentary_cooldown_minutes": 30,
        }
    )
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))

    app._publish_gui_activity_endpoint()

    synced_path = appdata / "com.live2d-only.app" / "runtime_settings.json"
    synced = json.loads(synced_path.read_text(encoding="utf-8"))
    assert synced["gui_activity_endpoint"] == DummyGuiHttpServer.activity_ingest_url()
    assert synced["gui_access_token"] == "gui-token"
    assert synced["sedentary_reminder_minutes"] == 45
    assert synced["sedentary_break_minutes"] == 8
    assert synced["sedentary_cooldown_minutes"] == 30


def test_apply_external_settings_hot_syncs_live2d_sedentary_values(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    app._save_runtime_settings({"gui_access_token": "gui-token"})
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))

    app.apply_external_settings(
        {
            "sedentary_reminder_minutes": 35,
            "sedentary_break_minutes": 6,
            "sedentary_cooldown_minutes": 25,
        }
    )

    synced_path = appdata / "com.live2d-only.app" / "runtime_settings.json"
    synced = json.loads(synced_path.read_text(encoding="utf-8"))
    assert synced["gui_access_token"] == "gui-token"
    assert synced["sedentary_reminder_minutes"] == 35
    assert synced["sedentary_break_minutes"] == 6
    assert synced["sedentary_cooldown_minutes"] == 25


def test_apply_external_settings_merges_partial_patch_before_saving_token(
    tmp_path, monkeypatch
):
    app = _make_app(tmp_path)
    app._save_runtime_settings(
        {
            "napcat_user_whitelist": ["10001"],
            "napcat_user_blacklist": ["10002"],
            "napcat_group_whitelist": ["20001"],
            "napcat_group_blacklist": ["20002"],
        }
    )
    monkeypatch.setattr(
        application_module, "_read_existing_napcat_token", lambda: "existing-token"
    )

    app.apply_external_settings({"sedentary_reminder_minutes": 25})

    saved = app._load_runtime_settings()
    assert saved["napcat_access_token"] == "existing-token"
    assert saved["napcat_user_whitelist"] == ["10001"]
    assert saved["napcat_user_blacklist"] == ["10002"]
    assert saved["napcat_group_whitelist"] == ["20001"]
    assert saved["napcat_group_blacklist"] == ["20002"]


def test_apply_external_settings_updates_sedentary_runtime_config(tmp_path):
    app = _make_app(tmp_path)

    old_values = {
        "SEDENTARY_REMINDER_MINUTES": config.SEDENTARY_REMINDER_MINUTES,
        "SEDENTARY_REMINDER_COOLDOWN_MINUTES": config.SEDENTARY_REMINDER_COOLDOWN_MINUTES,
        "SEDENTARY_BREAK_MINUTES": getattr(config, "SEDENTARY_BREAK_MINUTES", 5),
        "SEDENTARY_POPUP_ENABLED": config.SEDENTARY_POPUP_ENABLED,
        "SEDENTARY_POPUP_TITLE": config.SEDENTARY_POPUP_TITLE,
        "SEDENTARY_POPUP_MESSAGE": config.SEDENTARY_POPUP_MESSAGE,
        "SEDENTARY_POPUP_IMAGE_PATH": config.SEDENTARY_POPUP_IMAGE_PATH,
        "SEDENTARY_POPUP_SNOOZE_MINUTES": config.SEDENTARY_POPUP_SNOOZE_MINUTES,
        "SEDENTARY_POPUP_AUTO_CLOSE_SECONDS": config.SEDENTARY_POPUP_AUTO_CLOSE_SECONDS,
    }
    try:
        result = app.apply_external_settings(
            {
                "sedentary_reminder_minutes": 25,
                "sedentary_break_minutes": 7,
                "sedentary_cooldown_minutes": 12,
                "sedentary_popup_enabled": False,
                "sedentary_popup_title": "休息一下",
                "sedentary_popup_message": "{app_name}:{active_minutes}",
                "sedentary_popup_image_path": "D:/meme.png",
                "sedentary_popup_snooze_minutes": 3,
                "sedentary_popup_auto_close_seconds": 8,
            }
        )

        assert result["sedentary_live_applied"] is True
        assert config.SEDENTARY_REMINDER_MINUTES == 25
        assert config.SEDENTARY_BREAK_MINUTES == 7
        assert config.SEDENTARY_REMINDER_COOLDOWN_MINUTES == 12
        assert config.SEDENTARY_POPUP_ENABLED is False
        assert config.SEDENTARY_POPUP_TITLE == "休息一下"
        assert config.SEDENTARY_POPUP_MESSAGE == "{app_name}:{active_minutes}"
        assert config.SEDENTARY_POPUP_IMAGE_PATH == "D:/meme.png"
        assert config.SEDENTARY_POPUP_SNOOZE_MINUTES == 3
        assert config.SEDENTARY_POPUP_AUTO_CLOSE_SECONDS == 8
        assert app.screen_sensor.sedentary_interval_sec == 25 * 60
        assert app.screen_sensor.sedentary_cooldown_sec == 12 * 60
    finally:
        for key, value in old_values.items():
            setattr(config, key, value)


class FakeGuiWs:
    def __init__(self):
        self.capability_emitted = []

    def emit_capability(self, capability, payload):
        self.capability_emitted.append((capability, dict(payload)))


def test_apply_external_settings_broadcasts_activity_config_changed(tmp_path):
    app = _make_app(tmp_path)
    app.gui_ws_server = FakeGuiWs()
    app._save_runtime_settings(
        {
            "sedentary_reminder_minutes": 60,
            "activity_config_revision": 2,
        }
    )

    app.apply_external_settings(
        {
            "sedentary_reminder_minutes": 45,
            "activity_include_process_path": True,
        }
    )

    saved = app._load_runtime_settings()
    assert saved["activity_config_revision"] == 3
    assert app.get_activity_client_config()["revision"] == 3
    assert app.gui_ws_server.capability_emitted == [
        (
            "activity.config.v1",
            {"type": "activity_config_changed", "revision": 3},
        )
    ]


def test_live2d_only_sync_is_compatibility_not_enhanced_source(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    app.gui_http_server = DummyGuiHttpServer()
    app._save_runtime_settings(
        {
            "gui_access_token": "gui-token",
            "sedentary_reminder_minutes": 40,
            "activity_include_window_title": True,
            "activity_config_revision": 4,
        }
    )
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))

    app._publish_gui_activity_endpoint()

    synced_path = appdata / "com.live2d-only.app" / "runtime_settings.json"
    synced = json.loads(synced_path.read_text(encoding="utf-8"))
    # Compatibility path may still write endpoint/token for live2d-only.
    assert synced["gui_activity_endpoint"] == DummyGuiHttpServer.activity_ingest_url()
    # Enhanced must not treat that file as config source; client fields come from API.
    client = app.get_activity_client_config()
    assert "gui_access_token" not in client
    assert client["sedentary_reminder_minutes"] == 40
    assert client["include_window_title"] is True
    assert client["revision"] == 4
