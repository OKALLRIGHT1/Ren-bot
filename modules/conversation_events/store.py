"""SQLite-backed conversation event store (near-history authority)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _from_iso(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class ConversationEventStore:
    """Persists scoped conversation events with hard isolation."""

    def __init__(self, sqlite_store: Any) -> None:
        if sqlite_store is None:
            raise ValueError("sqlite_store is required")
        self.sqlite_store = sqlite_store
        ensure = getattr(sqlite_store, "ensure_conversation_events_schema", None)
        if callable(ensure):
            ensure()

    def append(self, event: ConversationEvent) -> ConversationEvent:
        event_id = str(event.event_id or "").strip() or uuid.uuid4().hex
        # Rebuild with generated id so validate() can pass when caller omitted it.
        if not str(event.event_id or "").strip():
            event = ConversationEvent(
                event_id=event_id,
                scope=event.scope,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                exact_text=event.exact_text,
                evidence_summary=event.evidence_summary,
                causal_parent_ids=event.causal_parent_ids,
                expires_at=event.expires_at,
                status=event.status,
                metadata=event.metadata,
            )
        event.validate()
        scope = event.scope
        scope.validate()

        parent_ids = tuple(
            str(item).strip() for item in (event.causal_parent_ids or ()) if str(item).strip()
        )
        for parent_id in parent_ids:
            parent = self.get(parent_id)
            if parent is None:
                raise ValueError(f"causal parent not found: {parent_id}")
            if parent.scope.as_tuple() != scope.as_tuple():
                raise ValueError("causal parent scope mismatch")

        metadata = dict(event.metadata or {})
        row = {
            "event_id": event_id,
            "persona_id": scope.persona_id.strip(),
            "person_id": scope.person_id.strip(),
            "channel": scope.channel.strip(),
            "conversation_id": scope.conversation_id.strip(),
            "event_type": event.event_type.value,
            "occurred_at": _to_iso(event.occurred_at),
            "exact_text": str(event.exact_text or ""),
            "evidence_summary": str(event.evidence_summary or ""),
            "causal_parent_ids_json": _json_dumps(list(parent_ids)),
            "expires_at": _to_iso(event.expires_at),
            "status": str(event.status or "active").strip() or "active",
            "metadata_json": _json_dumps(metadata),
        }
        with self.sqlite_store._connect() as conn:  # noqa: SLF001 - store owns schema
            conn.execute(
                """
                INSERT INTO conversation_events(
                  event_id, persona_id, person_id, channel, conversation_id,
                  event_type, occurred_at, exact_text, evidence_summary,
                  causal_parent_ids_json, expires_at, status, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["event_id"],
                    row["persona_id"],
                    row["person_id"],
                    row["channel"],
                    row["conversation_id"],
                    row["event_type"],
                    row["occurred_at"],
                    row["exact_text"],
                    row["evidence_summary"],
                    row["causal_parent_ids_json"],
                    row["expires_at"],
                    row["status"],
                    row["metadata_json"],
                ),
            )
            conn.commit()

        return ConversationEvent(
            event_id=event_id,
            scope=scope,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            exact_text=str(event.exact_text or ""),
            evidence_summary=str(event.evidence_summary or ""),
            causal_parent_ids=parent_ids,
            expires_at=event.expires_at,
            status=str(event.status or "active"),
            metadata=metadata,
        )

    def get(self, event_id: str) -> Optional[ConversationEvent]:
        event_id = str(event_id or "").strip()
        if not event_id:
            return None
        with self.sqlite_store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT * FROM conversation_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def list_dialog_window(
        self,
        scope: ConversationScope,
        *,
        now: Optional[datetime] = None,
        limit: int = 12,
    ) -> list[dict[str, str]]:
        """Project user/assistant message events into short-term style turns."""
        now = now or datetime.now(timezone.utc)
        events = self.list_recent(scope, now=now, limit=max(1, int(limit) * 3))
        dialog_types = {
            ConversationEventType.USER_MESSAGE,
            ConversationEventType.ASSISTANT_MESSAGE,
        }
        turns: list[dict[str, str]] = []
        for event in events:
            if event.event_type not in dialog_types:
                continue
            role = (
                "user"
                if event.event_type is ConversationEventType.USER_MESSAGE
                else "assistant"
            )
            content = str(event.exact_text or "").strip()
            if not content:
                continue
            turns.append(
                {
                    "role": role,
                    "content": content,
                    "event_id": str(event.event_id or ""),
                }
            )
        if len(turns) > max(1, int(limit)):
            turns = turns[-max(1, int(limit)) :]
        return turns

    def list_recent(
        self,
        scope: ConversationScope,
        *,
        now: datetime,
        limit: int = 20,
        include_expired: bool = False,
        statuses: Optional[Sequence[str]] = None,
    ) -> list[ConversationEvent]:
        scope.validate()
        limit = max(1, int(limit or 20))
        status_filter = [
            str(item).strip()
            for item in (statuses or ("active",))
            if str(item).strip()
        ]
        if not status_filter:
            status_filter = ["active"]
        placeholders = ",".join("?" for _ in status_filter)
        now_iso = _to_iso(now) or ""
        params: list[Any] = [
            scope.persona_id.strip(),
            scope.person_id.strip(),
            scope.channel.strip(),
            scope.conversation_id.strip(),
            *status_filter,
        ]
        expired_clause = ""
        if not include_expired:
            expired_clause = " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(now_iso)
        params.append(limit)
        # Fetch newest first for limit, then return chronological (oldest first).
        # rowid secondary key keeps same-timestamp pairs stable (user then assistant).
        sql = f"""
            SELECT * FROM conversation_events
            WHERE persona_id=? AND person_id=? AND channel=? AND conversation_id=?
              AND status IN ({placeholders})
              {expired_clause}
            ORDER BY occurred_at DESC, rowid DESC
            LIMIT ?
        """
        with self.sqlite_store._connect() as conn:  # noqa: SLF001
            rows = conn.execute(sql, tuple(params)).fetchall()
        events = [self._row_to_event(row) for row in rows]
        events.reverse()
        return events

    def mark_status(self, event_id: str, status: str) -> None:
        event_id = str(event_id or "").strip()
        status = str(status or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        if not status:
            raise ValueError("status is required")
        with self.sqlite_store._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE conversation_events SET status=? WHERE event_id=?",
                (status, event_id),
            )
            conn.commit()

    def _row_to_event(self, row: Mapping[str, Any]) -> ConversationEvent:
        data = dict(row)
        parents = _json_loads(data.get("causal_parent_ids_json"), [])
        if not isinstance(parents, list):
            parents = []
        metadata = _json_loads(data.get("metadata_json"), {})
        if not isinstance(metadata, dict):
            metadata = {}
        event_type_raw = str(data.get("event_type") or "").strip()
        try:
            event_type = ConversationEventType(event_type_raw)
        except ValueError:
            event_type = ConversationEventType.SYSTEM_NOTICE
        occurred = _from_iso(data.get("occurred_at")) or datetime.now(timezone.utc)
        return ConversationEvent(
            event_id=str(data.get("event_id") or ""),
            scope=ConversationScope(
                persona_id=str(data.get("persona_id") or ""),
                person_id=str(data.get("person_id") or ""),
                channel=str(data.get("channel") or ""),
                conversation_id=str(data.get("conversation_id") or ""),
            ),
            event_type=event_type,
            occurred_at=occurred,
            exact_text=str(data.get("exact_text") or ""),
            evidence_summary=str(data.get("evidence_summary") or ""),
            causal_parent_ids=tuple(str(item) for item in parents if str(item).strip()),
            expires_at=_from_iso(data.get("expires_at")),
            status=str(data.get("status") or "active"),
            metadata=metadata,
        )
