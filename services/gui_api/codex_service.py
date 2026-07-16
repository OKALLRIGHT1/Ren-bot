from __future__ import annotations

from typing import Any, Callable, Dict, Optional


DEFAULTS = {
    "codex_mode_enabled": False,
    "codex_last_path": "",
    "codex_allow_write": False,
    "codex_allow_exec": False,
    "codex_autorun": False,
    "codex_last_task_id": "",
    "codex_provider": "codex_cli",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


class CodexGuiService:
    def __init__(
        self,
        *,
        load_runtime: Optional[Callable[[], Dict[str, Any]]] = None,
        update_runtime: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._load_runtime = load_runtime
        self._update_runtime = update_runtime

    def _runtime(self) -> Dict[str, Any]:
        if self._load_runtime is None:
            return {}
        try:
            return _as_dict(self._load_runtime())
        except Exception:
            return {}

    def _normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        source = {**DEFAULTS, **_as_dict(raw)}
        return {
            "codex_mode_enabled": _bool(source.get("codex_mode_enabled"), False),
            "codex_last_path": str(source.get("codex_last_path") or "").strip(),
            "codex_allow_write": _bool(source.get("codex_allow_write"), False),
            "codex_allow_exec": _bool(source.get("codex_allow_exec"), False),
            "codex_autorun": _bool(source.get("codex_autorun"), False),
            "codex_last_task_id": str(source.get("codex_last_task_id") or "").strip(),
            "codex_provider": str(source.get("codex_provider") or "codex_cli").strip()
            or "codex_cli",
        }

    def get_settings(self) -> Dict[str, Any]:
        return {"ok": True, "data": self._normalize(self._runtime())}

    def save_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        if not body:
            return {"ok": False, "error": "empty_payload"}
        if self._update_runtime is None:
            return {"ok": False, "error": "runtime_store_unavailable"}
        current = self._normalize(self._runtime())
        merged = dict(current)
        for key in DEFAULTS.keys():
            if key in body:
                merged[key] = body.get(key)
        settings = self._normalize(merged)
        try:
            self._update_runtime(settings)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        return {"ok": True, "data": settings}
