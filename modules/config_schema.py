from __future__ import annotations

from typing import Any, Dict, List


SECRET_TOKENS = ("api_key", "token", "secret", "password", "access_key")


def infer_field_schema(name: str, raw: Any) -> Dict[str, Any]:
    key = str(name or "")
    info = raw if isinstance(raw, dict) else {"default": raw}
    default = info.get("default") if isinstance(info, dict) else raw
    declared = str(info.get("type") or "").strip().lower() if isinstance(info, dict) else ""
    choices = info.get("options") or info.get("choices") if isinstance(info, dict) else None
    secret = declared in {"secret", "password"} or any(token in key.lower() for token in SECRET_TOKENS)

    if declared:
        value_type = "string" if declared in {"secret", "password"} else declared
    elif isinstance(default, bool):
        value_type = "boolean"
    elif isinstance(default, int) and not isinstance(default, bool):
        value_type = "integer"
    elif isinstance(default, float):
        value_type = "number"
    elif isinstance(default, list):
        value_type = "array"
    elif isinstance(default, dict):
        value_type = "object"
    else:
        value_type = "string"

    ui_type = "password" if secret else value_type
    if choices:
        ui_type = "select"
    if value_type == "boolean":
        ui_type = "switch"
    if value_type in {"array", "object"}:
        ui_type = "json"

    return {
        "name": key,
        "type": value_type,
        "ui_type": ui_type,
        "default": default,
        "description": str(info.get("description") or info.get("label") or "") if isinstance(info, dict) else "",
        "choices": choices if isinstance(choices, list) else [],
        "required": bool(info.get("required", False)) if isinstance(info, dict) else False,
        "secret": bool(secret),
    }


def build_plugin_config_schema(trigger: str, config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    fields: List[Dict[str, Any]] = [infer_field_schema(key, value) for key, value in settings.items()]
    return {
        "trigger": str(trigger or cfg.get("trigger") or ""),
        "name": str(cfg.get("name") or trigger or ""),
        "description": str(cfg.get("description") or ""),
        "type": str(cfg.get("type") or "react"),
        "fields": fields,
        "access_control": cfg.get("access_control") if isinstance(cfg.get("access_control"), dict) else {},
    }
