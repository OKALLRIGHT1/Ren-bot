# modules/advanced_memory.py
"""Advanced memory facade (runtime entry for conversation memory).

External product code should depend on AdvancedMemorySystem and/or
modules.memory_sqlite.get_memory_store. Prefer not to construct
MemoryCoreService / Chroma collections directly from chat/GUI layers
(tests may still import lower layers).

Profile and graph implementations live under modules.memory.*; this
module orchestrates SQLite + Memory Core + vectors.
"""
import os
import re
import json
import time
import uuid
import hashlib
import itertools
import threading
from datetime import datetime, timezone
from typing import Optional
from modules.memory_sqlite import get_memory_store
from concurrent.futures import ThreadPoolExecutor
import jieba
import jieba.analyse
import chromadb
from config import SYSTEM_RULES_PROMPT, DEFAULT_PERSONA
from modules.character_manager import character_manager
from config import EMBEDDING_CONFIG, MEMORY_DB_PATH, MEMORY_SETTINGS, MODELS
from modules.memory import (
    GraphMemory as ModularGraphMemory,
    build_memory_metadata,
    clean_injected_context,
    deserialize_vector_metadata,
    post_process_memory_candidates,
    retrieve_knowledge_chunks,
    serialize_vector_metadata,
)
from modules.memory.short_term import ShortTermMemoryManager
from modules.embeddings import (
    ChromaEmbeddingFunction,
    EmbeddingUnavailableError,
    build_configured_embedding_service,
)
from modules.runtime_settings import load_runtime_settings_strict
from modules.memory_core import MemoryCoreService, MemoryVectorIndex
from modules.memory.knowledge_store import (
    delete_knowledge_by_dirs,
    import_knowledge_from_file as import_knowledge_file_modular,
    search_knowledge as search_knowledge_modular,
)

from core.logger import get_logger

# 可选：用于 LLM 记忆筛选（复用你现有 llm.py 的 chat_with_ai）
# 只在 MEMORY_SETTINGS["use_llm_selector"]=True 时才会调用
try:
    from modules.llm import chat_with_ai
except Exception:
    chat_with_ai = None



# Profile / Graph: use modules.memory.ProfileStore and ModularGraphMemory only.
# Local duplicate ProfileStore/GraphMemory classes were removed (dead code, 2026-07-30).

