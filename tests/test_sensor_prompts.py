from __future__ import annotations

from modules import sensor_prompts


def test_format_leaves_unknown_placeholders():
    text = sensor_prompts.format_template("窗口 {clean_title} {missing}", clean_title="Live2D")
    assert text == "窗口 Live2D {missing}"


def test_load_uses_override_and_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        sensor_prompts,
        "load_runtime_settings",
        lambda: {"sensor_prompts": {"self_scene": "看见 {clean_title} 了。"}},
    )
    loaded = sensor_prompts.load_sensor_prompts()
    assert loaded["self_scene"] == "看见 {clean_title} 了。"
    assert loaded["talk_rules"] == sensor_prompts.default_sensor_prompts()["talk_rules"]
    assert "程序窗口" not in loaded["self_scene"]
    assert "实体投影" not in loaded["self_awareness"]
    assert "实体投影" not in loaded["self_awareness_screenshot"]


def test_save_sensor_prompts_writes_runtime_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(sensor_prompts, "load_runtime_settings", lambda: {})
    monkeypatch.setattr(
        sensor_prompts,
        "update_runtime_settings",
        lambda patch: captured.update(patch) or patch,
    )
    saved = sensor_prompts.save_sensor_prompts({"self_scene": "  hi {clean_title}  "})
    assert captured["sensor_prompts"]["self_scene"] == "hi {clean_title}"
    assert saved["self_scene"] == "hi {clean_title}"
    assert saved["talk_rules"] == sensor_prompts.default_sensor_prompts()["talk_rules"]


def test_empty_override_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        sensor_prompts,
        "load_runtime_settings",
        lambda: {"sensor_prompts": {"self_scene": "   "}},
    )
    loaded = sensor_prompts.load_sensor_prompts()
    assert loaded["self_scene"] == sensor_prompts.default_sensor_prompts()["self_scene"]
