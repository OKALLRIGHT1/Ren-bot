"""Traceable mid-term conversation segments (compressed event projections)."""

from __future__ import annotations

import json
import re
import uuid
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

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False, "confidence must be a number"
    if confidence < 0.0 or confidence > 1.0:
        return False, "confidence must be in [0, 1]"

    blob = _source_blob(source_events)
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
        [e for e in source_events if e.event_type in _ASSISTANT_LIKE]
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
            for e in source_events
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
