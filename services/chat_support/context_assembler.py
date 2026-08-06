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


def _is_structural_line(line: str) -> bool:
    text = str(line or "").strip()
    return (
        not text
        or text.startswith("【")
        or text.startswith("（")
        or text == "使用规则："
    )


def _safe_error_code(error: Any) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    if text == "embedding_unavailable":
        return text
    if text.startswith("embedding_failed:"):
        return "embedding_failed"
    return "recall_error"


def _deduplicate_block_lines(
    block: str,
    *,
    layer: str,
    seen: set[str],
) -> tuple[str, list[str]]:
    kept: list[str] = []
    dropped: list[str] = []
    for line_number, raw_line in enumerate(str(block or "").splitlines(), start=1):
        normalized = " ".join(raw_line.split())
        if not _is_structural_line(normalized) and normalized in seen:
            dropped.append(f"{layer}:line:{line_number}")
            continue
        kept.append(raw_line)
        if not _is_structural_line(normalized):
            seen.add(normalized)
    return "\n".join(kept).strip(), dropped


def _fit_top_lines(block: str, max_chars: int) -> str:
    lines = str(block or "").splitlines()
    if not lines or max_chars <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if used + added > max_chars:
            break
        kept.append(line)
        used += added
    return "\n".join(kept).strip()


def _fit_active_block(block: str, max_chars: int) -> str:
    lines = str(block or "").splitlines()
    if not lines or max_chars <= 0:
        return ""
    content_start = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("- ")),
        len(lines),
    )
    prefix = lines[:content_start]
    content = lines[content_start:]
    prefix_text = _fit_top_lines("\n".join(prefix), max_chars)
    if not prefix_text:
        return ""
    remaining = max_chars - len(prefix_text)
    selected: list[str] = []
    for line in reversed(content):
        added = len(line) + 1
        if added > remaining:
            if not selected:
                break
            continue
        selected.append(line)
        remaining -= added
    selected.reverse()
    return "\n".join((prefix_text, *selected)).strip()


def _fit_mid_term_block(block: str, max_chars: int) -> str:
    lines = str(block or "").splitlines()
    if not lines or max_chars <= 0:
        return ""
    first_segment = next(
        (index for index, line in enumerate(lines) if line.startswith("- 段 ")),
        len(lines),
    )
    if first_segment == len(lines):
        return _fit_top_lines(block, max_chars)
    groups: list[list[str]] = []
    for line in lines[first_segment:]:
        if line.startswith("- 段 "):
            groups.append([line])
        elif groups:
            groups[-1].append(line)
    kept = lines[:first_segment]
    used = len("\n".join(kept))
    for group in groups:
        group_text = "\n".join(group)
        added = len(group_text) + (1 if kept else 0)
        if used + added > max_chars:
            break
        kept.extend(group)
        used += added
    return "\n".join(kept).strip()


