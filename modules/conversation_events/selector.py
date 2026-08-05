"""Deterministic recent-event selector (no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    EventBudget,
    SelectionResult,
)

_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]+")
_DEICTIC_FOLLOWUP_RE = re.compile(
    r"(?:你|这|那).{0,2}(?:指|说的|提的)(?:是)?(?:什么|啥|哪)"
)

# Strong deictic / causal follow-up cues (not bare "什么/啥").
_FOLLOWUP_MARKERS = (
    "刚才",
    "刚刚",
    "你说",
    "为什么这么说",
    "为什么突然",
    "怎么突然",
    "你怎么知道",
    "哪来的",
    "从哪",
    "看到了什么",
    "看见了什么",
    "你看我屏幕",
    "吐槽",
    "依据",
    "结论",
    "这么讲",
    "那句",
    "刚才那",
    "你这",
    "你刚",
    "凭啥",
    "提醒",
    "答应",
    "查到",
    "工具",
    "结果怎么样",
    "你搜到",
    "发给谁",
    "用什么工具",
    "失败原因",
)

@dataclass(frozen=True, slots=True)
class _Scored:
    event: ConversationEvent
    score: float
    reason: str


def _tokens(text: str) -> set[str]:
    raw = str(text or "").lower()
    tokens = {match.group(0) for match in _ASCII_TOKEN_RE.finditer(raw)}
    for match in _CJK_CHUNK_RE.finditer(raw):
        chunk = match.group(0)
        if len(chunk) == 1:
            tokens.add(chunk)
            continue
        for width in (2, 3):
            if len(chunk) < width:
                continue
            tokens.update(
                chunk[index : index + width]
                for index in range(len(chunk) - width + 1)
            )
    return tokens


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return float(len(inter)) / float(max(1, min(len(a), len(b))))


def _is_expired(event: ConversationEvent, now: datetime) -> bool:
    if event.expires_at is None:
        return False
    exp = event.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return exp <= n


def _visibility_system(event: ConversationEvent) -> bool:
    meta = event.metadata or {}
    return str(meta.get("visibility") or "").strip().lower() == "system"


def _has_content(event: ConversationEvent) -> bool:
    return bool(str(event.exact_text or "").strip() or str(event.evidence_summary or "").strip())


def _event_chars(event: ConversationEvent) -> int:
    return len(str(event.exact_text or "")) + len(str(event.evidence_summary or ""))


class RecentEventSelector:
    """Hard-filter then score recent events for prompt injection."""

    def select(
        self,
        current_text: str,
        candidates: Sequence[ConversationEvent],
        budget: Optional[EventBudget] = None,
        *,
        now: Optional[datetime] = None,
        used_counts: Optional[dict[str, int]] = None,
        short_term_texts: Optional[set[str]] = None,
    ) -> SelectionResult:
        budget = budget or EventBudget()
        now = now or datetime.now(timezone.utc)
        used_counts = used_counts or {}
        short_term_texts = {
            str(item or "").strip()
            for item in (short_term_texts or set())
            if str(item or "").strip()
        }
        current = str(current_text or "").strip()
        current_tokens = _tokens(current)
        looks_followup = any(
            marker in current for marker in _FOLLOWUP_MARKERS
        ) or bool(_DEICTIC_FOLLOWUP_RE.search(current))

        hard_passed: list[ConversationEvent] = []
        dropped: list[str] = []
        for event in candidates:
            if str(event.status or "").strip() != "active":
                dropped.append(event.event_id)
                continue
            if _is_expired(event, now):
                dropped.append(event.event_id)
                continue
            if _visibility_system(event):
                dropped.append(event.event_id)
                continue
            if not _has_content(event):
                dropped.append(event.event_id)
                continue
            if (
                event.event_type
                in {
                    ConversationEventType.USER_MESSAGE,
                    ConversationEventType.ASSISTANT_MESSAGE,
                }
                and str(event.exact_text or "").strip() in short_term_texts
            ):
                dropped.append(event.event_id)
                continue
            hard_passed.append(event)

        if not hard_passed:
            return SelectionResult(events=(), event_ids=(), reasons={}, dropped_ids=tuple(dropped))

        # Prefer newest first for adjacency.
        ordered = sorted(
            hard_passed,
            key=lambda e: e.occurred_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        last_speak: Optional[ConversationEvent] = None
        for event in ordered:
            if event.event_type in {
                ConversationEventType.ASSISTANT_MESSAGE,
                ConversationEventType.PROACTIVE_UTTERANCE,
                ConversationEventType.CARE_REMINDER,
                ConversationEventType.TOOL_RESULT,
                ConversationEventType.SCREEN_OBSERVATION,
            }:
                last_speak = event
                break

        overlaps = {
            event.event_id: _overlap(
                current_tokens,
                _tokens(f"{event.exact_text} {event.evidence_summary}"),
            )
            for event in ordered
        }
        relevance_threshold = 0.07 if looks_followup else 0.10
        anchor_ids: set[str] = set()
        semantic_ids = {
            event.event_id
            for event in ordered
            if overlaps.get(event.event_id, 0.0) >= relevance_threshold
        }
        if semantic_ids:
            anchor_ids.update(semantic_ids)
            for event in ordered:
                parent_ids = set(event.causal_parent_ids or ())
                if event.event_id in semantic_ids:
                    anchor_ids.update(parent_ids)
                if parent_ids & semantic_ids:
                    anchor_ids.add(event.event_id)
        elif looks_followup and last_speak is not None:
            anchor_ids.add(last_speak.event_id)
            anchor_ids.update(last_speak.causal_parent_ids or ())
            anchor_ids.update(
                event.event_id
                for event in ordered
                if last_speak.event_id in set(event.causal_parent_ids or ())
            )

        scored: list[_Scored] = []
        for event in ordered:
            score = 0.0
            reasons: list[str] = []

            ov = overlaps.get(event.event_id, 0.0)
            is_relevant = ov >= relevance_threshold or event.event_id in anchor_ids
            if not is_relevant:
                continue

            # 1) Direct adjacency only reorders events that passed relevance.
            if last_speak is not None and event.event_id == last_speak.event_id:
                score += 5.0
                reasons.append("last_speak")
            if last_speak is not None and event.event_id in set(last_speak.causal_parent_ids or ()):
                score += 4.5
                reasons.append("parent_of_last_speak")
            if last_speak is not None and last_speak.event_id in set(event.causal_parent_ids or ()):
                score += 3.5
                reasons.append("child_of_last_speak")

            # 2) Causal parents of any high-signal speak
            if event.event_type in {
                ConversationEventType.SCREEN_OBSERVATION,
                ConversationEventType.TOOL_RESULT,
            }:
                score += 0.5

            # 3) Lexical overlap
            if ov > 0:
                score += 2.0 * ov
                reasons.append(f"overlap:{ov:.2f}")

            # 4) Time decay (hours)
            age_sec = 0.0
            try:
                occurred = event.occurred_at
                if occurred.tzinfo is None:
                    occurred = occurred.replace(tzinfo=timezone.utc)
                n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
                age_sec = max(0.0, (n - occurred).total_seconds())
            except Exception:
                age_sec = 0.0
            age_hours = age_sec / 3600.0
            score += max(0.0, 1.5 - min(1.5, age_hours / 2.0))
            if age_hours < 0.25:
                reasons.append("fresh")

            # 5) Used count penalty
            used = int(used_counts.get(event.event_id, 0) or 0)
            if used:
                score -= 0.8 * used
                reasons.append(f"used:{used}")

            # Follow-up boost for speak + observation chain
            if looks_followup and event.event_type in {
                ConversationEventType.PROACTIVE_UTTERANCE,
                ConversationEventType.ASSISTANT_MESSAGE,
                ConversationEventType.SCREEN_OBSERVATION,
                ConversationEventType.CARE_REMINDER,
                ConversationEventType.TOOL_RESULT,
            }:
                score += 1.2
                reasons.append("followup_boost")

            if score <= 0.05:
                continue
            scored.append(
                _Scored(
                    event=event,
                    score=score,
                    reason=";".join(reasons) or "scored",
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)

        # Always try to keep parent of selected utterance.
        selected: list[ConversationEvent] = []
        reasons_map: dict[str, str] = {}
        total_chars = 0
        max_events = max(1, int(budget.max_events))
        max_chars = max(64, int(budget.max_chars))

        def try_add(item: _Scored) -> bool:
            nonlocal total_chars
            if any(e.event_id == item.event.event_id for e in selected):
                return False
            chars = _event_chars(item.event)
            if selected and (len(selected) >= max_events or total_chars + chars > max_chars):
                return False
            if not selected and chars > max_chars:
                # Still allow single oversized if nothing selected? Prefer skip.
                return False
            selected.append(item.event)
            reasons_map[item.event.event_id] = item.reason
            total_chars += chars
            return True

        for item in scored:
            if len(selected) >= max_events:
                break
            if not try_add(item):
                continue
            # Pull causal parents if room.
            for parent_id in item.event.causal_parent_ids or ():
                parent = next((c for c in ordered if c.event_id == parent_id), None)
                if parent is None:
                    continue
                parent_scored = next((s for s in scored if s.event.event_id == parent_id), None)
                if parent_scored is None:
                    parent_scored = _Scored(event=parent, score=0.1, reason="causal_parent")
                try_add(parent_scored)

        # Chronological order for prompt
        selected_sorted = sorted(
            selected,
            key=lambda e: e.occurred_at or datetime.min.replace(tzinfo=timezone.utc),
        )
        return SelectionResult(
            events=tuple(selected_sorted),
            event_ids=tuple(e.event_id for e in selected_sorted),
            reasons=reasons_map,
            dropped_ids=tuple(dropped),
            total_chars=total_chars,
        )
