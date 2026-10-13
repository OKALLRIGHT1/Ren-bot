"""Host-owned companion / quiet switch.

The floating window and the desktop command both call this. It is not a plugin
and it is not a model tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CompanionPreset:
    key: str
    label: str
    tts_enabled: bool
    dnd_mode: bool


PRESETS = {
    "companion": CompanionPreset("companion", "陪伴", True, False),
    "quiet": CompanionPreset("quiet", "安静", False, True),
}

# Old names still apply so leftover commands don't error.
ALIASES = {
    "eco": "quiet",
    "codex": "quiet",
}


def resolve_preset(name: str) -> Optional[CompanionPreset]:
    key = str(name or "").strip().lower()
    key = ALIASES.get(key, key)
    if not key:
        return None
    return PRESETS.get(key)


def apply_preset(app, name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return "模式名为空"
    preset = resolve_preset(raw)
    if preset is None:
        known = "、".join(item.label for item in PRESETS.values())
        return f"未知模式: {raw}。可用：{known}"
    app.set_tts_enabled(bool(preset.tts_enabled))
    app.set_dnd_mode(bool(preset.dnd_mode))
    voice = "开" if preset.tts_enabled else "关"
    manner = "不问不说" if preset.dnd_mode else "必要时会主动说话"
    return f"已切换到{preset.label}：语音{voice}，{manner}。"
