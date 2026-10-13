from __future__ import annotations

from typing import Any, Dict

from services.gui_api.sensor_prompts_service import SensorPromptsGuiService


def test_get_sensor_prompts_includes_field_metadata(monkeypatch):
    prompts = {"persona_extra": "旁边轻轻接一句。", "talk_rules": "最多 {max_chars} 字。"}
    monkeypatch.setattr(
        "services.gui_api.sensor_prompts_service.load_sensor_prompts",
        lambda: prompts,
    )
    result = SensorPromptsGuiService().get_settings()
    assert result["ok"] is True
    data = result["data"]
    assert data["prompts"]["persona_extra"] == "旁边轻轻接一句。"
    keys = [field["key"] for field in data["fields"]]
    assert "persona_extra" in keys
    assert "gatekeeper" in keys
    persona = next(field for field in data["fields"] if field["key"] == "persona_extra")
    assert persona["title"]
    assert "default" in persona


def test_save_sensor_prompts_nested_payload(monkeypatch):
    saved: list[Dict[str, Any]] = []
    monkeypatch.setattr(
        "services.gui_api.sensor_prompts_service.save_sensor_prompts",
        lambda prompts: saved.append(dict(prompts or {})) or dict(prompts or {}),
    )
    result = SensorPromptsGuiService().save_settings(
        {"prompts": {"persona_extra": "改过的总则"}}
    )
    assert result["ok"] is True
    assert saved[0]["persona_extra"] == "改过的总则"
    assert result["data"]["prompts"]["persona_extra"] == "改过的总则"


def test_preview_sensor_prompt_uses_sample_values(monkeypatch):
    monkeypatch.setattr(
        "services.gui_api.sensor_prompts_service.format_template",
        lambda template, **values: template.format_map(values),
    )
    result = SensorPromptsGuiService().preview(
        {"template": "窗口 [{clean_title}]"}
    )
    assert result["ok"] is True
    assert "Live2D Enhanced" in result["data"]["preview"]
