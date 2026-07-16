from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional

from modules.memory_core.categories import (
    CATEGORIES,
    category_counts,
    category_matches,
    category_options,
    classify_memory_record,
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _client_record(row: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(row or {})
    metadata = _as_dict(data.get("metadata"))
    category = classify_memory_record(data)
    automatic = dict(data)
    auto_meta = dict(metadata)
    auto_meta.pop("category_override", None)
    automatic["metadata"] = auto_meta
    return {
        "id": str(data.get("id") or ""),
        "kind": str(data.get("kind") or "other"),
        "key": str(data.get("key") or ""),
        "subject_id": str(data.get("subject_id") or ""),
        "session_id": str(data.get("session_id") or ""),
        "content": str(data.get("content") or ""),
        "confidence": float(data.get("confidence") or 0),
        "importance": float(data.get("importance") or 0),
        "status": str(data.get("status") or "active"),
        "manual_lock": bool(data.get("manual_lock")),
        "source_type": str(data.get("source_type") or ""),
        "source_id": str(data.get("source_id") or ""),
        "metadata": metadata,
        "category": category,
        "category_override": str(metadata.get("category_override") or ""),
        "auto_category": classify_memory_record(automatic),
        "updated_at": str(data.get("updated_at") or data.get("created_at") or ""),
    }


class MemoryGuiService:
    """Structured Memory Core + vector status for Qt and /gui HTTP."""

    def __init__(
        self,
        *,
        memory_core: Any = None,
        brain: Any = None,
        core_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._memory_core = memory_core
        self._brain = brain
        self._core_factory = core_factory

    def _core(self) -> Any:
        if self._memory_core is not None:
            return self._memory_core
        if self._core_factory is not None:
            self._memory_core = self._core_factory()
            return self._memory_core
        raise RuntimeError("memory_core_unavailable")

    def _safe_core(self) -> Optional[Any]:
        try:
            return self._core()
        except Exception:
            return None

    def categories_payload(self, counts: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
        counts = counts or {}
        rows = []
        for category in CATEGORIES:
            rows.append(
                {
                    "id": category.id,
                    "label": category.label,
                    "parent_id": category.parent_id,
                    "count": int(counts.get(category.id, 0)),
                    "overridable": category.id
                    in {item.id for item in category_options(include_parent=False)},
                }
            )
        return rows

    def list_core_records(
        self,
        *,
        status: str = "active",
        person_id: str = "",
        category_id: str = "all",
        query: str = "",
        limit: int = 500,
    ) -> Dict[str, Any]:
        core = self._safe_core()
        if core is None:
            return {"ok": False, "error": "memory_core_unavailable"}
        try:
            rows = core.list_memory_records(status=str(status or ""), limit=int(limit or 500))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        person_id = str(person_id or "").strip()
        if person_id == "owner":
            rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() in {"", "owner"}
            ]
        elif person_id:
            rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() == person_id
            ]
        counts = category_counts(rows)
        category_id = str(category_id or "all").strip() or "all"
        filtered = [
            row
            for row in rows
            if category_matches(category_id, classify_memory_record(row))
        ]
        query_text = str(query or "").strip().lower()
        if query_text:
            filtered = [
                row
                for row in filtered
                if query_text
                in " ".join(
                    str(row.get(key) or "")
                    for key in ("kind", "key", "content", "source_type", "source_id", "subject_id")
                ).lower()
            ]
        persons = []
        try:
            persons = list(core.list_persons() or [])
        except Exception:
            persons = []
        if not any(str(item.get("id") or "") == "owner" for item in persons):
            persons = [{"id": "owner", "label": "我"}] + persons
        return {
            "ok": True,
            "data": {
                "records": [_client_record(row) for row in filtered],
                "categories": self.categories_payload(counts),
                "persons": persons,
                "selected_category": category_id,
                "selected_person": person_id,
            },
        }

    def get_core_record(self, record_id: str) -> Dict[str, Any]:
        core = self._safe_core()
        if core is None:
            return {"ok": False, "error": "memory_core_unavailable"}
        record_id = str(record_id or "").strip()
        if not record_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            row = core.get_memory_record(record_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not row:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": _client_record(row)}

    def upsert_core_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        core = self._safe_core()
        if core is None:
            return {"ok": False, "error": "memory_core_unavailable"}
        content = str(payload.get("content") or "").strip()
        if not content:
            return {"ok": False, "error": "empty_content"}
        record_id = str(payload.get("id") or payload.get("record_id") or "").strip()
        fields = {
            "kind": str(payload.get("kind") or "other").strip() or "other",
            "key": str(payload.get("key") or "").strip(),
            "subject_id": str(payload.get("subject_id") or "owner").strip() or "owner",
            "session_id": str(payload.get("session_id") or "").strip(),
            "content": content,
            "confidence": float(payload.get("confidence") or 1.0),
            "importance": float(payload.get("importance") or 0.7),
            "manual_lock": bool(payload.get("manual_lock")),
        }
        try:
            if record_id:
                ok = core.update_memory_record(record_id, **fields)
                if not ok:
                    return {"ok": False, "error": "not_found"}
            else:
                record_id = core.upsert_memory_record(
                    **fields,
                    source_type=str(payload.get("source_type") or "manual_gui"),
                    source_id=str(payload.get("source_id") or uuid.uuid4().hex),
                )
            if "category_override" in payload:
                core.set_memory_category_override(
                    record_id, str(payload.get("category_override") or "")
                )
            detail = self.get_core_record(record_id)
            if not detail.get("ok"):
                return detail
            return {"ok": True, "data": detail["data"]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def set_category_override(self, record_id: str, category_id: str) -> Dict[str, Any]:
        core = self._safe_core()
        if core is None:
            return {"ok": False, "error": "memory_core_unavailable"}
        record_id = str(record_id or "").strip()
        if not record_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            ok = core.set_memory_category_override(record_id, str(category_id or ""))
            if not ok:
                return {"ok": False, "error": "not_found"}
            return self.get_core_record(record_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_core_record(self, record_id: str) -> Dict[str, Any]:
        core = self._safe_core()
        if core is None:
            return {"ok": False, "error": "memory_core_unavailable"}
        record_id = str(record_id or "").strip()
        if not record_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            ok = core.delete_memory_record(record_id)
            if not ok:
                return {"ok": False, "error": "not_found"}
            return {"ok": True, "data": {"id": record_id}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def vector_status(self) -> Dict[str, Any]:
        brain = self._brain
        if brain is None or not hasattr(brain, "get_memory_vector_status"):
            return {
                "ok": True,
                "data": {
                    "available": False,
                    "rebuild_required": False,
                    "indexed_count": 0,
                    "pending_count": 0,
                    "model": "",
                    "message": "brain_unavailable",
                },
            }
        try:
            data = dict(brain.get_memory_vector_status() or {})
            data["available"] = True
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def rebuild_vector_index(self) -> Dict[str, Any]:
        brain = self._brain
        if brain is None or not hasattr(brain, "rebuild_memory_vector_index"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            data = dict(brain.rebuild_memory_vector_index() or {})
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def test_embedding(self) -> Dict[str, Any]:
        brain = self._brain
        if brain is None or not hasattr(brain, "test_embedding_connection"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            data = dict(brain.test_embedding_connection() or {})
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
