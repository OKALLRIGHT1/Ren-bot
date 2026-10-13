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

# Direct user chat from the desktop HUD / mic / in-process GUI.
HUD_USER_SOURCES = frozenset(
    {
        "text_input",
        "tauri_gui",
        "qt_gui",
        "gui",
        "local",
        "hud",
        "voice",
    }
)

AMBIENT_SOURCES = frozenset(
    {
        "desktop",
        "screen_sensor",
        "music_sensor",
    }
)

LOCAL_LIVE2D_SOURCES = frozenset({*HUD_USER_SOURCES, *AMBIENT_SOURCES})

REMOTE_CHAT_SOURCES = {
    "qq_gateway",
    "napcat_qq",
}


def normalize_source(source: str) -> str:
    return str(source or "").strip().lower()


def is_hud_user_source(source: str) -> bool:
    return normalize_source(source) in HUD_USER_SOURCES


def is_local_actor_source(source: str) -> bool:
    key = normalize_source(source)
    return not key or key in LOCAL_LIVE2D_SOURCES

@dataclass(slots=True)
class MessageContext:
    source: str = "text_input"
    channel: str = "local"
    metadata: Dict[str, Any] = field(default_factory=dict)


def is_live2d_enabled_for_source(source: str) -> bool:
    source = normalize_source(source) or "text_input"
    # Only remote QQ channels skip the desktop character. HUD / Tauri GUI
    # used to be omitted from the local whitelist, so chat had no bubble or mouth.
    if source in REMOTE_CHAT_SOURCES:
        return False
    return True


def is_ui_append_enabled_for_source(source: str) -> bool:
    source = normalize_source(source) or "text_input"
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
