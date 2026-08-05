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
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence


logger = logging.getLogger(__name__)

LLMCall = Callable[..., str]

ALLOWED_KINDS = frozenset({"preference", "fact", "rule", "profile"})
WEEKDAY_TERMS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
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
    valid_days: int = 0
    evidence_ids: tuple[str, ...] = ()
    is_correction: bool = False
    supersede_keys: tuple[str, ...] = ()
    category_override: str = ""
    reason: str = ""


@dataclass(frozen=True)
class WritebackResult:
    job_reason: str
    facts_written: int = 0
    facts_skipped: int = 0
    summary_written: bool = False
    detail: str = ""
    record_ids: tuple[str, ...] = ()


class MemoryWritebackService:
    """Async long-term memory writeback worker (thread queue, non-blocking chat)."""

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

        self._queue: queue.Queue[Optional[WritebackJob]] = queue.Queue(
            maxsize=self.queue_maxsize
        )
        self._worker: Optional[threading.Thread] = None
        self._stopping = False
        self._start_lock = threading.Lock()
        self._last_job_at: dict[str, float] = {}
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
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        self._worker = None

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def flush(self, *, timeout: float = 5.0) -> bool:
        """Wait until the queue is drained (tests / graceful shutdown helpers)."""
        if self.inline:
            return True
        deadline = time.time() + max(0.05, float(timeout))
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return self._queue.unfinished_tasks == 0

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
        self._summary_counters[session_key] = (
            int(self._summary_counters.get(session_key, 0)) + 1
        )

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
        if (
            self.chat_summary_enabled
            and self._summary_counters[session_key] >= self.summary_message_threshold
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
                    self._summary_counters[session_key] = 0
        return enqueued_any

    def enqueue(self, job: WritebackJob) -> bool:
        if not self.enabled:
            return False
        self.start()
        cooldown_key = f"{job.session_id}:{job.person_id}:{job.reason}"
        now = time.time()
        last = float(self._last_job_at.get(cooldown_key, 0.0))
        if self.session_cooldown_sec > 0 and (now - last) < self.session_cooldown_sec:
            if job.reason != "explicit_user":
                self._bump("dropped")
                return False
        if self.inline:
            self._last_job_at[cooldown_key] = now
            self._bump("enqueued")
            try:
                self.process_job(job)
            except Exception as exc:
                self._bump("errors")
                logger.warning("inline memory writeback failed: %s", exc, exc_info=True)
                return False
            self._bump("processed")
            return True
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._bump("dropped")
            logger.warning("memory writeback queue full; dropped job reason=%s", job.reason)
            return False
        self._last_job_at[cooldown_key] = now
        self._bump("enqueued")
        return True

    # ------------------------------------------------------------------ worker
    def _worker_loop(self) -> None:
        while not self._stopping:
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                self._queue.task_done()
                break
            try:
                self.process_job(job)
            except Exception as exc:
                self._bump("errors")
                logger.warning("memory writeback job failed: %s", exc, exc_info=True)
            finally:
                self._queue.task_done()
                self._bump("processed")

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
            return bool(meta.get("is_owner"))
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
        facts = self._extract_person_facts(
            person_id=job.person_id,
            user_evidence=user_block,
            context_block=context_block,
            assistant_text=assistant_text,
            allowed_evidence_ids=allowed_ids,
            preferred_user_evidence_ids=user_id_set,
        )
        if not facts:
            return WritebackResult(job_reason=job.reason, detail="no_facts")

        source_map = {str(m.get("id") or ""): m for m in messages if m.get("id")}
        written_ids: list[str] = []
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
            else:
                skipped += 1
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
    ) -> list[ExtractedFact]:
        preferred_user_evidence_ids = set(preferred_user_evidence_ids or set())
        prompt = (
            "你是长期记忆写回器。只从目标用户原始发言中提取可长期保存的稳定事实。\n"
            "事实值必须能被用户原话直接支持；邻近上下文只用于补全省略/指代；"
            "机器人回复不能单独作为事实来源。\n"
            "用户纠正助手错误时，以用户最新纠正为准，并标记 is_correction=true，"
            "并在 supersede_keys 中放入被纠正的旧 key（若 key 相同可留空）。\n"
            "不要提取：闲聊客套、玩笑、猜测、反问、一次性临时安排、机器人情绪/承诺、"
            "没有用户确认的推测、把问句本身当事实。\n"
            "evidence_ids 必须引用「目标用户原始发言证据」里的 id，至少一条用户消息 id。\n"
            "没有可写事实时输出 {\"items\":[]}。\n"
            "严格只输出 JSON：\n"
            '{"items":[{"kind":"preference|fact|rule|profile","key":"稳定英文或点分键",'
            '"content":"简短中文事实陈述","confidence":0.0,"valid_days":0,'
            '"evidence_ids":["用户消息id"],"is_correction":false,'
            '"supersede_keys":[],"category_override":"","reason":""}]}\n'
            "key 示例：preferred_address, habit.meeting_weekday, likes.food, "
            "dislikes.xxx, status.recent, interaction.rule\n"
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
            return []
        payload = self._parse_json_object(raw)
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return []

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
            try:
                valid_days = max(0, min(3650, int(item.get("valid_days") or 0)))
            except Exception:
                valid_days = 0
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
            facts.append(
                ExtractedFact(
                    kind=kind,
                    key=key,
                    content=content,
                    confidence=confidence,
                    valid_days=valid_days,
                    evidence_ids=tuple(evidence_ids[:4]),
                    is_correction=is_correction,
                    supersede_keys=supersede_keys,
                    category_override=category,
                    reason=reason_text[:200],
                )
            )
        return facts

    def _upsert_fact(
        self,
        job: WritebackJob,
        fact: ExtractedFact,
        source_map: dict[str, dict[str, Any]],
    ) -> str:
        # Explicit supersede of alternate keys before writing.
        for old_key in fact.supersede_keys:
            if old_key and old_key != fact.key:
                self._supersede_key(job.person_id, fact.kind, old_key)

        fingerprint = hashlib.sha256(
            f"{job.person_id}|{fact.kind}|{fact.key}|{fact.content}".encode("utf-8")
        ).hexdigest()[:20]
        source_id = f"person_fact:{job.person_id}:{fingerprint}"
        valid_until = None
        if fact.valid_days:
            valid_until = (
                datetime.now(timezone.utc) + timedelta(days=fact.valid_days)
            ).isoformat()

        metadata: dict[str, Any] = {
            "writeback_source": "memory_writeback",
            "writeback_reason": job.reason,
            "evidence_source": "user_supported",
            "is_correction": bool(fact.is_correction),
            "extract_reason": fact.reason,
        }
        if fact.category_override:
            metadata["category_override"] = fact.category_override

        evidence = []
        for eid in fact.evidence_ids:
            quote = ""
            src = source_map.get(eid) or {}
            quote = str(src.get("content") or job.trigger_text or "")[:500]
            evidence.append({"type": "transcript", "id": eid, "quote": quote})

        importance = 0.6
        if fact.key in {"preferred_address", "reply_style"}:
            importance = 0.75
        if fact.key.startswith("habit") or fact.is_correction:
            importance = max(importance, 0.85)

        try:
            record_id = self.memory_core.upsert_memory_record(
                kind=fact.kind,
                key=fact.key,
                content=fact.content,
                subject_id=job.person_id,
                session_id=job.session_id if job.session_id != "global" else "",
                source_type="person_fact_writeback",
                source_id=source_id,
                confidence=fact.confidence,
                importance=importance,
                valid_until=valid_until,
                metadata=metadata,
                evidence=evidence,
            )
            return str(record_id or "")
        except Exception as exc:
            logger.warning("writeback upsert failed: %s", exc)
            return ""

    def _supersede_key(self, person_id: str, kind: str, key: str) -> None:
        kinds = tuple(
            dict.fromkeys(
                [
                    kind,
                    "fact",
                    "preference",
                    "profile",
                    "rule",
                ]
            )
        )
        try:
            rows = self.memory_core.list_memory_records(
                subject_id=person_id,
                kinds=kinds,
                limit=80,
            )
        except Exception:
            return
        for row in rows or []:
            if str(row.get("key") or "").strip() != key:
                continue
            if str(row.get("status") or "") != "active":
                continue
            try:
                self.memory_core.update_memory_record(
                    str(row.get("id")),
                    status="superseded",
                )
            except Exception:
                continue

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
