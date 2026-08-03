"""Delegate task state map (data/delegate_tasks.json)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.json_state_store import JsonTaskStore, now_iso

DELEGATE_TASKS_PATH = Path("./data/delegate_tasks.json")
MAX_TASKS = 200
_store = JsonTaskStore(DELEGATE_TASKS_PATH, max_tasks=MAX_TASKS)


def _now() -> str:
    return now_iso()


def _load() -> Dict[str, Any]:
    return _store.load()


def _save(data: Dict[str, Any]) -> bool:
    return _store.save(data)


def set_task_state(
    task_id: str,
    state: str,
    *,
    summary: str = "",
    source: str = "",
    triggers: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    triggers = [str(item).strip() for item in (triggers or []) if str(item).strip()]
    extra: Dict[str, Any] = {}
    if source:
        extra["source"] = str(source or "").strip()
    if triggers:
        extra["triggers"] = triggers[:20]
    history_extra: Dict[str, Any] = {}
    if triggers:
        history_extra["triggers"] = triggers[:20]
    return _store.set_task_state(
        task_id,
        state,
        summary=summary,
        meta=meta,
        extra_fields=extra,
        history_extra=history_extra,
    )


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _store.get_task(task_id)


def get_recent_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    return _store.get_recent_tasks(limit)
