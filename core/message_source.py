from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

try:
    from config import REMOTE_CHAT_UI_APPEND
except Exception:
    REMOTE_CHAT_UI_APPEND = True

try:
    from modules.runtime_settings import load_runtime_settings
except Exception:
    def load_runtime_settings() -> Dict[str, Any]:
        return {}

LOCAL_LIVE2D_SOURCES = {
    "text_input",
    "voice",
    "codex_input",
    "screen_sensor",
    "music_sensor",
}

REMOTE_CHAT_SOURCES = {
    "qq_gateway",
    "napcat_qq",
}

@dataclass(slots=True)
class MessageContext:
    source: str = "text_input"
    channel: str = "local"
    metadata: Dict[str, Any] = field(default_factory=dict)


def is_live2d_enabled_for_source(source: str) -> bool:
    source = str(source or "text_input").strip().lower()
    if source in REMOTE_CHAT_SOURCES:
        return False
    return source in LOCAL_LIVE2D_SOURCES or source == "text_input"


def is_ui_append_enabled_for_source(source: str) -> bool:
    source = str(source or "text_input").strip().lower()
    if source in REMOTE_CHAT_SOURCES:
        settings = load_runtime_settings()
        value = settings.get("remote_chat_ui_append")
        if isinstance(value, bool):
            return value
        return bool(REMOTE_CHAT_UI_APPEND)
    return True


def build_output_profile(source: str) -> Dict[str, bool]:
    live2d_enabled = is_live2d_enabled_for_source(source)
    return {
        "ui_append": is_ui_append_enabled_for_source(source),
        "show_bubble": live2d_enabled,
        "speak": live2d_enabled,
        "live2d_enabled": live2d_enabled,
    }
