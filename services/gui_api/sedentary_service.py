from __future__ import annotations

from typing import Any, Callable, Dict, Optional


DEFAULT_SEDENTARY = {
    "sedentary_reminder_minutes": 60,
    "sedentary_break_minutes": 5,
    "sedentary_cooldown_minutes": 30,
    "sedentary_popup_enabled": True,
    "sedentary_status_visible": True,
    "sedentary_popup_title": "该起来活动一下了",
    "sedentary_popup_message": "你已经连续使用 {app_name} {active_minutes} 分钟。",
    "sedentary_popup_image_path": "",
    "sedentary_popup_snooze_minutes": 10,
    "sedentary_popup_auto_close_seconds": 20,
}

SEDENTARY_KEYS = tuple(DEFAULT_SEDENTARY.keys())


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_value(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        number = int(value if value is not None else default)
    except Exception:
        number = int(default)
    return max(minimum, number)


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _str_value(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or str(default or "")


def render_sedentary_message(
    template: str,
    *,
    app_name: str = "电脑",
    active_minutes: int = 60,
) -> str:
    text = str(template or "")
    return (
        text.replace("{app_name}", str(app_name or "电脑"))
        .replace("{active_minutes}", str(max(0, int(active_minutes or 0))))
    )


class SedentaryGuiService:
    """Client-safe sedentary settings for Qt and /gui HTTP."""

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
        self.defaults = {**DEFAULT_SEDENTARY, **_as_dict(defaults)}

    def _runtime(self) -> Dict[str, Any]:
        if self._load_runtime is None:
            return {}
        try:
            data = self._load_runtime()
        except Exception:
            return {}
        return _as_dict(data)

    def _normalize(self, raw: Dict[str, Any] | None = None) -> Dict[str, Any]:
        source = {**self.defaults, **_as_dict(raw)}
        return {
            "sedentary_reminder_minutes": _int_value(
                source.get("sedentary_reminder_minutes"),
                int(self.defaults["sedentary_reminder_minutes"]),
                minimum=1,
            ),
            "sedentary_break_minutes": _int_value(
                source.get("sedentary_break_minutes"),
                int(self.defaults["sedentary_break_minutes"]),
                minimum=1,
            ),
            "sedentary_cooldown_minutes": _int_value(
                source.get("sedentary_cooldown_minutes"),
                int(self.defaults["sedentary_cooldown_minutes"]),
                minimum=1,
            ),
            "sedentary_popup_enabled": _bool_value(
                source.get("sedentary_popup_enabled"),
                bool(self.defaults["sedentary_popup_enabled"]),
            ),
            "sedentary_status_visible": _bool_value(
                source.get("sedentary_status_visible"),
                bool(self.defaults["sedentary_status_visible"]),
            ),
            "sedentary_popup_title": _str_value(
                source.get("sedentary_popup_title"),
                str(self.defaults["sedentary_popup_title"]),
            )
            or str(self.defaults["sedentary_popup_title"]),
            "sedentary_popup_message": _str_value(
                source.get("sedentary_popup_message"),
                str(self.defaults["sedentary_popup_message"]),
            )
            or str(self.defaults["sedentary_popup_message"]),
            "sedentary_popup_image_path": _str_value(
                source.get("sedentary_popup_image_path"),
                str(self.defaults["sedentary_popup_image_path"]),
            ),
            "sedentary_popup_snooze_minutes": _int_value(
                source.get("sedentary_popup_snooze_minutes"),
                int(self.defaults["sedentary_popup_snooze_minutes"]),
                minimum=1,
            ),
            "sedentary_popup_auto_close_seconds": _int_value(
                source.get("sedentary_popup_auto_close_seconds"),
                int(self.defaults["sedentary_popup_auto_close_seconds"]),
                minimum=0,
            ),
        }

    def _client_payload(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(settings)
        reminder = int(data["sedentary_reminder_minutes"])
        data["preview"] = {
            "title": data["sedentary_popup_title"],
            "message": render_sedentary_message(
                data["sedentary_popup_message"],
                app_name="电脑",
                active_minutes=reminder,
            ),
            "image_path": data["sedentary_popup_image_path"],
            "snooze_minutes": data["sedentary_popup_snooze_minutes"],
            "auto_close_seconds": data["sedentary_popup_auto_close_seconds"],
            "app_name": "电脑",
            "active_minutes": reminder,
        }
        return data

    def get_settings(self) -> Dict[str, Any]:
        settings = self._normalize(self._runtime())
        return {"ok": True, "data": self._client_payload(settings)}

    def save_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        if not body:
            return {"ok": False, "error": "empty_payload"}
        current = self._normalize(self._runtime())
        merged = dict(current)
        for key in SEDENTARY_KEYS:
            if key in body:
                merged[key] = body.get(key)
        settings = self._normalize(merged)
        if self._update_runtime is None:
            return {"ok": False, "error": "runtime_store_unavailable"}
        try:
            self._update_runtime(settings)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        apply_result: Dict[str, Any] = {}
        if self._apply_settings is not None:
            try:
                raw = self._apply_settings(settings)
                apply_result = _as_dict(raw)
            except Exception as exc:
                apply_result = {"error": str(exc)}
        if apply_result.get("error"):
            return {
                "ok": False,
                "error": f"apply_failed: {apply_result.get('error')}",
                "data": self._client_payload(settings),
            }
        data = self._client_payload(settings)
        data["applied"] = True
        return {"ok": True, "data": data}

    def preview(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        body = _as_dict(payload)
        base = self._normalize({**self._runtime(), **body})
        app_name = _str_value(body.get("app_name"), "电脑") or "电脑"
        active_minutes = _int_value(
            body.get("active_minutes"),
            int(base["sedentary_reminder_minutes"]),
            minimum=0,
        )
        return {
            "ok": True,
            "data": {
                "title": base["sedentary_popup_title"],
                "message": render_sedentary_message(
                    base["sedentary_popup_message"],
                    app_name=app_name,
                    active_minutes=active_minutes,
                ),
                "image_path": base["sedentary_popup_image_path"],
                "snooze_minutes": base["sedentary_popup_snooze_minutes"],
                "auto_close_seconds": base["sedentary_popup_auto_close_seconds"],
                "app_name": app_name,
                "active_minutes": active_minutes,
                "settings": base,
            },
        }
