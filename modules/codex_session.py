import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


CODEX_SESSION_PATH = Path("./data/codex_session.json")
MAX_ITEMS = 120
_LOCK = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    if not CODEX_SESSION_PATH.exists():
        return []
    try:
        with CODEX_SESSION_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: List[Dict[str, Any]]) -> bool:
    try:
        CODEX_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CODEX_SESSION_PATH.open("w", encoding="utf-8") as f:
            json.dump(items[-MAX_ITEMS:], f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_event(
    event_type: str,
    *,
    user_text: str = "",
    code_path: str = "",
    files: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
):
    with _LOCK:
        items = _load()
        items.append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "type": event_type,
                "user_text": user_text[:600],
                "code_path": code_path,
                "files": files or [],
                "meta": meta or {},
            }
        )
        _save(items)


def get_recent(limit: int = 20) -> List[Dict[str, Any]]:
    with _LOCK:
        items = _load()
        return items[-max(1, int(limit)) :]
