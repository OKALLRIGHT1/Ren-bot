from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from integrations.gui_http import GuiHttpServer


class FakeRequest:
    pass


class FakeGuiWs:
    def __init__(self) -> None:
        self.emitted: list[dict] = []
        self.capability_emitted: list[tuple[str, dict]] = []

    def emit(self, payload: Dict[str, Any]) -> None:
        self.emitted.append(dict(payload))

    def emit_capability(self, capability: str, payload: Dict[str, Any]) -> None:
        self.capability_emitted.append((capability, dict(payload)))


class FakeApp:
    def __init__(self, runtime_settings: Optional[Dict[str, Any]] = None) -> None:
        self.runtime_settings_path = Path("unused-runtime-settings.json")
        self._settings = dict(runtime_settings or {})
        self.gui_ws_server = FakeGuiWs()
        self.logger = None
        self.skill_manager = None
        self.plugin_manager = None
        self.mcp_bridge = None
        self.chat_gateway = None
        self.chat_gateway_server = None
        self.chat_service = None
        self.loop = None
        self.screen_sensor = None
        self._activity_config_revision = 0

    def _load_runtime_settings(self) -> Dict[str, Any]:
        return dict(self._settings)

    def get_activity_client_config(self) -> Dict[str, Any]:
        settings = self._load_runtime_settings()
        revision = int(settings.get("activity_config_revision") or self._activity_config_revision or 0)
        return {
            "revision": max(0, revision),
            "monitor_enabled": bool(settings.get("activity_monitor_enabled", True)),
            "sedentary_reminder_minutes": int(
                settings.get("sedentary_reminder_minutes", 60) or 60
            ),
            "sedentary_break_minutes": int(settings.get("sedentary_break_minutes", 5) or 5),
            "sedentary_cooldown_minutes": int(
                settings.get("sedentary_cooldown_minutes", 60) or 60
            ),
            "include_process_path": bool(
                settings.get("activity_include_process_path", False)
            ),
            "include_window_title": bool(
                settings.get("activity_include_window_title", False)
            ),
            "include_browser_context": bool(
                settings.get("activity_include_browser_context", False)
            ),
        }

    def notify_activity_config_changed(self, revision: int) -> None:
        payload = {"type": "activity_config_changed", "revision": int(revision)}
        server = self.gui_ws_server
        if hasattr(server, "emit_capability"):
            server.emit_capability("activity.config.v1", payload)
        elif hasattr(server, "emit"):
            server.emit(payload)


@pytest.mark.asyncio
async def test_activity_config_returns_only_client_fields():
    app = FakeApp(
        runtime_settings={
            "sedentary_reminder_minutes": 45,
            "sedentary_break_minutes": 8,
            "sedentary_cooldown_minutes": 30,
            "activity_monitor_enabled": True,
            "activity_include_process_path": True,
            "activity_include_window_title": False,
            "activity_include_browser_context": True,
            "activity_config_revision": 3,
            "gui_access_token": "must-not-leak",
            "napcat_access_token": "also-secret",
        }
    )
    response = await GuiHttpServer(app_ref=app)._handle_activity_config(FakeRequest())
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["ok"] is True
    data = payload["data"]
    assert data["sedentary_reminder_minutes"] == 45
    assert data["sedentary_break_minutes"] == 8
    assert data["sedentary_cooldown_minutes"] == 30
    assert data["monitor_enabled"] is True
    assert data["include_process_path"] is True
    assert data["include_window_title"] is False
    assert data["include_browser_context"] is True
    assert data["revision"] == 3
    assert "gui_access_token" not in response.text
    assert "must-not-leak" not in response.text
    assert "also-secret" not in response.text
    assert set(data.keys()) == {
        "revision",
        "monitor_enabled",
        "sedentary_reminder_minutes",
        "sedentary_break_minutes",
        "sedentary_cooldown_minutes",
        "include_process_path",
        "include_window_title",
        "include_browser_context",
    }


@pytest.mark.asyncio
async def test_activity_config_defaults_when_settings_missing():
    app = FakeApp(runtime_settings={})
    response = await GuiHttpServer(app_ref=app)._handle_activity_config(FakeRequest())
    payload = json.loads(response.text)
    data = payload["data"]
    assert data["monitor_enabled"] is True
    assert data["sedentary_reminder_minutes"] == 60
    assert data["sedentary_break_minutes"] == 5
    assert data["sedentary_cooldown_minutes"] == 60
    assert data["include_process_path"] is False
    assert data["include_window_title"] is False
    assert data["include_browser_context"] is False
    assert data["revision"] == 0


def test_notify_activity_config_changed_uses_capability_channel():
    app = FakeApp()
    app.notify_activity_config_changed(7)
    assert app.gui_ws_server.capability_emitted == [
        (
            "activity.config.v1",
            {"type": "activity_config_changed", "revision": 7},
        )
    ]
