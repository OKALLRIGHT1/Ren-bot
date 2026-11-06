"""Person-fact / chat-summary writeback into Memory Core long-term records.

Inspired by MaiBot PersonFactWriteback + chat summary writeback:

- Transcript always stays as raw evidence.
- Long-term memory_records are written only when a model extracts stable,
  user-supported facts (or a chat-window summary).
- Empty extraction means "do not remember".
- Corrections supersede the same stable key via MemoryCoreRepository.upsert_record.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence


logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_BACKOFF_CAP_SEC = 60.0
_FAILED_RETENTION_DAYS = 7


def _content_fingerprint(text: str) -> str:
    compact = re.sub(r"[\s\W_]+", "", str(text or "").lower(), flags=re.UNICODE)
    if not compact:
        return ""
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]


def _job_to_payload(job: "WritebackJob") -> dict[str, Any]:
    return {
        "session_id": job.session_id,
        "person_id": job.person_id,
        "trigger_role": job.trigger_role,
        "trigger_text": job.trigger_text,
        "reason": job.reason,
        "trigger_transcript_id": job.trigger_transcript_id,
        "character_id": job.character_id,
        "character_name": job.character_name,
        "meta": job.meta,
    }


def _job_from_payload(payload: dict[str, Any]) -> Optional["WritebackJob"]:
    if not isinstance(payload, dict):
        return None
    return WritebackJob(
        session_id=str(payload.get("session_id") or ""),
        person_id=str(payload.get("person_id") or "owner"),
        trigger_role=str(payload.get("trigger_role") or "user"),
        trigger_text=str(payload.get("trigger_text") or ""),
        reason=str(payload.get("reason") or "user_statement"),
        trigger_transcript_id=str(payload.get("trigger_transcript_id") or ""),
        character_id=str(payload.get("character_id") or ""),
        character_name=str(payload.get("character_name") or ""),
        meta=dict(payload.get("meta") or {}),
    )

LLMCall = Callable[..., str]

ALLOWED_KINDS = frozenset({"preference", "fact", "rule", "profile"})
WEEKDAY_TERMS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_ENTITY_STOPWORDS = frozenset(
    {"这个", "那个", "什么", "怎么", "我们", "你们", "他们", "自己", "用户", "主人"}
)
EPHEMERAL_MARKERS = (
    "哈哈",
    "好的",
    "收到",
    "嗯嗯",
    "晚安",
    "早安",
    "拜拜",
    "谢谢",
    "在吗",
    "？",
    "?",
    "哦",
    "嗯",
    "啊",
    "嘿",
)


@dataclass(frozen=True)
class WritebackJob:
    session_id: str
    person_id: str
    trigger_role: str
    trigger_text: str
    reason: str
    trigger_transcript_id: str = ""
    character_id: str = ""
    character_name: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ExtractedFact:
    kind: str
    key: str
    content: str
    confidence: float
    valid_from: str = ""
    valid_until: str = ""
    evidence_ids: tuple[str, ...] = ()
    is_correction: bool = False
    supersede_keys: tuple[str, ...] = ()
    category_override: str = ""
    reason: str = ""
    fact_id: str = ""
    cardinality: str = "single"
    stability: str = "stable"


@dataclass(frozen=True)
class ExtractedRelation:
    subject: str
    predicate: str
    object_name: str
    fact_id: str
    confidence: float = 0.8


@dataclass(frozen=True)
class Extraction:
    facts: list[ExtractedFact] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    summary: str = ""


@dataclass(frozen=True)
class WritebackResult:
    job_reason: str
    facts_written: int = 0
    facts_skipped: int = 0
    summary_written: bool = False
    detail: str = ""
    record_ids: tuple[str, ...] = ()


class MemoryWritebackService:
    """Async long-term memory writeback. Chat inserts a SQLite row and returns."""

    def __init__(
        self,
        memory_core: Any,
        *,
        llm_call: Optional[LLMCall] = None,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        self.memory_core = memory_core
        self.llm_call = llm_call or getattr(memory_core, "llm_call", None)
        self.settings = dict(settings or getattr(memory_core, "settings", {}) or {})
        self.enabled = self._setting_bool("memory_writeback_enabled", True)
        self.person_fact_enabled = self._setting_bool(
            "memory_writeback_person_fact_enabled", True
        )
        self.chat_summary_enabled = self._setting_bool(
            "memory_writeback_chat_summary_enabled", True
        )
        # Kept so old settings still parse. Durability no longer drops on a full queue.
        self.queue_maxsize = self._setting_int(
            "memory_writeback_queue_maxsize", 256, minimum=8, maximum=2000
        )
        self.context_messages = self._setting_int(
            "memory_writeback_context_messages", 12, minimum=2, maximum=40
        )
        self.summary_message_threshold = self._setting_int(
            "memory_writeback_summary_message_threshold",
            24,
            minimum=4,
            maximum=200,
        )
        self.min_confidence = self._setting_float(
            "memory_writeback_min_confidence", 0.7, minimum=0.0, maximum=1.0
        )
        self.max_facts_per_job = self._setting_int(
            "memory_writeback_max_facts", 5, minimum=1, maximum=12
        )
        self.session_cooldown_sec = self._setting_float(
            "memory_writeback_session_cooldown_sec", 2.0, minimum=0.0, maximum=120.0
        )
        self.explicit_user_immediate = self._setting_bool(
            "memory_writeback_explicit_user_immediate", True
        )
        # When true, process jobs on the caller thread (tests / debug only).
        self.inline = self._setting_bool("memory_writeback_inline", False)
        self.task_type = str(
            self.settings.get("memory_writeback_task_type") or "gatekeeper"
        ).strip() or "gatekeeper"

        self._wake = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False
        self._start_lock = threading.Lock()
        self._last_job_at: dict[str, float] = {}
        self._seen_fingerprints: set[str] = set()
        self._summary_counters: dict[str, int] = {}
        self._summary_last_window: dict[str, str] = {}
        self._stats_lock = threading.Lock()
        self._stats = {
            "enqueued": 0,
            "dropped": 0,
            "processed": 0,
            "facts_written": 0,
            "summaries_written": 0,
            "errors": 0,
            "failed_explicit": 0,
        }

    # ------------------------------------------------------------------ config
    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    def _setting_int(
        self, key: str, default: int, *, minimum: int, maximum: int
    ) -> int:
        try:
            value = int(self.settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _setting_float(
        self, key: str, default: float, *, minimum: float, maximum: float
    ) -> float:
        try:
            value = float(self.settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.enabled:
            return
        with self._start_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stopping = False
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="MemoryWritebackWorker",
                daemon=True,
            )
            self._worker.start()
            logger.info("memory writeback worker started")

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stopping = True
        self._wake.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        if worker is None or not worker.is_alive():
            self._worker = None

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def flush(self, *, timeout: float = 5.0) -> bool:
        """Wait until no pending or running writeback row remains."""
        if self.inline:
            return True
        self._wake.set()
        deadline = time.time() + max(0.05, float(timeout))
        while time.time() < deadline:
            if not self._has_open_jobs():
                return True
            self._wake.set()
            time.sleep(0.02)
        return not self._has_open_jobs()

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] = int(self._stats.get(key, 0)) + amount

    # ------------------------------------------------------------------ enqueue
    def observe_message(
        self,
        role: str,
        content: str,
        *,
        session_id: str = "",
        person_id: str = "owner",
        character_id: str = "",
        character_name: str = "",
        transcript_id: Any = None,
        meta: Optional[dict[str, Any]] = None,
        recent_messages: Optional[Sequence[dict[str, Any]]] = None,
    ) -> bool:
        """Called from MemoryCore after transcript write. Non-blocking."""
        if not self.enabled or self.llm_call is None:
            return False
        role = str(role or "").strip().lower()
        content = str(content or "").strip()
        if role not in {"user", "assistant"} or not content:
            return False
        safe_meta = dict(meta or {})
        if not self._is_trusted_source(safe_meta):
            return False
        if not self._is_real_message(role, content, safe_meta):
            return False

        session_key = str(session_id or "global").strip() or "global"
        person = str(person_id or "owner").strip() or "owner"
        with self._stats_lock:
            self._summary_counters[session_key] = (
                int(self._summary_counters.get(session_key, 0)) + 1
            )
            summary_count = self._summary_counters[session_key]

        reasons: list[str] = []
        if role == "assistant" and self.person_fact_enabled:
            if not self._looks_ephemeral(content):
                reasons.append("assistant_reply")
        if (
            role == "user"
            and self.person_fact_enabled
            and self.explicit_user_immediate
            and self._has_writeback_signal(content)
        ):
            reasons.append("explicit_user")
        if role == "user" and self.person_fact_enabled and "explicit_user" not in reasons:
            if not self._looks_ephemeral(content):
                reasons.append("user_statement")
        if (
            self.chat_summary_enabled
            and summary_count >= self.summary_message_threshold
        ):
            reasons.append("chat_summary_window")

        enqueued_any = False
        for reason in reasons:
            job = WritebackJob(
                session_id=session_key,
                person_id=person,
                trigger_role=role,
                trigger_text=content,
                reason=reason,
                trigger_transcript_id=str(transcript_id or "").strip(),
                character_id=str(character_id or "").strip(),
                character_name=str(character_name or "").strip(),
                meta={
                    **safe_meta,
                    "_recent_messages": list(recent_messages or [])[-self.context_messages :],
                },
            )
            if self.enqueue(job):
                enqueued_any = True
                if reason == "chat_summary_window":
                    with self._stats_lock:
                        self._summary_counters[session_key] = 0
        return enqueued_any

    def enqueue(self, job: WritebackJob) -> bool:
        if not self.enabled:
            return False
        fingerprint = _content_fingerprint(job.trigger_text)
        fingerprint_key = f"{job.session_id}:{job.person_id}:{fingerprint}"
        with self._stats_lock:
            duplicate = bool(fingerprint and fingerprint_key in self._seen_fingerprints)
        if duplicate:
            self._bump("dropped")
            return False
        row_id = self._insert_job(job, status="running" if self.inline else "pending")
        if row_id is None:
            self._bump("dropped")
            return False
        if fingerprint:
            with self._stats_lock:
                self._seen_fingerprints.add(fingerprint_key)
                if len(self._seen_fingerprints) > 500:
                    self._seen_fingerprints = set(list(self._seen_fingerprints)[-300:])
        self._bump("enqueued")
        if self.inline:
            return self._finish_inline(row_id, job)
        self.start()
        self._wake.set()
        return True

    def _store(self) -> Any:
        return getattr(self.memory_core, "store", None)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _due_after(self, seconds: float) -> str:
        current = datetime.fromisoformat(self._now_iso())
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return (current + timedelta(seconds=seconds)).isoformat()

    def _insert_job(self, job: WritebackJob, *, status: str) -> Optional[int]:
        store = self._store()
        insert = getattr(store, "insert_writeback_job", None)
        if not callable(insert):
            logger.warning("memory writeback has no durable store")
            return None
        try:
            payload = json.loads(
                json.dumps(_job_to_payload(job), ensure_ascii=False, default=str)
            )
            return int(insert(payload, status=status))
        except Exception as exc:
            logger.warning("memory writeback insert failed: %s", exc)
            return None

    def _has_open_jobs(self) -> bool:
        store = self._store()
        probe = getattr(store, "has_open_writeback_jobs", None)
        if not callable(probe):
            return False
        try:
            return bool(probe())
        except Exception as exc:
            logger.warning("memory writeback open-job check failed: %s", exc)
            return False

    def _recover(self) -> None:
        store = self._store()
        recover = getattr(store, "recover_writeback_jobs", None)
        if not callable(recover):
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FAILED_RETENTION_DAYS)
        try:
            recover(failed_before_iso=cutoff.isoformat())
        except Exception as exc:
            logger.warning("memory writeback recover failed: %s", exc)

    def _finish_inline(self, row_id: int, job: WritebackJob) -> bool:
        try:
            self.process_job(job)
        except Exception as exc:
            self._bump("errors")
            logger.warning("inline memory writeback failed: %s", exc, exc_info=True)
            self._record_failure(row_id, 0, job, exc)
            return False
        self._delete_job(row_id)
        self._bump("processed")
        return True

    def _delete_job(self, row_id: int) -> None:
        store = self._store()
        delete = getattr(store, "delete_writeback_job", None)
        if not callable(delete):
            return
        try:
            delete(row_id)
        except Exception as exc:
            logger.warning("memory writeback delete failed: %s", exc)

    def _record_failure(
        self, row_id: int, attempts: int, job: WritebackJob, exc: BaseException
    ) -> None:
        store = self._store()
        reschedule = getattr(store, "reschedule_writeback_job", None)
        if not callable(reschedule):
            return
        next_attempts = int(attempts) + 1
        message = str(exc or "").strip() or exc.__class__.__name__
        if next_attempts >= _MAX_ATTEMPTS:
            status = "failed"
            due_at = ""
            if job.reason == "explicit_user":
                self._bump("failed_explicit")
                logger.warning(
                    "memory writeback gave up on explicit_user after %s attempts: %s",
                    next_attempts,
                    str(job.trigger_text or "")[:80],
                )
        else:
            status = "pending"
            delay = min(_BACKOFF_CAP_SEC, float(2 ** next_attempts))
            due_at = self._due_after(delay)
        try:
            reschedule(
                row_id,
                status=status,
                attempts=next_attempts,
                due_at=due_at,
                last_error=message,
            )
        except Exception as write_exc:
            logger.warning("memory writeback reschedule failed: %s", write_exc)

    def _process_one_due_job(self) -> bool:
        store = self._store()
        claim = getattr(store, "claim_due_writeback_job", None)
        if not callable(claim):
            return False
        try:
            claimed = claim(self._now_iso())
        except Exception as exc:
            logger.warning("memory writeback claim failed: %s", exc)
            return False
        if not claimed:
            return False
        row_id = int(claimed.get("id") or 0)
        attempts = int(claimed.get("attempts") or 0)
        job = _job_from_payload(dict(claimed.get("payload") or {}))
        if job is None or row_id <= 0:
            if row_id > 0:
                self._delete_job(row_id)
            return True
        try:
            self.process_job(job)
        except Exception as exc:
            self._bump("errors")
            logger.warning("memory writeback job failed: %s", exc, exc_info=True)
            self._record_failure(row_id, attempts, job, exc)
        else:
            self._delete_job(row_id)
        self._bump("processed")
        return True

    # ------------------------------------------------------------------ worker
    def _worker_loop(self) -> None:
        self._recover()
        while not self._stopping:
            worked = False
            try:
                worked = self._process_one_due_job()
            except Exception as exc:
                logger.warning("memory writeback worker step failed: %s", exc, exc_info=True)
            if worked:
                continue
            self._wake.wait(timeout=0.5)
            self._wake.clear()

    def process_job(self, job: WritebackJob) -> WritebackResult:
        """Process one job synchronously (used by worker and tests)."""
        if job.reason == "chat_summary_window":
            return self._process_chat_summary(job)
        return self._process_person_facts(job)

    # ------------------------------------------------------------------ filters
    @staticmethod
    def _is_trusted_source(meta: dict[str, Any]) -> bool:
        source = str(meta.get("source") or "").strip().lower()
        if source in {"qq_gateway", "napcat_qq"}:
            if bool(meta.get("is_owner")):
                return True
            # 群友或私聊联系人的原话可以写入他们自己的画像，不能写进主人。
            return bool(str(meta.get("user_id") or "").strip())
        return True

    @staticmethod
    def _is_real_message(role: str, content: str, meta: dict[str, Any]) -> bool:
        if bool(meta.get("hidden")) or "[CMD:" in content or content.startswith("[tool_"):
            return False
        if bool(meta.get("tool")):
            return False
        path = str(meta.get("path") or "").strip().lower()
        blocked = (
            "tool",
            "background",
            "proactive",
            "task_followup",
            "sensor",
            "system",
            "short_reaction",
        )
        if any(path.startswith(prefix) for prefix in blocked):
            return False
        if "```" in content or len(content) > 1200:
            return False
        return role in {"user", "assistant"}

    @staticmethod
    def _looks_ephemeral(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return True
        if len(content) <= 8 and any(marker in content for marker in EPHEMERAL_MARKERS):
            return True
        return False

    @staticmethod
    def _has_writeback_signal(text: str) -> bool:
        clean = str(text or "").strip().lower()
        if not clean:
            return False
        # Pure questions / recall probes are not write signals.
        questionish = (
            clean.endswith(("?", "？", "吗", "么", "呢"))
            or any(
                cue in clean
                for cue in (
                    "周几",
                    "哪天",
                    "哪一",
                    "什么时候",
                    "记得吗",
                    "还记得",
                    "是不是",
                )
            )
        )
        statement_cues = (
            "其实",
            "不是",
            "记住",
            "记下来",
            "帮我记",
            "我喜欢",
            "我不喜欢",
            "我习惯",
            "以后叫",
            "叫我",
            "过敏",
        )
        if questionish and not any(cue in clean for cue in statement_cues):
            return False
        signals = (
            "我喜欢",
            "我不喜欢",
            "我讨厌",
            "叫我",
            "称呼我",
            "我希望你",
            "不要叫我",
            "我习惯",
            "我正在",
            "我最近",
            "我更喜欢",
            "我平常",
            "我一般",
            "我通常",
            "其实是",
            "其实我",
            "不是周",
            "是周",
            "每周",
            "固定周",
            "记住",
            "记下来",
            "帮我记",
            "以后叫",
            "别再",
            "不要再",
            "过敏",
            "我住",
            "我在",
            "我的名字",
        )
        if any(signal in clean for signal in signals):
            return True
        if any(day in clean for day in WEEKDAY_TERMS) and any(
            term in clean for term in ("开会", "会议", "上班", "休息", "值班")
        ):
            return True
        return False

    # ------------------------------------------------------------------ evidence
    def _collect_evidence_messages(self, job: WritebackJob) -> list[dict[str, Any]]:
        recent = list(job.meta.get("_recent_messages") or [])
        messages: list[dict[str, Any]] = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = self._clean_text(item.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append(
                {
                    "id": str(item.get("id") or "").strip(),
                    "role": role,
                    "content": content,
                    "observed_at": str(item.get("ts_iso") or item.get("observed_at") or "").strip(),
                }
            )

        # Prefer store transcript when available (stable ids).
        store = getattr(self.memory_core, "store", None)
        if store is not None and hasattr(store, "list_transcript"):
            try:
                rows = store.list_transcript(
                    session_id=job.session_id if job.session_id != "global" else None,
                    session_scope="specific" if job.session_id not in {"", "global"} else "all",
                    limit=self.context_messages,
                    offset=0,
                )
                # list_transcript returns newest first
                ordered = list(reversed(rows or []))
                store_msgs = []
                for row in ordered:
                    role = str(row.get("role") or "").strip().lower()
                    content = self._clean_text(row.get("content"))
                    if role not in {"user", "assistant"} or not content:
                        continue
                    if not self._is_real_message(role, content, row.get("meta") or {}):
                        continue
                    store_msgs.append(
                        {
                            "id": str(row.get("id") or "").strip(),
                            "role": role,
                            "content": content[:500],
                            "observed_at": str(row.get("ts_iso") or "").strip(),
                        }
                    )
                if store_msgs:
                    messages = store_msgs[-self.context_messages :]
            except Exception as exc:
                logger.debug("writeback transcript load failed: %s", exc)

        # Ensure trigger text is present.
        if job.trigger_text:
            trigger_id = str(job.trigger_transcript_id or "").strip() or f"trigger:{job.reason}"
            if not any(
                str(item.get("content") or "") == job.trigger_text
                or str(item.get("id") or "") == trigger_id
                for item in messages
            ):
                messages.append(
                    {
                        "id": trigger_id,
                        "role": job.trigger_role,
                        "content": job.trigger_text[:500],
                    }
                )
        return messages[-self.context_messages :]

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or "[CMD:" in text or text.startswith("[tool_"):
            return ""
        return text

    def _format_evidence(self, messages: Sequence[dict[str, Any]]) -> tuple[str, str, list[str]]:
        user_lines: list[str] = []
        context_lines: list[str] = []
        user_ids: list[str] = []
        for item in messages:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            mid = str(item.get("id") or "")
            line = f"id={mid} {role}: {content}"
            if role == "user":
                user_lines.append(line)
                if mid:
                    user_ids.append(mid)
            context_lines.append(line)
        user_block = "\n".join(user_lines) if user_lines else "(无目标用户发言)"
        context_block = "\n".join(context_lines) if context_lines else "(无上下文)"
        return user_block, context_block, user_ids

    # ------------------------------------------------------------------ person facts
    def _process_person_facts(self, job: WritebackJob) -> WritebackResult:
        if not self.person_fact_enabled or self.llm_call is None:
            return WritebackResult(job_reason=job.reason, detail="person_fact_disabled")

        messages = self._collect_evidence_messages(job)
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return WritebackResult(job_reason=job.reason, detail="no_user_evidence")

        user_block, context_block, user_ids = self._format_evidence(messages)
        assistant_text = ""
        for item in reversed(messages):
            if item.get("role") == "assistant":
                assistant_text = str(item.get("content") or "")
                break
        if job.trigger_role == "assistant":
            assistant_text = job.trigger_text or assistant_text

        allowed_ids = {
            str(m.get("id") or "")
            for m in messages
            if str(m.get("id") or "").strip()
        }
        user_id_set = set(user_ids)
        extraction = self._extract_person_facts(
            person_id=job.person_id,
            user_evidence=user_block,
            context_block=context_block,
            assistant_text=assistant_text,
            allowed_evidence_ids=allowed_ids,
            preferred_user_evidence_ids=user_id_set,
        )
        facts = extraction.facts
        if not facts and not extraction.relations and not extraction.summary:
            self._note_checkpoint(job)
            return WritebackResult(job_reason=job.reason, detail="no_facts")

        source_map = {str(m.get("id") or ""): m for m in messages if m.get("id")}
        written_ids: list[str] = []
        written_facts: dict[str, ExtractedFact] = {}
        fact_records: dict[str, str] = {}
        written_pairs: list[tuple[ExtractedFact, str]] = []
        skipped = 0
        for fact in facts[: self.max_facts_per_job]:
            # Hard gate: at least one evidence must be user-supported when user
            # messages exist in the window (MaiBot-style).
            if user_id_set and not any(
                eid in user_id_set or str(eid).startswith("trigger:")
                for eid in fact.evidence_ids
            ):
                skipped += 1
                continue
            record_id = self._upsert_fact(job, fact, source_map)
            if record_id:
                written_ids.append(record_id)
                written_pairs.append((fact, record_id))
                if fact.fact_id:
                    written_facts[fact.fact_id] = fact
                    fact_records[fact.fact_id] = record_id
                self._store_paragraph(job, fact.content, record_id)
            else:
                skipped += 1
        relation_ids = self._apply_world(
            job,
            extraction,
            written_facts=written_facts,
            fact_records=fact_records,
            written_pairs=written_pairs,
        )
        written_ids.extend(relation_ids)
        if extraction.summary:
            summary_id = self._store_summary(job, extraction.summary)
            if summary_id:
                written_ids.append(summary_id)
        self._note_checkpoint(job)
        self._bump("facts_written", len(written_ids))
        return WritebackResult(
            job_reason=job.reason,
            facts_written=len(written_ids),
            facts_skipped=skipped,
            detail="ok" if written_ids else "all_skipped",
            record_ids=tuple(written_ids),
        )

    def _extract_person_facts(
        self,
        *,
        person_id: str,
        user_evidence: str,
        context_block: str,
        assistant_text: str,
        allowed_evidence_ids: set[str],
        preferred_user_evidence_ids: Optional[set[str]] = None,
    ) -> Extraction:
        preferred_user_evidence_ids = set(preferred_user_evidence_ids or set())
        prompt = (
            "你是长期记忆写回器。只根据目标用户原话，抽出以后还有用的增量。\n"
            "邻近上下文只用于还原「这个」「那个」这类指代。还原不了的关系不要写。\n"
            "机器人回复不能单独作为事实来源。同群出现不推断人际关系。\n"
            "临时安排不要写成长期偏好、身份或健康状况。更正只写最终状态，"
            "并标记 is_correction=true。\n"
            "不要提取闲聊、客套、玩笑、猜测、问句本身、只有机器人说过的话。\n"
            "目录里的人必须沿用给定 person_id，不要把昵称并成另一个人。"
            "目录外的真实对象保留原名。\n"
            "relations 里的每条边必须给出支撑它的 fact_id。两个名字一起出现不等于有关系。\n"
            "evidence_ids 必须引用下面以 id= 开头的用户消息。\n"
            "没有增量时返回空的 summary、entities、relations、facts。\n"
            "cardinality：称呼、星期、唯一身份用 single；喜好和可以并存的关系用 set。\n"
            "严格只输出 JSON：\n"
            '{"summary":"","entities":["名字"],'
            '"relations":[{"subject":"","predicate":"","object":"","fact_id":"f1"}],'
            '"facts":[{"fact_id":"f1","kind":"preference|fact|rule|profile",'
            '"key":"稳定英文或点分键","content":"简短中文","confidence":0.0,'
            '"cardinality":"single|set","stability":"stable|temporal",'
            '"valid_from":"","valid_until":"","evidence_ids":["用户消息id"],'
            '"is_correction":false,"supersede_keys":[],"category_override":"","reason":""}]}\n'
            "key 示例：preferred_address, habit.meeting_weekday, likes.food。\n"
            "habit/周几类事实 category_override 填 habits。\n\n"
            f"目标人物 person_id={person_id}\n"
            f"目标用户原始发言证据：\n{user_evidence}\n\n"
            f"邻近上下文：\n{context_block}\n\n"
            f"机器人回复（仅参考，不可单独取证）：\n{assistant_text or '(无)'}\n"
        )
        try:
            raw = self.llm_call(
                [{"role": "user", "content": prompt}],
                task_type=self.task_type,
                caller="memory_writeback_extract",
            )
        except Exception as exc:
            logger.warning("memory writeback extract failed: %s", exc)
            return Extraction()
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return Extraction()
        raw_items = payload.get("facts")
        if not isinstance(raw_items, list):
            raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raw_items = []

        facts: list[ExtractedFact] = []
        seen_keys: set[str] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "fact").strip().lower()
            if kind not in ALLOWED_KINDS:
                continue
            key = self._normalize_key(str(item.get("key") or ""))
            content = self._clean_text(item.get("content"))[:300]
            if not key or not content or len(content) < 4:
                continue
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
            except Exception:
                confidence = 0.0
            if confidence < self.min_confidence:
                continue
            evidence_ids = []
            for value in item.get("evidence_ids") or []:
                eid = str(value or "").strip()
                if not eid:
                    continue
                if allowed_evidence_ids and eid not in allowed_evidence_ids:
                    # Allow synthetic trigger ids used when store ids are missing.
                    if not eid.startswith("trigger:"):
                        continue
                evidence_ids.append(eid)
            if not evidence_ids:
                continue
            # Prefer / require user evidence when available.
            if preferred_user_evidence_ids:
                user_hits = [
                    eid
                    for eid in evidence_ids
                    if eid in preferred_user_evidence_ids
                    or str(eid).startswith("trigger:")
                ]
                if not user_hits:
                    continue
                # Put user evidence first for storage.
                evidence_ids = user_hits + [
                    eid for eid in evidence_ids if eid not in user_hits
                ]
            valid_from = self._explicit_bound(item.get("valid_from"))
            valid_until = self._explicit_bound(item.get("valid_until"))
            supersede_keys = tuple(
                self._normalize_key(str(value))
                for value in (item.get("supersede_keys") or [])
                if self._normalize_key(str(value))
            )
            category = str(item.get("category_override") or "").strip()
            if key.startswith("habit") and not category:
                category = "habits"
            # Auto-detect correction language in content/reason when model forgets flag.
            is_correction = bool(item.get("is_correction"))
            reason_text = str(item.get("reason") or "")
            if not is_correction and any(
                cue in (content + reason_text)
                for cue in ("纠正", "更正", "其实是", "不是", "改成", "改为")
            ):
                is_correction = True
            dedupe = f"{kind}:{key}:{content}"
            if dedupe in seen_keys:
                continue
            seen_keys.add(dedupe)
            cardinality = str(item.get("cardinality") or "single").strip().lower()
            if cardinality not in {"single", "set"}:
                cardinality = "single"
            stability = str(item.get("stability") or "stable").strip().lower()
            if stability not in {"stable", "temporal"}:
                stability = "stable"
            facts.append(
                ExtractedFact(
                    kind=kind,
                    key=key,
                    content=content,
                    confidence=confidence,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    evidence_ids=tuple(evidence_ids[:4]),
                    is_correction=is_correction,
                    supersede_keys=supersede_keys,
                    category_override=category,
                    reason=reason_text[:200],
                    fact_id=str(item.get("fact_id") or "").strip(),
                    cardinality=cardinality,
                    stability=stability,
                )
            )
        entities = []
        raw_entities = payload.get("entities")
        if isinstance(raw_entities, list):
            for name in raw_entities:
                clean_name = self._clean_text(name)[:40]
                if len(clean_name) >= 2 and clean_name not in _ENTITY_STOPWORDS:
                    entities.append(clean_name)
        relations: list[ExtractedRelation] = []
        raw_relations = payload.get("relations") if isinstance(payload, dict) else None
        if isinstance(raw_relations, list):
            known_facts = {fact.fact_id for fact in facts if fact.fact_id}
            for item in raw_relations:
                if not isinstance(item, dict):
                    continue
                fact_id = str(item.get("fact_id") or "").strip()
                if not fact_id or fact_id not in known_facts:
                    continue
                subject = self._clean_text(item.get("subject"))[:40]
                predicate = self._clean_text(item.get("predicate"))[:20]
                object_name = self._clean_text(item.get("object"))[:40]
                if (
                    len(subject) < 2
                    or len(object_name) < 2
                    or not predicate
                    or subject in _ENTITY_STOPWORDS
                    or object_name in _ENTITY_STOPWORDS
                ):
                    continue
                relations.append(
                    ExtractedRelation(
                        subject=subject,
                        predicate=predicate,
                        object_name=object_name,
                        fact_id=fact_id,
                    )
                )
        summary = self._clean_text(payload.get("summary") if isinstance(payload, dict) else "")[:300]
        return Extraction(
            facts=facts,
            entities=entities,
            relations=relations,
            summary=summary,
        )

    def _upsert_fact(
        self,
        job: WritebackJob,
        fact: ExtractedFact,
        source_map: dict[str, dict[str, Any]],
    ) -> str:
        fingerprint = hashlib.sha256(
            f"{job.person_id}|{fact.kind}|{fact.key}|{fact.content}|{','.join(fact.evidence_ids)}".encode("utf-8")
        ).hexdigest()[:20]
        source_id = f"person_fact:{job.person_id}:{fingerprint}"

        key = fact.key
        status = "active"
        supersede_keys = fact.supersede_keys
        if fact.cardinality == "set" and not fact.is_correction:
            digest = hashlib.sha256(self._normalize_key(fact.content).encode("utf-8")).hexdigest()[:8]
            key = f"{fact.key}.{digest}"
        elif not fact.is_correction:
            finder = getattr(getattr(self.memory_core, "repository", None), "find_active_persona", None)
            existing = finder(subject_id=job.person_id, kind=fact.kind, key=fact.key) if callable(finder) else None
            if existing and str(existing.get("content") or "").strip() != fact.content:
                status = "conflicted"
                supersede_keys = ()
        metadata: dict[str, Any] = {
            "writeback_source": "memory_writeback",
            "writeback_reason": job.reason,
            "evidence_source": "user_supported",
            "is_correction": bool(fact.is_correction),
            "extract_reason": fact.reason,
            "cardinality": fact.cardinality,
            "stability": fact.stability,
            "fact_key": fact.key,
        }
        if fact.category_override:
            metadata["category_override"] = fact.category_override

        evidence = []
        observed_at = ""
        for eid in fact.evidence_ids:
            src = source_map.get(eid) or {}
            quote = str(src.get("content") or job.trigger_text or "")[:500]
            item_observed = str(src.get("observed_at") or src.get("ts_iso") or "").strip()
            if item_observed and not observed_at:
                observed_at = item_observed
            evidence.append(
                {
                    "type": "transcript",
                    "id": eid,
                    "quote": quote,
                    "observed_at": item_observed,
                }
            )

        importance = 0.6
        if fact.key in {"preferred_address", "reply_style"}:
            importance = 0.75
        if fact.key.startswith("habit") or fact.is_correction:
            importance = max(importance, 0.85)

        try:
            record_id = self.memory_core.upsert_memory_record(
                kind=fact.kind,
                key=key,
                content=fact.content,
                subject_id=job.person_id,
                session_id="",
                source_type="person_fact_writeback",
                source_id=source_id,
                confidence=fact.confidence,
                importance=importance,
                status=status,
                valid_from=fact.valid_from or None,
                valid_until=fact.valid_until or None,
                metadata=metadata,
                evidence=evidence,
                supersede_keys=supersede_keys,
                observed_at=observed_at or None,
            )
            return str(record_id or "")
        except Exception as exc:
            logger.warning("writeback upsert failed: %s", exc)
            return ""

    @staticmethod
    def _explicit_bound(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text.lower() in {"0", "null", "none", "undefined"}:
            return ""
        return text

    def _store_paragraph(self, job: WritebackJob, content: str, record_id: str) -> str:
        repo = getattr(self.memory_core, "repository", None)
        upsert = getattr(repo, "upsert_paragraph", None)
        if not callable(upsert):
            return ""
        return str(
            upsert(
                external_id=f"person_fact:{record_id}",
                content=content,
                source_type="person_fact_writeback",
                session_id=job.session_id,
                subject_id=job.person_id,
            )
            or ""
        )

    def _note_checkpoint(self, job: WritebackJob) -> None:
        trigger_id = str(job.trigger_transcript_id or "").strip()
        if not trigger_id:
            return
        repo = getattr(self.memory_core, "repository", None)
        setter = getattr(repo, "set_meta", None)
        if not callable(setter):
            return
        setter(
            f"writeback_cursor:{job.session_id}:{job.person_id}",
            trigger_id,
        )

    def _store_summary(self, job: WritebackJob, summary: str) -> str:
        text = self._clean_text(summary)[:300]
        if len(text) < 4:
            return ""
        session_id = "" if job.session_id == "global" else job.session_id
        try:
            record_id = self.memory_core.upsert_memory_record(
                kind="summary",
                key=f"chat_window:{job.session_id or 'global'}",
                content=text,
                subject_id=job.person_id,
                session_id=session_id,
                source_type="chat_window_writeback",
                source_id=f"chat_window:{job.session_id}:{job.person_id}",
                confidence=0.7,
                importance=0.5,
                metadata={
                    "writeback_source": "memory_writeback",
                    "writeback_reason": job.reason,
                },
            )
        except Exception as exc:
            logger.warning("writeback window summary failed: %s", exc)
            return ""
        return str(record_id or "")

    def _apply_world(
        self,
        job: WritebackJob,
        extraction: Extraction,
        *,
        written_facts: dict[str, ExtractedFact],
        fact_records: dict[str, str],
        written_pairs: list[tuple[ExtractedFact, str]],
    ) -> list[str]:
        repo = getattr(self.memory_core, "repository", None)
        if repo is None or not hasattr(repo, "upsert_entity"):
            return []
        cache: dict[str, str] = {}
        names = list(extraction.entities)
        for relation in extraction.relations:
            names.extend((relation.subject, relation.object_name))
        for name in names:
            self._entity_id(repo, cache, name)
        for fact, record_id in written_pairs:
            content_norm = repo.normalize_entity_name(fact.content)
            hits = [
                entity_id
                for norm, entity_id in cache.items()
                if norm and norm in content_norm
            ]
            if hits:
                repo.link_record_entities(record_id, hits)
        written: list[str] = []
        for relation in extraction.relations:
            fact = written_facts.get(relation.fact_id)
            fact_record = fact_records.get(relation.fact_id)
            if fact is None or not fact_record:
                continue
            subject_id = self._entity_id(repo, cache, relation.subject)
            object_id = self._entity_id(repo, cache, relation.object_name)
            predicate = self._clean_text(relation.predicate)[:20]
            subject_norm = repo.normalize_entity_name(relation.subject)
            object_norm = repo.normalize_entity_name(relation.object_name)
            predicate_norm = repo.normalize_entity_name(predicate)
            if not subject_id or not object_id or not predicate_norm:
                continue
            key = f"rel.{subject_norm}.{predicate_norm}.{object_norm}"[:180]
            content = (
                f"{self._clean_text(relation.subject)}{predicate}"
                f"{self._clean_text(relation.object_name)}"
            )[:300]
            supersede: list[str] = []
            if fact.is_correction and hasattr(self.memory_core, "list_memory_records"):
                current = self.memory_core.list_memory_records(
                    status="active",
                    subject_id=job.person_id,
                    kinds=("relation",),
                    limit=200,
                )
                for row in current:
                    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    old_key = str(row.get("key") or "")
                    if (
                        str(meta.get("subject_entity_id") or "") == subject_id
                        and str(meta.get("predicate") or "") == predicate
                        and str(meta.get("object_entity_id") or "") != object_id
                        and old_key
                        and old_key != key
                    ):
                        supersede.append(old_key)
            evidence = [
                {"type": "transcript", "id": eid, "quote": fact.content[:500]}
                for eid in fact.evidence_ids
            ]
            source_id = (
                "relation:"
                + hashlib.sha256(f"{job.person_id}|{key}".encode("utf-8")).hexdigest()[:20]
            )
            record_id = self.memory_core.upsert_memory_record(
                kind="relation",
                key=key,
                content=content,
                subject_id=job.person_id,
                session_id="",
                source_type="relation_writeback",
                source_id=source_id,
                confidence=fact.confidence,
                importance=0.7,
                status="active",
                metadata={
                    "writeback_source": "memory_writeback",
                    "writeback_reason": job.reason,
                    "subject_entity_id": subject_id,
                    "predicate": predicate,
                    "object_entity_id": object_id,
                    "supporting_fact_id": fact_record,
                    "cardinality": "single",
                },
                evidence=evidence,
                supersede_keys=tuple(supersede),
            )
            if not record_id:
                continue
            written.append(str(record_id))
            repo.link_record_entities(record_id, [subject_id, object_id])
        return written

    def _entity_id(self, repo: Any, cache: dict[str, str], name: str) -> str:
        clean = self._clean_text(name)[:40]
        if len(clean) < 2 or clean in _ENTITY_STOPWORDS:
            return ""
        norm = repo.normalize_entity_name(clean)
        if len(norm) < 2:
            return ""
        cached = cache.get(norm)
        if cached:
            return cached
        entity_id = str(repo.upsert_entity(clean) or "")
        if entity_id:
            cache[norm] = entity_id
        return entity_id

    # ------------------------------------------------------------------ chat summary
    def _process_chat_summary(self, job: WritebackJob) -> WritebackResult:
        if not self.chat_summary_enabled or self.llm_call is None:
            return WritebackResult(job_reason=job.reason, detail="summary_disabled")
        messages = self._collect_evidence_messages(job)
        # Require a real window, but scale with configured threshold (tests may use 4).
        min_messages = max(4, min(self.summary_message_threshold, self.summary_message_threshold // 2 + 2))
        if len(messages) < min_messages:
            return WritebackResult(job_reason=job.reason, detail="summary_too_few_messages")

        window_fp = hashlib.sha256(
            "|".join(
                f"{m.get('id')}:{m.get('role')}:{m.get('content')}" for m in messages
            ).encode("utf-8")
        ).hexdigest()[:24]
        if self._summary_last_window.get(job.session_id) == window_fp:
            return WritebackResult(job_reason=job.reason, detail="summary_duplicate_window")

        lines = [
            f"id={m.get('id')} {m.get('role')}: {str(m.get('content') or '')[:220]}"
            for m in messages
        ]
        prompt = (
            "请把下列真实对话压缩成一段可写入长期记忆的中期摘要。\n"
            "只写有依据的事件、决定、用户稳定偏好线索；不要编造。\n"
            "不要逐句复述闲聊。只输出一段中文，不要 JSON。\n\n"
            + "\n".join(lines)
        )
        try:
            raw = self.llm_call(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="memory_writeback_summary",
            )
        except Exception as exc:
            logger.warning("memory writeback summary failed: %s", exc)
            return WritebackResult(job_reason=job.reason, detail=f"summary_llm_error:{exc}")
        summary = self._clean_text(raw)[:1200]
        if len(summary) < 20:
            return WritebackResult(job_reason=job.reason, detail="summary_empty")

        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            record_id = self.memory_core.upsert_memory_record(
                kind="summary",
                key=f"chat_summary:{job.session_id}:{day}:{window_fp[:8]}",
                content=summary,
                subject_id=job.person_id,
                session_id=job.session_id if job.session_id != "global" else "",
                source_type="chat_summary_writeback",
                source_id=f"chat_summary:{job.session_id}:{window_fp}",
                confidence=0.72,
                importance=0.55,
                metadata={
                    "writeback_source": "memory_writeback",
                    "writeback_reason": job.reason,
                    "window_fingerprint": window_fp,
                },
            )
        except Exception as exc:
            return WritebackResult(job_reason=job.reason, detail=f"summary_upsert_error:{exc}")

        self._summary_last_window[job.session_id] = window_fp
        self._bump("summaries_written")
        return WritebackResult(
            job_reason=job.reason,
            summary_written=True,
            detail="ok",
            record_ids=(str(record_id),),
        )

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _normalize_key(raw: str) -> str:
        key = str(raw or "").strip().lower()
        key = re.sub(r"\s+", "_", key)
        key = re.sub(r"[^a-z0-9_./-]+", "", key)
        return key[:80]

    @staticmethod
    def _parse_json_object(raw: str) -> Optional[dict[str, Any]]:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, dict) else None
