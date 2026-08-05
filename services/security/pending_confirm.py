"""Shared pending confirmation store for ActionGate + agent tool confirms."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class PendingConfirmAction:
    trigger: str
    summary: str
    payload: Dict[str, Any]
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.monotonic() >= float(self.expires_at)


class PendingConfirmStore:
    """In-process pending confirmation (one active action at a time)."""

    def __init__(self) -> None:
        self._pending: Optional[PendingConfirmAction] = None

    def set(
        self,
        *,
        trigger: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
        expires_in: int = 300,
    ) -> PendingConfirmAction:
        try:
            ttl = max(30, int(expires_in or 300))
        except (TypeError, ValueError):
            ttl = 300
        action = PendingConfirmAction(
            trigger=str(trigger or "").strip(),
            summary=str(summary or "需要确认这个操作。").strip(),
            payload=dict(payload or {}),
            expires_at=time.monotonic() + ttl,
        )
        self._pending = action
        return action

    def peek(self) -> Optional[PendingConfirmAction]:
        pending = self._pending
        if pending is None:
            return None
        if pending.expired:
            self._pending = None
            return None
        return pending

    def take(self) -> Optional[PendingConfirmAction]:
        pending = self.peek()
        self._pending = None
        return pending

    def clear(self) -> None:
        self._pending = None

    def has_pending(self) -> bool:
        return self.peek() is not None


_default_store: Optional[PendingConfirmStore] = None


def get_pending_confirm_store() -> PendingConfirmStore:
    global _default_store
    if _default_store is None:
        _default_store = PendingConfirmStore()
    return _default_store
