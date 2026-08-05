"""Single near-history read path: ContextAssembler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from modules.conversation_events.models import (
    AssembledContext,
    ConversationEvent,
    ConversationScope,
    EventBudget,
)
from modules.conversation_events.prompt import format_recent_event_block
from modules.conversation_events.selector import RecentEventSelector
from modules.conversation_events.store import ConversationEventStore


@dataclass(slots=True)
class ContextAssembler:
    """Compose recent / mid-term / short-term materials for build_prompt."""

    store: Optional[ConversationEventStore] = None
    selector: Optional[RecentEventSelector] = None
    enabled: bool = True
    max_events: int = 3
    max_chars: int = 900
    list_limit: int = 24

    def __post_init__(self) -> None:
        if self.selector is None:
            self.selector = RecentEventSelector()

    def assemble(
        self,
        *,
        current_user_text: str,
        scope: Optional[ConversationScope] = None,
        short_term_messages: Optional[Sequence[Mapping[str, Any]]] = None,
        long_term_block: str = "",
        mid_term_block: str = "",
        active_session_block: str = "",
        now: Optional[datetime] = None,
        candidates: Optional[Sequence[ConversationEvent]] = None,
        used_counts: Optional[dict[str, int]] = None,
    ) -> AssembledContext:
        short_msgs = tuple(
            dict(item) for item in (short_term_messages or ()) if isinstance(item, Mapping)
        )
        if not self.enabled or self.store is None and candidates is None:
            return AssembledContext(
                recent_event_block="",
                active_session_block=str(active_session_block or ""),
                mid_term_block=str(mid_term_block or ""),
                long_term_block=str(long_term_block or ""),
                short_term_messages=short_msgs,
                selected_event_ids=(),
                selected_segment_ids=(),
                trace={
                    "enabled": bool(self.enabled),
                    "reason": "disabled_or_no_store",
                },
            )

        now = now or datetime.now(timezone.utc)
        event_list: list[ConversationEvent]
        if candidates is not None:
            event_list = list(candidates)
        else:
            if scope is None:
                return AssembledContext(
                    recent_event_block="",
                    active_session_block=str(active_session_block or ""),
                    mid_term_block=str(mid_term_block or ""),
                    long_term_block=str(long_term_block or ""),
                    short_term_messages=short_msgs,
                    selected_event_ids=(),
                    selected_segment_ids=(),
                    trace={"enabled": True, "reason": "missing_scope"},
                )
            event_list = self.store.list_recent(
                scope, now=now, limit=max(1, int(self.list_limit))
            )

        selection = self.selector.select(
            current_user_text,
            event_list,
            budget=EventBudget(max_events=self.max_events, max_chars=self.max_chars),
            now=now,
            used_counts=used_counts,
            short_term_texts={
                str(item.get("content") or "").strip()
                for item in short_msgs
                if str(item.get("role") or "").strip() in {"user", "assistant"}
                and str(item.get("content") or "").strip()
            },
        )
        recent_block = format_recent_event_block(selection.events)

        # Dedup: strip observation-like lines from short_term if they fully
        # duplicate selected event exact_text (keep dialog turns).
        selected_texts = {
            str(e.exact_text or "").strip()
            for e in selection.events
            if str(e.exact_text or "").strip()
        }
        deduped_short: list[Mapping[str, str]] = []
        for item in short_msgs:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if (
                role == "assistant"
                and content
                and content in selected_texts
                and any(
                    tag in content
                    for tag in ("[视觉观察]", "[屏幕观察]", "【温馨提醒】")
                )
            ):
                continue
            deduped_short.append({"role": role, "content": content})

        return AssembledContext(
            recent_event_block=recent_block,
            active_session_block=str(active_session_block or ""),
            mid_term_block=str(mid_term_block or ""),
            long_term_block=str(long_term_block or ""),
            short_term_messages=tuple(deduped_short),
            selected_event_ids=tuple(selection.event_ids),
            selected_segment_ids=(),
            trace={
                "enabled": True,
                "candidate_count": len(event_list),
                "selected_event_ids": list(selection.event_ids),
                "selected_reasons": dict(selection.reasons),
                "dropped_ids": list(selection.dropped_ids),
                "recent_block_chars": len(recent_block),
                "total_chars": selection.total_chars,
            },
        )