class AdvancedMemorySystem:
    def __init__(self):
        self._lock = threading.Lock()
        self._vector_lock = threading.Lock()
        # 默认 2，后续会从 MEMORY_SETTINGS 覆盖
        self.recall_min_chars = 2

        # 🟢 [修正] ThreadPoolExecutor 来自 concurrent.futures，不是 threading
        # max_workers=1 保证写入顺序，避免并发写入导致时序混乱
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._vector_executor = ThreadPoolExecutor(max_workers=1)
        self._vector_schedule_lock = threading.Lock()
        self._vector_sync_future = None

        # 1. 数据库连接
        self.sqlite_store = get_memory_store()
        self.memory_core = MemoryCoreService(
            self.sqlite_store,
            llm_call=chat_with_ai,
            settings=MEMORY_SETTINGS,
        )
        self.memory_core.initialize()

        # 2. ChromaDB 连接
        self.chroma_client = chromadb.PersistentClient(path=MEMORY_DB_PATH)
        self.embedding_service = build_configured_embedding_service(
            models=MODELS,
            runtime_settings=load_runtime_settings_strict(),
            legacy_config=EMBEDDING_CONFIG,
        )
        self.embedding_fn = ChromaEmbeddingFunction(self.embedding_service)

        self.memory_collection = self.chroma_client.get_or_create_collection(
            name="waifu_memory_advanced", embedding_function=self.embedding_fn
        )
        self.knowledge_collection_metadata = {
            "embedding_model": self.embedding_service.model,
            "index_version": 1,
        }
        if self.embedding_service.expected_dimension:
            self.knowledge_collection_metadata["embedding_dimension"] = int(
                self.embedding_service.expected_dimension
            )
        self.knowledge_collection = self.chroma_client.get_or_create_collection(
            name="waifu_knowledge_base",
            embedding_function=self.embedding_fn,
            metadata=self.knowledge_collection_metadata,
        )
        self.knowledge_embedding_compatibility = (
            self._knowledge_collection_compatibility(
                self.knowledge_collection,
                model=self.embedding_service.model,
                dimension=self.embedding_service.expected_dimension,
            )
        )
        self.memory_vector_collection_name = self._memory_vector_collection_name(
            self.embedding_service.model,
            self.embedding_service.expected_dimension,
        )
        self.memory_vector_collection_metadata = {
            "embedding_model": self.embedding_service.model,
            "index_version": 1,
        }
        if self.embedding_service.expected_dimension:
            self.memory_vector_collection_metadata["embedding_dimension"] = int(
                self.embedding_service.expected_dimension
            )
        self.current_memory_collection = self.chroma_client.get_or_create_collection(
            name=self.memory_vector_collection_name,
            metadata=self.memory_vector_collection_metadata,
        )
        self.memory_vector_index = MemoryVectorIndex(
            repository=self.memory_core.repository,
            collection=self.current_memory_collection,
            embedding_service=self.embedding_service,
        )
        self.memory_core.vector_search = self._query_memory_vector
        self.memory_core.vector_job_notifier = self._schedule_memory_vector_sync

        # 3. 图谱
        self.graph = ModularGraphMemory()

        # 4. Profile 档案管理
        self.profile_path = os.path.join(
            os.path.dirname(MEMORY_DB_PATH), "profile.json"
        )
        self.profile = None
        self.profile_enabled = False
        self.participant_filtering_enabled = bool(
            MEMORY_SETTINGS.get("participant_filtering_enabled", True)
        )
        self.graph_rerank_enabled = bool(
            MEMORY_SETTINGS.get("graph_rerank_enabled", True)
        )

        # 5. 短期记忆 (RAM)
        self.max_short_term = int(MEMORY_SETTINGS.get("max_short_term", 12))
        self.short_term_manager = ShortTermMemoryManager(
            self.sqlite_store, self.max_short_term
        )
        self.short_term_manager.restore_global()
        self.short_term_memory = self.short_term_manager.short_term_memory
        self.session_short_term_memory = (
            self.short_term_manager.session_short_term_memory
        )
        self._session_short_term_loaded = (
            self.short_term_manager._session_short_term_loaded
        )

        # 6. Near-history ContextAssembler (single read path for recent events)
        self.conversation_events_enabled = bool(
            MEMORY_SETTINGS.get("conversation_events_enabled", True)
        )
        self.short_term_from_events = bool(
            MEMORY_SETTINGS.get("short_term_from_events", True)
        )
        self.mid_term_enabled = bool(MEMORY_SETTINGS.get("mid_term_enabled", True))
        self.mid_term_segment_source_items = max(
            4,
            int(MEMORY_SETTINGS.get("mid_term_segment_source_items", 10) or 10),
        )
        self.context_assembler = None
        self._last_assembled_context = None
        # conversation_id / session_key -> ordered event_ids awaiting mid-term summary
        self._pending_mid_term_event_ids: dict[str, list[str]] = {}
        self._pending_mid_term_lock = threading.Lock()
        if self.conversation_events_enabled and self.sqlite_store is not None:
            try:
                from modules.conversation_events.store import ConversationEventStore
                from modules.conversation_events.mid_term import (
                    MidTermRecallService,
                    MidTermSegmentStore,
                )
                from services.chat_support.context_assembler import ContextAssembler

                event_store = ConversationEventStore(self.sqlite_store)
                mid_term_recall_service = None
                if self.mid_term_enabled:
                    mid_term_recall_service = MidTermRecallService(
                        segment_store=MidTermSegmentStore(self.sqlite_store),
                        event_store=event_store,
                        embedding_service=self.embedding_service,
                        recall_max_items=int(
                            MEMORY_SETTINGS.get("mid_term_recall_max_items", 1) or 1
                        ),
                        active_max_chars=int(
                            MEMORY_SETTINGS.get("active_session_max_chars", 500) or 500
                        ),
                        mid_term_max_chars=int(
                            MEMORY_SETTINGS.get("mid_term_max_chars", 1800) or 1800
                        ),
                    )
                self.context_assembler = ContextAssembler(
                    store=event_store,
                    enabled=True,
                    max_events=int(
                        MEMORY_SETTINGS.get("recent_event_max_items", 3) or 3
                    ),
                    max_chars=int(
                        MEMORY_SETTINGS.get("recent_event_max_chars", 900) or 900
                    ),
                    active_max_chars=int(
                        MEMORY_SETTINGS.get("active_session_max_chars", 500) or 500
                    ),
                    mid_term_max_chars=int(
                        MEMORY_SETTINGS.get("mid_term_max_chars", 1800) or 1800
                    ),
                    long_term_max_chars=int(
                        MEMORY_SETTINGS.get("memory_core_context_max_chars", 1200)
                        or 1200
                    ),
                    mid_term_enabled=self.mid_term_enabled,
                    mid_term_recall_service=mid_term_recall_service,
                    owner_cross_channel_recent_enabled=bool(
                        MEMORY_SETTINGS.get(
                            "owner_cross_channel_recent_enabled", True
                        )
                    ),
                    owner_cross_channel_max_items=int(
                        MEMORY_SETTINGS.get("owner_cross_channel_max_items", 4) or 4
                    ),
                    owner_cross_channel_max_chars=int(
                        MEMORY_SETTINGS.get("owner_cross_channel_max_chars", 700)
                        or 700
                    ),
                    owner_cross_channel_max_age_sec=int(
                        MEMORY_SETTINGS.get(
                            "owner_cross_channel_max_age_sec", 6 * 3600
                        )
                        or (6 * 3600)
                    ),
                )
            except Exception as exc:
                self.context_assembler = None
                try:
                    self._logger.warning(
                        f"[ConversationEvents] assembler init failed: {exc}"
                    )
                except Exception:
                    pass

        # 配置
        self.store_roles = set(MEMORY_SETTINGS.get("store_roles", ["user"]))
        self.long_term_enabled = bool(MEMORY_SETTINGS.get("long_term_enabled", True))

        # 工具历史
        self.tool_history = []
        self.max_tool_history = 12
        self.tool_context_max_chars = 500

        # Logger
        self._logger = get_logger()

        # ========== 补全缺失的配置属性 ==========

        # 检索配置
        # 兼容新旧 key，优先使用 config.py 中的新命名
        self.cand_k = int(
            MEMORY_SETTINGS.get(
                "memory_recall_candidates", MEMORY_SETTINGS.get("cand_k", 8)
            )
        )
        self.final_k = int(
            MEMORY_SETTINGS.get(
                "memory_recall_final", MEMORY_SETTINGS.get("final_k", 3)
            )
        )
        self.sim_threshold = float(
            MEMORY_SETTINGS.get(
                "memory_sim_threshold", MEMORY_SETTINGS.get("sim_threshold", 0.28)
            )
        )
        self.half_life_days = float(
            MEMORY_SETTINGS.get(
                "memory_half_life_days", MEMORY_SETTINGS.get("half_life_days", 30.0)
            )
        )
        self.recall_roles = MEMORY_SETTINGS.get(
            "recall_roles", ["user", "assistant", "summary"]
        )
        self.use_llm_selector = bool(MEMORY_SETTINGS.get("use_llm_selector", False))
        self.llm_selector_min_interval_sec = float(
            MEMORY_SETTINGS.get("llm_selector_min_interval_sec", 20)
        )
        self._last_llm_selector_ts = 0.0
        self.recall_min_chars = int(
            MEMORY_SETTINGS.get("recall_min_chars", self.recall_min_chars)
        )

        # 图扩展配置
        self.graph_expand_enabled = bool(
            MEMORY_SETTINGS.get("graph_expand_enabled", True)
        )
        self.graph_expand_min_chars = int(
            MEMORY_SETTINGS.get("graph_expand_min_chars", 6)
        )

        # 调试配置
        self.debug_prompt_injection = bool(
            MEMORY_SETTINGS.get("debug_prompt_injection", False)
        )

        # 缓存
        self._query_cache = {}
        self._cache_ttl = 300
        self._cache_hits = 0
        self._cache_misses = 0

        if self.embedding_service.enabled:
            self._schedule_memory_vector_sync(limit=10)

    @staticmethod
    def _memory_vector_collection_name(model: str, dimension: int | None) -> str:
        identity = f"{str(model or 'unconfigured')}|{int(dimension or 0)}"
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        return f"memory_core_v1_{suffix}"

    @staticmethod
    def _knowledge_collection_compatibility(
        collection,
        *,
        model: str,
        dimension: int | None,
    ) -> dict:
        expected_model = str(model or "").strip()
        expected_dimension = int(dimension or 0)
        metadata = dict(getattr(collection, "metadata", None) or {})
        collection_model = str(metadata.get("embedding_model") or "").strip()
        try:
            collection_dimension = int(metadata.get("embedding_dimension") or 0)
        except (TypeError, ValueError):
            collection_dimension = 0
        try:
            count = int(collection.count())
        except Exception:
            count = 0

        model_mismatch = bool(
            count
            and collection_model
            and expected_model
            and collection_model != expected_model
        )
        dimension_mismatch = bool(
            count
            and collection_dimension
            and expected_dimension
            and collection_dimension != expected_dimension
        )
        metadata_incomplete = not collection_model or collection_dimension <= 0
        unknown_metadata_mismatch = bool(count and metadata_incomplete)
        rebuild_required = (
            model_mismatch or dimension_mismatch or unknown_metadata_mismatch
        )
        if not rebuild_required:
            updated = dict(metadata)
            if expected_model:
                updated["embedding_model"] = expected_model
            if expected_dimension > 0:
                updated["embedding_dimension"] = expected_dimension
            updated.setdefault("index_version", 1)
            modifier = getattr(collection, "modify", None)
            if updated != metadata and callable(modifier):
                modifier(metadata=updated)

        return {
            "rebuild_required": rebuild_required,
            "collection_count": count,
            "collection_model": collection_model,
            "collection_dimension": collection_dimension or None,
            "runtime_model": expected_model,
            "runtime_dimension": expected_dimension or None,
        }

    def _ensure_knowledge_collection_compatible(self) -> None:
        if self.knowledge_embedding_compatibility.get("rebuild_required"):
            raise EmbeddingUnavailableError(
                "知识库向量与当前嵌入模型不兼容，请清空并重新导入知识库"
            )

    def get_memory_vector_status(self) -> dict:
        return self.memory_vector_index.status()

    def test_embedding_connection(self) -> dict:
        self.embedding_service.embed(["Live2D-Suzu embedding connection test"])
        self._schedule_memory_vector_sync(limit=10)
        return self.embedding_service.status()

    def query_memory_vector(
        self,
        text: str,
        *,
        person_id: str = "owner",
        limit: int = 10,
    ) -> list[dict]:
        return self._query_memory_vector(
            text,
            person_id=person_id,
            session_id="",
            limit=limit,
        )

    def process_memory_vector_jobs(self, limit: int = 100) -> dict:
        with self._vector_lock:
            return self.memory_vector_index.process_pending(limit=limit)

    def _schedule_memory_vector_sync(self, limit: int = 100) -> bool:
        embedding_state = str(
            (self.embedding_service.status() or {}).get("state") or ""
        )
        if embedding_state in {"disabled", "unconfigured", "error"}:
            return False
        index_status = dict(self.memory_vector_index.status() or {})
        if index_status.get("rebuild_required"):
            return False
        with self._vector_schedule_lock:
            if self._vector_sync_future is not None and not self._vector_sync_future.done():
                return False
            self._vector_sync_future = self._vector_executor.submit(
                self.process_memory_vector_jobs,
                limit=limit,
            )
        return True

    def _query_memory_vector(self, text: str, **kwargs) -> list[dict]:
        embedding_status = dict(self.embedding_service.status() or {})
        if embedding_status.get("state") in {"disabled", "unconfigured", "error"}:
            raise EmbeddingUnavailableError(
                str(embedding_status.get("last_error") or embedding_status.get("state"))
            )
        self._schedule_memory_vector_sync(limit=10)
        with self._vector_lock:
            index = self.memory_vector_index
        return index.query(text, **kwargs)

    def rebuild_memory_vector_index(self) -> dict:
        with self._vector_lock:
            try:
                self.chroma_client.delete_collection(
                    name=self.memory_vector_collection_name
                )
            except ValueError:
                pass
            self.current_memory_collection = (
                self.chroma_client.get_or_create_collection(
                    name=self.memory_vector_collection_name,
                    metadata=self.memory_vector_collection_metadata,
                )
            )
            self.memory_vector_index = MemoryVectorIndex(
                repository=self.memory_core.repository,
                collection=self.current_memory_collection,
                embedding_service=self.embedding_service,
            )
            self.memory_core.vector_search = self._query_memory_vector
            self.memory_core.vector_job_notifier = self._schedule_memory_vector_sync
            queued = self.memory_core.rebuild_vector_jobs()
        if self.embedding_service.enabled:
            self._schedule_memory_vector_sync(limit=10)
        return {
            "queued": queued,
            "collection": self.memory_vector_collection_name,
        }

    def _extract_keywords(self, text: str):
        """提取关键词，用于图谱扩展"""
        if not text:
            return []
        try:
            # 使用 jieba 提取关键词
            return jieba.analyse.extract_tags(text, topK=5)
        except Exception:
            return []

    def _stable_md5(self, text: str) -> str:
        """生成稳定的 MD5 hash"""
        if not text:
            return ""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def get_knowledge_stats(self) -> dict:
        stats = {"chunk_count": 0, "collection_name": "waifu_knowledge_base"}
        try:
            stats["chunk_count"] = int(self.knowledge_collection.count())
        except Exception:
            pass
        stats["embedding"] = self.embedding_service.status()
        stats["rebuild_required"] = bool(
            self.knowledge_embedding_compatibility.get("rebuild_required")
        )
        stats["compatibility"] = dict(self.knowledge_embedding_compatibility)
        return stats

    def rebuild_knowledge_collection(self) -> bool:
        try:
            self.chroma_client.delete_collection("waifu_knowledge_base")
        except ValueError:
            pass
        except Exception:
            active_logger = getattr(self, "_logger", None)
            if active_logger is not None:
                active_logger.exception("Failed to delete knowledge collection")
            return False
        try:
            self.knowledge_collection = self.chroma_client.get_or_create_collection(
                name="waifu_knowledge_base",
                embedding_function=self.embedding_fn,
                metadata=self.knowledge_collection_metadata,
            )
            self.knowledge_embedding_compatibility = (
                self._knowledge_collection_compatibility(
                    self.knowledge_collection,
                    model=self.embedding_service.model,
                    dimension=self.embedding_service.expected_dimension,
                )
            )
            return True
        except Exception:
            return False

    def delete_knowledge_by_dirs(self, dirs) -> int:
        return delete_knowledge_by_dirs(self.knowledge_collection, dirs)

    def _restore_short_term_from_db(self):
        self.short_term_manager.restore_global()
        self.short_term_memory = self.short_term_manager.short_term_memory

    def _restore_session_short_term_from_db(self, session_id: str):
        self.short_term_manager.restore_session(session_id)
        self.session_short_term_memory = (
            self.short_term_manager.session_short_term_memory
        )
        self._session_short_term_loaded = (
            self.short_term_manager._session_short_term_loaded
        )

    def _legacy_short_term_context(self, session_id: str = None) -> list[dict]:
        """RAM / transcript short-term window (fallback when events projection fails)."""
        session_key = str(session_id or "").strip()
        if session_key:
            self._restore_session_short_term_from_db(session_key)
            return list(self.session_short_term_memory.get(session_key, []))
        return list(self.short_term_memory)

    def _get_short_term_context(
        self,
        *,
        session_id: str = None,
        conversation_scope=None,
    ) -> list[dict]:
        if getattr(self, "short_term_from_events", False) and conversation_scope is not None:
            assembler = getattr(self, "context_assembler", None)
            event_store = getattr(assembler, "store", None)
            if event_store is not None:
                try:
                    # Successful projection wins — including empty (no dialog events yet).
                    return list(
                        event_store.list_dialog_window(
                            conversation_scope,
                            limit=max(1, int(self.max_short_term)),
                        )
                    )
                except Exception as exc:
                    try:
                        self._logger.warning(
                            "[ConversationEvents] short-term projection failed; "
                            f"falling back to legacy short_term: {exc}"
                        )
                    except Exception:
                        pass
                    return self._legacy_short_term_context(session_id)

        return self._legacy_short_term_context(session_id)

    def _append_short_term_memory(
        self,
        role,
        content,
        session_id: str = None,
        *,
        event_id: str = "",
    ):
        evicted = self.short_term_manager.append(
            role,
            content,
            session_id=session_id,
            event_id=event_id,
        )
        self.short_term_memory = self.short_term_manager.short_term_memory
        self.session_short_term_memory = (
            self.short_term_manager.session_short_term_memory
        )
        self._session_short_term_loaded = (
            self.short_term_manager._session_short_term_loaded
        )
        return evicted

    def _bucket_key_for_mid_term(self, session_id: str = None, meta: dict = None) -> str:
        safe_meta = dict(meta or {})
        conversation_id = str(
            safe_meta.get("conversation_id")
            or safe_meta.get("context_session_id")
            or session_id
            or "global"
        ).strip()
        return conversation_id or "global"

    def note_evicted_for_mid_term(
        self,
        evicted: Optional[dict],
        *,
        session_id: str = None,
        meta: dict = None,
    ) -> Optional[list[str]]:
        """Accumulate evicted short-term turns that carry event_id for mid-term.

        Returns a ready batch of event_ids when the segment source threshold is
        reached; otherwise None. Does not call the LLM here.
        """
        if not getattr(self, "mid_term_enabled", False):
            return None
        if not isinstance(evicted, dict):
            return None
        event_id = str(evicted.get("event_id") or "").strip()
        if not event_id:
            return None
        bucket_key = self._bucket_key_for_mid_term(session_id=session_id, meta=meta)
        ready: Optional[list[str]] = None
        lock = getattr(self, "_pending_mid_term_lock", None)
        if lock is None:
            self._pending_mid_term_lock = threading.Lock()
            lock = self._pending_mid_term_lock
        with lock:
            pending = self._pending_mid_term_event_ids.setdefault(bucket_key, [])
            if event_id not in pending:
                pending.append(event_id)
            threshold = int(
                getattr(self, "mid_term_segment_source_items", 10) or 10
            )
            if len(pending) >= max(4, threshold):
                ready = list(pending[:threshold])
                del pending[:threshold]
        return ready

    def drain_pending_mid_term_event_ids(
        self, bucket_key: str = "", *, max_items: int = 0
    ) -> list[str]:
        """Pop pending event ids for a conversation bucket (tests / flush)."""
        key = str(bucket_key or "global").strip() or "global"
        limit = int(max_items or 0)
        lock = getattr(self, "_pending_mid_term_lock", None)
        if lock is None:
            return []
        with lock:
            pending = self._pending_mid_term_event_ids.get(key) or []
            if not pending:
                return []
            if limit <= 0 or limit >= len(pending):
                out = list(pending)
                self._pending_mid_term_event_ids[key] = []
                return out
            out = list(pending[:limit])
            del pending[:limit]
            return out

    # ================= 核心：添加记忆 (异步优化版) =================

    def add_memory(
        self,
        role,
        content,
        session_id: str = None,
        meta: dict = None,
        memory_session_id: str = None,
        event_id: str = "",
    ):
        """
        主线程只做最快的内存操作(RAM)，慢速 IO 操作(SQLite/Chroma/LLM提取)扔到后台线程池。
        这样可以显著减少 UI 卡顿。
        """
        safe_meta = dict(meta or {})
        resolved_event_id = str(
            event_id or safe_meta.get("event_id") or ""
        ).strip()
        with self._lock:
            # 1. 极速写入 RAM 短期记忆 (立即生效，供下一轮对话使用)
            evicted = self._append_short_term_memory(
                role,
                content,
                session_id=session_id,
                event_id=resolved_event_id,
            )

        ready_batch = self.note_evicted_for_mid_term(
            evicted, session_id=session_id, meta=safe_meta
        )
        if ready_batch and getattr(self, "mid_term_enabled", False):
            try:
                self._executor.submit(
                    self._background_build_mid_term_segment,
                    ready_batch,
                    session_id,
                    safe_meta,
                )
            except Exception:
                # Keep ids for a later flush; do not drop provenance.
                lock = getattr(self, "_pending_mid_term_lock", None)
                if lock is not None:
                    bucket_key = self._bucket_key_for_mid_term(
                        session_id=session_id, meta=safe_meta
                    )
                    with lock:
                        pending = self._pending_mid_term_event_ids.setdefault(
                            bucket_key, []
                        )
                        for eid in reversed(ready_batch):
                            if eid not in pending:
                                pending.insert(0, eid)

        # 2. 提交慢速任务到后台 (SQLite, Chroma, Graph, Profile提取)
        self._executor.submit(
            self._background_save_memory,
            role,
            content,
            session_id,
            memory_session_id,
            meta,
        )

    def _background_build_mid_term_segment(
        self,
        event_ids: list[str],
        session_id: str = None,
        meta: dict = None,
    ) -> None:
        """Build a mid-term segment from source event ids (non-blocking)."""
        if not event_ids or not getattr(self, "mid_term_enabled", False):
            return
        try:
            from modules.conversation_events.mid_term import MidTermSegmentBuilder
            from modules.conversation_events.store import ConversationEventStore

            sqlite = getattr(self, "sqlite_store", None)
            if sqlite is None:
                return
            store = ConversationEventStore(sqlite)
            builder = MidTermSegmentBuilder(
                store=store,
                sqlite_store=sqlite,
                llm_callable=self._summarize_mid_term_events,
            )
            builder.build_from_event_ids(list(event_ids))
        except Exception as exc:
            try:
                self._logger.warning(
                    "[MidTerm] background segment build failed: %s", exc
                )
            except Exception:
                pass

    def _summarize_mid_term_events(self, events) -> str:
        if chat_with_ai is None:
            raise RuntimeError("summary LLM unavailable")
        rows = []
        for event in events:
            rows.append(
                {
                    "event_id": str(event.event_id or ""),
                    "type": event.event_type.value,
                    "exact_text": str(event.exact_text or ""),
                    "evidence_summary": str(event.evidence_summary or ""),
                    "metadata": dict(event.metadata or {}),
                }
            )
        prompt = (
            "只根据下列带 event_id 的会话事件生成中期摘要 JSON，不得补充来源中没有的事实。\n"
            "必须输出单个 JSON 对象，字段：source_event_ids、topics、user_state、"
            "assistant_commitments、unresolved_threads、entities、recall_cues、"
            "summary、confidence、status。source_event_ids 只能使用输入 ID；"
            "assistant_commitments 只能来自 assistant/proactive/care 事件；"
            "status 使用 active。\n事件：\n"
            + json.dumps(rows, ensure_ascii=False)
        )
        return str(
            chat_with_ai(
                [{"role": "system", "content": prompt}],
                task_type="summary",
                caller="mid_term_segment",
            )
            or ""
        )

    def _background_save_memory(
        self,
        role,
        content,
        session_id: str = None,
        memory_session_id: str = None,
        meta: dict = None,
    ):
        """Persist real messages through Memory Core's single write path."""
        try:
            safe_meta = dict(meta or {})
            persistent_session_id = str(memory_session_id or session_id or "").strip()
            if persistent_session_id:
                safe_meta["session_id"] = persistent_session_id
            user_id = str(safe_meta.get("user_id") or "").strip()
            is_owner = bool(safe_meta.get("is_owner"))
            source = str(safe_meta.get("source") or "").strip().lower()
            person_id = "owner"
            if user_id and source in {"qq_gateway", "napcat_qq"} and not is_owner:
                person_id = f"qq:{user_id}"
            active_character = (
                character_manager.get_active_character() if character_manager else {}
            )
            character_id = str(
                getattr(character_manager, "data", {}).get("active_id") or ""
            ).strip()
            character_name = str(
                (active_character or {}).get("name") or ""
            ).strip()
            self.memory_core.record_message(
                role,
                content,
                session_id=persistent_session_id,
                person_id=person_id,
                character_id=character_id,
                character_name=character_name,
                meta=safe_meta,
            )

        except Exception as e:
            if self._logger:
                self._logger.exception(f"Memory Core background write failed: {e}")

    def _fetch_profile_from_db(self) -> str:
        """从 SQLite 获取 User 和 当前角色 的档案"""
        if not self.sqlite_store:
            return ""

        # 获取当前角色ID
        active_id = "default_char"
        if character_manager:
            active_id = character_manager.data.get("active_id", "default_char")

        # 查库：只查 active 的档案数据
        items = self.sqlite_store.list_items(status="active", limit=1000)

        user_lines = []
        agent_lines = []

        for it in items:
            typ = it.get("type")
            text = it.get("text", "")
            tags = it.get("tags") or []

            # 兼容旧tags: 如果 tags 是字符串，尝试转列表（有些库可能会这样）
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []

            # ---------------- User 档案 ----------------
            if typ == "user_profile" or "role:user" in tags:
                if "name" in tags:
                    user_lines.insert(0, f"- 称呼：{text}")
                elif "status" in tags:
                    user_lines.append(f"- 状态：{text}")
                elif "dislikes" in tags:
                    user_lines.append(f"- 雷点：{text}")
                elif "note" in tags:
                    user_lines.append(f"★ 备注：{text}")
                elif "likes" in tags:
                    cat = (
                        tags[-1] if len(tags) > 1 and tags[-1] != "likes" else "general"
                    )
                    user_lines.append(f"- 喜好({cat})：{text}")

            # ---------------- Agent 档案 (需匹配 ID) ----------------
            elif typ == "agent_profile" or any(t.startswith("role:") for t in tags):
                # 检查归属
                role_tag = next((t for t in tags if t.startswith("role:")), None)
                # 如果有 role:xxx 且不等于当前 active_id，跳过
                if role_tag and role_tag != f"role:{active_id}":
                    continue
                # 如果没有 role:xxx，默认视为通用或 default_char

                if "name" in tags:
                    agent_lines.insert(0, f"- 你的名字：{text}")
                elif "traits" in tags:
                    agent_lines.append(f"- 性格：{text}")
                elif "dislikes" in tags:
                    agent_lines.append(f"- 讨厌：{text}")
                elif "likes" in tags:
                    cat = (
                        tags[-1] if len(tags) > 1 and tags[-1] != "likes" else "general"
                    )
                    agent_lines.append(f"- 喜好({cat})：{text}")

        out = []
        if user_lines:
            out.append("【用户档案】")
            out.extend(user_lines)
        if agent_lines:
            out.append("\n【自我认知 (你)】")
            out.extend(agent_lines)

        return "\n".join(out)

    def _should_store_long_term(self, role: str, content: str) -> bool:
        """
        判断是否需要存入长期记忆 (规则 + LLM 双重判断)
        """
        if not self.long_term_enabled:
            return False

        if role not in self.store_roles:
            return False

        t = (content or "").strip()
        if not t:
            return False

        # 1. 基础过滤：太短的通常是废话 (嗯、哦、哈哈)
        # 中文环境下，少于 2 个字且没有特定符号的，基本可以扔
        if len(t) < 2:
            return False

        # 过滤常见口语噪声
        noise = [
            "嗯",
            "哦",
            "好的",
            "行",
            "哈哈",
            "ok",
            "OK",
            "emmm",
            "…",
            "...",
            "真的吗",
            "是吗",
        ]
        if t.lower() in noise:
            return False

        # 2. 【快速通道】规则判断 (省流)
        # 如果包含这些强特征词，直接存，不需要问 LLM
        fast_triggers = [
            "我叫",
            "名字",
            "生日",
            "住在",
            "工作",
            "学校",
            "喜欢",
            "讨厌",
            "不爱",
            "爱好",
            "偏好",
            "记住",
            "别忘",
            "提醒",
            "计划",
            "目标",
            "正在",
            "打算",
            "准备",
            "最近",
            "忙",
            "专利",
            "项目",  # 把刚才加的也放这
            "因为",
            "所以",
            "觉得",
            "认为",
        ]
        if any(k in t for k in fast_triggers):
            return True

        # 3. 【智能通道】LLM 语义判断 (漏网之鱼)
        # 如果没命中关键词，但句子长度尚可(比如 > 4字)，可能是隐晦的重要信息
        # 比如：“彻底搞砸了，心情很差” (没命中关键词，但很重要)
        if len(t) >= 4 and chat_with_ai and self.use_llm_selector:
            now_ts = time.time()
            if now_ts - self._last_llm_selector_ts < self.llm_selector_min_interval_sec:
                return False
            try:
                # 使用最便宜的模型 (gatekeeper / summary)
                # 构造一个极简 Prompt
                prompt = f"""
Judge if this message contains useful facts/status/emotions worth remembering.
Message: "{t}"
Output ONLY "YES" or "NO".
"""
                decision = chat_with_ai(
                    [{"role": "user", "content": prompt}],
                    task_type="gatekeeper",  # 👈 用最便宜的模型
                    caller="memory_selector",
                )
                self._last_llm_selector_ts = now_ts

                if decision and "YES" in decision.strip().upper():
                    print(f"🧠 [Memory] LLM 判定此句值得记忆: {t}")
                    return True
            except Exception:
                pass

        return False

    def _format_memory_item(self, meta: dict, doc: str) -> str:
        role = meta.get("role", "user")
        ts = meta.get("ts", "")
        short_ts = ts.replace("T", " ").replace("Z", "")[:16] if ts else ""
        prefix = "你" if role == "user" else "我"
        return f"- [{short_ts}] {prefix}：{doc}"

    def _recency_score(self, ts_iso: str) -> float:
        if not ts_iso:
            return 0.0
        try:
            t = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_days = max(0.0, (now - t).total_seconds() / 86400.0)
            # 半衰期模型：score = 0.5^(age/half_life)
            return 0.5 ** (age_days / max(1e-6, self.half_life_days))
        except Exception:
            return 0.0

    @staticmethod
    def _dist_to_sim(dist: float) -> float:
        # Chroma distance：不同 backend 可能不同，这里做一个安全映射（越小越相似）
        try:
            d = float(dist)
        except Exception:
            return 0.0
        # 常见 cosine distance 在 0~2，取 1-d 的近似，再 clamp
        sim = 1.0 - d
        return max(0.0, min(1.0, sim))

    @staticmethod
    def _is_recall_intent_query(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False

        if t.startswith("/"):
            return False
        cues = [
            "记得",
            "还记得",
            "忘了",
            "之前",
            "刚才",
            "上午",
            "早上",
            "昨天",
            "前天",
            "说过",
            "提过",
            "怎么了",
            "为什么",
            "当时",
            "回忆",
            "remember",
            "forgot",
            "earlier",
            "previously",
            "what happened",
            "腹泻",
            "断食",
            "拉肚子",
            "体检",
            "医院",
            "生病",
            "不舒服",
        ]
        return any(k in t for k in cues)

    def _extract_recall_terms(self, text: str) -> list:
        t = (text or "").strip()
        if not t:
            return []
        terms = []
        try:
            for w in jieba.lcut(t):
                w = (w or "").strip()
                if len(w) >= 2:
                    terms.append(w.lower())
        except Exception:
            pass
        for w in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", t):
            terms.append(w.lower())

        stop = {
            "今天",
            "现在",
            "这个",
            "那个",
            "就是",
            "然后",
            "因为",
            "所以",
            "觉得",
            "怎么",
            "什么",
            "一下",
            "一下子",
            "可以",
            "是不是",
            "有没有",
            "为什么",
            "你",
            "我",
            "他",
            "她",
            "它",
            "我们",
            "你们",
            "他们",
            "please",
            "could",
            "would",
            "should",
            "think",
            "about",
        }
        dedup, seen = [], set()
        for w in terms:
            if w in stop or len(w) < 2 or w in seen:
                continue
            seen.add(w)
            dedup.append(w)
        return dedup[:18]

    @staticmethod
    def _role_recall_weight(role: str, strict_user_fact: bool = False) -> float:
        r = (role or "").strip().lower()
        if strict_user_fact:
            if r == "user":
                return 0.20
            if r == "summary":
                return 0.12
            return -0.15
        if r == "user":
            return 0.10
        if r == "summary":
            return 0.05
        if r == "assistant":
            return -0.03
        return 0.0

    @staticmethod
    def _score_text_overlap(doc: str, terms: list) -> float:
        if not doc or not terms:
            return 0.0
        d = doc.lower()
        hit = sum(1 for t in terms if t in d)
        return min(1.0, hit / max(1.0, len(terms)))

    def _retrieve_from_transcript_fallback(
        self,
        search_text: str,
        limit: int = 4,
        strict_user_fact: bool = False,
        session_id: str = None,
    ) -> list:
        """
        向量召回为空时，从 transcript 做轻量兜底召回，避免“明明说过却回忆不到”。
        """
        if not self.sqlite_store:
            return []
        t = (search_text or "").strip()
        if not t:
            return []

        terms = self._extract_recall_terms(t)
        role_allow = (
            {"user", "summary"} if strict_user_fact else set(self.recall_roles or [])
        )
        items = []
        seen = set()
        session_key = str(session_id or "").strip()
        try:
            rows = self.sqlite_store.list_transcript(
                limit=max(limit * 12, 120),
                offset=0,
                session_id=session_key,
                session_scope="specific" if session_key else "global",
            )
            for kw in terms[:6]:
                try:
                    rows.extend(
                        self.sqlite_store.list_transcript(
                            query=kw,
                            limit=18,
                            offset=0,
                            session_id=session_key,
                            session_scope="specific" if session_key else "global",
                        )
                    )
                except Exception:
                    pass

            for r in rows:
                role = (r.get("role") or "user").strip()
                if role_allow and role not in role_allow:
                    continue
                doc = str(r.get("content") or "").strip()
                if not doc:
                    continue
                row_id = int(r.get("id", 0) or 0)
                if row_id and row_id in seen:
                    continue
                overlap = self._score_text_overlap(doc, terms)
                if terms and overlap <= 0.0:
                    continue
                ts_iso = str(r.get("ts_iso") or "")
                rec = self._recency_score(ts_iso)
                role_w = self._role_recall_weight(
                    role, strict_user_fact=strict_user_fact
                )
                score = overlap * 0.62 + rec * 0.28 + role_w
                items.append(
                    {
                        "id": f"tr_{row_id}",
                        "doc": doc,
                        "meta": {
                            "role": role,
                            "ts": ts_iso,
                            "kind": "transcript_fallback",
                        },
                        "sim": overlap,
                        "rec": rec,
                        "score": score,
                    }
                )
                if row_id:
                    seen.add(row_id)
        except Exception:
            return []
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[: max(1, int(limit))]

    # ---------- 新增：导入知识（修复 hash(chunk) 不稳定问题） ----------
    def import_knowledge_from_file(self, file_path, progress_callback=None):
        self._ensure_knowledge_collection_compatible()
        result = import_knowledge_file_modular(
            self.knowledge_collection, self._stable_md5, file_path, progress_callback=progress_callback
        )
        if isinstance(result, dict):
            return result
        return {"added": int(result or 0), "skipped": 0, "total": int(result or 0)}

    def search_knowledge(self, search_text: str, k: int = 3):
        self._ensure_knowledge_collection_compatible()
        return search_knowledge_modular(self.knowledge_collection, search_text, k=k)

    # ---------- 记忆写入 ----------
    # def add_memory(self, role, content):
    #     """添加记忆（线程安全 + 双写 SQLite/Chroma）"""
    #     with self._lock:
    #         try:
    #             # 1. 🟢 [修复] 必须先写入 SQLite (全量日志)
    #             try:
    #                 self.sqlite_store.add_transcript(role, content)
    #             except Exception as e:
    #                 print(f"❌ [Memory] SQLite 写入严重失败: {e}")
    #
    #             # 2. 更新 RAM 短期记忆
    #             self.short_term_memory.append({"role": role, "content": content})
    #             if len(self.short_term_memory) > self.max_short_term:
    #                 self.short_term_memory.pop(0)
    #
    #             # 3. 更新 Profile (JSON)
    #
    #             # 4. 写入 Vector DB (条件过滤)
    #             if self._should_store_long_term(role, content):
    #                 meta = {
    #                     "role": role,
    #                     "ts": datetime.now(timezone.utc).isoformat(),
    #                     "kind": "chat",
    #                 }
    #                 msg_id = f"mem_{int(time.time() * 1000)}_{role}_{uuid.uuid4().hex[:8]}"
    #
    #                 try:
    #                     self.memory_collection.add(
    #                         documents=[content],
    #                         metadatas=[meta],
    #                         ids=[msg_id],
    #                     )
    #                 except Exception as e:
    #                     print(f"⚠️ [Memory] 向量库写入失败: {e}")
    #
    #             # 5. 更新图谱 (仅用户)
    #             if role == "user":
    #                 try:
    #                     keywords = self._extract_keywords(content)
    #                     for k1, k2 in itertools.combinations(keywords, 2):
    #                         self.graph.add_concept_link(k1, k2)
    #                 except Exception as e:
    #                     print(f"⚠️ [Memory] 图记忆更新失败: {e}")
    #
    #         except Exception as e:
    #             print(f"❌ [Memory] add_memory 主流程异常: {e}")
    #             import traceback
    #             traceback.print_exc()

    # ---------- 记忆检索：候选召回 + 时间衰减重排 + 可选 LLM 决策 ----------
    def _retrieve_memories(self, search_text: str, session_id: str = None):
        # ✅ 性能优化：检查查询缓存
        session_key = str(session_id or "").strip()
        cache_key = self._stable_md5(search_text + f":{self.final_k}:{session_key}")
        if cache_key in self._query_cache:
            cached_time, cached_result = self._query_cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                self._cache_hits += 1
                return cached_result

        self._cache_misses += 1

        candidates = []
        seen_doc = set()
        strict_user_fact = self._is_recall_intent_query(search_text)
        role_allow = (
            {"user", "summary"} if strict_user_fact else set(self.recall_roles or [])
        )

        try:
            query_kwargs = {
                "query_texts": [search_text],
                "n_results": self.cand_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if session_key:
                query_kwargs["where"] = {"session_id": session_key}
            res = self.memory_collection.query(**query_kwargs)

            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            ids = (res.get("ids") or [[]])[0]

            # 某些版本可能 ids 为空/长度不齐，兜底
            if not ids or len(ids) != len(docs):
                ids = list(ids) if ids else []
                for i in range(len(docs) - len(ids)):
                    ids.append(f"mem_noid_{i}")

            for doc, meta, dist, _id in zip(docs, metas, dists, ids):
                meta = meta or {}
                if isinstance(meta, dict):
                    meta = deserialize_vector_metadata(meta)
                if not session_key and str(meta.get("session_id") or "").strip():
                    continue
                role = (meta.get("role") or "user").strip()

                # ✅ role 过滤：默认只召回 user，减少带偏
                if role_allow and role not in role_allow:
                    continue

                sim = self._dist_to_sim(dist)
                if sim < self.sim_threshold:
                    continue

                doc_norm = re.sub(r"\s+", " ", (doc or "").strip())
                if not doc_norm:
                    continue

                doc_key = self._stable_md5(doc_norm)
                if doc_key in seen_doc:
                    continue
                seen_doc.add(doc_key)

                rec = self._recency_score(meta.get("ts", ""))
                role_w = self._role_recall_weight(
                    role, strict_user_fact=strict_user_fact
                )
                score = sim * 0.68 + rec * 0.27 + role_w

                candidates.append(
                    {
                        "id": _id,
                        "doc": doc_norm,
                        "meta": meta,
                        "sim": sim,
                        "rec": rec,
                        "score": score,
                    }
                )
        except Exception:
            pass

        fb_items = self._retrieve_from_transcript_fallback(
            search_text,
            limit=max(self.final_k, 4),
            strict_user_fact=strict_user_fact,
            session_id=session_key,
        )
        if not candidates:
            candidates = fb_items
        elif fb_items:
            known = {str(c.get("id")) for c in candidates}
            for it in fb_items:
                if str(it.get("id")) in known:
                    continue
                candidates.append(it)

        sender_id = (
            session_key.split(":", 1)[1] if session_key.startswith("private:") else ""
        )
        candidates = post_process_memory_candidates(
            self,
            candidates,
            search_text,
            sender_id=sender_id,
        )

        # 先按综合分排序
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # 可选：让 LLM 从 topN 里挑最相关的 2~3 条
        if self.use_llm_selector and chat_with_ai and len(candidates) > self.final_k:
            picked = self._llm_pick_memories(
                search_text, candidates[: min(10, len(candidates))], want=self.final_k
            )
            if picked:
                id_set = set(picked)
                candidates = [c for c in candidates if c["id"] in id_set]
                order = {mid: i for i, mid in enumerate(picked)}
                candidates.sort(key=lambda x: order.get(x["id"], 9999))

        top = candidates[: self.final_k]
        self._query_cache[cache_key] = (time.time(), top)
        return top

    def _llm_pick_memories(self, query: str, candidates: list, want: int = 3):
        """
        输出：候选 id 列表（最多 want 个）
        """
        try:
            lines = []
            for i, c in enumerate(candidates):
                role = c["meta"].get("role", "user")
                ts = c["meta"].get("ts", "")
                lines.append(
                    f"{i}. id={c['id']} role={role} ts={ts}\n   内容：{c['doc']}"
                )

            prompt = (
                "你是一个“记忆筛选器”。任务：从候选记忆中挑选与当前问题最相关的记忆。\n"
                "规则：\n"
                f"- 最多选 {want} 条\n"
                "- 优先选择：用户偏好/身份信息/未完成计划/明确事实\n"
                "- 如果不相关就不要选\n"
                '输出要求：只输出 JSON，例如：{"ids":["id1","id2"]}\n\n'
                f"当前输入：{query}\n\n候选记忆：\n" + "\n".join(lines)
            )

            resp = (
                chat_with_ai(
                    [{"role": "system", "content": prompt}],
                    task_type="summary",
                    caller="memory_rerank",
                )
                or ""
            )

            m = re.search(r"\{.*\}", resp, flags=re.S)
            if not m:
                return []
            obj = json.loads(m.group(0))
            ids = obj.get("ids", [])
            if not isinstance(ids, list):
                return []
            cand_ids = {c["id"] for c in candidates}
            ids = [x for x in ids if isinstance(x, str) and x in cand_ids]
            return ids[:want]
        except Exception:
            return []

    def _retrieve_knowledge(self, search_text: str, k: int = 2):
        try:
            return self.search_knowledge(search_text, k=k)
        except Exception as exc:
            active_logger = getattr(self, "_logger", None)
            if active_logger is not None:
                active_logger.warning("Knowledge recall unavailable: %s", exc)
            return []

    # ---------- 缓存管理（性能优化） ----------
    def clear_query_cache(self):
        """清理过期的查询缓存"""
        now = time.time()
        self._query_cache = {
            k: v for k, v in self._query_cache.items() if now - v[0] < self._cache_ttl
        }
        # ✅ 修复：使用 self._logger
        if self._logger:
            self._logger.info(f"查询缓存已清理，剩余 {len(self._query_cache)} 条")
        else:
            print(f"🧠 [Memory] 查询缓存已清理，剩余 {len(self._query_cache)} 条")

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        total = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total * 100) if total > 0 else 0

        stats = {
            "total_queries": total,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": hit_rate,
            "cached_items": len(self._query_cache),
        }

        # ✅ 修复：使用 self._logger
        if self._logger:
            self._logger.debug(f"缓存统计: {stats}")

        return stats

    def _extract_runtime_system_additions(
        self, system_persona: str, time_header: str, core_persona: str
    ) -> str:
        """保留 ChatService 在运行时追加的上下文，例如来源、历史片段和 Skills。"""
        extra = str(system_persona or "")
        for block in (time_header, core_persona, DEFAULT_PERSONA, SYSTEM_RULES_PROMPT):
            block_text = str(block or "").strip()
            if block_text:
                extra = extra.replace(block_text, "")
        extra = extra.strip()
        if not extra:
            return ""
        tool_markers = [
            "【可用工具能力】",
            "【工具】",
            "【可委托任务】",
            "【远程MCP工具】",
        ]
        cut_positions = [extra.find(marker) for marker in tool_markers]
        cut_positions = [pos for pos in cut_positions if pos >= 0]
        if cut_positions:
            extra = extra[: min(cut_positions)]
        return re.sub(r"\n{3,}", "\n\n", extra).strip()

    # ---------- 构建 Prompt ----------
    # ---------- 工具使用记录（用于 ToolRouter/工具轮上下文） ----------
    def record_tool_use(self, triggers, tool_feedback: str = "", user_text: str = ""):
        """记录本轮工具执行信息（不默认注入到 prompt，只有 tool_intent 才会注入）。"""
        # ✅ 并发安全：使用锁保护工具历史记录
        with self._lock:
            try:
                trig = [
                    t.strip()
                    for t in (triggers or [])
                    if isinstance(t, str) and t.strip()
                ]
                if not trig and not tool_feedback:
                    return
                item = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "triggers": trig[:12],
                    "user": (user_text or "").strip()[:120],
                    "result": (tool_feedback or "").strip()[:900],
                }
                self.tool_history.append(item)
                if len(self.tool_history) > self.max_tool_history:
                    self.tool_history = self.tool_history[-self.max_tool_history :]
            except Exception:
                pass

    def _format_tool_history(self, tool_intent=None) -> str:
        """只挑与本轮 tool_intent 相关的最近几条，避免浪费 token。"""
        try:
            intent = set(
                [
                    t.strip()
                    for t in (tool_intent or [])
                    if isinstance(t, str) and t.strip()
                ]
            )
            if not intent:
                return ""
            if not self.tool_history:
                return ""

            picked = []
            for it in reversed(self.tool_history):
                it_trig = set(it.get("triggers") or [])
                if it_trig & intent:
                    picked.append(it)
                if len(picked) >= 3:
                    break

            if not picked:
                return ""

            picked.reverse()
            lines = []
            for it in picked:
                ts = (it.get("ts") or "").replace("T", " ").replace("Z", "")[:16]
                trig = ",".join(it.get("triggers") or [])
                u = it.get("user") or ""
                r = it.get("result") or ""
                lines.append(f"- [{ts}] triggers={trig}\n  用户：{u}\n  结果：{r}")

            out = "\n".join(lines).strip()
            if len(out) > self.tool_context_max_chars:
                out = out[-self.tool_context_max_chars :]
            return out
        except Exception:
            return ""

    def build_prompt(
        self,
        current_user_text,
        system_persona,
        tool_intent=None,
        session_id: str = None,
        memory_session_id: str = None,
        person_id: str = "owner",
        conversation_scope=None,
    ):
        print("🔍 [系统] 正在构建统一记忆上下文.")

        time_header = ""
        if "【当前时间】" in system_persona:
            time_header = system_persona.split("\n")[0]

        active_char = character_manager.get_active_character()
        active_character_id = str(
            getattr(character_manager, "data", {}).get("active_id") or ""
        ).strip()
        if active_char and active_char.get("prompt"):
            core_persona = active_char["prompt"]
        else:
            core_persona = DEFAULT_PERSONA
        final_system = f"{time_header}\n\n{core_persona}\n\n{SYSTEM_RULES_PROMPT}"

        runtime_additions = self._extract_runtime_system_additions(
            system_persona, time_header, core_persona
        )
        if runtime_additions:
            final_system += "\n\n" + runtime_additions

        tool_desc = ""
        if "【可用工具能力】" in system_persona:
            parts = system_persona.split("【可用工具能力】")
            if len(parts) > 1:
                tool_desc = "【可用工具能力】" + parts[1]
        elif "【工具】" in system_persona:
            parts = system_persona.split("【工具】")
            if len(parts) > 1:
                tool_desc = "【工具】" + parts[1]

        if tool_desc:
            final_system += "\n\n" + tool_desc

        raw_user = (current_user_text or "").strip()
        tool_mode = bool(tool_intent)
        session_key = str(session_id or "").strip()
        memory_session_key = str(memory_session_id or session_key).strip()
        short_ctx = self._get_short_term_context(
            session_id=session_key,
            conversation_scope=conversation_scope,
        )
        recent_messages = [
            item
            for item in short_ctx[-8:]
            if isinstance(item, dict)
            and str(item.get("role") or "").strip() in {"user", "assistant"}
        ]

        if tool_mode:
            profile = self.memory_core.get_person_profile(
                person_id,
                max_items=self.memory_core.profile_max_items,
            )
            memory_intent = "none"
            profile_text = profile.text
            mem_text = ""
        else:
            memory_context = self.memory_core.build_reply_context(
                raw_user,
                session_id=memory_session_key,
                person_id=person_id,
                recent_messages=recent_messages,
            )
            memory_intent = memory_context.intent
            profile_text = memory_context.profile_text
            mem_text = memory_context.memory_text

        character_profile_text = ""
        if active_character_id:
            try:
                character_profile = self.memory_core.get_character_profile(
                    active_character_id
                )
                character_profile_text = character_profile.text
            except Exception:
                character_profile_text = ""

        know_text = ""
        if not tool_mode and memory_intent == "none" and len(raw_user) >= 8:
            know_items = self._retrieve_knowledge(raw_user, k=2)
            if know_items:
                know_text = "\n".join([f"· {item}" for item in know_items])

        sqlite_tasks_text = ""
        try:
            from modules.memory_sqlite import format_active_tasks_for_prompt

            if self.sqlite_store:
                sqlite_tasks_text = format_active_tasks_for_prompt(
                    self.sqlite_store, limit=6
                )
        except Exception:
            pass

        if profile_text:
            final_system += (
                "\n\n【当前用户画像】\n"
                "只把这些信息作为理解用户的背景；当前消息与画像冲突时，以当前消息为准。\n"
                + profile_text
            )

        if character_profile_text:
            final_system += (
                "\n\n【当前角色补充档案】\n"
                "这些是你当前角色的自我认知补充，不是用户信息；与核心角色设定冲突时，以核心角色设定为准。\n"
                + character_profile_text
            )

        if sqlite_tasks_text:
            final_system += "\n\n【当前待办/承诺】:\n" + sqlite_tasks_text

        if know_text:
            final_system += "\n\n【相关知识库】:\n" + clean_injected_context(know_text)

        # Near-history: only via ContextAssembler (T2 single read path).
        assembled = None
        recent_event_block = ""
        active_session_block = ""
        mid_term_block = ""
        cross_channel_recent_block = ""
        resolved_memory_block = mem_text or ""
        context_assembler = getattr(self, "context_assembler", None)
        if context_assembler is not None and not tool_mode:
            try:
                assembled = context_assembler.assemble(
                    current_user_text=raw_user,
                    scope=conversation_scope,
                    short_term_messages=short_ctx,
                    long_term_block=mem_text or "",
                )
                self._last_assembled_context = assembled
                recent_event_block = str(
                    getattr(assembled, "recent_event_block", "") or ""
                )
                active_session_block = str(
                    getattr(assembled, "active_session_block", "") or ""
                )
                mid_term_block = str(
                    getattr(assembled, "mid_term_block", "") or ""
                )
                cross_channel_recent_block = str(
                    getattr(assembled, "cross_channel_recent_block", "") or ""
                )
                resolved_memory_block = str(
                    getattr(assembled, "long_term_block", "") or ""
                )
                if assembled.short_term_messages:
                    short_ctx = [
                        dict(item) for item in assembled.short_term_messages
                    ]
            except Exception as exc:
                try:
                    self._logger.warning(
                        f"[ConversationEvents] assemble failed: {exc}"
                    )
                except Exception:
                    pass

        if recent_event_block:
            final_system += "\n\n" + recent_event_block
        if cross_channel_recent_block:
            final_system += "\n\n" + cross_channel_recent_block
        if active_session_block:
            final_system += "\n\n" + active_session_block
        if mid_term_block:
            final_system += "\n\n" + mid_term_block

        if resolved_memory_block:
            final_system += (
                "\n\n【经筛选的长期记忆】\n"
                "这些记录只用于回答当前问题，不要逐条复述，也不要补全记录中没有的事实。"
                "若记录里没有明确的周几、日期或次数，就直说没查到可靠依据，禁止猜测。"
                "\n"
                + resolved_memory_block
            )
        elif not mem_text and memory_intent in {"episode", "profile"}:
            final_system += (
                "\n\n【经筛选的长期记忆】\n"
                "当前没有找到与这个问题直接相关的可靠记录。"
                "请明确说没查到可靠记事或依据，不要编造周几、日期、次数或“查了下记事”的假结论。\n"
            )

        tool_ctx = self._format_tool_history(tool_intent)
        if tool_ctx:
            final_system += "\n\n【工具使用记录】:\n" + tool_ctx

        messages = [{"role": "system", "content": final_system}]
        if memory_intent in {"episode", "profile"}:
            short_ctx = [
                m for m in short_ctx if (m.get("role") or "").strip() == "user"
            ][-4:]
        while short_ctx:
            last = short_ctx[-1]
            if (
                str(last.get("role") or "").strip() == "user"
                and str(last.get("content") or "").strip() == raw_user
            ):
                short_ctx.pop()
                continue
            break
        messages += short_ctx[-self.max_short_term :]
        messages += [{"role": "user", "content": current_user_text}]

        return messages
