from __future__ import annotations

from pathlib import Path

from services.gui_api.status_screen_service import StatusScreenGuiService


def test_get_and_save_status_screen_config(tmp_path: Path):
    path = tmp_path / "display_state_config.json"
    service = StatusScreenGuiService(
        config_path=path,
        status_text_getter=lambda: "MQTT ready",
    )
    got = service.get_config()
    assert got["ok"] is True
    assert got["data"]["status_text"] == "MQTT ready"
    saved = service.save_config(
        {
            "metric_mode": "custom",
            "metric_text": "CPU 10%",
            "default_icon_bits": "00" * 10,
            "emotion_icons": {"happy": {"icon_bits": "11" * 4, "icon_w": 32, "icon_h": 32}},
        }
    )
    assert saved["ok"] is True
    assert saved["data"]["metric_mode"] == "custom"
    assert saved["data"]["metric_text"] == "CPU 10%"
    assert saved["data"]["has_default_icon"] is True
    assert "happy" in saved["data"]["emotion_keys"]
    assert path.exists()
