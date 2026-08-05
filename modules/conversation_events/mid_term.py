"""Traceable mid-term conversation segments (compressed event projections)."""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
    MidTermSegment,
)
from modules.conversation_events.store import ConversationEventStore

# Roles / types allowed as mid-term sources (plan: user + assistant + proactive + tool).
_SOURCE_TYPES = {
    ConversationEventType.USER_MESSAGE,
    ConversationEventType.ASSISTANT_MESSAGE,
    ConversationEventType.PROACTIVE_UTTERANCE,
    ConversationEventType.CARE_REMINDER,
    ConversationEventType.TOOL_CALL,
    ConversationEventType.TOOL_RESULT,
    ConversationEventType.SCREEN_OBSERVATION,
}

_ASSISTANT_LIKE = {
    ConversationEventType.ASSISTANT_MESSAGE,
    ConversationEventType.PROACTIVE_UTTERANCE,
    ConversationEventType.CARE_REMINDER,
}

# Precise tokens that must appear in source text if present in summary fields.
_PRECISE_TOKEN_RE = re.compile(
    r"(?:"
    r"https?://\S+"
    r"|[A-Za-z]:\\[^\s]+"
    r"|/\S+"
    r"|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"
    r"|\d+(?:\.\d+)?%"
    r"|\d{2,}"
    r")"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
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


def _source_blob(events: Sequence[ConversationEvent]) -> str:
    parts: list[str] = []
    for event in events:
        parts.append(str(event.exact_text or ""))
        parts.append(str(event.evidence_summary or ""))
        meta = event.metadata or {}
        for key in ("tool_name", "result_summary", "arguments_summary"):
            if meta.get(key):
                parts.append(str(meta.get(key)))
    return "\n".join(parts)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return tuple(out)
    text = str(value or "").strip()
    return (text,) if text else ()


def validate_summary(
    payload: Mapping[str, Any],
    source_events: Sequence[ConversationEvent],
) -> tuple[bool, str]:
    """Validate model/stub summary against source events. Returns (ok, error)."""
    if not isinstance(payload, Mapping):
        return False, "payload must be a mapping"
    source_ids = {
        str(event.event_id).strip()
        for event in source_events
        if str(event.event_id or "").strip()
    }
    if not source_ids:
        return False, "source_events empty"

    claimed = _as_str_tuple(payload.get("source_event_ids"))
    if not claimed:
        return False, "source_event_ids must be non-empty"
    if any(item not in source_ids for item in claimed):
        return False, "source_event_ids must be a subset of input event ids"
    claimed_ids = set(claimed)
    claimed_events = [
        event for event in source_events if event.event_id in claimed_ids
    ]

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False, "confidence must be a number"
    if confidence < 0.0 or confidence > 1.0:
        return False, "confidence must be in [0, 1]"

    blob = _source_blob(claimed_events)
    for entity in _as_str_tuple(payload.get("entities")):
        if len(entity) >= 2 and entity not in blob:
            return False, f"unsupported entity not in claimed sources: {entity}"

    # Precise tokens in free-text fields must appear in sources.
    text_fields = [
        str(payload.get("summary") or ""),
        " ".join(_as_str_tuple(payload.get("topics"))),
        " ".join(_as_str_tuple(payload.get("entities"))),
        " ".join(_as_str_tuple(payload.get("assistant_commitments"))),
        " ".join(_as_str_tuple(payload.get("unresolved_threads"))),
        " ".join(_as_str_tuple(payload.get("user_state"))),
        " ".join(_as_str_tuple(payload.get("recall_cues"))),
    ]
    joined = "\n".join(text_fields)
    for token in _PRECISE_TOKEN_RE.findall(joined):
        token = str(token or "").strip()
        if len(token) < 2:
            continue
        if token not in blob:
            return False, f"unsupported precise token not in sources: {token}"

    # assistant_commitments only from assistant-like events
    assistant_blob = _source_blob(
        [e for e in claimed_events if e.event_type in _ASSISTANT_LIKE]
    )
    for commitment in _as_str_tuple(payload.get("assistant_commitments")):
        # Require at least some lexical support from assistant-like text
        # (full phrase match is too strict for paraphrases; use token overlap).
        words = [w for w in re.findall(r"[\w\u4e00-\u9fff]{2,}", commitment)]
        if not words:
            continue
        if not any(w in assistant_blob for w in words):
            return False, f"assistant_commitment not supported by assistant events: {commitment}"

    # unresolved_threads must not claim failure when only success results exist
    unresolved = _as_str_tuple(payload.get("unresolved_threads"))
    if unresolved:
        tool_results = [
            e
            for e in claimed_events
            if e.event_type is ConversationEventType.TOOL_RESULT
        ]
        if tool_results:
            any_failure = False
            all_success = True
            for event in tool_results:
                meta = event.metadata or {}
                success = meta.get("success")
                text = f"{event.exact_text} {event.evidence_summary}".lower()
                if success is False or any(
                    marker in text for marker in ("失败", "failed", "error", "错误")
                ):
                    any_failure = True
                    all_success = False
                elif success is True or any(
                    marker in text for marker in ("成功", "success", "ok")
                ):
                    pass
                else:
                    all_success = False
            fail_words = ("失败", "failed", "error", "错误", "未解决", "卡住")
            if all_success and not any_failure:
                for thread in unresolved:
                    if any(w in thread.lower() for w in fail_words):
                        return (
                            False,
                            "unresolved_threads cannot mark successful tool results as failed",
                        )

    summary = str(payload.get("summary") or "").strip()
    if not summary and not claimed:
        return False, "summary or source_event_ids required"
    return True, ""


def build_stub_summary(source_events: Sequence[ConversationEvent]) -> dict[str, Any]:
    """Deterministic low-confidence stub from exact source text (no LLM)."""
    bullets: list[str] = []
    commitments: list[str] = []
    unresolved: list[str] = []
    topics: list[str] = []
    for event in source_events:
        text = str(event.exact_text or event.evidence_summary or "").strip()
        if not text:
            continue
        label = event.event_type.value
        line = f"[{label}] {text}"
        if len(line) > 180:
            line = line[:177] + "..."
        bullets.append(line)
        if event.event_type in _ASSISTANT_LIKE:
            if any(k in text for k in ("提醒", "稍后", "一会", "帮你", "答应", "记得")):
                commitments.append(text[:120])
        if event.event_type is ConversationEventType.TOOL_RESULT:
            meta = event.metadata or {}
            success = meta.get("success")
            low = text.lower()
            if success is False or any(
                m in low for m in ("失败", "failed", "error", "错误")
            ):
                unresolved.append(text[:120])
        if event.event_type is ConversationEventType.USER_MESSAGE and len(topics) < 4:
            topics.append(text[:40])

    source_ids = tuple(
        str(e.event_id) for e in source_events if str(e.event_id or "").strip()
    )
    summary = "\n".join(f"- {b}" for b in bullets[:16]) or "(empty segment)"
    return {
        "source_event_ids": list(source_ids),
        "topics": topics,
        "user_state": [],
        "assistant_commitments": commitments,
        "unresolved_threads": unresolved,
        "entities": [],
        "recall_cues": topics[:3],
        "summary": summary,
        "confidence": 0.25,
        "status": "stub",
    }


def format_mid_term_block(segments: Sequence[MidTermSegment], *, max_chars: int = 1800) -> str:
    """Format mid-term segments for prompt injection."""
    if not segments:
        return ""
    lines = ["【中期会话摘要】", "（压缩承托，优先以最近原文为准）"]
    used = 0
    for seg in segments:
        header = f"- 段 {seg.segment_id[:8]} conf={seg.confidence:.2f} status={seg.status}"
        body = str(seg.summary or "").strip()
        chunk = header + "\n" + body
        if used + len(chunk) > max_chars and used > 0:
            break
        lines.append(chunk)
        used += len(chunk)
        if seg.assistant_commitments:
            lines.append("  承诺: " + "；".join(seg.assistant_commitments[:4]))
        if seg.unresolved_threads:
            lines.append("  未决: " + "；".join(seg.unresolved_threads[:4]))
    return "\n".join(lines).strip()


@dataclass(frozen=True, slots=True)
class MidTermRecallResult:
    active_session_block: str = ""
    mid_term_block: str = ""
    active_segment_id: str = ""
    recalled_segment_ids: tuple[str, ...] = ()
    error: str = ""


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(item) ** 2 for item in left))
    right_norm = math.sqrt(sum(float(item) ** 2 for item in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _segment_recall_text(segment: MidTermSegment) -> str:
    parts = [
        segment.summary,
        " ".join(segment.topics),
        " ".join(segment.entities),
        " ".join(segment.recall_cues),
        " ".join(segment.assistant_commitments),
        " ".join(segment.unresolved_threads),
    ]
    return "\n".join(str(item or "").strip() for item in parts if str(item or "").strip())


class MidTermRecallService:
    """Recall scoped mid-term context without a lexical fallback."""

    def __init__(
        self,
        *,
        segment_store: "MidTermSegmentStore",
        event_store: ConversationEventStore,
        embedding_service: Any = None,
        relevance_threshold: float = 0.72,
        history_limit: int = 20,
        recall_max_items: int = 1,
        active_max_chars: int = 1200,
        mid_term_max_chars: int = 1800,
        embedding_cache_max_items: int = 128,
    ) -> None:
        self.segment_store = segment_store
        self.event_store = event_store
        self.embedding_service = embedding_service
        self.relevance_threshold = max(0.0, min(1.0, float(relevance_threshold)))
        self.history_limit = max(2, int(history_limit or 20))
        self.recall_max_items = max(1, int(recall_max_items or 1))
        self.active_max_chars = max(200, int(active_max_chars or 1200))
        self.mid_term_max_chars = max(200, int(mid_term_max_chars or 1800))
        self.embedding_cache_max_items = max(1, int(embedding_cache_max_items or 128))
        self._segment_embedding_cache: dict[tuple[str, str], tuple[float, ...]] = {}
        self._segment_embedding_lock = threading.Lock()

    def _active_session_block(
        self,
        latest: MidTermSegment,
        events: Sequence[ConversationEvent],
        excluded_event_ids: set[str],
        *,
        include_segment: bool = True,
    ) -> str:
        header_lines = [
            "【当前会话状态｜内部参考】",
            "以下是当前会话最近压缩状态及其后发生的原始事件；原始事件优先。",
        ]
        detail_lines: list[str] = []
        if include_segment and latest.assistant_commitments:
            detail_lines.append(
                "承诺：" + "；".join(latest.assistant_commitments[:4])
            )
        if include_segment and latest.unresolved_threads:
            detail_lines.append(
                "未决：" + "；".join(latest.unresolved_threads[:4])
            )
        after = [
            event
            for event in events
            if event.scope.as_tuple() == latest.scope.as_tuple()
            and event.occurred_at > latest.range_end
            and event.event_id not in excluded_event_ids
        ]
        after.sort(key=lambda event: event.occurred_at)
        after_lines: list[str] = []
        for event in after[-8:]:
            text = str(event.exact_text or event.evidence_summary or "").strip()
            if text:
                after_lines.append(f"- [{event.event_type.value}] {text}")
        if not include_segment and not after_lines:
            return ""

        prefix = "\n".join(header_lines)
        remaining = max(0, self.active_max_chars - len(prefix) - 1)
        selected_after: list[str] = []
        for line in reversed(after_lines):
            separator = 1 if selected_after else 0
            if len(line) + separator <= remaining:
                selected_after.append(line)
                remaining -= len(line) + separator
            elif not selected_after and remaining > 0:
                selected_after.append(line[:remaining])
                remaining = 0
            if remaining <= 0:
                break
        selected_after.reverse()

        selected_details: list[str] = []
        for line in detail_lines:
            separator = 1 if selected_after or selected_details else 0
            if len(line) + separator > remaining:
                continue
            selected_details.append(line)
            remaining -= len(line) + separator

        summary_budget = max(0, remaining - (1 if selected_after or selected_details else 0))
        summary = (
            str(latest.summary or "").strip()[:summary_budget]
            if include_segment
            else ""
        )
        return "\n".join(
            item
            for item in (
                prefix,
                summary,
                *selected_details,
                *selected_after,
            )
            if item
        ).strip()

    def recall(
        self,
        *,
        current_text: str,
        scope: ConversationScope,
        available_events: Optional[Sequence[ConversationEvent]] = None,
        excluded_event_ids: Optional[set[str]] = None,
    ) -> MidTermRecallResult:
        scope.validate()
        segments = self.segment_store.list_for_scope(
            scope, limit=self.history_limit
        )
        excluded = {
            str(event_id or "").strip()
            for event_id in (excluded_event_ids or set())
            if str(event_id or "").strip()
        }
        if not segments:
            return MidTermRecallResult()

        latest = segments[0]
        latest_is_raw = bool(excluded.intersection(latest.source_event_ids))
        events = (
            list(available_events)
            if available_events is not None
            else self.event_store.list_recent(scope, now=_now(), limit=24)
        )
        active_block = self._active_session_block(
            latest,
            events,
            excluded,
            include_segment=not latest_is_raw,
        )
        older = [
            segment
            for segment in segments[1:]
            if not excluded.intersection(segment.source_event_ids)
        ]
        if not older:
            return MidTermRecallResult(
                active_session_block=active_block,
                active_segment_id=latest.segment_id,
            )
        if self.embedding_service is None:
            return MidTermRecallResult(
                active_session_block=active_block,
                active_segment_id=latest.segment_id,
                error="embedding_unavailable",
            )

        query = str(current_text or "").strip()
        documents = [_segment_recall_text(segment) for segment in older]
        try:
            keys = [
                (segment.segment_id, document)
                for segment, document in zip(older, documents)
            ]
            with self._segment_embedding_lock:
                cached = {
                    key: self._segment_embedding_cache[key]
                    for key in keys
                    if key in self._segment_embedding_cache
                }
            missing = [
                (key, document)
                for key, document in zip(keys, documents)
                if key not in cached
            ]
            vectors = self.embedding_service.embed(
                [query, *(document for _key, document in missing)]
            )
            if len(vectors) != len(missing) + 1:
                raise ValueError("embedding result count mismatch")
            query_vector = vectors[0]
            if missing:
                with self._segment_embedding_lock:
                    for index, (key, _document) in enumerate(missing, start=1):
                        vector = tuple(float(item) for item in vectors[index])
                        self._segment_embedding_cache[key] = vector
                        cached[key] = vector
                    while (
                        len(self._segment_embedding_cache)
                        > self.embedding_cache_max_items
                    ):
                        oldest = next(iter(self._segment_embedding_cache))
                        self._segment_embedding_cache.pop(oldest, None)
            segment_vectors = [cached[key] for key in keys]
            ranked = sorted(
                (
                    (_cosine_similarity(query_vector, segment_vectors[index]), segment)
                    for index, segment in enumerate(older)
                ),
                key=lambda item: item[0],
                reverse=True,
            )
        except Exception as exc:
            return MidTermRecallResult(
                active_session_block=active_block,
                active_segment_id=latest.segment_id,
                error=f"embedding_failed:{exc}",
            )

        recalled = [
            segment
            for score, segment in ranked
            if score >= self.relevance_threshold
        ][: self.recall_max_items]
        return MidTermRecallResult(
            active_session_block=active_block,
            mid_term_block=format_mid_term_block(
                recalled, max_chars=self.mid_term_max_chars
            ),
            active_segment_id=latest.segment_id,
            recalled_segment_ids=tuple(segment.segment_id for segment in recalled),
        )


class MidTermSegmentStore:
    """Persistence helpers for mid_term_segments."""

    def __init__(self, sqlite_store: Any) -> None:
        if sqlite_store is None:
            raise ValueError("sqlite_store is required")
        self.sqlite_store = sqlite_store
        ensure = getattr(sqlite_store, "ensure_mid_term_segments_schema", None)
        if callable(ensure):
            ensure()

    def save(self, segment: MidTermSegment, *, summary_payload: Optional[Mapping[str, Any]] = None) -> MidTermSegment:
        segment.validate()
        payload = dict(summary_payload or {})
        if not payload:
            payload = {
                "topics": list(segment.topics),
                "user_state": list(segment.user_state),
                "assistant_commitments": list(segment.assistant_commitments),
                "unresolved_threads": list(segment.unresolved_threads),
                "entities": list(segment.entities),
                "recall_cues": list(segment.recall_cues),
                "summary": segment.summary,
                "source_event_ids": list(segment.source_event_ids),
                "confidence": segment.confidence,
                "status": segment.status,
            }
        scope = segment.scope
        with self.sqlite_store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                INSERT OR REPLACE INTO mid_term_segments(
                  segment_id, persona_id, person_id, channel, conversation_id,
                  range_start, range_end, summary_json, source_event_ids_json,
                  confidence, embedding_json, status, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    segment.segment_id,
                    scope.persona_id.strip(),
                    scope.person_id.strip(),
                    scope.channel.strip(),
                    scope.conversation_id.strip(),
                    _to_iso(segment.range_start),
                    _to_iso(segment.range_end),
                    _json_dumps(payload),
                    _json_dumps(list(segment.source_event_ids)),
                    float(segment.confidence),
                    None,
                    str(segment.status or "active"),
                    _to_iso(_now()),
                ),
            )
            conn.commit()
        return segment

    def list_for_scope(
        self,
        scope: ConversationScope,
        *,
        limit: int = 3,
        statuses: Optional[Sequence[str]] = None,
    ) -> list[MidTermSegment]:
        scope.validate()
        limit = max(1, int(limit or 1))
        status_filter = [
            str(s).strip() for s in (statuses or ("active", "stub")) if str(s).strip()
        ]
        if not status_filter:
            status_filter = ["active", "stub"]
        placeholders = ",".join("?" for _ in status_filter)
        params: list[Any] = [
            scope.persona_id.strip(),
            scope.person_id.strip(),
            scope.channel.strip(),
            scope.conversation_id.strip(),
            *status_filter,
            limit,
        ]
        sql = f"""
            SELECT * FROM mid_term_segments
            WHERE persona_id=? AND person_id=? AND channel=? AND conversation_id=?
              AND status IN ({placeholders})
            ORDER BY range_end DESC
            LIMIT ?
        """
        with self.sqlite_store._connect() as conn:  # noqa: SLF001
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_segment(row) for row in rows]

    def _row_to_segment(self, row: Mapping[str, Any]) -> MidTermSegment:
        data = dict(row)
        payload = _json_loads(data.get("summary_json"), {})
        if not isinstance(payload, dict):
            payload = {}
        source_ids = _json_loads(data.get("source_event_ids_json"), [])
        if not isinstance(source_ids, list):
            source_ids = list(payload.get("source_event_ids") or [])
        scope = ConversationScope(
            persona_id=str(data.get("persona_id") or ""),
            person_id=str(data.get("person_id") or ""),
            channel=str(data.get("channel") or ""),
            conversation_id=str(data.get("conversation_id") or ""),
        )
        return MidTermSegment(
            segment_id=str(data.get("segment_id") or ""),
            scope=scope,
            range_start=_from_iso(data.get("range_start")) or _now(),
            range_end=_from_iso(data.get("range_end")) or _now(),
            topics=_as_str_tuple(payload.get("topics")),
            user_state=_as_str_tuple(payload.get("user_state")),
            assistant_commitments=_as_str_tuple(payload.get("assistant_commitments")),
            unresolved_threads=_as_str_tuple(payload.get("unresolved_threads")),
            entities=_as_str_tuple(payload.get("entities")),
            recall_cues=_as_str_tuple(payload.get("recall_cues")),
            source_event_ids=tuple(str(i) for i in source_ids if str(i).strip()),
            summary=str(payload.get("summary") or ""),
            confidence=float(data.get("confidence") or payload.get("confidence") or 0.0),
            status=str(data.get("status") or payload.get("status") or "active"),
        )


