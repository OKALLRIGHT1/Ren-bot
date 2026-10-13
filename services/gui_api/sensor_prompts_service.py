"""Screen-interjection prompt templates for /gui HTTP."""

from __future__ import annotations

from typing import Any, Dict

from modules.sensor_prompts import (
    PROMPT_FIELDS,
    format_template,
    load_sensor_prompts,
    save_sensor_prompts,
)

PREVIEW_VALUES = {
    "clean_title": "Live2D Enhanced",
    "category": "self",
    "count": 1,
    "max_chars": 15,
    "active_name": "五十铃怜",
    "costume_line": "- 你当前穿着/模型服装：校服。",
    "description": "窗口里是代码编辑器，正在改 chat_service.py。",
    "context_block": "",
    "self_awareness_hint": "【Live2D自我识别】（预览）",
    "emo_tags": "[smile]",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _field_meta() -> list[dict[str, str]]:
    return [
        {
            "key": str(field["key"]),
            "title": str(field["title"]),
            "desc": str(field["desc"]),
            "placeholders": str(field.get("placeholders") or ""),
            "default": str(field.get("default") or ""),
        }
        for field in PROMPT_FIELDS
    ]


class SensorPromptsGuiService:
    def get_settings(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "prompts": load_sensor_prompts(),
                "fields": _field_meta(),
            },
        }

    def save_settings(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        incoming = _as_dict(payload)
        prompts = incoming.get("prompts")
        if not isinstance(prompts, dict):
            prompts = incoming
        saved = save_sensor_prompts(prompts)
        return {"ok": True, "data": {"prompts": saved, "fields": _field_meta()}}

    def preview(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        incoming = _as_dict(payload)
        template = str(incoming.get("template") or "")
        return {
            "ok": True,
            "data": {"preview": format_template(template, **PREVIEW_VALUES)},
        }
