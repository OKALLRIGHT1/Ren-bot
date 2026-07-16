from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.config_schema import build_plugin_config_schema, infer_field_schema
from services.gui_api.models_service import SecretUpdate


MASKED = "********"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _setting_default(value: Any) -> Any:
    if isinstance(value, dict) and "default" in value:
        return value.get("default")
    return value


def _is_secret_setting(key: str, value: Any) -> bool:
    field = infer_field_schema(key, value)
    return bool(field.get("secret"))


def _mask_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (settings or {}).items():
        if _is_secret_setting(str(key), value):
            if isinstance(value, dict):
                row = dict(value)
                current = str(row.get("default") or "").strip()
                row["default"] = MASKED if current else ""
                row["has_value"] = bool(current)
                out[key] = row
            else:
                current = str(value or "").strip()
                out[key] = {
                    "type": "secret",
                    "default": MASKED if current else "",
                    "has_value": bool(current),
                }
        else:
            out[key] = value
    return out


def _client_plugin(row: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = _as_dict(config)
    aliases = cfg.get("aliases") if isinstance(cfg.get("aliases"), list) else row.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
    return {
        "trigger": str(row.get("trigger") or cfg.get("trigger") or ""),
        "name": str(row.get("name") or cfg.get("name") or row.get("trigger") or ""),
        "type": str(row.get("type") or cfg.get("type") or "react"),
        "description": str(row.get("description") or cfg.get("description") or ""),
        "enabled": bool(row.get("enabled", cfg.get("enabled", True))),
        "version": str(row.get("version") or ""),
        "author": str(row.get("author") or ""),
        "aliases": [str(item).strip() for item in aliases if str(item).strip()],
        "access_control": _as_dict(row.get("access_control") or cfg.get("access_control")),
        "access_summary": str(row.get("access_summary") or ""),
    }


class PluginsGuiService:
    """Structured plugin list/config helpers shared by Qt and /gui HTTP."""

    def __init__(self, *, manager: Any = None) -> None:
        self.manager = manager

    def list_plugins(self) -> Dict[str, Any]:
        manager = self.manager
        if manager is None:
            return {"ok": False, "error": "plugin_manager_unavailable"}
        try:
            rows = []
            if hasattr(manager, "get_all_plugins_info"):
                rows = manager.get_all_plugins_info() or []
            plugins = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                trigger = str(item.get("trigger") or "").strip()
                config = {}
                if trigger and hasattr(manager, "get_plugin_config"):
                    try:
                        config = manager.get_plugin_config(trigger) or {}
                    except Exception:
                        config = {}
                plugins.append(_client_plugin(item, config if isinstance(config, dict) else {}))
            return {"ok": True, "data": {"plugins": plugins, "count": len(plugins)}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_config(self, trigger: str) -> Dict[str, Any]:
        trigger = str(trigger or "").strip()
        if not trigger:
            return {"ok": False, "error": "invalid_trigger"}
        manager = self.manager
        if manager is None or not hasattr(manager, "get_plugin_config"):
            return {"ok": False, "error": "plugin_manager_unavailable"}
        try:
            config = manager.get_plugin_config(trigger) or {}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not isinstance(config, dict):
            return {"ok": False, "error": "not_found"}
        settings = _as_dict(config.get("settings"))
        client_config = dict(config)
        client_config["settings"] = _mask_settings(settings)
        if hasattr(manager, "get_plugin_config_schema"):
            try:
                schema = manager.get_plugin_config_schema(trigger) or {}
            except Exception:
                schema = build_plugin_config_schema(trigger, config)
        else:
            schema = build_plugin_config_schema(trigger, config)
        if not isinstance(schema, dict):
            schema = {"trigger": trigger, "fields": []}
        # annotate secret fields with has_value for form UX
        fields = []
        for field in list(schema.get("fields") or []):
            if not isinstance(field, dict):
                continue
            row = dict(field)
            name = str(row.get("name") or "")
            if row.get("secret") and name in settings:
                current = _setting_default(settings.get(name))
                row["has_value"] = bool(str(current or "").strip())
                row["default"] = MASKED if row["has_value"] else ""
            fields.append(row)
        schema = dict(schema)
        schema["fields"] = fields
        return {
            "ok": True,
            "data": {
                "trigger": trigger,
                "config": client_config,
                "schema": schema,
                "form": self._form_values(settings, fields),
            },
        }

    def _form_values(self, settings: Dict[str, Any], fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        field_map = {str(field.get("name") or ""): field for field in fields if isinstance(field, dict)}
        for key, raw in settings.items():
            field = field_map.get(str(key), {})
            default = _setting_default(raw)
            if field.get("secret"):
                values[key] = MASKED if str(default or "").strip() else ""
            else:
                values[key] = default
        return values

    def save_settings(self, trigger: str, form_values: Dict[str, Any]) -> Dict[str, Any]:
        trigger = str(trigger or "").strip()
        if not trigger:
            return {"ok": False, "error": "invalid_trigger"}
        manager = self.manager
        if manager is None or not hasattr(manager, "get_plugin_config") or not hasattr(
            manager, "save_plugin_config"
        ):
            return {"ok": False, "error": "plugin_manager_unavailable"}
        try:
            current = manager.get_plugin_config(trigger) or {}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not isinstance(current, dict):
            return {"ok": False, "error": "not_found"}
        config = dict(current)
        settings = _as_dict(config.get("settings"))
        body = _as_dict(form_values)
        secret_values: Dict[str, str] = {}
        for key, raw in settings.items():
            if key not in body:
                continue
            if _is_secret_setting(str(key), raw):
                secret = SecretUpdate.parse(body.get(key))
                current_secret = str(_setting_default(raw) or "").strip()
                next_secret = secret.apply(current_secret)
                secret_values[str(key)] = next_secret
                if isinstance(raw, dict):
                    row = dict(raw)
                    row["default"] = next_secret
                    settings[key] = row
                else:
                    settings[key] = next_secret
            else:
                if isinstance(raw, dict):
                    row = dict(raw)
                    row["default"] = body.get(key)
                    settings[key] = row
                else:
                    settings[key] = body.get(key)
        config["settings"] = settings
        if secret_values:
            config["_secret_values"] = secret_values
        try:
            ok = bool(manager.save_plugin_config(trigger, config))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "save_failed"}
        return self.get_config(trigger)

    def save_raw_config(self, trigger: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        trigger = str(trigger or "").strip()
        if not trigger:
            return {"ok": False, "error": "invalid_trigger"}
        manager = self.manager
        if manager is None or not hasattr(manager, "save_plugin_config"):
            return {"ok": False, "error": "plugin_manager_unavailable"}
        body = _as_dict(payload)
        try:
            current = manager.get_plugin_config(trigger) if hasattr(manager, "get_plugin_config") else {}
            if not isinstance(current, dict):
                current = {}
            # restore masked secrets from current
            current_settings = _as_dict(current.get("settings"))
            next_settings = _as_dict(body.get("settings"))
            secret_values: Dict[str, str] = {}
            for key, raw in current_settings.items():
                if not _is_secret_setting(str(key), raw):
                    continue
                incoming = next_settings.get(key, raw)
                incoming_default = _setting_default(incoming)
                secret = SecretUpdate.parse(incoming_default)
                current_secret = str(_setting_default(raw) or "").strip()
                next_secret = secret.apply(current_secret)
                secret_values[str(key)] = next_secret
                if isinstance(incoming, dict):
                    row = dict(incoming)
                    row["default"] = next_secret
                    next_settings[key] = row
                else:
                    next_settings[key] = next_secret
            body["settings"] = next_settings
            if secret_values:
                body["_secret_values"] = secret_values
            ok = bool(manager.save_plugin_config(trigger, body))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "save_failed"}
        return self.get_config(trigger)
