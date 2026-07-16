from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from services.gui_api.models_service import SecretUpdate


DEFAULTS = {
    "napcat_enabled": False,
    "napcat_webhook_host": "127.0.0.1",
    "napcat_webhook_port": 6700,
    "napcat_webhook_path": "/chat/napcat",
    "napcat_access_token": "",
    "napcat_api_base": "http://127.0.0.1:3000",
    "napcat_api_token": "",
    "napcat_reply_enabled": True,
    "napcat_allow_private": True,
    "napcat_allow_group": True,
    "napcat_group_require_at": True,
    "napcat_owner_user_ids": [],
    "napcat_owner_label": "主人",
    "napcat_image_vision_enabled": True,
    "napcat_image_prompt": "",
    "napcat_voice_reply_enabled": False,
    "napcat_voice_reply_probability": 0.0,
    "napcat_filter_mode": "off",
    "napcat_user_whitelist": [],
    "napcat_user_blacklist": [],
    "napcat_group_whitelist": [],
    "napcat_group_blacklist": [],
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_id_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "")
        items = [part.strip() for part in text.replace("\n", ",").split(",")]
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _int(value: Any, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return int(default)


def _float(value: Any, default: float) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


class QqGatewayGuiService:
    def __init__(
        self,
        *,
        load_runtime: Optional[Callable[[], Dict[str, Any]]] = None,
        update_runtime: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        apply_settings: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._load_runtime = load_runtime
        self._update_runtime = update_runtime
        self._apply_settings = apply_settings
        self.defaults = {**DEFAULTS, **_as_dict(defaults)}

    def _runtime(self) -> Dict[str, Any]:
        if self._load_runtime is None:
            return {}
        try:
            return _as_dict(self._load_runtime())
        except Exception:
            return {}

    def _normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        source = {**self.defaults, **_as_dict(raw)}
        return {
            "napcat_enabled": _bool(source.get("napcat_enabled"), bool(self.defaults["napcat_enabled"])),
            "napcat_webhook_host": str(source.get("napcat_webhook_host") or self.defaults["napcat_webhook_host"]).strip(),
            "napcat_webhook_port": max(1, min(65535, _int(source.get("napcat_webhook_port"), int(self.defaults["napcat_webhook_port"])))),
            "napcat_webhook_path": str(source.get("napcat_webhook_path") or self.defaults["napcat_webhook_path"]).strip() or "/chat/napcat",
            "napcat_access_token": str(source.get("napcat_access_token") or ""),
            "napcat_api_base": str(source.get("napcat_api_base") or self.defaults["napcat_api_base"]).strip(),
            "napcat_api_token": str(source.get("napcat_api_token") or ""),
            "napcat_reply_enabled": _bool(source.get("napcat_reply_enabled"), True),
            "napcat_allow_private": _bool(source.get("napcat_allow_private"), True),
            "napcat_allow_group": _bool(source.get("napcat_allow_group"), True),
            "napcat_group_require_at": _bool(source.get("napcat_group_require_at"), True),
            "napcat_owner_user_ids": _parse_id_list(source.get("napcat_owner_user_ids")),
            "napcat_owner_label": str(source.get("napcat_owner_label") or "主人").strip() or "主人",
            "napcat_image_vision_enabled": _bool(source.get("napcat_image_vision_enabled"), True),
            "napcat_image_prompt": str(source.get("napcat_image_prompt") or ""),
            "napcat_voice_reply_enabled": _bool(source.get("napcat_voice_reply_enabled"), False),
            "napcat_voice_reply_probability": max(
                0.0,
                min(1.0, _float(source.get("napcat_voice_reply_probability"), 0.0)),
            ),
            "napcat_filter_mode": str(source.get("napcat_filter_mode") or "off").strip() or "off",
            "napcat_user_whitelist": _parse_id_list(source.get("napcat_user_whitelist")),
            "napcat_user_blacklist": _parse_id_list(source.get("napcat_user_blacklist")),
            "napcat_group_whitelist": _parse_id_list(source.get("napcat_group_whitelist")),
            "napcat_group_blacklist": _parse_id_list(source.get("napcat_group_blacklist")),
        }

    def _client(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(settings)
        access = str(data.pop("napcat_access_token") or "")
        api = str(data.pop("napcat_api_token") or "")
        data["has_access_token"] = bool(access.strip())
        data["has_api_token"] = bool(api.strip())
        data["napcat_access_token"] = "********" if access.strip() else ""
        data["napcat_api_token"] = "********" if api.strip() else ""
        return data

    def get_settings(self) -> Dict[str, Any]:
        settings = self._normalize(self._runtime())
        return {"ok": True, "data": self._client(settings)}

    def save_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        if not body:
            return {"ok": False, "error": "empty_payload"}
        if self._update_runtime is None:
            return {"ok": False, "error": "runtime_store_unavailable"}
        current = self._normalize(self._runtime())
        merged = dict(current)
        for key in DEFAULTS.keys():
            if key not in body:
                continue
            if key in {"napcat_access_token", "napcat_api_token"}:
                secret = SecretUpdate.parse(body.get(key))
                merged[key] = secret.apply(str(current.get(key) or ""))
            else:
                merged[key] = body.get(key)
        settings = self._normalize(merged)
        try:
            self._update_runtime(settings)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        if self._apply_settings is not None:
            try:
                self._apply_settings(settings)
            except Exception:
                pass
        return {"ok": True, "data": self._client(settings)}
