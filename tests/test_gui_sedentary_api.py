from __future__ import annotations

from typing import Any, Dict

from services.gui_api.sedentary_service import SedentaryGuiService


class FakeRuntime:
    def __init__(self, data: Dict[str, Any] | None = None) -> None:
        self.data = dict(data or {})
        self.saved: list[Dict[str, Any]] = []

    def load(self) -> Dict[str, Any]:
        return dict(self.data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.data.update(patch or {})
        self.saved.append(dict(self.data))
        return dict(self.data)


class FakeApp:
    def __init__(self) -> None:
        self.applied: list[Dict[str, Any]] = []
        self.apply_error = ""

    def apply_external_settings(self, settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = dict(settings or {})
        self.applied.append(payload)
        if self.apply_error:
            return {"error": self.apply_error}
        return {"ok": True, "applied": True}


def test_get_sedentary_settings_uses_defaults_and_runtime():
    runtime = FakeRuntime(
        {
            "sedentary_reminder_minutes": 45,
            "sedentary_popup_enabled": False,
            "sedentary_popup_title": "起来动动",
        }
    )
    service = SedentaryGuiService(
        load_runtime=runtime.load,
        update_runtime=runtime.update,
        defaults={
            "sedentary_reminder_minutes": 60,
            "sedentary_break_minutes": 5,
            "sedentary_cooldown_minutes": 30,
            "sedentary_popup_enabled": True,
            "sedentary_status_visible": True,
            "sedentary_popup_title": "该起来活动一下了",
            "sedentary_popup_message": "你已经连续使用 {app_name} {active_minutes} 分钟。",
            "sedentary_popup_image_path": "",
            "sedentary_popup_snooze_minutes": 10,
            "sedentary_popup_auto_close_seconds": 20,
        },
    )
    result = service.get_settings()
    assert result["ok"] is True
    data = result["data"]
    assert data["sedentary_reminder_minutes"] == 45
    assert data["sedentary_break_minutes"] == 5
    assert data["sedentary_popup_enabled"] is False
    assert data["sedentary_popup_title"] == "起来动动"
    assert data["preview"]["message"]


def test_save_sedentary_settings_updates_runtime_and_applies():
    runtime = FakeRuntime({"activity_config_revision": 2})
    app = FakeApp()
    service = SedentaryGuiService(
        load_runtime=runtime.load,
        update_runtime=runtime.update,
        apply_settings=app.apply_external_settings,
        defaults={
            "sedentary_reminder_minutes": 60,
            "sedentary_break_minutes": 5,
            "sedentary_cooldown_minutes": 30,
            "sedentary_popup_enabled": True,
            "sedentary_status_visible": True,
            "sedentary_popup_title": "该起来活动一下了",
            "sedentary_popup_message": "你已经连续使用 {app_name} {active_minutes} 分钟。",
            "sedentary_popup_image_path": "",
            "sedentary_popup_snooze_minutes": 10,
            "sedentary_popup_auto_close_seconds": 20,
        },
    )
    saved = service.save_settings(
        {
            "sedentary_reminder_minutes": 90,
            "sedentary_break_minutes": 8,
            "sedentary_cooldown_minutes": 40,
            "sedentary_popup_enabled": True,
            "sedentary_status_visible": False,
            "sedentary_popup_title": "休息一下",
            "sedentary_popup_message": "{app_name} 已用 {active_minutes} 分钟",
            "sedentary_popup_image_path": "assets/rest.png",
            "sedentary_popup_snooze_minutes": 15,
            "sedentary_popup_auto_close_seconds": 0,
        }
    )
    assert saved["ok"] is True
    assert saved["data"]["sedentary_reminder_minutes"] == 90
    assert saved["data"]["sedentary_status_visible"] is False
    assert runtime.saved
    assert app.applied
    assert app.applied[0]["sedentary_reminder_minutes"] == 90


def test_preview_renders_template_placeholders():
    service = SedentaryGuiService(
        load_runtime=lambda: {
            "sedentary_reminder_minutes": 50,
            "sedentary_popup_title": "起来",
            "sedentary_popup_message": "你已经连续使用 {app_name} {active_minutes} 分钟。",
        },
        defaults={
            "sedentary_reminder_minutes": 60,
            "sedentary_break_minutes": 5,
            "sedentary_cooldown_minutes": 30,
            "sedentary_popup_enabled": True,
            "sedentary_status_visible": True,
            "sedentary_popup_title": "该起来活动一下了",
            "sedentary_popup_message": "你已经连续使用 {app_name} {active_minutes} 分钟。",
            "sedentary_popup_image_path": "",
            "sedentary_popup_snooze_minutes": 10,
            "sedentary_popup_auto_close_seconds": 20,
        },
    )
    preview = service.preview(
        {
            "sedentary_popup_title": "起来",
            "sedentary_popup_message": "你已经连续使用 {app_name} {active_minutes} 分钟。",
            "sedentary_reminder_minutes": 50,
            "app_name": "Chrome",
        }
    )
    assert preview["ok"] is True
    assert preview["data"]["title"] == "起来"
    assert "Chrome" in preview["data"]["message"]
    assert "50" in preview["data"]["message"]
