import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.dialogs.plugin_manager import (
    PluginEditorDialog,
    is_plausible_url,
    setting_ui_hidden,
    webui_url_from_settings,
)


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_wss_url_is_accepted():
    assert is_plausible_url("wss://ws-api.wolfx.jp/all_eew") is True
    assert is_plausible_url("ws://example.local/all") is True
    assert is_plausible_url("https://example.com") is True
    assert is_plausible_url("not-a-url") is False


def test_setting_ui_hidden_flag():
    assert setting_ui_hidden({"ui": "hidden", "type": "text"}) is True
    assert setting_ui_hidden({"type": "bool"}) is False


def test_webui_url_from_settings_uses_port():
    assert (
        webui_url_from_settings(
            {"status_ui_port": {"type": "number", "default": 8765}}
        )
        == "http://127.0.0.1:8765/"
    )


class _FakePluginManager:
    def __init__(self, config):
        self.config = config
        self.saved = None

    def get_plugin_config(self, trigger):
        del trigger
        return json.loads(json.dumps(self.config))

    def get_plugin_config_schema(self, trigger):
        del trigger
        return {"fields": []}

    def _normalize_access_control(self, access_control):
        return access_control or {}

    def save_plugin_config(self, trigger, config):
        del trigger
        self.saved = config
        return True


def test_plugin_editor_hides_hidden_settings_and_keeps_them_on_save(monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    raw = {
        "name": "测试插件",
        "trigger": "test_hidden_settings",
        "type": "react",
        "description": "hidden settings fixture",
        "timeout_sec": 30,
        "settings": {
            "enabled": {"type": "bool", "label": "启用", "default": True},
            "watch_regions_json": {
                "type": "json",
                "ui": "hidden",
                "default": [{"name": "demo"}],
            },
            "wolfx_ws_url": {
                "type": "text",
                "ui": "hidden",
                "default": "wss://example.invalid/all",
            },
            "status_ui_port": {
                "type": "number",
                "ui": "hidden",
                "default": 8765,
            },
            "status_ui_url": {
                "type": "link",
                "label": "状态页",
                "default": "",
            },
        },
    }
    manager = _FakePluginManager(raw)
    dialog = PluginEditorDialog(
        plugin_manager=manager, trigger="test_hidden_settings"
    )
    assert "watch_regions_json" not in dialog.config_fields
    assert "wolfx_ws_url" not in dialog.config_fields
    assert "enabled" in dialog.config_fields
    assert "status_ui_url" in dialog.config_fields
    field_type, widget = dialog.config_fields["status_ui_url"]
    assert field_type == "link"
    assert widget.text().startswith("http://127.0.0.1:")
    dialog._save_config()
    assert manager.saved is not None
    saved_settings = manager.saved.get("settings") or {}
    assert "watch_regions_json" in saved_settings
    assert saved_settings["wolfx_ws_url"]["default"].startswith("wss://")
    dialog.close()
