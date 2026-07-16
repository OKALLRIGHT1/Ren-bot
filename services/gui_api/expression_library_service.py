from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


RUNTIME_KEYS = (
    "expression_library_enabled",
    "expression_library_use_in_chat",
    "expression_library_use_in_screen",
    "expression_library_max_prompt_items",
)

DEFAULT_RUNTIME = {
    "expression_library_enabled": True,
    "expression_library_use_in_chat": True,
    "expression_library_use_in_screen": True,
    "expression_library_max_prompt_items": 4,
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _content_list(row: Dict[str, Any]) -> List[str]:
    raw = row.get("content_list")
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw if str(item).strip()]
        if items:
            return items
    example = str(row.get("example") or "").strip()
    return [example] if example else []


def _client_pattern(row: Dict[str, Any]) -> Dict[str, Any]:
    content = _content_list(row)
    return {
        "id": str(row.get("id") or ""),
        "character_id": str(row.get("character_id") or ""),
        "character_name": str(row.get("character_name") or ""),
        "scene": str(row.get("scene") or "chat"),
        "situation": str(row.get("situation") or ""),
        "style": str(row.get("style") or ""),
        "example": content[0] if content else str(row.get("example") or ""),
        "content_list": content,
        "source": str(row.get("source") or "manual"),
        "quality_score": float(row.get("quality_score") or 0),
        "use_count": int(row.get("use_count") or 0),
        "enabled": bool(row.get("enabled", True)),
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
    }


class ExpressionLibraryGuiService:
    def __init__(
        self,
        *,
        store: Any = None,
        store_factory: Optional[Callable[[], Any]] = None,
        load_runtime: Optional[Callable[[], Dict[str, Any]]] = None,
        update_runtime: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._store = store
        self._store_factory = store_factory
        self._load_runtime = load_runtime
        self._update_runtime = update_runtime

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        if self._store_factory is not None:
            self._store = self._store_factory()
            return self._store
        raise RuntimeError("memory_store_unavailable")

    def _runtime_settings(self) -> Dict[str, Any]:
        raw = {}
        if self._load_runtime is not None:
            try:
                raw = _as_dict(self._load_runtime())
            except Exception:
                raw = {}
        max_items = int(raw.get("expression_library_max_prompt_items", DEFAULT_RUNTIME["expression_library_max_prompt_items"]) or 4)
        return {
            "expression_library_enabled": bool(
                raw.get("expression_library_enabled", DEFAULT_RUNTIME["expression_library_enabled"])
            ),
            "expression_library_use_in_chat": bool(
                raw.get("expression_library_use_in_chat", DEFAULT_RUNTIME["expression_library_use_in_chat"])
            ),
            "expression_library_use_in_screen": bool(
                raw.get("expression_library_use_in_screen", DEFAULT_RUNTIME["expression_library_use_in_screen"])
            ),
            "expression_library_max_prompt_items": max(1, min(8, max_items)),
        }

    def list_patterns(
        self,
        *,
        character_name: str = "",
        scene: str = "",
        query: str = "",
        enabled_only: bool = False,
        limit: int = 500,
    ) -> Dict[str, Any]:
        try:
            store = self._get_store()
            rows = store.list_expression_patterns(
                character_name=str(character_name or ""),
                scene=str(scene or ""),
                enabled_only=bool(enabled_only),
                query=str(query or ""),
                limit=int(limit or 500),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "memory_store_unavailable"}
        patterns = [_client_pattern(row) for row in rows]
        return {
            "ok": True,
            "data": {
                "patterns": patterns,
                "count": len(patterns),
                "runtime": self._runtime_settings(),
            },
        }

    def get_pattern(self, pattern_id: str) -> Dict[str, Any]:
        pattern_id = str(pattern_id or "").strip()
        if not pattern_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            store = self._get_store()
            row = store.get_expression_pattern(pattern_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "memory_store_unavailable"}
        if not row:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": _client_pattern(row)}

    def upsert_pattern(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        content = body.get("content_list")
        if not isinstance(content, list):
            content = []
        content = [str(item).strip() for item in content if str(item).strip()]
        example = str(body.get("example") or "").strip()
        if not content and example:
            content = [example]
        style = str(body.get("style") or "").strip()
        if not style and not content:
            return {"ok": False, "error": "empty_fields"}
        row = {
            "id": str(body.get("id") or "").strip(),
            "character_id": str(body.get("character_id") or "").strip(),
            "character_name": str(body.get("character_name") or "").strip(),
            "scene": str(body.get("scene") or "chat").strip().lower() or "chat",
            "situation": str(body.get("situation") or "").strip(),
            "style": style,
            "example": content[0] if content else example,
            "content_list": content,
            "source": str(body.get("source") or "manual").strip() or "manual",
            "quality_score": float(body.get("quality_score") or 0),
            "use_count": int(body.get("use_count") or 0),
            "enabled": bool(body.get("enabled", True)),
            "meta": _as_dict(body.get("meta")),
        }
        try:
            store = self._get_store()
            pattern_id = store.upsert_expression_pattern(row)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return self.get_pattern(str(pattern_id or row["id"]))

    def delete_pattern(self, pattern_id: str) -> Dict[str, Any]:
        pattern_id = str(pattern_id or "").strip()
        if not pattern_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            store = self._get_store()
            ok = bool(store.delete_expression_pattern(pattern_id))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": {"id": pattern_id, "deleted": True}}

    def set_enabled(self, pattern_ids: List[str], enabled: bool) -> Dict[str, Any]:
        ids = [str(item).strip() for item in (pattern_ids or []) if str(item).strip()]
        if not ids:
            return {"ok": False, "error": "empty_ids"}
        updated = 0
        try:
            store = self._get_store()
            for pattern_id in ids:
                row = store.get_expression_pattern(pattern_id)
                if not row:
                    continue
                row = dict(row)
                row["enabled"] = bool(enabled)
                store.upsert_expression_pattern(row)
                updated += 1
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "data": {"updated": updated, "enabled": bool(enabled)}}

    def get_runtime(self) -> Dict[str, Any]:
        return {"ok": True, "data": self._runtime_settings()}

    def save_runtime(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        if not body:
            return {"ok": False, "error": "empty_payload"}
        current = self._runtime_settings()
        patch = dict(current)
        for key in RUNTIME_KEYS:
            if key in body:
                patch[key] = body.get(key)
        if "expression_library_max_prompt_items" in patch:
            try:
                patch["expression_library_max_prompt_items"] = max(
                    1, min(8, int(patch["expression_library_max_prompt_items"]))
                )
            except Exception:
                patch["expression_library_max_prompt_items"] = 4
        for key in (
            "expression_library_enabled",
            "expression_library_use_in_chat",
            "expression_library_use_in_screen",
        ):
            patch[key] = bool(patch.get(key))
        if self._update_runtime is None:
            return {"ok": False, "error": "runtime_store_unavailable"}
        try:
            self._update_runtime(patch)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        return {"ok": True, "data": self._runtime_settings()}
