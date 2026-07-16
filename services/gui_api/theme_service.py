from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional


COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_color(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not COLOR_RE.match(text):
        return None
    return text


def _flatten_palette(palette: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in (palette or {}).items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_palette(value, path))
        else:
            color = _normalize_color(value)
            if color is not None:
                out[path] = color
            elif isinstance(value, (int, float, bool, str)):
                out[path] = str(value)
    return out


def _unflatten_palette(flat: Dict[str, Any]) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    for path, value in (flat or {}).items():
        parts = [part for part in str(path).split(".") if part]
        if not parts:
            continue
        cursor = root
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        color = _normalize_color(value)
        cursor[parts[-1]] = color if color is not None else value
    return root


class ThemeGuiService:
    def __init__(
        self,
        *,
        load_runtime: Optional[Callable[[], Dict[str, Any]]] = None,
        update_runtime: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        themes: Optional[Dict[str, Dict[str, Any]]] = None,
        default_theme: str = "",
    ) -> None:
        self._load_runtime = load_runtime
        self._update_runtime = update_runtime
        self.themes = dict(themes or {})
        self.default_theme = str(default_theme or "") or (
            next(iter(self.themes.keys()), "")
        )

    def _runtime(self) -> Dict[str, Any]:
        if self._load_runtime is None:
            return {}
        try:
            return _as_dict(self._load_runtime())
        except Exception:
            return {}

    def _current_theme_name(self, runtime: Dict[str, Any] | None = None) -> str:
        data = runtime if runtime is not None else self._runtime()
        name = str(data.get("theme_name") or self.default_theme or "").strip()
        if name not in self.themes and self.themes:
            name = self.default_theme if self.default_theme in self.themes else next(iter(self.themes))
        return name

    def _merged_palette(self, runtime: Dict[str, Any] | None = None) -> Dict[str, Any]:
        data = runtime if runtime is not None else self._runtime()
        theme_name = self._current_theme_name(data)
        base = dict(self.themes.get(theme_name) or {})
        overrides = _as_dict(data.get("ui_palette"))
        # merge shallow + nested console dicts
        merged = dict(base)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged.get(key) or {})
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
        merged["theme_name"] = theme_name
        return merged

    def list_themes(self) -> Dict[str, Any]:
        runtime = self._runtime()
        current = self._current_theme_name(runtime)
        themes = [{"name": name, "preview": {"accent": _as_dict(palette).get("accent", "")}} for name, palette in self.themes.items()]
        return {
            "ok": True,
            "data": {
                "current": current,
                "themes": themes,
                "palette": self._merged_palette(runtime),
                "flat": _flatten_palette(self._merged_palette(runtime)),
            },
        }

    def get_palette(self) -> Dict[str, Any]:
        runtime = self._runtime()
        palette = self._merged_palette(runtime)
        return {
            "ok": True,
            "data": {
                "theme_name": self._current_theme_name(runtime),
                "palette": palette,
                "flat": _flatten_palette(palette),
            },
        }

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        if not body:
            return {"ok": False, "error": "empty_payload"}
        if self._update_runtime is None:
            return {"ok": False, "error": "runtime_store_unavailable"}
        runtime = self._runtime()
        theme_name = str(body.get("theme_name") or runtime.get("theme_name") or self.default_theme).strip()
        if self.themes and theme_name not in self.themes:
            return {"ok": False, "error": "invalid_theme"}
        ui_palette = body.get("ui_palette")
        if ui_palette is None and isinstance(body.get("flat"), dict):
            ui_palette = _unflatten_palette(body.get("flat") or {})
        if ui_palette is None:
            ui_palette = _as_dict(runtime.get("ui_palette"))
        if not isinstance(ui_palette, dict):
            return {"ok": False, "error": "invalid_palette"}
        patch = {
            "theme_name": theme_name,
            "ui_palette": ui_palette,
        }
        try:
            self._update_runtime(patch)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        return self.get_palette()
