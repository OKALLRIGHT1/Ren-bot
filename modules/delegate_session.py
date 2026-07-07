import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


DELEGATE_SESSION_PATH = Path("./data/delegate_session.json")
MAX_ITEMS = 200
_LOCK = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    if not DELEGATE_SESSION_PATH.exists():
        return []
    try:
        with DELEGATE_SESSION_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: List[Dict[str, Any]]) -> bool:
    try:
        DELEGATE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DELEGATE_SESSION_PATH.open("w", encoding="utf-8") as f:
            json.dump(items[-MAX_ITEMS:], f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_event(
    event_type: str,
    *,
    task_id: str = "",
    user_text: str = "",
    triggers: Optional[List[str]] = None,
    text: str = "",
    meta: Optional[Dict[str, Any]] = None,
):
    with _LOCK:
        items = _load()
        items.append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "type": str(event_type or "").strip(),
                "task_id": str(task_id or "").strip(),
                "user_text": str(user_text or "")[:600],
                "triggers": list(triggers or [])[:12],
                "text": str(text or "")[:1200],
                "meta": meta or {},
            }
        )
        _save(items)


def get_recent(limit: int = 20, *, task_id: str = "") -> List[Dict[str, Any]]:
    with _LOCK:
        items = _load()
        if task_id:
            tid = str(task_id or "").strip()
            items = [
                item for item in items if str(item.get("task_id") or "").strip() == tid
            ]
        return items[-max(1, int(limit)) :]
