"""Codex assistant task state map (data/codex_tasks.json)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.json_state_store import JsonTaskStore, now_iso

CODEX_TASKS_PATH = Path("./data/codex_tasks.json")
MAX_TASKS = 200
_store = JsonTaskStore(CODEX_TASKS_PATH, max_tasks=MAX_TASKS)


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
    code_path: str = "",
    summary: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    return _store.set_task_state(
        task_id,
        state,
        summary=summary,
        meta=meta,
        extra_fields={"code_path": code_path} if code_path else {},
    )


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _store.get_task(task_id)


def get_recent_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    return _store.get_recent_tasks(limit)
