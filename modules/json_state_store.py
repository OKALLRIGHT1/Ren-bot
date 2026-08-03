"""Shared JSON file helpers for session/event logs and task state maps.

Used by codex_* and delegate_* facades so path/namespace stay separate while
load/save/history logic is not duplicated.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JsonListStore:
    """Thread-safe append-only JSON list store (session/event log)."""

    def __init__(self, path: Path, *, max_items: int = 200):
        self.path = Path(path)
        self.max_items = max(1, int(max_items))
        self._lock = threading.Lock()

    def load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save(self, items: List[Dict[str, Any]]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as fh:
                json.dump(items[-self.max_items :], fh, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def append(self, item: Dict[str, Any]) -> None:
        with self._lock:
            items = self.load()
            items.append(item)
            self.save(items)

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            items = self.load()
            return items[-max(1, int(limit)) :]

    def recent_filtered(
        self, limit: int = 20, *, predicate
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = self.load()
            if predicate is not None:
                items = [item for item in items if predicate(item)]
            return items[-max(1, int(limit)) :]


class JsonTaskStore:
    """Thread-safe JSON map of task_id -> task dict with history."""

    def __init__(self, path: Path, *, max_tasks: int = 200, max_history: int = 80):
        self.path = Path(path)
        self.max_tasks = max(1, int(max_tasks))
        self.max_history = max(1, int(max_history))
        self._lock = threading.Lock()

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"tasks": {}}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return {"tasks": {}}
            tasks = data.get("tasks", {})
            if not isinstance(tasks, dict):
                tasks = {}
            return {"tasks": tasks}
        except Exception:
            return {"tasks": {}}

    def save(self, data: Dict[str, Any]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tasks = data.get("tasks", {})
            if isinstance(tasks, dict) and len(tasks) > self.max_tasks:
                sorted_items = sorted(
                    tasks.items(),
                    key=lambda kv: str((kv[1] or {}).get("updated_at", "")),
                    reverse=True,
                )
                tasks = dict(sorted_items[: self.max_tasks])
                data["tasks"] = tasks
            with self.path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def set_task_state(
        self,
        task_id: str,
        state: str,
        *,
        summary: str = "",
        meta: Optional[Dict[str, Any]] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        history_extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        task_id = str(task_id or "").strip()
        if not task_id:
            return False
        state = str(state or "").strip() or "unknown"
        summary = str(summary or "").strip()
        meta = meta or {}
        extra_fields = extra_fields or {}
        history_extra = history_extra or {}
        now = now_iso()

        with self._lock:
            data = self.load()
            tasks = data.setdefault("tasks", {})
            task = tasks.get(task_id)
            if not isinstance(task, dict):
                task = {
                    "task_id": task_id,
                    "state": state,
                    "summary": summary[:400],
                    "created_at": now,
                    "updated_at": now,
                    "meta": {},
                    "history": [],
                }
                for key, value in extra_fields.items():
                    if value not in (None, "", [], {}):
                        task[key] = value
            else:
                task["state"] = state
                if summary:
                    task["summary"] = summary[:400]
                task["updated_at"] = now
                existing_meta = task.get("meta", {})
                if not isinstance(existing_meta, dict):
                    existing_meta = {}
                task["meta"] = existing_meta
                for key, value in extra_fields.items():
                    if value not in (None, "", [], {}):
                        task[key] = value

            if meta:
                task["meta"].update(meta)

            history = task.get("history", [])
            if not isinstance(history, list):
                history = []
            entry = {
                "time": now,
                "state": state,
                "summary": summary[:200],
                "meta": meta,
            }
            entry.update(history_extra)
            history.append(entry)
            if len(history) > self.max_history:
                history = history[-self.max_history :]
            task["history"] = history

            tasks[task_id] = task
            data["tasks"] = tasks
            return self.save(data)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        task_id = str(task_id or "").strip()
        if not task_id:
            return None
        with self._lock:
            data = self.load()
            task = data.get("tasks", {}).get(task_id)
            return task if isinstance(task, dict) else None

    def get_recent_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        n = max(1, min(200, int(limit)))
        with self._lock:
            data = self.load()
            tasks = data.get("tasks", {})
            if not isinstance(tasks, dict):
                return []
            items = [v for v in tasks.values() if isinstance(v, dict)]
            items.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
            return items[:n]