@dataclass(slots=True)
class ContextAssembler:
    """Compose recent / mid-term / short-term materials for build_prompt."""

    store: Optional[ConversationEventStore] = None
    selector: Optional[RecentEventSelector] = None
    enabled: bool = True
    max_events: int = 3
    max_chars: int = 900
    active_max_chars: int = 500
    mid_term_max_chars: int = 1800
    long_term_max_chars: int = 1200
    list_limit: int = 24
    mid_term_enabled: bool = False
    mid_term_recall_service: Any = None

    def __post_init__(self) -> None:
        if self.selector is None:
            self.selector = RecentEventSelector()

    def _trace(
        self,
        *,
        scope: Optional[ConversationScope],
        candidates: Sequence[ConversationEvent] = (),
        selected_event_ids: Sequence[str] = (),
        selection_reasons: Optional[Mapping[str, str]] = None,
        selected_segment_ids: Sequence[str] = (),
        layer_chars: Optional[Mapping[str, int]] = None,
        deduplicated_items: Sequence[str] = (),
        **extra: Any,
    ) -> dict[str, Any]:
        trace = {
            "source": "events",
            "conversation_id": str(
                getattr(scope, "conversation_id", "") or ""
            ),
            "candidate_event_ids": [
                str(event.event_id) for event in candidates if str(event.event_id or "")
            ],
            "selected_event_ids": [str(item) for item in selected_event_ids],
            "selection_reasons": dict(selection_reasons or {}),
            "selected_segment_ids": [str(item) for item in selected_segment_ids],
            "layer_chars": dict(
                layer_chars
                or {"recent": 0, "active": 0, "mid_term": 0, "long_term": 0}
            ),
            "deduplicated_items": [str(item) for item in deduplicated_items],
            "planner_triggered": False,
        }
        trace.update(extra)
        return trace

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
            resolved_active, active_dedup = _deduplicate_block_lines(
                str(active_session_block or ""), layer="active", seen=set()
            )
            resolved_mid, mid_dedup = _deduplicate_block_lines(
                str(mid_term_block or ""),
                layer="mid_term",
                seen={
                    " ".join(line.split())
                    for line in resolved_active.splitlines()
                    if not _is_structural_line(line)
                },
            )
            resolved_long, long_dedup = _deduplicate_block_lines(
                str(long_term_block or ""),
                layer="long_term",
                seen={
                    " ".join(line.split())
                    for line in (resolved_active + "\n" + resolved_mid).splitlines()
                    if not _is_structural_line(line)
                },
            )
            resolved_active = _fit_active_block(
                resolved_active, max(0, int(self.active_max_chars))
            )
            resolved_mid = _fit_mid_term_block(
                resolved_mid, max(0, int(self.mid_term_max_chars))
            )
            resolved_long = _fit_top_lines(
                resolved_long, max(0, int(self.long_term_max_chars))
            )
            return AssembledContext(
                recent_event_block="",
                active_session_block=resolved_active,
                mid_term_block=resolved_mid,
                long_term_block=resolved_long,
                short_term_messages=short_msgs,
                selected_event_ids=(),
                selected_segment_ids=(),
                trace=self._trace(
                    scope=scope,
                    layer_chars={
                        "recent": 0,
                        "active": len(resolved_active),
                        "mid_term": len(resolved_mid),
                        "long_term": len(resolved_long),
                    },
                    deduplicated_items=(*active_dedup, *mid_dedup, *long_dedup),
                    enabled=bool(self.enabled),
                    reason="disabled_or_no_store",
                ),
            )

        now = now or datetime.now(timezone.utc)
        if scope is None:
            bounded_long_term = _fit_top_lines(
                str(long_term_block or ""), max(0, int(self.long_term_max_chars))
            )
            return AssembledContext(
                recent_event_block="",
                active_session_block="",
                mid_term_block="",
                long_term_block=bounded_long_term,
                short_term_messages=short_msgs,
                selected_event_ids=(),
                selected_segment_ids=(),
                trace=self._trace(
                    scope=None,
                    layer_chars={
                        "recent": 0,
                        "active": 0,
                        "mid_term": 0,
                        "long_term": len(bounded_long_term),
                    },
                    enabled=True,
                    reason="missing_scope",
                ),
            )
        event_list: list[ConversationEvent]
        if candidates is not None:
            event_list = [
                event
                for event in candidates
                if event.scope.as_tuple() == scope.as_tuple()
            ]
        else:
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
        injected_events = list(selection.events)
        recent_block = format_recent_event_block(injected_events)
        budget_dropped_ids: list[str] = []
        while recent_block and len(recent_block) > max(0, int(self.max_chars)):
            budget_dropped_ids.append(injected_events[0].event_id)
            injected_events.pop(0)
            recent_block = format_recent_event_block(injected_events)
        injected_event_ids = tuple(event.event_id for event in injected_events)
        injected_reasons = {
            event_id: reason
            for event_id, reason in selection.reasons.items()
            if event_id in injected_event_ids
        }
        resolved_active_session_block = str(active_session_block or "")
        resolved_mid_term_block = str(mid_term_block or "")
        selected_segment_ids: tuple[str, ...] = ()
        mid_term_error = ""
        if (
            self.mid_term_enabled
            and self.mid_term_recall_service is not None
            and scope is not None
        ):
            excluded_event_ids = {
                str(item.get("event_id") or "").strip()
                for item in short_msgs
                if str(item.get("event_id") or "").strip()
            }
            excluded_event_ids.update(injected_event_ids)
            try:
                recall = self.mid_term_recall_service.recall(
                    current_text=current_user_text,
                    scope=scope,
                    available_events=event_list,
                    excluded_event_ids=excluded_event_ids,
                )
                resolved_active_session_block = str(
                    recall.active_session_block or ""
                )
                resolved_mid_term_block = str(recall.mid_term_block or "")
                selected_segment_ids = tuple(
                    segment_id
                    for segment_id in (
                        recall.active_segment_id,
                        *recall.recalled_segment_ids,
                    )
                    if str(segment_id or "").strip()
                )
                mid_term_error = _safe_error_code(recall.error)
            except Exception as exc:
                mid_term_error = f"recall_exception:{type(exc).__name__}"

        # Dedup: strip observation-like lines from short_term if they fully
        # duplicate selected event exact_text (keep dialog turns).
        selected_texts = {
            str(e.exact_text or "").strip()
            for e in injected_events
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

        seen_lines = {
            " ".join(line.split())
            for line in recent_block.splitlines()
            if not _is_structural_line(line)
        }
        resolved_active_session_block, active_dedup = _deduplicate_block_lines(
            resolved_active_session_block, layer="active", seen=seen_lines
        )
        resolved_mid_term_block, mid_dedup = _deduplicate_block_lines(
            resolved_mid_term_block, layer="mid_term", seen=seen_lines
        )
        resolved_long_term_block, long_dedup = _deduplicate_block_lines(
            str(long_term_block or ""), layer="long_term", seen=seen_lines
        )
        resolved_active_session_block = _fit_active_block(
            resolved_active_session_block, max(0, int(self.active_max_chars))
        )
        resolved_mid_term_block = _fit_mid_term_block(
            resolved_mid_term_block, max(0, int(self.mid_term_max_chars))
        )
        resolved_long_term_block = _fit_top_lines(
            resolved_long_term_block, max(0, int(self.long_term_max_chars))
        )
        mid_term_segment_count = sum(
            1
            for line in resolved_mid_term_block.splitlines()
            if line.startswith("- 段 ")
        )
        if resolved_mid_term_block and mid_term_segment_count == 0:
            mid_term_segment_count = max(0, len(selected_segment_ids) - 1)
        injected_segment_ids: tuple[str, ...] = ()
        if selected_segment_ids:
            active_ids = selected_segment_ids[:1] if resolved_active_session_block else ()
            recalled_ids = selected_segment_ids[1 : 1 + mid_term_segment_count]
            injected_segment_ids = (*active_ids, *recalled_ids)
        deduplicated_items = [*active_dedup, *mid_dedup, *long_dedup]
        layer_chars = {
            "recent": len(recent_block),
            "active": len(resolved_active_session_block),
            "mid_term": len(resolved_mid_term_block),
            "long_term": len(resolved_long_term_block),
        }

        return AssembledContext(
            recent_event_block=recent_block,
            active_session_block=resolved_active_session_block,
            mid_term_block=resolved_mid_term_block,
            long_term_block=resolved_long_term_block,
            short_term_messages=tuple(deduped_short),
            selected_event_ids=injected_event_ids,
            selected_segment_ids=injected_segment_ids,
            trace=self._trace(
                scope=scope,
                candidates=event_list,
                selected_event_ids=injected_event_ids,
                selection_reasons=injected_reasons,
                selected_segment_ids=injected_segment_ids,
                layer_chars=layer_chars,
                deduplicated_items=deduplicated_items,
                enabled=True,
                candidate_count=len(event_list),
                dropped_ids=[*selection.dropped_ids, *budget_dropped_ids],
                recent_block_chars=len(recent_block),
                total_chars=selection.total_chars,
                mid_term_error=mid_term_error,
            ),
        )
