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
            status_text = str(status or "")
            if status_text == "active" and hasattr(core, "list_current_memory_records"):
                rows = core.list_current_memory_records(limit=int(limit or 500))
            else:
                rows = core.list_memory_records(status=status_text, limit=int(limit or 500))
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
        persons = self._collect_persons(core, rows)
        categories = self.categories_payload(counts)
        return {
            "ok": True,
            "data": {
                "records": [_client_record(row) for row in filtered],
                "categories": categories,
                "category_tree": self._category_tree(categories),
                "persons": persons,
                "selected_category": category_id,
                "selected_person": person_id,
            },
        }

    def get_profile_overview(
        self,
        *,
        person_id: str = "owner",
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Person/character archive view: records grouped by category tree."""
        person_id = str(person_id or "owner").strip() or "owner"
        listed = self.list_core_records(
            status="active",
            person_id=person_id,
            category_id="all",
            query="",
            limit=limit,
        )
        if not listed.get("ok"):
            return listed
        payload = listed.get("data") or {}
        records = list(payload.get("records") or [])
        categories = list(payload.get("categories") or [])
        persons = list(payload.get("persons") or [])
        by_category: Dict[str, List[Dict[str, Any]]] = {}
        for row in records:
            cat = str(row.get("category") or "uncategorized").strip() or "uncategorized"
            by_category.setdefault(cat, []).append(row)

        groups: List[Dict[str, Any]] = []
        for category in categories:
            cid = str(category.get("id") or "")
            if not cid or cid == "all":
                continue
            parent_id = str(category.get("parent_id") or "")
            # Parent categories with children only carry rollup counts; leaf/content
            # groups hold actual rows (plus parent-only categories like dislikes).
            children = [
                item
                for item in categories
                if str(item.get("parent_id") or "") == cid
            ]
            rows = list(by_category.get(cid) or [])
            if children and not rows:
                # still expose parent shell for collapsible UI
                groups.append(
                    {
                        "id": cid,
                        "label": category.get("label") or cid,
                        "parent_id": parent_id,
                        "count": int(category.get("count") or 0),
                        "is_parent": True,
                        "records": [],
                        "children": [
                            {
                                "id": str(child.get("id") or ""),
                                "label": child.get("label") or child.get("id"),
                                "parent_id": cid,
                                "count": int(child.get("count") or 0),
                                "records": list(by_category.get(str(child.get("id") or "")) or []),
                            }
                            for child in children
                        ],
                    }
                )
                continue
            if children:
                groups.append(
                    {
                        "id": cid,
                        "label": category.get("label") or cid,
                        "parent_id": parent_id,
                        "count": int(category.get("count") or 0),
                        "is_parent": True,
                        "records": rows,
                        "children": [
                            {
                                "id": str(child.get("id") or ""),
                                "label": child.get("label") or child.get("id"),
                                "parent_id": cid,
                                "count": int(child.get("count") or 0),
                                "records": list(by_category.get(str(child.get("id") or "")) or []),
                            }
                            for child in children
                        ],
                    }
                )
            elif not parent_id:
                groups.append(
                    {
                        "id": cid,
                        "label": category.get("label") or cid,
                        "parent_id": "",
                        "count": int(category.get("count") or len(rows)),
                        "is_parent": False,
                        "records": rows,
                        "children": [],
                    }
                )

        person_label = next(
            (
                str(item.get("label") or item.get("id") or "")
                for item in persons
                if str(item.get("id") or "") == person_id
            ),
            "我" if person_id == "owner" else person_id,
        )
        return {
            "ok": True,
            "data": {
                "person_id": person_id,
                "person_label": person_label,
                "persons": persons,
                "record_count": len(records),
                "groups": groups,
                "categories": categories,
                "category_tree": self._category_tree(categories),
            },
        }

    def _category_tree(self, categories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        nodes = [dict(item) for item in categories if str(item.get("id") or "")]
        children_map: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            parent = str(node.get("parent_id") or "")
            children_map.setdefault(parent, []).append(node)
        def attach(parent_id: str) -> List[Dict[str, Any]]:
            result = []
            for node in children_map.get(parent_id, []):
                item = dict(node)
                item["children"] = attach(str(item.get("id") or ""))
                result.append(item)
            return result
        # Top-level: no parent, preserve declared order via original list.
        top = []
        for node in nodes:
            if str(node.get("parent_id") or ""):
                continue
            item = dict(node)
            item["children"] = attach(str(item.get("id") or ""))
            top.append(item)
        return top

    def _collect_persons(self, core: Any, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build UI person list: owner + characters + known contacts + subjects in data."""
        by_id: Dict[str, Dict[str, Any]] = {}

        def put(person_id: str, label: str = "", *, kind: str = "") -> None:
            pid = str(person_id or "").strip()
            if not pid:
                return
            current = by_id.get(pid) or {"id": pid, "label": "", "kind": ""}
            text = str(label or "").strip()
            if text and (not current.get("label") or current.get("label") == current.get("id")):
                current["label"] = text
            if kind and not current.get("kind"):
                current["kind"] = kind
            if not current.get("label"):
                current["label"] = pid
            by_id[pid] = current

        put("owner", "我", kind="owner")

        # Known contacts from persons table (QQ members, etc.)
        try:
            for item in list(core.list_persons() or []):
                if not isinstance(item, dict):
                    continue
                pid = str(
                    item.get("id")
                    or item.get("person_id")
                    or ""
                ).strip()
                label = str(
                    item.get("label")
                    or item.get("display_name")
                    or item.get("name")
                    or ""
                ).strip()
                put(pid, label, kind=str(item.get("relationship") or item.get("kind") or "person"))
        except Exception:
            pass

        # Live2D characters each own a character:{id} memory scope.
        try:
            from modules.character_manager import character_manager

            characters = dict(character_manager.get_all_characters() or {})
        except Exception:
            characters = {}
        for character_id, payload in characters.items():
            cid = str(character_id or "").strip()
            if not cid:
                continue
            cfg = payload if isinstance(payload, dict) else {}
            name = str(cfg.get("name") or cid).strip() or cid
            put(f"character:{cid}", name, kind="character")

        # Any subject already present on records (including unrepaired legacy ids).
        for row in rows or []:
            subject = str((row or {}).get("subject_id") or "").strip()
            if not subject:
                continue
            if subject.startswith("character:"):
                label = subject.split(":", 1)[1]
                put(subject, label, kind="character")
            else:
                put(subject, subject, kind="subject")

        def sort_key(item: Dict[str, Any]) -> tuple:
            pid = str(item.get("id") or "")
            kind = str(item.get("kind") or "")
            if pid == "owner":
                return (0, "", "")
            if kind == "character" or pid.startswith("character:"):
                return (1, str(item.get("label") or pid).lower(), pid)
            return (2, str(item.get("label") or pid).lower(), pid)

        return [by_id[key] for key in sorted(by_id.keys(), key=lambda k: sort_key(by_id[k]))]

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
            selection = self.get_embedding_selection().get("data") or {}
            return {
                "ok": True,
                "data": {
                    "available": False,
                    "rebuild_required": False,
                    "indexed_count": 0,
                    "pending_count": 0,
                    "model": "",
                    "message": "brain_unavailable",
                    "embedding_selection": selection,
                },
            }
        try:
            data = dict(brain.get_memory_vector_status() or {})
            data["available"] = True
            selection = self.get_embedding_selection()
            if selection.get("ok"):
                data["embedding_selection"] = selection.get("data") or {}
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

    def test_embedding(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = _as_dict(payload)
        model_ids = body.get("model_ids")
        model_id = str(body.get("model_id") or "").strip()
        # Prefer explicit draft queue from UI; otherwise exercise live brain path.
        if model_ids is not None or model_id:
            return self._test_embedding_queue(model_ids=model_ids, model_id=model_id)
        brain = self._brain
        if brain is None or not hasattr(brain, "test_embedding_connection"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            data = dict(brain.test_embedding_connection() or {})
            return {"ok": True, "data": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_embedding_selection(self) -> Dict[str, Any]:
        try:
            from config import EMBEDDING_CONFIG, MODELS
            from modules.embeddings import (
                embedding_model_ids_from_runtime,
                resolve_embedding_config,
            )
            from modules.model_catalog import list_model_options
            from modules.ollama_service import ollama_status
            from modules.runtime_settings import load_runtime_settings
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "import_failed"}

        runtime = load_runtime_settings()
        chain = embedding_model_ids_from_runtime(runtime)
        candidates = list_model_options(MODELS, purposes="embedding")
        resolved = None
        resolve_error = ""
        try:
            resolved = resolve_embedding_config(
                model_ids=chain,
                models=MODELS,
                legacy_config=EMBEDDING_CONFIG,
            )
        except Exception as exc:
            resolve_error = str(exc)

        ollama = {}
        try:
            ollama = ollama_status(runtime)
        except Exception as exc:
            ollama = {"ok": False, "running": False, "message": str(exc)}

        return {
            "ok": True,
            "data": {
                "model_ids": chain,
                "model_id": chain[0] if chain else "",
                "candidates": candidates,
                "ollama_autostart_enabled": bool(
                    runtime.get("ollama_autostart_enabled", False)
                ),
                "ollama": ollama,
                "resolved": {
                    "source": getattr(resolved, "source", ""),
                    "model_id": getattr(resolved, "model_id", ""),
                    "model_name": getattr(resolved, "model_name", ""),
                    "provider": getattr(resolved, "provider", ""),
                    "api_url": getattr(resolved, "api_url", ""),
                    "expected_dimension": getattr(resolved, "expected_dimension", None),
                    "chain_model_ids": list(getattr(resolved, "chain_model_ids", ()) or ()),
                    "enabled": bool(getattr(resolved, "enabled", False)),
                }
                if resolved is not None
                else {},
                "resolve_error": resolve_error,
                "legacy": {
                    "enabled": bool((EMBEDDING_CONFIG or {}).get("enabled", False)),
                    "provider": str((EMBEDDING_CONFIG or {}).get("provider") or ""),
                    "api_url": str((EMBEDDING_CONFIG or {}).get("api_url") or ""),
                    "model_name": str(
                        (EMBEDDING_CONFIG or {}).get("model_name")
                        or (EMBEDDING_CONFIG or {}).get("model")
                        or ""
                    ),
                    "expected_dimension": (EMBEDDING_CONFIG or {}).get("expected_dimension"),
                },
            },
        }

    def save_embedding_selection(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        try:
            from config import EMBEDDING_CONFIG, MODELS
            from modules.embeddings import resolve_embedding_config
            from modules.runtime_settings import (
                save_embedding_model_selection,
                save_ollama_autostart,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "import_failed"}

        if "ollama_autostart_enabled" in body and "model_ids" not in body and "model_id" not in body:
            try:
                save_ollama_autostart(bool(body.get("ollama_autostart_enabled")))
            except Exception as exc:
                return {"ok": False, "error": str(exc) or "save_failed"}
            return self.get_embedding_selection()

        if "model_ids" in body:
            model_ids = body.get("model_ids")
        elif "model_id" in body:
            model_ids = body.get("model_id")
        else:
            return {"ok": False, "error": "empty_selection"}

        try:
            resolve_embedding_config(
                model_ids=model_ids if not isinstance(model_ids, str) else [model_ids],
                models=MODELS,
                legacy_config=EMBEDDING_CONFIG,
            )
            save_embedding_model_selection(model_ids=model_ids)
            if "ollama_autostart_enabled" in body:
                save_ollama_autostart(bool(body.get("ollama_autostart_enabled")))
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        return self.get_embedding_selection()

    def _test_embedding_queue(
        self,
        *,
        model_ids: Any = None,
        model_id: str = "",
    ) -> Dict[str, Any]:
        try:
            from config import EMBEDDING_CONFIG, MODELS
            from modules.embeddings import build_configured_embedding_service
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "import_failed"}

        runtime: Dict[str, Any] = {}
        if model_ids is not None:
            runtime["embedding_model_ids"] = model_ids
        elif model_id:
            runtime["embedding_model_id"] = model_id
        try:
            service = build_configured_embedding_service(
                models=MODELS,
                runtime_settings=runtime,
                legacy_config=EMBEDDING_CONFIG,
            )
            service.embed(["Live2D-Suzu embedding connection test"])
            return {"ok": True, "data": dict(service.status() or {})}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "test_failed"}

    def get_ollama_status(self) -> Dict[str, Any]:
        try:
            from modules.ollama_service import ollama_status
            from modules.runtime_settings import load_runtime_settings
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "import_failed"}
        try:
            return {"ok": True, "data": ollama_status(load_runtime_settings())}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "status_failed"}

    def ensure_ollama(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = _as_dict(payload)
        try:
            from modules.ollama_service import ensure_ollama_service
            from modules.runtime_settings import load_runtime_settings, save_ollama_autostart
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "import_failed"}
        try:
            if "ollama_autostart_enabled" in body:
                save_ollama_autostart(bool(body.get("ollama_autostart_enabled")))
            settings = load_runtime_settings()
            result = ensure_ollama_service(
                settings,
                force=bool(body.get("force", True)),
                wait_seconds=float(body.get("wait_seconds") or 12),
            )
            if not result.get("running"):
                return {
                    "ok": False,
                    "error": str(result.get("error") or "ollama_not_running"),
                    "data": result,
                }
            return {"ok": True, "data": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "start_failed"}

    def list_transcript(
        self,
        *,
        role: str = "",
        query: str = "",
        limit: int = 300,
        offset: int = 0,
    ) -> Dict[str, Any]:
        core = self._safe_core()
        store = getattr(core, "store", None) if core is not None else None
        if store is None or not hasattr(store, "list_transcript"):
            return {"ok": False, "error": "store_unavailable"}
        try:
            rows = store.list_transcript(
                role=role or None,
                query=query,
                limit=int(limit or 300),
                offset=int(offset or 0),
            )
            return {"ok": True, "data": {"records": list(rows or [])}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def query_vector(
        self,
        query: str,
        *,
        person_id: str = "owner",
        limit: int = 10,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "error": "empty_query"}
        brain = self._brain
        if brain is None or not hasattr(brain, "query_memory_vector"):
            return {"ok": False, "error": "brain_unavailable"}
        try:
            rows = brain.query_memory_vector(
                query,
                person_id=str(person_id or "owner").strip() or "owner",
                limit=int(limit or 10),
            )
            return {"ok": True, "data": {"records": list(rows or [])}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list_legacy_vectors(
        self,
        *,
        query: str = "",
        limit: int = 30,
    ) -> Dict[str, Any]:
        collection = self._legacy_vector_collection()
        if collection is None:
            return {"ok": False, "error": "legacy_vector_unavailable"}
        query = str(query or "").strip()
        limit = max(1, int(limit or 30))
        try:
            if query:
                result = collection.query(
                    query_texts=[query],
                    n_results=limit,
                    include=["documents", "metadatas", "distances"],
                )
                ids = (result.get("ids") or [[]])[0]
                documents = (result.get("documents") or [[]])[0]
                metadata = (result.get("metadatas") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                rows = [
                    {
                        "id": item_id,
                        "document": documents[index] if index < len(documents) else "",
                        "metadata": metadata[index] if index < len(metadata) else {},
                        "distance": distances[index] if index < len(distances) else None,
                    }
                    for index, item_id in enumerate(ids)
                ]
            else:
                result = collection.get(include=["documents", "metadatas"], limit=limit)
                ids = result.get("ids") or []
                documents = result.get("documents") or []
                metadata = result.get("metadatas") or []
                rows = [
                    {
                        "id": item_id,
                        "document": documents[index] if index < len(documents) else "",
                        "metadata": metadata[index] if index < len(metadata) else {},
                        "distance": None,
                    }
                    for index, item_id in enumerate(ids)
                ]
            count = 0
            try:
                count = int(collection.count() or 0)
            except Exception:
                count = len(rows)
            return {"ok": True, "data": {"records": rows, "count": count}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _legacy_vector_collection(self):
        cached = getattr(self, "_legacy_collection", None)
        if cached is not None:
            return cached
        try:
            import chromadb

            from config import EMBEDDING_CONFIG, MEMORY_DB_PATH, MODELS
            from modules.embeddings import (
                ChromaEmbeddingFunction,
                build_configured_embedding_service,
            )
            from modules.runtime_settings import load_runtime_settings
        except Exception:
            return None
        service = getattr(self._brain, "embedding_service", None) if self._brain is not None else None
        if service is None:
            service = build_configured_embedding_service(
                models=MODELS,
                runtime_settings=load_runtime_settings(),
                legacy_config=EMBEDDING_CONFIG,
            )
        client = chromadb.PersistentClient(path=MEMORY_DB_PATH)
        collection = client.get_or_create_collection(
            name="waifu_memory_advanced",
            embedding_function=ChromaEmbeddingFunction(service),
        )
        self._legacy_collection = collection
        return collection
