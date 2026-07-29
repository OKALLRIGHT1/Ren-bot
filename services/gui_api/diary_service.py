from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def is_diary_episode(row: Dict[str, Any]) -> bool:
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    return "daily_log" in {str(tag).strip() for tag in tags}


def _client_row(row: Dict[str, Any]) -> Dict[str, Any]:
    tags = [str(tag).strip() for tag in (row.get("tags") or []) if str(tag).strip()]
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "summary": str(row.get("summary") or ""),
        "status": str(row.get("status") or "active"),
        "tags": tags,
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


class DiaryGuiService:
    def __init__(
        self,
        *,
        store: Any = None,
        store_factory: Optional[Callable[[], Any]] = None,
        export_root: Optional[Path] = None,
    ) -> None:
        self._store = store
        self._store_factory = store_factory
        self.export_root = Path(export_root or "output")

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        if self._store_factory is not None:
            self._store = self._store_factory()
            return self._store
        raise RuntimeError("memory_store_unavailable")

    def list_diaries(self, *, query: str = "", limit: int = 500) -> Dict[str, Any]:
        try:
            store = self._get_store()
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "memory_store_unavailable"}
        try:
            rows = store.list_episodes(
                status="active",
                query=str(query or ""),
                limit=int(limit or 500),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        diaries = [_client_row(row) for row in rows if is_diary_episode(row)]
        return {"ok": True, "data": {"diaries": diaries, "count": len(diaries)}}

    def get_diary(self, diary_id: str) -> Dict[str, Any]:
        diary_id = str(diary_id or "").strip()
        if not diary_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            store = self._get_store()
            row = store.get_episode(diary_id) if hasattr(store, "get_episode") else None
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not row or not is_diary_episode(row):
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": _client_row(row)}

    def upsert_diary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        if not title or not summary:
            return {"ok": False, "error": "empty_fields"}
        try:
            store = self._get_store()
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "memory_store_unavailable"}
        diary_id = str(payload.get("id") or "").strip()
        current = None
        if diary_id and hasattr(store, "get_episode"):
            try:
                current = store.get_episode(diary_id)
            except Exception:
                current = None
        tags = [str(tag).strip() for tag in (payload.get("tags") or []) if str(tag).strip()]
        if current and isinstance(current.get("tags"), list) and not tags:
            tags = [str(tag).strip() for tag in current.get("tags") or [] if str(tag).strip()]
        if "daily_log" not in tags:
            tags.append("daily_log")
        row = dict(current or {})
        row.update(
            {
                "id": diary_id or row.get("id") or "",
                "title": title,
                "summary": summary,
                "status": str(payload.get("status") or row.get("status") or "active"),
                "tags": tags,
            }
        )
        try:
            saved_id = store.upsert_episode(row)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return self.get_diary(str(saved_id or diary_id))

    def delete_diary(self, diary_id: str) -> Dict[str, Any]:
        diary_id = str(diary_id or "").strip()
        if not diary_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            store = self._get_store()
            ok = bool(store.delete_episode(diary_id))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": {"id": diary_id}}

    def export_markdown(
        self,
        *,
        query: str = "",
        ids: Optional[List[str]] = None,
        path: str = "",
    ) -> Dict[str, Any]:
        listed = self.list_diaries(query=query, limit=1000)
        if not listed.get("ok"):
            return listed
        rows = list(listed["data"]["diaries"])
        selected_ids = {str(item).strip() for item in (ids or []) if str(item).strip()}
        if selected_ids:
            rows = [row for row in rows if row["id"] in selected_ids]
        if not rows:
            return {"ok": False, "error": "no_diaries"}
        export_path = Path(path) if str(path or "").strip() else (
            self.export_root / f"Diary_Export_{datetime.now():%Y%m%d_%H%M}.md"
        )
        try:
            export_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"# 角色日记\n\n> 导出时间：{datetime.now():%Y-%m-%d %H:%M}\n"]
            for row in rows:
                lines.append(f"\n## {row.get('title') or '未命名日记'}\n")
                lines.append(str(row.get("summary") or "").strip())
                lines.append("\n\n---\n")
            export_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "data": {
                "path": str(export_path.resolve()),
                "count": len(rows),
            },
        }
