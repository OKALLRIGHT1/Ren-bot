"""Codex assistant session event log (data/codex_session.json)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.json_state_store import JsonListStore, now_iso

CODEX_SESSION_PATH = Path("./data/codex_session.json")
MAX_ITEMS = 120
_store = JsonListStore(CODEX_SESSION_PATH, max_items=MAX_ITEMS)


def _load() -> List[Dict[str, Any]]:
    return _store.load()


def _save(items: List[Dict[str, Any]]) -> bool:
    return _store.save(items)


def add_event(
    event_type: str,
    *,
    user_text: str = "",
    code_path: str = "",
    files: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
):
    _store.append(
        {
            "time": now_iso(),
            "type": event_type,
            "user_text": user_text[:600],
            "code_path": code_path,
            "files": files or [],
            "meta": meta or {},
        }
    )


def get_recent(limit: int = 20) -> List[Dict[str, Any]]:
    return _store.recent(limit)
