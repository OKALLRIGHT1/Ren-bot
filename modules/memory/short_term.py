import logging
from typing import Dict, List


logger = logging.getLogger(__name__)


class ShortTermMemoryManager:
    def __init__(self, sqlite_store, max_short_term: int):
        self.sqlite_store = sqlite_store
        self.max_short_term = int(max_short_term)
        self.short_term_memory: List[Dict[str, str]] = []
        self.session_short_term_memory: Dict[str, List[Dict[str, str]]] = {}
        self._session_short_term_loaded = set()

    def restore_global(self):
        if not self.sqlite_store:
            return
        try:
            rows = self.sqlite_store.list_transcript(
                limit=self.max_short_term, session_scope="global"
            )
            if rows:
                self.short_term_memory = [
                    {"role": r["role"], "content": r["content"]} for r in reversed(rows)
                ]
                logger.info("Restored %s global short-term memories", len(self.short_term_memory))
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
                self.session_short_term_memory[session_key] = [
                    {"role": r["role"], "content": r["content"]} for r in reversed(rows)
                ]
            else:
                self.session_short_term_memory.setdefault(session_key, [])
            self._session_short_term_loaded.add(session_key)
        except Exception as e:
            logger.warning("Failed to restore session memory (%s): %s", session_key, e)

    def append(self, role, content, session_id: str = None):
        item = {"role": role, "content": content}
        session_key = str(session_id or "").strip()
        if session_key:
            bucket = self.session_short_term_memory.setdefault(session_key, [])
            bucket.append(item)
            if len(bucket) > self.max_short_term:
                bucket.pop(0)
            self._session_short_term_loaded.add(session_key)
            return
        self.short_term_memory.append(item)
        if len(self.short_term_memory) > self.max_short_term:
            self.short_term_memory.pop(0)

    def get_context(self, session_id: str = None, *, recall_intent: bool = False):
        session_key = str(session_id or "").strip()
        if session_key:
            self.restore_session(session_key)
            short_ctx = list(self.session_short_term_memory.get(session_key, []))
        else:
            short_ctx = list(self.short_term_memory)
        if recall_intent:
            short_ctx = [
                m for m in short_ctx if (m.get("role") or "").strip() == "user"
            ]
        return short_ctx
