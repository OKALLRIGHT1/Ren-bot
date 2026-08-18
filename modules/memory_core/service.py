from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .categories import OVERRIDABLE_CATEGORY_IDS
from .models import MemoryProfile, ReplyMemoryContext
from .repository import MemoryCoreRepository, is_current, parse_memory_time


logger = logging.getLogger(__name__)

LLMCall = Callable[..., str]
VectorSearch = Callable[..., list[dict[str, Any]]]


@dataclass
class _PendingReply:
    session_id: str
    person_id: str
    character_name: str
    text: str
    source: str
    created_at: float
    expression_ids: tuple[str, ...] = ()


class MemoryCoreService:
    RECALL_CUES = (
        "还记得",
        "记得吗",
        "之前",
        "上次",
        "以前",
        "昨天",
        "前天",
        "最近",
        "说过",
        "提过",
        "当时",
        "发生了什么",
        "remember",
        "last time",
        "previously",
    )
    # Habit / frequency questions must go through episode recall, not free chat.
    HABIT_RECALL_CUES = (
        "周几",
        "哪几天",
        "哪天",
        "平常都",
        "平常是",
        "平常几",
        "一般是",
        "一般周",
        "一般几",
        "通常是",
        "通常都",
        "通常几",
        "每周几",
        "每周都",
        "固定周",
        "固定哪",
        "经常是",
        "经常周",
    )
    PROFILE_CUES = (
        "我喜欢什么",
        "我讨厌什么",
        "你了解我",
        "我的偏好",
        "怎么称呼我",
        "我叫什么",
        "对我的印象",
        "你觉得我是",
    )
    ACTIVITY_CUES = (
        "开会多久",
        "用了多久",
        "使用多久",
        "使用时间",
        "学习了吗",
        "学习了多久",
        "玩了多久",
        "工作多久",
    )
    NO_MEMORY_EVIDENCE_CUES = (
        "没找到",
        "没有找到",
        "没查到",
        "没有查到",
        "不记得",
        "想不起来",
        "没有可靠记录",
    )
    RECALL_SEARCH_NOISE_CUES = (
        "多长时间",
        "什么时候",
        "用户询问",
        "还记得",
        "最近一次",
        "几小时",
        "几分钟",
        "多久",
        "记得",
    )

    POSITIVE_WORDS = ("谢谢", "可以", "懂了", "有用", "不错", "好用", "正是", "成功了", "修好了")
    NEGATIVE_WORDS = ("不对", "错了", "没用", "答非所问", "理解错", "不是这个", "重来")

    def __init__(
        self,
        store: Any,
        *,
        llm_call: Optional[LLMCall] = None,
        vector_search: Optional[VectorSearch] = None,
        vector_job_notifier: Optional[Callable[[], None]] = None,
        settings: Optional[dict[str, Any]] = None,
        character_catalog_getter: Optional[Callable[[], dict[str, dict[str, Any]]]] = None,
    ) -> None:
        self.store = store
        self.repository = MemoryCoreRepository(store)
        self.llm_call = llm_call
        self.vector_search = vector_search
        self.vector_job_notifier = vector_job_notifier
        self._character_catalog_getter = character_catalog_getter
        self.settings = dict(settings or {})
        self.enabled = self._setting_bool("memory_core_enabled", True)
        self.profile_max_items = self._setting_int(
            "memory_core_profile_max_items", 6, minimum=1, maximum=30
        )
        self.candidate_limit = self._setting_int(
            "memory_core_candidate_limit", 12, minimum=1, maximum=100
        )
        self.final_limit = self._setting_int(
            "memory_core_final_limit", 3, minimum=1, maximum=12
        )
        self.context_max_chars = self._setting_int(
            "memory_core_context_max_chars", 1200, minimum=80, maximum=12000
        )
        self.impression_window = self._setting_int(
            "memory_core_impression_window", 8, minimum=1, maximum=30
        )
        self.profile_learning_enabled = self._setting_bool(
            "memory_core_profile_learning_enabled", True
        )
        self.expression_learning_enabled = self._setting_bool(
            "memory_core_expression_learning_enabled", True
        )
        self.learning_batch_messages = self._setting_int(
            "memory_core_learning_batch_messages", 10, minimum=2, maximum=100
        )
        self._initialized = False
        self._pending_replies: dict[str, _PendingReply] = {}
        self._learning_buffers: dict[str, list[dict[str, Any]]] = {}
        self._selected_expression_ids: dict[str, tuple[str, ...]] = {}
        self._writeback_service: Any = None

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    def _setting_int(
        self,
        key: str,
        default: int,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self.settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def initialize(self) -> dict[str, int]:
        result = self.repository.initialize(
            character_catalog=self._get_character_catalog(),
        )
        self._initialized = True
        self._ensure_writeback_service()
        return result

    def _ensure_writeback_service(self) -> Any:
        if self._writeback_service is not None:
            return self._writeback_service
        if not self._setting_bool("memory_writeback_enabled", True):
            return None
        try:
            from services.memory_writeback import MemoryWritebackService
        except Exception as exc:
            logger.warning("memory writeback import failed: %s", exc)
            return None
        self._writeback_service = MemoryWritebackService(
            self,
            llm_call=self.llm_call,
            settings=self.settings,
        )
        try:
            self._writeback_service.start()
        except Exception as exc:
            logger.warning("memory writeback start failed: %s", exc)
        return self._writeback_service

    def get_writeback_service(self) -> Any:
        self._ensure_initialized()
        return self._ensure_writeback_service()

    def writeback_stats(self) -> dict[str, int]:
        service = self.get_writeback_service()
        if service is None:
            return {}
        try:
            return dict(service.stats() or {})
        except Exception:
            return {}

    def stop_writeback(self, *, timeout: float = 2.0) -> None:
        """Stop async writeback worker (app shutdown / tests)."""
        service = self._writeback_service
        if service is None:
            return
        try:
            service.stop(timeout=timeout)
        except Exception as exc:
            logger.warning("memory writeback stop failed: %s", exc)

    def _get_character_catalog(self) -> dict[str, dict[str, Any]]:
        if self._character_catalog_getter is not None:
            value = self._character_catalog_getter()
            return dict(value or {})
        try:
            from modules.character_manager import character_manager

            return dict(character_manager.get_all_characters() or {})
        except Exception:
            return {}

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def upsert_memory_record(self, **kwargs: Any) -> str:
        self._ensure_initialized()
        record_id, created = self.repository.upsert_record(**kwargs)
        if created:
            self._notify_vector_worker()
        return record_id

    def list_memory_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return self.repository.list_records(**kwargs)

    def list_current_memory_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return self.repository.list_current_records(**kwargs)

    def list_persons(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return self.repository.list_persons()

    def get_memory_record(self, record_id: str) -> Optional[dict[str, Any]]:
        self._ensure_initialized()
        return self.repository.get_record(record_id)

    def update_memory_record(self, record_id: str, **changes: Any) -> bool:
        self._ensure_initialized()
        updated = self.repository.update_record(record_id, changes)
        if updated:
            self._notify_vector_worker()
        return updated

    def set_memory_category_override(self, record_id: str, category_id: str) -> bool:
        self._ensure_initialized()
        category_id = str(category_id or "").strip()
        if category_id and category_id not in OVERRIDABLE_CATEGORY_IDS:
            raise ValueError(f"unknown memory category: {category_id}")
        if category_id:
            return self.repository.update_record_metadata(
                record_id,
                {"category_override": category_id},
            )
        return self.repository.update_record_metadata(
            record_id,
            {},
            remove_keys=("category_override",),
        )

    def delete_memory_record(self, record_id: str) -> bool:
        self._ensure_initialized()
        deleted = self.repository.delete_record(record_id)
        if deleted:
            self._notify_vector_worker()
        return deleted

    def _notify_vector_worker(self) -> None:
        if self.vector_job_notifier is None:
            return
        try:
            self.vector_job_notifier()
        except RuntimeError as exc:
            logger.warning("memory vector worker notification failed: %s", exc)

    def list_vector_jobs(self, **kwargs: Any) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return self.repository.list_vector_jobs(**kwargs)

    def mark_vector_job_indexed(self, record_id: str, **kwargs: Any) -> bool:
        self._ensure_initialized()
        return self.repository.mark_vector_job_indexed(record_id, **kwargs)

    def mark_vector_job_failed(self, record_id: str, error: str) -> bool:
        self._ensure_initialized()
        return self.repository.mark_vector_job_failed(record_id, error)

    def vector_job_stats(self) -> dict[str, int]:
        self._ensure_initialized()
        return self.repository.vector_job_stats()

    def rebuild_vector_jobs(self) -> int:
        self._ensure_initialized()
        return self.repository.rebuild_vector_jobs()

    def record_message(
        self,
        role: str,
        content: str,
        *,
        session_id: str = "",
        person_id: str = "owner",
        character_id: str = "",
        character_name: str = "",
        meta: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        self._ensure_initialized()
        role = str(role or "").strip().lower()
        content = str(content or "").strip()
        if role not in {"user", "assistant", "summary"} or not content:
            return None
        safe_meta = dict(meta or {})
        transcript_id = self.store.add_transcript(
            role,
            content,
            meta=safe_meta,
            session_id=str(session_id or "").strip() or None,
        )
        if role == "summary":
            _record_id, created = self.repository.upsert_record(
                kind="summary",
                content=content,
                subject_id=person_id,
                session_id=session_id,
                source_type="transcript_summary",
                source_id=str(transcript_id or ""),
                confidence=0.7,
                importance=0.55,
                metadata=safe_meta,
            )
            if created:
                self._notify_vector_worker()
            return transcript_id
        if not self._is_real_learning_message(role, content, safe_meta):
            return transcript_id

        writeback_enabled = self._setting_bool("memory_writeback_enabled", True)
        learning_enabled = bool(self.enabled) and (
            self.profile_learning_enabled or self.expression_learning_enabled
        )
        # Writeback is independent of profile/expression learning toggles: chat
        # corrections must still become long-term memory_records.
        if not learning_enabled and not (bool(self.enabled) and writeback_enabled):
            return transcript_id

        session_key = str(session_id or "global").strip() or "global"
        buffer = self._learning_buffers.setdefault(session_key, [])
        buffer.append(
            {
                "id": str(transcript_id or ""),
                "role": role,
                "content": content,
                "person_id": person_id,
                "character_id": character_id,
                "character_name": character_name,
                "meta": safe_meta,
            }
        )
        # Keep enough recent turns for writeback evidence even when learning batch
        # is small or learning is disabled.
        buffer_limit = max(
            24,
            self.learning_batch_messages * 2,
            self._setting_int(
                "memory_writeback_context_messages", 12, minimum=2, maximum=40
            )
            * 2,
        )
        if len(buffer) > buffer_limit:
            del buffer[:-buffer_limit]
        trusted = self._is_trusted_learning_source(safe_meta)

        if learning_enabled and trusted:
            if (
                self.profile_learning_enabled
                and role == "user"
                and self._has_explicit_profile_signal(content)
            ):
                self._learn_profile_from_messages(buffer[-6:], person_id=person_id)
            if len(buffer) >= self.learning_batch_messages:
                batch = list(buffer[-self.learning_batch_messages :])
                if self.profile_learning_enabled:
                    self._learn_profile_from_messages(batch, person_id=person_id)
                if self.expression_learning_enabled:
                    self._learn_expressions_from_messages(
                        batch,
                        session_id=session_key,
                        person_id=person_id,
                        character_id=character_id,
                        character_name=character_name,
                    )
                # Do not clear the whole buffer: writeback still needs recent turns.
                # Keep a trailing window after batch learning.
                keep = max(
                    self.learning_batch_messages,
                    self._setting_int(
                        "memory_writeback_context_messages",
                        12,
                        minimum=2,
                        maximum=40,
                    ),
                )
                if len(buffer) > keep:
                    del buffer[:-keep]

        # MaiBot-style long-term writeback: extract stable user-supported facts
        # asynchronously. Transcript is already stored above; empty extract = no write.
        if bool(self.enabled) and writeback_enabled and trusted and role in {
            "user",
            "assistant",
        }:
            writeback = self._ensure_writeback_service()
            if writeback is not None:
                try:
                    context_n = self._setting_int(
                        "memory_writeback_context_messages",
                        12,
                        minimum=2,
                        maximum=40,
                    )
                    writeback.observe_message(
                        role,
                        content,
                        session_id=session_key,
                        person_id=person_id,
                        character_id=character_id,
                        character_name=character_name,
                        transcript_id=transcript_id,
                        meta=safe_meta,
                        recent_messages=list(buffer[-context_n:]),
                    )
                except Exception as exc:
                    logger.warning("memory writeback observe failed: %s", exc)
        return transcript_id

    @staticmethod
    def _is_real_learning_message(role: str, content: str, meta: dict[str, Any]) -> bool:
        if bool(meta.get("hidden")) or "[CMD:" in content or content.startswith("[tool_"):
            return False
        if bool(meta.get("tool")):
            return False
        path = str(meta.get("path") or "").strip().lower()
        blocked_paths = (
            "tool",
            "background",
            "proactive",
            "task_followup",
            "sensor",
            "system",
            "short_reaction",
        )
        if any(path.startswith(prefix) for prefix in blocked_paths):
            return False
        if "```" in content or len(content) > 1200:
            return False
        return role in {"user", "assistant"}

    @staticmethod
    def _is_trusted_learning_source(meta: dict[str, Any]) -> bool:
        source = str(meta.get("source") or "").strip().lower()
        if source in {"qq_gateway", "napcat_qq"}:
            return bool(meta.get("is_owner"))
        return True

    @staticmethod
    def _has_explicit_profile_signal(text: str) -> bool:
        clean = str(text or "").strip().lower()
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
            # Habit / schedule corrections that should enter profile learning promptly.
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
        )
        return any(signal in clean for signal in signals)

    def _learn_profile_from_messages(self, messages: list[dict[str, Any]], *, person_id: str) -> int:
        if not self.llm_call or not messages:
            return 0
        lines = [
            f"id={item['id']} {item['role']}: {self._clean_context_text(item['content'])[:260]}"
            for item in messages
            if self._clean_context_text(item.get("content"))
        ]
        if not lines:
            return 0
        prompt = (
            "从真实聊天中提取关于用户的稳定事实、偏好、习惯、雷区、互动要求或临时状态。\n"
            "不要推测；只有消息有明确证据时才提取。称呼、固定偏好、习惯（如周几开会）和明确纠正应优先。\n"
            "用户纠正助手错误时，以用户最新纠正为准，写入纠正后的事实。\n"
            "只有用户原话明确给出起点或终点时才填 valid_from / valid_until；稳定事实和习惯不要填。\n"
            "不要输出 valid_days。\n"
            "严格只输出 JSON："
            '{"items":[{"kind":"preference|fact|rule|profile","key":"稳定字段名",'
            '"content":"简短事实","confidence":0.0,"valid_from":"","valid_until":"",'
            '"evidence_ids":["1"]}]}\n\n'
            + "\n".join(lines)
        )
        try:
            raw = self.llm_call(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="profile_extract_v2",
            )
            payload = self._parse_json_object(raw)
        except Exception as exc:
            logger.warning("profile learning failed: %s", exc)
            return 0
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return 0
        source_map = {str(item["id"]): item for item in messages}
        inserted = 0

        for item in raw_items[:8]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "fact").strip().lower()
            if kind not in {"preference", "fact", "rule", "profile"}:
                continue
            key = str(item.get("key") or "").strip()[:80]
            fact = self._clean_context_text(item.get("content"))[:300]
            if not key or not fact:
                continue
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
            except Exception:
                confidence = 0.7
            if confidence < 0.6:
                continue
            evidence_ids = [
                str(value).strip()
                for value in (item.get("evidence_ids") or [])
                if str(value).strip() in source_map
            ][:4]
            if not evidence_ids:
                continue
            valid_from = self._explicit_bound(item.get("valid_from"))
            valid_until = self._explicit_bound(item.get("valid_until"))
            _record_id, created = self.repository.upsert_record(
                kind=kind,
                key=key,
                content=fact,
                subject_id=person_id,
                session_id="",
                source_type="learned_profile",
                source_id=f"{person_id}:{key}:{evidence_ids[-1]}",
                confidence=confidence,
                importance=0.75 if key in {"preferred_address", "reply_style"} else 0.6,
                valid_from=valid_from,
                valid_until=valid_until,
                evidence=[
                    {
                        "type": "transcript",
                        "id": evidence_id,
                        "quote": source_map[evidence_id]["content"],
                    }
                    for evidence_id in evidence_ids
                ],
            )
            inserted += int(created)
            if created:
                self._notify_vector_worker()
        return inserted

    def _learn_expressions_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
        person_id: str,
        character_id: str,
        character_name: str,
    ) -> int:
        if not self.llm_call or not character_name:
            return 0
        lines = [
            f"id={item['id']} {item['role']}: {self._clean_context_text(item['content'])[:220]}"
            for item in messages
            if self._clean_context_text(item.get("content"))
        ]
        if len(lines) < 6:
            return 0
        prompt = (
            "从这些真实聊天中提炼可复用的自然表达习惯。\n"
            "只提取场景与表达方式，不提取事实，不复制隐私，不学习代码、命令或工具文本。\n"
            "表达方式应描述语气和句式，不能要求覆盖角色核心人设。\n"
            "严格只输出 JSON："
            '{"items":[{"situation":"使用场景","style":"自然表达方式",'
            '"examples":["短例句"],"quality":0.0,"source_ids":["1"]}]}\n\n'
            + "\n".join(lines)
        )
        try:
            raw = self.llm_call(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="expression_learner_v2",
            )
            payload = self._parse_json_object(raw)
        except Exception as exc:
            logger.warning("expression learning failed: %s", exc)
            return 0
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return 0
        known_ids = {str(item["id"]) for item in messages}
        inserted = 0
        for item in raw_items[:6]:
            if not isinstance(item, dict):
                continue
            situation = self._clean_context_text(item.get("situation"))[:120]
            style = self._clean_context_text(item.get("style"))[:180]
            examples = [
                self._clean_context_text(value)[:120]
                for value in (item.get("examples") or [])
                if self._clean_context_text(value)
            ][:4]
            source_ids = [
                str(value).strip()
                for value in (item.get("source_ids") or [])
                if str(value).strip() in known_ids
            ][:6]
            try:
                quality = max(0.0, min(10.0, float(item.get("quality", 6.0))))
            except Exception:
                quality = 6.0
            if not situation or not style or not source_ids or quality < 5.5:
                continue
            self.upsert_expression_pattern(
                character_id=character_id,
                character_name=character_name,
                scene="chat",
                situation=situation,
                style=style,
                examples=examples,
                source="learned_v2",
                quality_score=quality,
                session_id=session_id,
                person_id=person_id,
                confidence=min(1.0, quality / 10.0),
                evidence=source_ids,
            )
            inserted += 1
        return inserted

    @staticmethod
    def _parse_json_object(raw: Any) -> dict[str, Any]:
        match = re.search(r"\{.*\}", str(raw or ""), flags=re.S)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def get_person_profile(self, person_id: str, *, max_items: int = 8) -> MemoryProfile:
        self._ensure_initialized()
        person_id = str(person_id or "owner").strip() or "owner"
        rows = self.repository.list_current_records(
            subject_id=person_id,
            kinds=("profile", "fact", "preference", "rule"),
            limit=500,
        )
        key_priority = {
            "preferred_address": 100,
            "reply_style": 95,
            "name": 90,
            "identity_summary": 85,
            "interaction_rule": 80,
            "notes": 70,
            "dislikes": 65,
            "status": 10,
        }
        rows.sort(
            key=lambda row: (
                1 if row.get("manual_lock") else 0,
                key_priority.get(str(row.get("key") or ""), 50),
                float(row.get("importance") or 0),
                float(row.get("confidence") or 0),
                str(row.get("updated_at") or ""),
            ),
            reverse=True,
        )
        active: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for row in rows:
            key = str(row.get("key") or "").strip()
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            active.append(row)
            if len(active) >= max(1, int(max_items)):
                break
        lines = [self._format_profile_record(row) for row in active]
        lines = [line for line in lines if line]
        text = "\n".join(f"- {line[:220]}" for line in lines)
        self.repository.save_profile_snapshot(
            person_id=person_id,
            summary=text,
            record_ids=[str(row["id"]) for row in active],
        )
        return MemoryProfile(person_id=person_id, text=text, records=tuple(active))

    def get_character_profile(
        self,
        character_id: str,
        *,
        max_items: int = 12,
    ) -> MemoryProfile:
        self._ensure_initialized()
        character_id = str(character_id or "").strip()
        person_id = self.repository.character_subject_id(character_id)
        if not person_id:
            return MemoryProfile(person_id="")
        rows = self.repository.list_current_records(
            subject_id=person_id,
            kinds=("profile", "fact", "preference", "rule"),
            limit=max(20, int(max_items) * 3),
        )
        active = [row for row in rows if str(row.get("key") or "") != "name"]
        active = active[: max(1, int(max_items))]
        lines = [self._format_character_profile_record(row) for row in active]
        text = "\n".join(f"- {line[:220]}" for line in lines if line)
        return MemoryProfile(person_id=person_id, text=text, records=tuple(active))

    @staticmethod
    def _format_character_profile_record(row: dict[str, Any]) -> str:
        content = str(row.get("content") or "").strip()
        key = str(row.get("key") or "").strip().lower()
        if not content:
            return ""
        if key.startswith("identity.traits"):
            return f"性格：{content}"
        if key.startswith("likes"):
            return f"喜欢：{content}"
        if key.startswith("dislikes"):
            return f"不喜欢：{content}"
        if key.startswith(("reply.", "interaction.")):
            return f"互动方式：{content}"
        return content

    @staticmethod
    def _format_profile_record(row: dict[str, Any]) -> str:
        content = str(row.get("content") or "").strip()
        if not content or content.lower() in {"[]", "{}", "null", "none"}:
            return ""
        key = str(row.get("key") or "").strip()
        if key == "preferred_address":
            return content
        if key == "name":
            return f"用户称呼或名字：{content}"
        if key == "reply_style" or key.startswith("reply."):
            return f"互动偏好：{content}"
        if key == "identity_summary":
            return f"身份印象：{content}"
        if key.startswith("likes."):
            return f"喜欢：{content}"
        if key.startswith("dislikes"):
            return f"不喜欢：{content}"
        if key.startswith("status"):
            return f"近期状态：{content}"
        return content

    @staticmethod
    def _explicit_bound(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text or text.lower() in {"0", "null", "none", "undefined"}:
            return None
        return text

    @staticmethod
    def _parse_time(value: str) -> float:
        parsed = parse_memory_time(value)
        if parsed is None:
            raise ValueError(f"invalid memory time: {value}")
        return parsed

    @classmethod
    def detect_intent(cls, text: str) -> str:
        clean = str(text or "").strip().lower()
        if not clean or clean.startswith("/"):
            return "none"
        # Single-episode time cues and habit/weekday questions both need grounded recall.
        if any(cue in clean for cue in ("上次", "最近一次", "什么时候")):
            return "episode"
        if any(cue in clean for cue in cls.HABIT_RECALL_CUES):
            return "episode"
        activity_action = any(
            cue in clean
            for cue in ("学习", "开会", "会议", "工作", "打游戏", "玩游戏", "使用", "打开")
        )
        activity_measure = any(
            cue in clean for cue in ("多久", "多长时间", "几小时", "几分钟", "了吗", "次数")
        )
        if any(cue in clean for cue in cls.ACTIVITY_CUES) or (
            activity_action and activity_measure
        ):
            return "activity"
        if any(cue in clean for cue in cls.PROFILE_CUES):
            return "profile"
        if any(cue in clean for cue in cls.RECALL_CUES):
            return "episode"
        return "none"

    def build_reply_context(
        self,
        user_text: str,
        *,
        session_id: str = "",
        person_id: str = "owner",
        recent_messages: Optional[list[dict[str, Any]]] = None,
        include_profile: bool = True,
        use_llm: bool = True,
    ) -> ReplyMemoryContext:
        self._ensure_initialized()
        clean_text = str(user_text or "").strip()
        if not self.enabled:
            return ReplyMemoryContext(intent="none")
        intent = self.detect_intent(clean_text)
        profile = (
            self.get_person_profile(person_id, max_items=self.profile_max_items)
            if include_profile
            else MemoryProfile(person_id)
        )
        if intent in {"none", "activity"}:
            return ReplyMemoryContext(intent=intent, profile_text=profile.text)

        impression = (
            self._build_chat_impression(clean_text, recent_messages or [])
            if use_llm
            else clean_text
        )
        candidates = self._collect_candidates(
            impression or clean_text,
            session_id=session_id,
            person_id=person_id,
            intent=intent,
            allow_llm=use_llm,
        )
        lexical_candidate_count = len(candidates)
        vector_status = "disabled"
        vector_error = ""
        vector_candidate_count = 0
        if self.vector_search is not None:
            try:
                vector_rows = self.vector_search(
                    impression or clean_text,
                    person_id=person_id,
                    session_id=session_id,
                    limit=self.candidate_limit,
                )
                candidates, vector_candidate_count = self._merge_vector_candidates(
                    candidates,
                    vector_rows,
                    person_id=person_id,
                    session_id=session_id,
                    intent=intent,
                )
                vector_status = "ok"
            except Exception as exc:
                vector_status = "unavailable"
                vector_error = str(exc)
                logger.warning("memory vector recall unavailable: %s", exc)
        if intent == "episode":
            candidates = [
                item
                for item in candidates
                if not self._is_unreliable_recall_candidate(item, clean_text)
            ]
        selected = self._select_memories(
            clean_text,
            impression,
            candidates,
            want=self.final_limit,
            use_llm=use_llm,
        )
        selected_ids = [str(item["id"]) for item in selected]
        memory_text = self._format_memory_reference(
            selected,
            max_chars=self.context_max_chars,
        )
        self.repository.save_query_log(
            session_id=session_id,
            person_id=person_id,
            query=clean_text,
            impression=impression,
            intent=intent,
            candidate_ids=[str(item["id"]) for item in candidates],
            selected_ids=selected_ids,
        )
        return ReplyMemoryContext(
            intent=intent,
            impression=impression,
            profile_text=profile.text,
            memory_text=memory_text,
            selected_ids=tuple(selected_ids),
            diagnostics={
                "candidate_count": len(candidates),
                "lexical_candidate_count": lexical_candidate_count,
                "vector_candidate_count": vector_candidate_count,
                "vector_status": vector_status,
                "vector_error": vector_error,
            },
        )

    def _build_chat_impression(self, user_text: str, recent_messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for item in recent_messages[-self.impression_window :]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = self._clean_context_text(item.get("content"))
            if content:
                lines.append(f"{role}: {content[:220]}")
        if not self.llm_call:
            return user_text
        prompt = (
            "请把当前问题和最近真实对话概括成一段用于长期记忆检索的聊天印象。\n"
            "聚焦当前话题、相关人物、时间和旧事件，不要补充没有依据的事实。\n"
            "只输出一段简短中文，不要列表或 JSON。\n\n"
            f"最近消息：\n{chr(10).join(lines) or '无'}\n\n当前问题：{user_text}"
        )
        try:
            result = self.llm_call(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="memory_impression",
            )
        except Exception as exc:
            logger.warning("memory impression failed: %s", exc)
            return user_text
        return self._clean_context_text(result)[:500] or user_text

    @staticmethod
    def _clean_context_text(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or "[CMD:" in text or text.startswith("[tool_"):
            return ""
        return text

    def _collect_candidates(
        self,
        search_text: str,
        *,
        session_id: str,
        person_id: str,
        intent: str,
        allow_llm: bool = True,
    ) -> list[dict[str, Any]]:
        kinds = ("profile", "fact", "preference", "rule") if intent == "profile" else (
            "episode",
            "fact",
            "preference",
            "summary",
            "other",
        )
        token_text = (
            self._episode_search_text(search_text)
            if intent == "episode"
            else search_text
        )
        query_tokens = self._tokens(token_text)
        records = self.repository.list_current_records(
            subject_id=person_id,
            session_id=session_id,
            kinds=kinds,
            limit=300,
        )
        if intent == "episode":
            records.extend(
                self.repository.list_transcript_candidates(
                    session_id=session_id,
                    query_terms=query_tokens,
                    limit=1000,
                )
            )
        scored: list[dict[str, Any]] = []
        now = time.time()
        for item in records:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if intent == "episode" and self._is_unreliable_recall_candidate(
                item, search_text
            ):
                continue
            item_tokens = self._tokens(content + " " + str(item.get("key") or ""))
            overlap = len(query_tokens & item_tokens) / max(1, len(query_tokens))
            substring_bonus = 0.0
            compact_query = re.sub(r"\s+", "", token_text)
            if compact_query and len(compact_query) >= 4 and compact_query in re.sub(r"\s+", "", content):
                substring_bonus = 0.4
            recency = self._recency_score(item.get("updated_at"), now)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            query_context_bonus = 0.7 if metadata.get("query_context_match") else 0.0
            habit_bonus = 0.0
            key_text = str(item.get("key") or "").strip().lower()
            habit_query = any(
                cue in search_text
                for cue in ("周几", "哪天", "平常", "一般", "通常", "习惯", "每周")
            )
            if habit_query:
                if key_text.startswith("habit") or key_text.startswith("habits"):
                    habit_bonus += 0.55
                if any(
                    day in content
                    for day in ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
                ) and any(term in content for term in ("开会", "会议", "习惯")):
                    habit_bonus += 0.35
                # Long diary noise should not drown short habit facts.
                if len(content) > 420 and str(item.get("kind") or "") in {
                    "episode",
                    "summary",
                }:
                    habit_bonus -= 0.35
            score = (
                overlap * 0.55
                + substring_bonus
                + float(item.get("confidence") or 0) * 0.2
                + float(item.get("importance") or 0) * 0.1
                + recency * 0.15
                + query_context_bonus
                + habit_bonus
            )
            if (
                overlap <= 0
                and substring_bonus <= 0
                and query_context_bonus <= 0
                and (not self.llm_call or not allow_llm)
            ):
                continue
            candidate = dict(item)
            candidate["score"] = score
            scored.append(candidate)
        scored.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        return scored[: self.candidate_limit]

    @classmethod
    def _episode_search_text(cls, text: str) -> str:
        cleaned = str(text or "")
        for cue in cls.RECALL_SEARCH_NOISE_CUES:
            cleaned = cleaned.replace(cue, "")
        cleaned = cleaned.strip(" ，。！？?、")
        return cleaned or str(text or "")

    @classmethod
    def _is_unreliable_recall_candidate(
        cls,
        item: dict[str, Any],
        query: str,
    ) -> bool:
        content = str(item.get("content") or "").strip()
        if any(cue in content for cue in cls.NO_MEMORY_EVIDENCE_CUES):
            return True
        if str(item.get("key") or "").strip().lower() == "user_task":
            return True
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if metadata.get("has_linked_answer"):
            return True
        expects_fact_answer = any(
            cue in query
            for cue in (
                "多久",
                "多长时间",
                "什么时候",
                "几小时",
                "几分钟",
                "哪天",
                "日期",
                "周几",
                "哪几天",
                "平常",
                "一般",
                "通常",
            )
        )
        role = str(metadata.get("role") or "").strip().lower()
        is_question = content.rstrip().endswith(("?", "？", "吗", "么"))
        return expects_fact_answer and role == "user" and is_question

    @staticmethod
    def _candidate_kinds(intent: str) -> tuple[str, ...]:
        if intent == "profile":
            return ("profile", "fact", "preference", "rule")
        return ("episode", "fact", "preference", "summary", "other")

    @staticmethod
    def _record_matches_scope(
        record: dict[str, Any],
        *,
        person_id: str,
        session_id: str,
    ) -> bool:
        subject = str(record.get("subject_id") or "").strip()
        person = str(person_id or "owner").strip() or "owner"
        if person == "owner":
            if subject not in {"", "owner"}:
                return False
        elif subject != person:
            return False
        item_session = str(record.get("session_id") or "").strip()
        requested_session = str(session_id or "").strip()
        return not requested_session or item_session in {"", requested_session}

    def _merge_vector_candidates(
        self,
        lexical: list[dict[str, Any]],
        vector_rows: Iterable[dict[str, Any]],
        *,
        person_id: str,
        session_id: str,
        intent: str,
    ) -> tuple[list[dict[str, Any]], int]:
        merged = {str(item.get("id") or ""): dict(item) for item in lexical}
        valid_vector_ids: set[str] = set()
        allowed_kinds = set(self._candidate_kinds(intent))
        for vector_row in vector_rows or []:
            record_id = str(vector_row.get("id") or "").strip()
            if not record_id:
                continue
            try:
                vector_score = max(
                    0.0,
                    min(1.0, float(vector_row.get("vector_score") or 0.0)),
                )
            except (TypeError, ValueError):
                continue
            record = self.repository.get_record(record_id)
            if (
                record is None
                or not is_current(record)
                or str(record.get("kind") or "") not in allowed_kinds
                or not self._record_matches_scope(
                    record,
                    person_id=person_id,
                    session_id=session_id,
                )
            ):
                continue
            valid_vector_ids.add(record_id)
            current = merged.get(record_id)
            if current is None:
                current = dict(record)
                current["score"] = (
                    vector_score * 0.55
                    + float(record.get("importance") or 0.0) * 0.1
                )
                merged[record_id] = current
            else:
                lexical_score = float(current.get("score") or 0.0)
                current["score"] = max(
                    lexical_score,
                    lexical_score * 0.7 + vector_score * 0.3,
                )
            current["vector_score"] = max(
                float(current.get("vector_score") or 0.0),
                vector_score,
            )
        rows = list(merged.values())
        rows.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return rows[: self.candidate_limit], len(valid_vector_ids)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        clean = str(text or "").lower()
        tokens = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", clean))
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", clean):
            for size in (2, 3, 4):
                for index in range(max(0, len(chunk) - size + 1)):
                    tokens.add(chunk[index : index + size])
        return tokens

    @staticmethod
    def _recency_score(raw_time: Any, now: float) -> float:
        try:
            timestamp = MemoryCoreService._parse_time(str(raw_time or ""))
        except Exception:
            return 0.0
        days = max(0.0, (now - timestamp) / 86400.0)
        return math.exp(-days / 90.0)

    def _select_memories(
        self,
        query: str,
        impression: str,
        candidates: list[dict[str, Any]],
        *,
        want: int,
        use_llm: bool = True,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        if not self.llm_call or not use_llm:
            return [item for item in candidates if float(item.get("score") or 0) >= 0.28][:want]
        lines = []
        for item in candidates:
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            ts = str(meta.get("ts") or item.get("updated_at") or "")[:19]
            lines.append(
                f"id={item['id']} kind={item.get('kind','')} time={ts} score={float(item.get('score') or 0):.3f} "
                f"content={str(item.get('content') or '')[:260]}"
            )
        prompt = (
            "你是记忆候选筛选器，只能从候选中选择能直接帮助回答当前问题的记录。\n"
            "天气、画图、工具报错、其他话题和仅仅时间接近的内容必须排除。\n"
            "用户询问时长、日期或具体事实时，只选择明确包含该事实的记录。\n"
            f"最多选择 {want} 条；没有足够相关的记录就返回空数组。\n"
            '严格只输出 JSON：{"selected_ids":["id1"]}\n\n'
            f"当前问题：{query}\n聊天印象：{impression}\n\n候选：\n" + "\n".join(lines)
        )
        try:
            raw = self.llm_call(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="memory_selector",
            )
            selected_ids = self._parse_selected_ids(raw, candidates, want)
        except Exception as exc:
            logger.warning("memory selector failed: %s", exc)
            selected_ids = None
        if selected_ids is None:
            return [item for item in candidates if float(item.get("score") or 0) >= 0.35][:want]
        mapping = {str(item["id"]): item for item in candidates}
        return [mapping[item_id] for item_id in selected_ids if item_id in mapping]

    @staticmethod
    def _parse_selected_ids(raw: Any, candidates: list[dict[str, Any]], limit: int) -> Optional[list[str]]:
        text = str(raw or "").strip()
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return None
        raw_ids = payload.get("selected_ids") if isinstance(payload, dict) else None
        if not isinstance(raw_ids, list):
            return None
        allowed = {str(item["id"]) for item in candidates}
        selected: list[str] = []
        for raw_id in raw_ids:
            item_id = str(raw_id or "").strip()
            if item_id in allowed and item_id not in selected:
                selected.append(item_id)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _format_memory_reference(items: Iterable[dict[str, Any]], *, max_chars: int) -> str:
        lines: list[str] = []
        for item in items:
            content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
            if not content:
                continue
            if len(content) > 180:
                content = content[:177].rstrip() + "..."
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            timestamp = str(meta.get("ts") or item.get("updated_at") or "")[:10]
            prefix = f"[{timestamp}] " if timestamp else ""
            lines.append(f"- {prefix}{content}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        return text

    def upsert_expression_pattern(
        self,
        *,
        character_id: str = "",
        character_name: str,
        scene: str,
        situation: str,
        style: str,
        examples: Optional[list[str]] = None,
        source: str = "learned",
        quality_score: float = 0.0,
        session_id: str = "",
        person_id: str = "",
        confidence: float = 0.5,
        evidence: Optional[list[str]] = None,
    ) -> str:
        self._ensure_initialized()
        identity = str(character_id or "").strip() or str(character_name or "").strip()
        normalized = re.sub(r"\s+", "", f"{identity}|{scene}|{situation}|{style}").lower()
        pattern_id = "xp_" + __import__("hashlib").sha256(normalized.encode("utf-8")).hexdigest()[:12]
        payload = {
            "id": pattern_id,
            "character_id": character_id,
            "character_name": character_name,
            "scene": scene,
            "situation": situation,
            "style": style,
            "content_list": examples or [],
            "source": source,
            "quality_score": quality_score,
            "enabled": True,
            "meta": {
                "session_id": session_id,
                "person_id": person_id,
                "confidence": max(0.0, min(1.0, float(confidence))),
                "evidence": evidence or [],
            },
        }
        stored_id = self.store.upsert_expression_pattern(payload)
        self.repository.update_expression_learning_fields(
            stored_id,
            session_id=session_id,
            person_id=person_id,
            confidence=confidence,
            evidence=evidence or [],
        )
        return stored_id

    def select_expressions(
        self,
        *,
        user_text: str,
        character_id: str = "",
        character_name: str,
        scene: str = "chat",
        recent_messages: Optional[list[dict[str, Any]]] = None,
        limit: int = 3,
        session_id: str = "",
        person_id: str = "owner",
        use_llm: bool = True,
    ) -> list[str]:
        self._ensure_initialized()
        rows = self.store.list_expression_patterns(
            character_id=character_id,
            character_name=character_name,
            scene=scene,
            person_id=person_id,
            enabled_only=True,
            limit=40,
        )
        if not rows:
            return []
        rows = rows[:16]
        query = self._clean_context_text(user_text)
        if recent_messages:
            recent = [
                self._clean_context_text(item.get("content"))
                for item in recent_messages[-6:]
                if isinstance(item, dict)
            ]
            query = " ".join([item for item in [*recent, query] if item])
        selected_rows: list[dict[str, Any]] = []
        llm_failed = False
        if use_llm and self.llm_call:
            lines = [
                f"id={row['id']} situation={row.get('situation','')} style={row.get('style','')} "
                f"examples={' / '.join((row.get('content_list') or [])[:2])}"
                for row in rows
            ]
            prompt = (
                "你是表达方式选择器。根据当前真实聊天语境，从候选中选择 0 到 3 条自然适合本轮回复的表达习惯。\n"
                "不要选择与当前场景无关、会覆盖角色人设或会让回复模板化的内容。\n"
                '严格只输出 JSON：{"selected_ids":["id1"]}\n\n'
                f"当前语境：{query}\n\n候选：\n" + "\n".join(lines)
            )
            try:
                raw = self.llm_call(
                    [{"role": "user", "content": prompt}],
                    task_type="summary",
                    caller="expression_selector",
                )
                selected_ids = self._parse_selected_ids(raw, rows, limit)
            except Exception as exc:
                logger.warning("expression selector failed: %s", exc)
                selected_ids = None
            if selected_ids is None:
                llm_failed = True
            else:
                mapping = {str(row["id"]): row for row in rows}
                selected_rows = [mapping[item_id] for item_id in selected_ids if item_id in mapping]
        if not selected_rows and (not use_llm or not self.llm_call or llm_failed):
            query_tokens = self._tokens(query)
            scored = []
            for row in rows:
                row_tokens = self._tokens(
                    f"{row.get('situation','')} {row.get('style','')} {' '.join(row.get('content_list') or [])}"
                )
                overlap = len(query_tokens & row_tokens) / max(1, len(query_tokens))
                if overlap > 0:
                    scored.append((overlap, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            selected_rows = [row for _score, row in scored[:limit]]
        hints: list[str] = []
        for row in selected_rows[:limit]:
            situation = str(row.get("situation") or "").strip()
            style = str(row.get("style") or "").strip()
            if situation and style:
                hints.append(f"当{situation}时，可以{style}。")
            elif style:
                hints.append(style)
        if selected_rows and hasattr(self.store, "bump_expression_pattern_use"):
            selected_ids = [str(row["id"]) for row in selected_rows]
            self.store.bump_expression_pattern_use(selected_ids)
            self.repository.mark_expressions_selected(selected_ids)
            if session_id:
                self._selected_expression_ids[session_id] = tuple(selected_ids)
        return hints

    def record_reply(
        self,
        *,
        session_id: str,
        person_id: str = "owner",
        character_name: str = "",
        text: str,
        source: str = "",
    ) -> None:
        session_id = str(session_id or "").strip()
        if not session_id or not str(text or "").strip():
            return
        self._pending_replies[session_id] = _PendingReply(
            session_id=session_id,
            person_id=person_id,
            character_name=character_name,
            text=str(text or "")[:1200],
            source=source,
            created_at=time.time(),
            expression_ids=self._selected_expression_ids.pop(session_id, ()),
        )

    def observe_followup(self, *, session_id: str, text: str, source: str = "") -> Optional[dict[str, Any]]:
        pending = self._pending_replies.pop(str(session_id or "").strip(), None)
        if pending is None or time.time() - pending.created_at > 1800:
            return None
        followup = str(text or "").strip()
        if not followup:
            return None
        labels: list[str] = []
        score = 0
        lower = followup.lower()
        if any(word in lower for word in self.POSITIVE_WORDS):
            labels.append("positive")
            score += 1
        if any(word in lower for word in self.NEGATIVE_WORDS):
            labels.append("negative")
            score -= 1
        if not labels:
            labels.append("continued")
        payload = {
            "session_id": pending.session_id,
            "person_id": pending.person_id,
            "character_name": pending.character_name,
            "reply_text": pending.text,
            "followup_text": followup,
            "labels": labels,
            "score": score,
            "source": source or pending.source,
        }
        self.repository.record_feedback(payload)
        self.repository.apply_expression_feedback(list(pending.expression_ids), score)
        return payload

    def feedback_stats(self, *, session_id: str, limit: int = 80) -> dict[str, Any]:
        self._ensure_initialized()
        return self.repository.feedback_stats(session_id=session_id, limit=limit)

    def list_feedback(self, *, session_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return self.repository.list_feedback(session_id=session_id, limit=limit)

    def query_activity(self, user_text: str) -> str:
        self._ensure_initialized()
        from datetime import date, timedelta

        clean = str(user_text or "").strip()
        target_date = date.today()
        if "昨天" in clean:
            target_date -= timedelta(days=1)
        elif "前天" in clean:
            target_date -= timedelta(days=2)
        date_text = target_date.isoformat()
        stats = self.store.get_daily_screen_stats(date_text)
        if not stats:
            return f"{date_text} 没有可靠的活动记录"
        if self.llm_call:
            compact = {
                "date": stats.get("date") or date_text,
                "counts": stats.get("counts") or {},
                "durations_seconds": stats.get("durations") or {},
                "category_totals": stats.get("category_totals") or {},
                "segments": (stats.get("segments") or [])[-80:],
                "observations": (stats.get("observations") or [])[-60:],
            }
            payload = json.dumps(compact, ensure_ascii=False)
            if len(payload) > 9000:
                payload = payload[-9000:]
            prompt = (
                "你是活动统计查询器，只能根据给出的记录回答。\n"
                "时长字段单位为秒，需要转换成自然的小时或分钟。\n"
                "没有能证明用户所问活动的数据时，明确回答没有可靠记录，不要猜测。\n"
                "只输出一句或两句自然中文。\n\n"
                f"问题：{clean}\n活动数据：{payload}"
            )
            try:
                result = self.llm_call(
                    [{"role": "user", "content": prompt}],
                    task_type="summary",
                    caller="activity_query",
                )
                answer = self._clean_context_text(result)
                if answer:
                    return answer[:500]
            except Exception as exc:
                logger.warning("activity query failed: %s", exc)
        summary = str(stats.get("summary_text") or "").strip()
        if summary:
            return summary[:1200]
        durations = stats.get("durations") if isinstance(stats.get("durations"), dict) else {}
        if not durations:
            return f"{date_text} 有活动记录，但没有可用的时长统计"
        top = sorted(
            ((str(name), float(seconds or 0)) for name, seconds in durations.items()),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        return "；".join(
            f"{name} {max(1, round(seconds / 60))} 分钟" for name, seconds in top if seconds > 0
        ) or f"{date_text} 没有可用的活动时长"
