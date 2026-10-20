"""Client connection and renderer settings for Live2D-Suzu."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(
    os.environ.get("SUZU_CLIENT_CONFIG")
    or ROOT / "data" / "client_settings.json"
)

_DEFAULTS: Dict[str, Any] = {
    "backend_ws_url": "ws://127.0.0.1:8096/gui",
    "backend_http_url": "http://127.0.0.1:8097/gui",
    "backend_token": "",
    "live2d_exe": "",
    "auto_start_live2d": True,
}

_LOCK = threading.Lock()
_cache: Dict[str, Any] | None = None


def auto_start_live2d_enabled(data: Dict[str, Any] | None = None) -> bool:
    source = data if isinstance(data, dict) else load()
    value = source.get("auto_start_live2d", True)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    return True


def load() -> Dict[str, Any]:
    global _cache
    with _LOCK:
        if _cache is None:
            data: Dict[str, Any] = dict(_DEFAULTS)
            try:
                if CONFIG_PATH.exists():
                    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        for key in _DEFAULTS:
                            if key in raw:
                                data[key] = raw[key]
            except Exception:
                pass
            _cache = data
        return dict(_cache)


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    global _cache
    with _LOCK:
        current = dict(_DEFAULTS)
        try:
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    current.update({k: v for k, v in raw.items() if k in _DEFAULTS})
        except Exception:
            pass
        for key, value in patch.items():
            if key in _DEFAULTS:
                current[key] = value
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(CONFIG_PATH)
        _cache = dict(current)
        return dict(current)


def effective() -> Dict[str, Any]:
    cfg = load()
    ws_env = os.environ.get("GUI_WS_URL", "").strip()
    http_env = os.environ.get("GUI_HTTP_URL", "").strip()
    token_env = (
        os.environ.get("GUI_TOKEN", "") or os.environ.get("GUI_ACCESS_TOKEN", "")
    ).strip()
    live2d_exe_env = os.environ.get("SUZU_LIVE2D_EXE", "").strip()
    if ws_env:
        cfg["backend_ws_url"] = ws_env
    if http_env:
        cfg["backend_http_url"] = http_env
    if token_env:
        cfg["backend_token"] = token_env
    if live2d_exe_env:
        cfg["live2d_exe"] = live2d_exe_env
    return cfg
