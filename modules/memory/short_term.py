import logging
import threading
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


def _restored_event_id(row: Dict[str, Any]) -> str:
    meta = row.get("meta") if isinstance(row, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    existing = str(meta.get("event_id") or "").strip()
    if existing:
        return existing
    row_id = str((row or {}).get("id") or "").strip()
    return f"legacy:{row_id}" if row_id else ""


class ShortTermMemoryManager:
    """In-memory short-term dialog window (hot cache / projection target).

    Append may carry an optional ``event_id`` so evicted items can feed
    mid-term segmentation without losing provenance.
    """

    def __init__(self, sqlite_store, max_short_term: int):
        self.sqlite_store = sqlite_store
        self.max_short_term = int(max_short_term)
        self.short_term_memory: List[Dict[str, str]] = []
        self.session_short_term_memory: Dict[str, List[Dict[str, str]]] = {}
        self._session_short_term_loaded = set()
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_item(
        role: Any,
        content: Any,
        event_id: str = "",
    ) -> Dict[str, str]:
        item: Dict[str, str] = {
            "role": str(role or "").strip(),
            "content": str(content or ""),
        }
        eid = str(event_id or "").strip()
        if eid:
            item["event_id"] = eid
        return item

    def restore_global(self):
        if not self.sqlite_store:
            return
        try:
            rows = self.sqlite_store.list_transcript(
                limit=self.max_short_term, session_scope="global"
            )
            if rows:
                restored = [
                    self._normalize_item(
                        r["role"],
                        r["content"],
                        event_id=_restored_event_id(r),
                    )
                    for r in reversed(rows)
                ]
                with self._lock:
                    self.short_term_memory = restored
                logger.info(
                    "Restored %s global short-term memories",
                    len(self.short_term_memory),
                )
        except Exception as e:
            logger.warning("Failed to restore global short-term memory: %s", e)

    def restore_session(self, session_id: str):
        session_key = str(session_id or "").strip()
        if (
            not session_key
            or not self.sqlite_store
            or session_key in self._session_short_term_loaded
        ):
            return
        try:
            rows = self.sqlite_store.list_transcript(
                limit=self.max_short_term,
                context_session_id=session_key,
            )
            if rows:
                restored = [
                    self._normalize_item(
                        r["role"],
                        r["content"],
                        event_id=_restored_event_id(r),
                    )
                    for r in reversed(rows)
                ]
            else:
                restored = None
            with self._lock:
                if restored is not None:
                    self.session_short_term_memory[session_key] = restored
                else:
                    self.session_short_term_memory.setdefault(session_key, [])
                self._session_short_term_loaded.add(session_key)
        except Exception as e:
            logger.warning("Failed to restore session memory (%s): %s", session_key, e)

    def append(
        self,
        role,
        content,
        session_id: str = None,
        *,
        event_id: str = "",
    ) -> Optional[Dict[str, str]]:
        """Append a turn. Returns the full evicted item when the window slides, else None."""
        item = self._normalize_item(role, content, event_id=event_id)
        session_key = str(session_id or "").strip()
        with self._lock:
            return self._append_unlocked(item, session_key)

    def _append_unlocked(self, item: Dict[str, str], session_key: str) -> Optional[Dict[str, str]]:
        if session_key:
            bucket = self.session_short_term_memory.setdefault(session_key, [])
            bucket.append(item)
            evicted: Optional[Dict[str, str]] = None
            if len(bucket) > self.max_short_term:
                raw = bucket.pop(0)
                evicted = dict(raw) if isinstance(raw, dict) else None
            self._session_short_term_loaded.add(session_key)
            return evicted

        self.short_term_memory.append(item)
        if len(self.short_term_memory) > self.max_short_term:
            raw = self.short_term_memory.pop(0)
            return dict(raw) if isinstance(raw, dict) else None
        return None

    def clear(self, session_id: str = None) -> None:
        session_key = str(session_id or "").strip()
        with self._lock:
            self._clear_unlocked(session_key)

    def _clear_unlocked(self, session_key: str) -> None:
        if session_key:
            self.session_short_term_memory[session_key] = []
            self._session_short_term_loaded.discard(session_key)
            return
        self.short_term_memory.clear()
        self.session_short_term_memory.clear()
        self._session_short_term_loaded.clear()

    def get_context(self, session_id: str = None, *, recall_intent: bool = False):
        session_key = str(session_id or "").strip()
        if session_key:
            self.restore_session(session_key)
            with self._lock:
                short_ctx = list(self.session_short_term_memory.get(session_key, []))
        else:
            with self._lock:
                short_ctx = list(self.short_term_memory)
        if recall_intent:
            short_ctx = [
                m for m in short_ctx if (m.get("role") or "").strip() == "user"
            ]
        return short_ctx
