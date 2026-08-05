"""Conversation event data models (near-history single source of truth)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional


class ConversationEventType(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    SCREEN_OBSERVATION = "screen_observation"
    PROACTIVE_UTTERANCE = "proactive_utterance"
    CARE_REMINDER = "care_reminder"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM_NOTICE = "system_notice"


@dataclass(frozen=True, slots=True)
class ConversationScope:
    persona_id: str
    person_id: str
    channel: str
    conversation_id: str

    def validate(self) -> None:
        required = {
            "persona_id": self.persona_id,
            "person_id": self.person_id,
            "channel": self.channel,
            "conversation_id": self.conversation_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"conversation scope missing: {', '.join(missing)}")

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            str(self.persona_id).strip(),
            str(self.person_id).strip(),
            str(self.channel).strip(),
            str(self.conversation_id).strip(),
        )


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    event_id: str
    scope: ConversationScope
    event_type: ConversationEventType
    occurred_at: datetime
    exact_text: str
    evidence_summary: str
    causal_parent_ids: tuple[str, ...] = ()
    expires_at: Optional[datetime] = None
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not str(self.event_id or "").strip():
            raise ValueError("event_id is required")
        self.scope.validate()
        if not isinstance(self.event_type, ConversationEventType):
            raise ValueError("event_type must be ConversationEventType")
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("occurred_at must be datetime")
        status = str(self.status or "").strip()
        if not status:
            raise ValueError("status is required")


@dataclass(frozen=True, slots=True)
class EventBudget:
    max_events: int = 3
    max_chars: int = 900


@dataclass(frozen=True, slots=True)
class SelectionResult:
    events: tuple[ConversationEvent, ...]
    event_ids: tuple[str, ...]
    reasons: Mapping[str, str] = field(default_factory=dict)
    dropped_ids: tuple[str, ...] = ()
    total_chars: int = 0


@dataclass(frozen=True, slots=True)
class AssembledContext:
    recent_event_block: str
    active_session_block: str
    mid_term_block: str
    long_term_block: str
    short_term_messages: tuple[Mapping[str, str], ...]
    selected_event_ids: tuple[str, ...]
    selected_segment_ids: tuple[str, ...]
    trace: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MidTermSegment:
    """Immutable mid-term conversation segment (compressed projection of events)."""

    segment_id: str
    scope: ConversationScope
    range_start: datetime
    range_end: datetime
    topics: tuple[str, ...] = ()
    user_state: tuple[str, ...] = ()
    assistant_commitments: tuple[str, ...] = ()
    unresolved_threads: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    recall_cues: tuple[str, ...] = ()
    source_event_ids: tuple[str, ...] = ()
    summary: str = ""
    confidence: float = 0.0
    status: str = "active"

    def validate(self) -> None:
        if not str(self.segment_id or "").strip():
            raise ValueError("segment_id is required")
        self.scope.validate()
        if not self.source_event_ids:
            raise ValueError("source_event_ids must be non-empty")
        conf = float(self.confidence)
        if conf < 0.0 or conf > 1.0:
            raise ValueError("confidence must be in [0, 1]")
