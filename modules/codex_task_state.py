import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


CODEX_TASKS_PATH = Path("./data/codex_tasks.json")
MAX_TASKS = 200
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> Dict[str, Any]:
    if not CODEX_TASKS_PATH.exists():
        return {"tasks": {}}
    try:
        with CODEX_TASKS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"tasks": {}}
        tasks = data.get("tasks", {})
        if not isinstance(tasks, dict):
            tasks = {}
        return {"tasks": tasks}
    except Exception:
        return {"tasks": {}}


def _save(data: Dict[str, Any]) -> bool:
    try:
        CODEX_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tasks = data.get("tasks", {})
        if isinstance(tasks, dict) and len(tasks) > MAX_TASKS:
            sorted_items = sorted(
                tasks.items(),
                key=lambda kv: str((kv[1] or {}).get("updated_at", "")),
                reverse=True,
            )
            tasks = dict(sorted_items[:MAX_TASKS])
            data["tasks"] = tasks
        with CODEX_TASKS_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def set_task_state(
    task_id: str,
    state: str,
    *,
    code_path: str = "",
    summary: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    task_id = str(task_id or "").strip()
    if not task_id:
        return False
    state = str(state or "").strip() or "unknown"
    summary = str(summary or "").strip()
    meta = meta or {}
    now = _now()

    with _LOCK:
        data = _load()
        tasks = data.setdefault("tasks", {})
        task = tasks.get(task_id)
        if not isinstance(task, dict):
            task = {
                "task_id": task_id,
                "state": state,
                "code_path": code_path,
                "summary": summary[:400],
                "created_at": now,
                "updated_at": now,
                "meta": {},
                "history": [],
            }
        else:
            task["state"] = state
            if code_path:
                task["code_path"] = code_path
            if summary:
                task["summary"] = summary[:400]
            task["updated_at"] = now
            existing_meta = task.get("meta", {})
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            task["meta"] = existing_meta

        if meta:
            task["meta"].update(meta)

        history = task.get("history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "time": now,
                "state": state,
                "summary": summary[:200],
                "meta": meta,
            }
        )
        if len(history) > 80:
            history = history[-80:]
        task["history"] = history

        tasks[task_id] = task
        data["tasks"] = tasks
        return _save(data)


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    with _LOCK:
        data = _load()
        task = data.get("tasks", {}).get(task_id)
        return task if isinstance(task, dict) else None


def get_recent_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    n = max(1, min(200, int(limit)))
    with _LOCK:
        data = _load()
        tasks = data.get("tasks", {})
        if not isinstance(tasks, dict):
            return []
        items = [v for v in tasks.values() if isinstance(v, dict)]
        items.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
        return items[:n]
