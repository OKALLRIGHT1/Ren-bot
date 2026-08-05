"""Unified write API for conversation events (scope + dual-write T1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)
from modules.conversation_events.store import ConversationEventStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationEventService:
    """Single write entry for near-history events."""

    def __init__(
        self,
        *,
        store: Optional[ConversationEventStore],
        gateway_context_service: Any,
        enabled: bool = True,
        default_persona_id: str = "suzu",
        screen_event_ttl_sec: int = 1800,
        care_event_ttl_user_turns: int = 4,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.gateway_context_service = gateway_context_service
        self.enabled = bool(enabled)
        self.default_persona_id = str(default_persona_id or "suzu").strip() or "suzu"
        self.screen_event_ttl_sec = max(60, int(screen_event_ttl_sec or 1800))
        self.care_event_ttl_user_turns = max(1, int(care_event_ttl_user_turns or 4))
        self.logger = logger

    @property
    def is_ready(self) -> bool:
        return bool(self.enabled and self.store is not None)

    def resolve_scope(
        self,
        ctx: Optional[Mapping[str, Any]],
        *,
        persona_id: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> ConversationScope:
        ctx_dict = dict(ctx or {})
        if "conversation_scope" in ctx_dict and isinstance(
            ctx_dict.get("conversation_scope"), ConversationScope
        ):
            scope = ctx_dict["conversation_scope"]
            scope.validate()
            return scope

        channel, conversation_id = self.gateway_context_service.event_scope_parts(
            ctx_dict
        )
        resolved_persona = str(
            persona_id
            or ctx_dict.get("persona_id")
            or self.default_persona_id
            or "suzu"
        ).strip() or "suzu"
        resolved_person = str(
            person_id
            or ctx_dict.get("memory_person_id")
            or ctx_dict.get("person_id")
            or "owner"
        ).strip() or "owner"
        scope = ConversationScope(
            persona_id=resolved_persona,
            person_id=resolved_person,
            channel=channel,
            conversation_id=conversation_id,
        )
        scope.validate()
        return scope

    def append(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        event_type: ConversationEventType,
        exact_text: str = "",
        evidence_summary: str = "",
        causal_parent_ids: Sequence[str] = (),
        expires_at: Optional[datetime] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        event_id: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
        persona_id: Optional[str] = None,
        person_id: Optional[str] = None,
        status: str = "active",
    ) -> Optional[ConversationEvent]:
        if not self.is_ready:
            return None
        scope = self.resolve_scope(ctx, persona_id=persona_id, person_id=person_id)
        event = ConversationEvent(
            event_id=str(event_id or uuid.uuid4().hex),
            scope=scope,
            event_type=event_type,
            occurred_at=occurred_at or _now(),
            exact_text=str(exact_text or ""),
            evidence_summary=str(evidence_summary or ""),
            causal_parent_ids=tuple(
                str(item).strip()
                for item in (causal_parent_ids or ())
                if str(item).strip()
            ),
            expires_at=expires_at,
            status=str(status or "active"),
            metadata=dict(metadata or {}),
        )
        try:
            return self.store.append(event)
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"[ConversationEventService] append failed: {exc}")
            raise

    def list_recent_for_ctx(
        self,
        ctx: Optional[Mapping[str, Any]],
        *,
        limit: int = 20,
        now: Optional[datetime] = None,
        persona_id: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> list[ConversationEvent]:
        if not self.is_ready:
            return []
        scope = self.resolve_scope(ctx, persona_id=persona_id, person_id=person_id)
        return self.store.list_recent(scope, now=now or _now(), limit=limit)

    async def record_message_pair(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        user_text: str,
        assistant_text: str,
        metadata: Optional[Mapping[str, Any]] = None,
        existing_user_event_id: str = "",
        assistant_parent_event_id: str = "",
    ) -> tuple[str, str]:
        """Record one user + one assistant event. Returns (user_id, assistant_id)."""
        if not self.is_ready:
            return "", ""
        meta = dict(metadata or {})
        user_at = _now()
        user_event_id = str(existing_user_event_id or "").strip()
        if user_event_id:
            user_event = self.store.get(user_event_id)
            if user_event is None:
                raise ValueError(f"existing user event not found: {user_event_id}")
            if user_event.event_type != ConversationEventType.USER_MESSAGE:
                raise ValueError("existing user event must be a user message")
            if user_event.scope.as_tuple() != self.resolve_scope(ctx).as_tuple():
                raise ValueError("existing user event scope mismatch")
        else:
            user_event = self.append(
                ctx=ctx,
                event_type=ConversationEventType.USER_MESSAGE,
                exact_text=str(user_text or ""),
                evidence_summary="",
                metadata={**meta, "role": "user"},
                occurred_at=user_at,
            )
        parent_event_id = str(assistant_parent_event_id or "").strip()
        parent_ids = (
            (parent_event_id,)
            if parent_event_id
            else ((user_event.event_id,) if user_event else ())
        )
        assistant_event = self.append(
            ctx=ctx,
            event_type=ConversationEventType.ASSISTANT_MESSAGE,
            exact_text=str(assistant_text or ""),
            evidence_summary="",
            causal_parent_ids=parent_ids,
            metadata={**meta, "role": "assistant"},
            occurred_at=user_at + timedelta(microseconds=1),
        )
        return (
            user_event.event_id if user_event else "",
            assistant_event.event_id if assistant_event else "",
        )

    def record_user_message(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        text: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ConversationEvent]:
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.USER_MESSAGE,
            exact_text=str(text or ""),
            metadata=dict(metadata or {}),
        )

    def record_assistant_message(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        text: str,
        parent_event_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ConversationEvent]:
        parents = (parent_event_id,) if str(parent_event_id or "").strip() else ()
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.ASSISTANT_MESSAGE,
            exact_text=str(text or ""),
            causal_parent_ids=parents,
            metadata=dict(metadata or {}),
        )

    def record_screen_observation(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        evidence_summary: str,
        exact_text: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        ttl_sec: Optional[int] = None,
    ) -> Optional[ConversationEvent]:
        ttl = int(ttl_sec if ttl_sec is not None else self.screen_event_ttl_sec)
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.SCREEN_OBSERVATION,
            exact_text=str(exact_text or ""),
            evidence_summary=str(evidence_summary or ""),
            expires_at=_now() + timedelta(seconds=ttl),
            metadata=dict(metadata or {}),
        )

    def record_proactive_utterance(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        text: str,
        parent_event_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        ttl_sec: Optional[int] = None,
    ) -> Optional[ConversationEvent]:
        ttl = int(ttl_sec if ttl_sec is not None else self.screen_event_ttl_sec)
        parents = (parent_event_id,) if str(parent_event_id or "").strip() else ()
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.PROACTIVE_UTTERANCE,
            exact_text=str(text or ""),
            causal_parent_ids=parents,
            expires_at=_now() + timedelta(seconds=ttl),
            metadata=dict(metadata or {}),
        )

    def record_care_reminder(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        text: str,
        reason: str = "",
        parent_event_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        ttl_sec: int = 7200,
    ) -> Optional[ConversationEvent]:
        parents = (parent_event_id,) if str(parent_event_id or "").strip() else ()
        meta = dict(metadata or {})
        if reason:
            meta["reason"] = reason
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.CARE_REMINDER,
            exact_text=str(text or ""),
            evidence_summary=str(reason or ""),
            causal_parent_ids=parents,
            expires_at=_now() + timedelta(seconds=max(60, int(ttl_sec))),
            metadata=meta,
        )

    def record_tool_call(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        tool_name: str,
        arguments_summary: str = "",
        parent_event_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ConversationEvent]:
        parents = (parent_event_id,) if str(parent_event_id or "").strip() else ()
        meta = dict(metadata or {})
        meta["tool_name"] = str(tool_name or "")
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.TOOL_CALL,
            exact_text=str(tool_name or ""),
            evidence_summary=str(arguments_summary or ""),
            causal_parent_ids=parents,
            metadata=meta,
        )

    def record_tool_result(
        self,
        *,
        ctx: Optional[Mapping[str, Any]],
        tool_name: str,
        success: bool,
        result_summary: str,
        parent_event_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ConversationEvent]:
        parents = (parent_event_id,) if str(parent_event_id or "").strip() else ()
        meta = dict(metadata or {})
        meta["tool_name"] = str(tool_name or "")
        meta["success"] = bool(success)
        return self.append(
            ctx=ctx,
            event_type=ConversationEventType.TOOL_RESULT,
            exact_text=str(tool_name or ""),
            evidence_summary=str(result_summary or ""),
            causal_parent_ids=parents,
            metadata=meta,
        )