class MidTermSegmentBuilder:
    """Build validated mid-term segments from conversation event ids."""

    def __init__(
        self,
        *,
        store: ConversationEventStore,
        sqlite_store: Any,
        llm_callable: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.store = store
        self.segment_store = MidTermSegmentStore(sqlite_store)
        self.llm_callable = llm_callable
        self.last_error: str = ""
        self.last_status: str = ""

    def load_source_events(self, event_ids: Sequence[str]) -> list[ConversationEvent]:
        events: list[ConversationEvent] = []
        for raw_id in event_ids:
            event_id = str(raw_id or "").strip()
            if not event_id:
                continue
            event = self.store.get(event_id)
            if event is None:
                continue
            if event.event_type not in _SOURCE_TYPES:
                continue
            events.append(event)
        events.sort(key=lambda e: e.occurred_at or datetime.min.replace(tzinfo=timezone.utc))
        return events

    def build_from_event_ids(
        self,
        event_ids: Sequence[str],
        *,
        allow_stub_on_failure: bool = True,
    ) -> Optional[MidTermSegment]:
        self.last_error = ""
        self.last_status = ""
        events = self.load_source_events(event_ids)
        if not events:
            self.last_error = "no source events"
            self.last_status = "failed"
            return None

        # Hard isolation: all events must share the same scope.
        scope0 = events[0].scope
        for event in events[1:]:
            if event.scope.as_tuple() != scope0.as_tuple():
                self.last_error = "source events scope mismatch"
                self.last_status = "failed"
                return None

        payload: Optional[dict[str, Any]] = None
        if self.llm_callable is not None:
            try:
                raw = self.llm_callable(events)
                if isinstance(raw, str):
                    payload = json.loads(raw)
                elif isinstance(raw, Mapping):
                    payload = dict(raw)
                else:
                    payload = None
            except Exception as exc:
                self.last_error = f"llm_failed:{exc}"
                payload = None

        if payload is not None:
            ok, err = validate_summary(payload, events)
            if not ok:
                self.last_error = err
                payload = None

        if payload is None:
            if not allow_stub_on_failure:
                self.last_status = "failed"
                return None
            payload = build_stub_summary(events)
            self.last_status = "stub"
        else:
            self.last_status = str(payload.get("status") or "active")

        ok, err = validate_summary(payload, events)
        if not ok:
            self.last_error = err
            self.last_status = "failed"
            return None

        range_start = events[0].occurred_at
        range_end = events[-1].occurred_at
        segment = MidTermSegment(
            segment_id=str(uuid.uuid4().hex),
            scope=scope0,
            range_start=range_start,
            range_end=range_end,
            topics=_as_str_tuple(payload.get("topics")),
            user_state=_as_str_tuple(payload.get("user_state")),
            assistant_commitments=_as_str_tuple(payload.get("assistant_commitments")),
            unresolved_threads=_as_str_tuple(payload.get("unresolved_threads")),
            entities=_as_str_tuple(payload.get("entities")),
            recall_cues=_as_str_tuple(payload.get("recall_cues")),
            source_event_ids=_as_str_tuple(payload.get("source_event_ids")),
            summary=str(payload.get("summary") or "").strip(),
            confidence=float(payload.get("confidence") or 0.0),
            status=str(payload.get("status") or self.last_status or "active"),
        )
        return self.segment_store.save(segment, summary_payload=payload)
