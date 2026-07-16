from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


DEFAULT_CONFIG = {
    "metric_mode": "auto_ram",
    "metric_text": "",
    "default_icon_bits": "",
    "default_icon_rgb565": "",
    "default_icon_w": 32,
    "default_icon_h": 32,
    "emotion_icons": {},
}


class StatusScreenGuiService:
    def __init__(
        self,
        *,
        config_path: str | Path,
        status_text_getter: Optional[Callable[[], str]] = None,
        publish: Optional[Callable[[Dict[str, Any]], Any]] = None,
        load_config: Optional[Callable[[], Dict[str, Any]]] = None,
        save_config_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.status_text_getter = status_text_getter
        self.publish = publish
        self._load_config = load_config
        self._save_config_fn = save_config_fn

    def _read(self) -> Dict[str, Any]:
        if self._load_config is not None:
            try:
                data = self._load_config()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        if not self.config_path.exists():
            return dict(DEFAULT_CONFIG)
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return dict(DEFAULT_CONFIG)
        return data if isinstance(data, dict) else dict(DEFAULT_CONFIG)

    def _write(self, data: Dict[str, Any]) -> None:
        if self._save_config_fn is not None:
            self._save_config_fn(data)
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _client(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        data = {**DEFAULT_CONFIG, **_as_dict(raw)}
        emotion = _as_dict(data.get("emotion_icons"))
        status_text = ""
        if self.status_text_getter is not None:
            try:
                status_text = str(self.status_text_getter() or "")
            except Exception:
                status_text = ""
        return {
            "metric_mode": str(data.get("metric_mode") or "auto_ram"),
            "metric_text": str(data.get("metric_text") or ""),
            "default_icon_w": int(data.get("default_icon_w") or 32),
            "default_icon_h": int(data.get("default_icon_h") or 32),
            "has_default_icon": bool(str(data.get("default_icon_bits") or "").strip()),
            "emotion_keys": sorted(str(key) for key in emotion.keys()),
            "emotion_count": len(emotion),
            "status_text": status_text,
            # keep raw blobs only for advanced edit
            "default_icon_bits": str(data.get("default_icon_bits") or ""),
            "default_icon_rgb565": str(data.get("default_icon_rgb565") or ""),
            "emotion_icons": emotion,
        }

    def get_config(self) -> Dict[str, Any]:
        return {"ok": True, "data": self._client(self._read())}

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        if not body:
            return {"ok": False, "error": "empty_payload"}
        current = self._read()
        next_cfg = dict(current)
        for key in (
            "metric_mode",
            "metric_text",
            "default_icon_bits",
            "default_icon_rgb565",
            "default_icon_w",
            "default_icon_h",
            "emotion_icons",
        ):
            if key in body:
                next_cfg[key] = body.get(key)
        try:
            self._write(next_cfg)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        return self.get_config()

    def test_publish(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        body = _as_dict(payload)
        if self.publish is None:
            return {"ok": False, "error": "publish_unavailable"}
        cfg = self._read()
        send_payload = {
            "metric_mode": body.get("metric_mode", cfg.get("metric_mode")),
            "metric_text": body.get("metric_text", cfg.get("metric_text")),
            "icon_bits": body.get("icon_bits") or cfg.get("default_icon_bits") or "",
            "icon_rgb565": body.get("icon_rgb565") or cfg.get("default_icon_rgb565") or "",
            "icon_w": body.get("icon_w") or cfg.get("default_icon_w") or 32,
            "icon_h": body.get("icon_h") or cfg.get("default_icon_h") or 32,
        }
        try:
            self.publish(send_payload)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "publish_failed"}
        return {"ok": True, "data": {"published": True, "payload_keys": sorted(send_payload.keys())}}
