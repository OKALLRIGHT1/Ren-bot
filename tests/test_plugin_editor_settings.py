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


def test_status_setting_is_readonly_and_not_saved(monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    raw = {
        "name": "桌面终端",
        "trigger": "smart_desk",
        "type": "react",
        "description": "status fixture",
        "timeout_sec": 8,
        "settings": {
            "mqtt_host": {
                "type": "string",
                "label": "MQTT 主机",
                "default": "127.0.0.1",
                "placeholder": "127.0.0.1",
            },
            "last_nfc_uid": {
                "type": "status",
                "label": "最近 UID",
                "default": "尚未刷卡",
            },
        },
    }

    class Live:
        def runtime_status(self):
            return {"last_nfc_uid": "04:AA:BB:CC"}

    manager = _FakePluginManager(raw)
    manager.plugins = {"smart_desk": Live()}
    dialog = PluginEditorDialog(plugin_manager=manager, trigger="smart_desk")
    field_type, widget = dialog.config_fields["last_nfc_uid"]
    assert field_type == "status"
    assert widget.isReadOnly()
    assert widget.text() == "04:AA:BB:CC"
    widget.setText("HACK")
    dialog._save_config()
    saved = manager.saved["settings"]["last_nfc_uid"]["default"]
    assert saved == "尚未刷卡"
    assert manager.saved["settings"]["mqtt_host"]["default"] == "127.0.0.1"
    dialog.close()


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


def _dialog_for(config):
    manager = _FakePluginManager(config)
    dialog = PluginEditorDialog(plugin_manager=manager, trigger=config["trigger"])
    return manager, dialog


def test_float_number_keeps_precision_and_integer_stays_int(monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    raw = {
        "name": "表情包库",
        "trigger": "meme_pack",
        "type": "direct",
        "description": "float fixture",
        "timeout_sec": 20,
        "settings": {
            "auto_probability": {
                "type": "number",
                "label": "自动发送概率",
                "description": "0 到 1",
                "default": 0.12,
                "min": 0,
                "max": 1,
            },
            "session_cooldown_seconds": {
                "type": "number",
                "label": "会话冷却秒数",
                "default": 120,
                "min": 5,
                "max": 7200,
            },
        },
    }
    manager, dialog = _dialog_for(raw)
    _float_type, probability = dialog.config_fields["auto_probability"]
    _int_type, cooldown = dialog.config_fields["session_cooldown_seconds"]
    assert isinstance(probability, QtWidgets.QDoubleSpinBox)
    assert isinstance(cooldown, QtWidgets.QSpinBox)
    assert probability.value() == 0.12
    assert cooldown.value() == 120
    probability.setValue(0.35)
    dialog._save_config()
    assert manager.saved["settings"]["auto_probability"]["default"] == 0.35
    assert manager.saved["settings"]["session_cooldown_seconds"]["default"] == 120
    assert manager.saved["type"] == "direct"
    dialog.close()


def test_plugin_without_settings_opens_access_and_keeps_delegate_type(monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    raw = {
        "name": "记忆与画像查询",
        "trigger": "memory_tools",
        "type": "delegate",
        "description": "查询记忆",
        "timeout_sec": 30,
        "aliases": ["/memory"],
        "settings": {},
    }
    manager, dialog = _dialog_for(raw)
    assert dialog._active_section == "access"
    assert dialog.param_search.isHidden() is True
    assert dialog.type_combo.currentText() == "delegate"
    assert dialog.params_empty.isHidden() is False
    dialog._save_config()
    assert manager.saved["type"] == "delegate"
    assert manager.saved["aliases"] == ["/memory"]
    dialog.close()


def test_parameter_search_filters_cards_and_permissions_follow_qq_switch():
    _app()
    raw = {
        "name": "联网搜索",
        "trigger": "search_web",
        "type": "delegate",
        "description": "搜索",
        "timeout_sec": 60,
        "settings": {
            "tavily_api_key": {
                "type": "secret",
                "label": "Tavily API Key",
                "description": "第一优先级",
                "default": "",
            },
            "num_results": {
                "type": "number",
                "label": "默认返回条数",
                "description": "返回条数",
                "default": 5,
                "min": 1,
                "max": 10,
            },
            "fallback_ddg": {
                "type": "bool",
                "label": "失败回退 DDG",
                "description": "失败时回退",
                "default": True,
            },
            "use_answer": {
                "type": "bool",
                "label": "问题优先",
                "description": "优先回答",
                "default": True,
            },
            "use_contents": {
                "type": "bool",
                "label": "自动拉正文",
                "description": "拉取正文",
                "default": False,
            },
        },
    }
    _manager, dialog = _dialog_for(raw)
    assert dialog._active_section == "params"
    assert dialog.param_search.isHidden() is False
    _field_type, switch = dialog.config_fields["fallback_ddg"]
    assert switch.isChecked() is True
    switch.setChecked(False)
    assert switch.isChecked() is False
    dialog.param_search.setText("tavily")
    visible = [entry["key"] for entry in dialog._param_entries if not entry["card"].isHidden()]
    assert visible == ["tavily_api_key"]
    dialog.param_search.clear()
    assert all(not entry["card"].isHidden() for entry in dialog._param_entries)
    dialog.allow_remote_qq_checkbox.setChecked(False)
    assert dialog.allow_qq_owner_checkbox.isEnabled() is False
    assert dialog.allow_group_without_at_checkbox.isEnabled() is False
    dialog.allow_remote_qq_checkbox.setChecked(True)
    assert dialog.allow_qq_others_checkbox.isEnabled() is True
    dialog._section_buttons["meta"].click()
    assert dialog._active_section == "meta"
    dialog.refresh_theme()
    dialog.close()


def test_missing_name_shows_inline_notice_and_does_not_save(monkeypatch):
    _app()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    raw = {
        "name": "测试插件",
        "trigger": "demo_plugin",
        "type": "react",
        "description": "demo",
        "settings": {"enabled": {"type": "bool", "label": "启用", "default": True}},
    }
    manager, dialog = _dialog_for(raw)
    dialog.name_input.clear()
    dialog._save_config()
    assert manager.saved is None
    assert dialog.notice_bar.isHidden() is False
    assert "主触发词" in dialog.notice_label.text() or "显示名称" in dialog.notice_label.text()
    assert dialog._active_section == "meta"
    dialog.close()
