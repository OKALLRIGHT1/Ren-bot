from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from services.gui_api.status_screen_service import (
    StatusScreenGuiService,
    image_bytes_to_icon_payload,
)


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


def _make_png_bytes(color=(0, 0, 0, 255), size=(64, 64)) -> bytes:
    from PIL import Image

    img = Image.new("RGBA", size, color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_bytes_to_icon_payload_black_square():
    payload = image_bytes_to_icon_payload(_make_png_bytes(), size=32)
    assert payload["icon_w"] == 32
    assert payload["icon_h"] == 32
    # 32x32 bits => 128 bytes => 256 hex chars
    assert len(payload["icon_bits"]) == 256
    # solid black should set all bits
    assert set(payload["icon_bits"]) <= set("0123456789abcdef")
    assert "ff" in payload["icon_bits"]
    assert len(payload["icon_rgb565"]) == 32 * 32 * 4
    assert payload["preview_data_url"].startswith("data:image/png;base64,")


def test_convert_image_from_path_and_base64(tmp_path: Path):
    png = tmp_path / "icon.png"
    png.write_bytes(_make_png_bytes(color=(20, 20, 20, 255)))
    service = StatusScreenGuiService(config_path=tmp_path / "cfg.json")

    from_path = service.convert_image(path=str(png), size=32)
    assert from_path["ok"] is True
    assert from_path["data"]["icon_w"] == 32
    assert len(from_path["data"]["icon_bits"]) == 256

    b64 = base64.b64encode(png.read_bytes()).decode("ascii")
    from_b64 = service.convert_image(image_base64=b64, size=32)
    assert from_b64["ok"] is True
    assert from_b64["data"]["icon_bits"] == from_path["data"]["icon_bits"]

    missing = service.convert_image(path=str(tmp_path / "nope.png"))
    assert missing["ok"] is False
    assert missing["error"] == "image_not_found"

    empty = service.convert_image()
    assert empty["ok"] is False
    assert empty["error"] == "empty_image"
