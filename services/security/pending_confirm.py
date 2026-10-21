"""Per-session pending confirmation for ActionGate and agent tools."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


_TRAILING_PUNCT = "。！？!?….,，、~～"


def session_key_from_ctx(ctx: Optional[Dict[str, Any]]) -> str:
    data = ctx if isinstance(ctx, dict) else {}
    channel = data.get("channel_meta") if isinstance(data.get("channel_meta"), dict) else {}
    for raw in (
        channel.get("session_id"),
        data.get("memory_session_id"),
        data.get("session_id"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    source = str(data.get("source") or "local").strip().lower() or "local"
    user_id = str(channel.get("user_id") or data.get("user_id") or "").strip()
    return f"{source}:{user_id}" if user_id else source


def normalize_decision_word(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").strip().lower())
    return compact.strip(_TRAILING_PUNCT)


def confirm_prompt(summary: str) -> str:
    text = str(summary or "需要确认这个操作。").strip()
    return f"{text}\n回复 确认，或回复 取消。"


@dataclass
class PendingConfirmAction:
    trigger: str
    summary: str
    payload: Dict[str, Any]
    expires_at: float
    session_key: str = ""

    @property
    def expired(self) -> bool:
        return time.monotonic() >= float(self.expires_at)


class PendingConfirmStore:
    """One pending action per session. A new one does not replace an unexpired one."""

    def __init__(self) -> None:
        self._pending: Dict[str, PendingConfirmAction] = {}
        self._lock = threading.Lock()

    def try_set(
        self,
        *,
        trigger: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
        expires_in: int = 300,
        session_key: str = "",
    ) -> tuple[PendingConfirmAction, str]:
        try:
            ttl = max(120, min(300, int(expires_in or 300)))
        except (TypeError, ValueError):
            ttl = 300
        key = str(session_key or "").strip() or "local"
        action = PendingConfirmAction(
            trigger=str(trigger or "").strip(),
            summary=str(summary or "需要确认这个操作。").strip(),
            payload=dict(payload or {}),
            expires_at=time.monotonic() + ttl,
            session_key=key,
        )
        with self._lock:
            current = self._peek_unlocked(key)
            if current is not None:
                return current, "busy"
            self._pending[key] = action
            return action, "stored"

    def set(
        self,
        *,
        trigger: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
        expires_in: int = 300,
        session_key: str = "",
    ) -> PendingConfirmAction:
        action, _status = self.try_set(
            trigger=trigger,
            summary=summary,
            payload=payload,
            expires_in=expires_in,
            session_key=session_key,
        )
        return action

    def peek(self, session_key: Optional[str] = None) -> Optional[PendingConfirmAction]:
        with self._lock:
            if session_key is None:
                self._drop_expired_unlocked()
                if len(self._pending) == 1:
                    return next(iter(self._pending.values()))
                return None
            return self._peek_unlocked(str(session_key or "").strip() or "local")

    def take(self, session_key: Optional[str] = None) -> Optional[PendingConfirmAction]:
        with self._lock:
            if session_key is None:
                self._drop_expired_unlocked()
                if len(self._pending) != 1:
                    return None
                key = next(iter(self._pending))
            else:
                key = str(session_key or "").strip() or "local"
            pending = self._peek_unlocked(key)
            self._pending.pop(key, None)
            return pending

    def clear(self, session_key: Optional[str] = None) -> None:
        with self._lock:
            if session_key is None:
                self._pending.clear()
                return
            self._pending.pop(str(session_key or "").strip() or "local", None)

    def has_pending(self, session_key: Optional[str] = None) -> bool:
        if session_key is None:
            with self._lock:
                self._drop_expired_unlocked()
                return bool(self._pending)
        return self.peek(session_key) is not None

    def _peek_unlocked(self, key: str) -> Optional[PendingConfirmAction]:
        pending = self._pending.get(key)
        if pending is None:
            return None
        if pending.expired:
            self._pending.pop(key, None)
            return None
        return pending

    def _drop_expired_unlocked(self) -> None:
        stale = [key for key, action in self._pending.items() if action.expired]
        for key in stale:
            self._pending.pop(key, None)


_default_store: Optional[PendingConfirmStore] = None


def get_pending_confirm_store() -> PendingConfirmStore:
    global _default_store
    if _default_store is None:
        _default_store = PendingConfirmStore()
    return _default_store
