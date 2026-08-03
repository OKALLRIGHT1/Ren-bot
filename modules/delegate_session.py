"""Delegate / secondary-brain session event log (data/delegate_session.json)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.json_state_store import JsonListStore, now_iso

DELEGATE_SESSION_PATH = Path("./data/delegate_session.json")
MAX_ITEMS = 200
_store = JsonListStore(DELEGATE_SESSION_PATH, max_items=MAX_ITEMS)


def _load() -> List[Dict[str, Any]]:
    return _store.load()


def _save(items: List[Dict[str, Any]]) -> bool:
    return _store.save(items)


def add_event(
    event_type: str,
    *,
    task_id: str = "",
    user_text: str = "",
    triggers: Optional[List[str]] = None,
    text: str = "",
    meta: Optional[Dict[str, Any]] = None,
):
    _store.append(
        {
            "time": now_iso(),
            "type": str(event_type or "").strip(),
            "task_id": str(task_id or "").strip(),
            "user_text": str(user_text or "")[:600],
            "triggers": list(triggers or [])[:12],
            "text": str(text or "")[:1200],
            "meta": meta or {},
        }
    )


def get_recent(limit: int = 20, *, task_id: str = "") -> List[Dict[str, Any]]:
    if task_id:
        tid = str(task_id or "").strip()
        return _store.recent_filtered(
            limit,
            predicate=lambda item: str(item.get("task_id") or "").strip() == tid,
        )
    return _store.recent(limit)
