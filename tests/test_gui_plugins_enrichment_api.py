from __future__ import annotations

from typing import Any, Dict

from services.gui_api.plugins_service import PluginsGuiService


class FakeManager:
    def __init__(self) -> None:
        self.plugin_configs = {
            "demo": {
                "name": "Demo",
                "trigger": "demo",
                "type": "direct",
                "description": "demo plugin",
                "enabled": True,
                "aliases": ["demo", "演示"],
                "access_control": {"allow_remote_qq": True, "allow_group_without_at": False},
                "settings": {
                    "api_token": {"type": "secret", "label": "Token", "default": "real-secret"},
                    "threshold": {"type": "integer", "label": "阈值", "default": 3},
                    "enabled_feature": {"type": "boolean", "label": "功能", "default": True},
                },
            }
        }
        self.plugins = {"demo": object()}
        self.saved: Dict[str, Dict[str, Any]] = {}

    def get_all_plugins_info(self):
        return [
            {
                "trigger": "demo",
                "name": "Demo",
                "type": "direct",
                "description": "demo plugin",
                "enabled": True,
                "version": "1.0",
                "author": "test",
                "access_control": {"allow_remote_qq": True},
                "access_summary": "QQ 远程可用",
            }
        ]

    def get_plugin_config(self, trigger: str):
        return self.plugin_configs.get(trigger)

    def get_plugin_config_schema(self, trigger: str):
        from modules.config_schema import build_plugin_config_schema

        return build_plugin_config_schema(trigger, self.plugin_configs.get(trigger) or {})

    def save_plugin_config(self, trigger: str, config: Dict[str, Any]) -> bool:
        self.saved[trigger] = config
        self.plugin_configs[trigger] = config
        return True


def test_list_plugins_includes_aliases_and_access():
    service = PluginsGuiService(manager=FakeManager())
    listed = service.list_plugins()
    assert listed["ok"] is True
    plugin = listed["data"]["plugins"][0]
    assert plugin["trigger"] == "demo"
    assert plugin["access_summary"]


def test_get_config_masks_secret_and_exposes_fields():
    service = PluginsGuiService(manager=FakeManager())
    result = service.get_config("demo")
    assert result["ok"] is True
    settings = result["data"]["config"]["settings"]
    token = settings["api_token"]
    if isinstance(token, dict):
        assert token.get("default") in {"", "********", "****", "[masked]"} or token.get("has_value") is True
    fields = result["data"]["schema"]["fields"]
    names = {field["name"] for field in fields}
    assert "api_token" in names
    assert "threshold" in names
    secret_field = next(field for field in fields if field["name"] == "api_token")
    assert secret_field["secret"] is True


def test_save_settings_form_keeps_secret_when_masked():
    manager = FakeManager()
    service = PluginsGuiService(manager=manager)
    saved = service.save_settings(
        "demo",
        {
            "threshold": 9,
            "enabled_feature": False,
            "api_token": "********",
        },
    )
    assert saved["ok"] is True
    threshold = manager.saved["demo"]["settings"]["threshold"]
    if isinstance(threshold, dict):
        assert threshold.get("default") == 9
    else:
        assert threshold == 9
    token = manager.saved["demo"]["settings"]["api_token"]
    if isinstance(token, dict):
        assert token.get("default") == "real-secret"
    else:
        assert token == "real-secret"
