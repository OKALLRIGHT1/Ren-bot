from __future__ import annotations

import os
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from modules.memory_core.categories import classify_memory_record
from modules.memory_core.models import MemoryProfile, ReplyMemoryContext
from modules.conversation_events.models import AssembledContext


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_advanced_memory_prompt_only_keeps_bounded_recent_context(monkeypatch):
    import modules.advanced_memory as advanced_memory

    class FakeCore:
        enabled = True

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="episode", memory_text="- 会议持续四十分钟")

        def get_character_profile(self, character_id, *args, **kwargs):
            assert character_id == "char_tomori"
            return MemoryProfile(
                person_id="character:char_tomori",
                text="- 喜欢：星星",
            )

    brain = advanced_memory.AdvancedMemorySystem.__new__(advanced_memory.AdvancedMemorySystem)
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"历史消息 {index}"}
        for index in range(20)
    ]
    brain.session_short_term_memory = {}
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain._retrieve_knowledge = lambda *args, **kwargs: []
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        SimpleNamespace(
            data={"active_id": "char_tomori"},
            get_active_character=lambda: {"prompt": "角色设定"},
        ),
    )

    messages = brain.build_prompt(
        "我上次开会开了多久",
        "系统设定",
        session_id="",
        person_id="owner",
    )

    assert "会议持续四十分钟" in messages[0]["content"]
    assert "当前角色补充档案" in messages[0]["content"]
    assert "喜欢：星星" in messages[0]["content"]
    assert len(messages) <= 6
    assert all(item["role"] != "assistant" for item in messages)
    assert "历史消息 0" not in "\n".join(item["content"] for item in messages)


def test_advanced_memory_separates_short_context_from_shared_long_term(monkeypatch):
    import modules.advanced_memory as advanced_memory

    calls = []

    class FakeCore:
        enabled = True

        def build_reply_context(self, *args, **kwargs):
            calls.append(dict(kwargs))
            return ReplyMemoryContext(intent="episode", memory_text="- 共享长期记忆")

        def get_character_profile(self, *args, **kwargs):
            return MemoryProfile(person_id="character:test")

    brain = advanced_memory.AdvancedMemorySystem.__new__(advanced_memory.AdvancedMemorySystem)
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = []
    brain.session_short_term_memory = {
        "group:100": [{"role": "user", "content": "第一个群的短期内容"}],
        "group:200": [{"role": "user", "content": "第二个群的短期内容"}],
    }
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain._retrieve_knowledge = lambda *args, **kwargs: []
    brain._restore_session_short_term_from_db = lambda *args, **kwargs: None
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        SimpleNamespace(
            data={"active_id": "test"},
            get_active_character=lambda: {"prompt": "角色设定"},
        ),
    )

    messages = brain.build_prompt(
        "还记得以前的事吗",
        "系统设定",
        session_id="group:200",
        memory_session_id="owner_shared",
        person_id="owner",
    )
    rendered = "\n".join(str(item.get("content") or "") for item in messages)

    assert calls[-1]["session_id"] == "owner_shared"
    assert "第二个群的短期内容" in rendered
    assert "第一个群的短期内容" not in rendered


def test_advanced_memory_injects_context_layers_in_priority_order(monkeypatch):
    import modules.advanced_memory as advanced_memory

    class FakeCore:
        enabled = True

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="episode", memory_text="长期事实")

        def get_character_profile(self, *args, **kwargs):
            return MemoryProfile(person_id="character:test")

    class FakeAssembler:
        def assemble(self, **kwargs):
            return AssembledContext(
                recent_event_block="【最近发生的事｜内部参考】\n近因原文",
                active_session_block="【当前会话状态｜内部参考】\n当前状态",
                mid_term_block="【中期会话摘要】\n历史片段",
                long_term_block="经 Assembler 裁切后的长期事实",
                short_term_messages=(),
                selected_event_ids=("event-1",),
                selected_segment_ids=("segment-1",),
                trace={},
                cross_channel_recent_block="【另一通道近史｜内部参考｜时间邻近】\n跨通道摘要",
            )

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = []
    brain.session_short_term_memory = {}
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain.context_assembler = FakeAssembler()
    brain._retrieve_knowledge = lambda *args, **kwargs: []
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        SimpleNamespace(
            data={"active_id": "test"},
            get_active_character=lambda: {"prompt": "角色设定"},
        ),
    )

    messages = brain.build_prompt(
        "我们刚才决定了什么",
        "系统设定",
        conversation_scope=SimpleNamespace(),
    )
    system = messages[0]["content"]

    recent_at = system.index("【最近发生的事｜内部参考】")
    cross_at = system.index("【另一通道近史｜内部参考｜时间邻近】")
    active_at = system.index("【当前会话状态｜内部参考】")
    mid_at = system.index("【中期会话摘要】")
    long_at = system.index("【经筛选的长期记忆】")
    assert recent_at < cross_at < active_at < mid_at < long_at
    assert "经 Assembler 裁切后的长期事实" in system
    assert "\n长期事实" not in system


def test_advanced_memory_does_not_report_missing_when_long_term_was_deduplicated(
    monkeypatch,
):
    import modules.advanced_memory as advanced_memory

    class FakeCore:
        enabled = True

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="episode", memory_text="重复事实")

        def get_character_profile(self, *args, **kwargs):
            return MemoryProfile(person_id="character:test")

    class FakeAssembler:
        def assemble(self, **kwargs):
            return AssembledContext(
                recent_event_block="",
                active_session_block="【当前会话状态｜内部参考】\n重复事实",
                mid_term_block="",
                long_term_block="",
                short_term_messages=(),
                selected_event_ids=(),
                selected_segment_ids=("segment-1",),
                trace={},
            )

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = []
    brain.session_short_term_memory = {}
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain.context_assembler = FakeAssembler()
    brain._retrieve_knowledge = lambda *args, **kwargs: []
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        SimpleNamespace(
            data={"active_id": "test"},
            get_active_character=lambda: {"prompt": "角色设定"},
        ),
    )

    system = brain.build_prompt(
        "我们刚才决定了什么",
        "系统设定",
        conversation_scope=SimpleNamespace(),
    )[0]["content"]

    assert "重复事实" in system
    assert "当前没有找到与这个问题直接相关的可靠记录" not in system


def test_short_term_context_restores_group_from_shared_long_term_session(tmp_path):
    from modules.memory.short_term import ShortTermMemoryManager
    from modules.memory_sqlite import MemorySQLite

    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    store.add_transcript(
        "user",
        "第二个群重启前的内容",
        session_id="owner_shared",
        meta={"context_session_id": "group:200"},
    )
    store.add_transcript(
        "user",
        "第一个群重启前的内容",
        session_id="owner_shared",
        meta={"context_session_id": "group:100"},
    )

    manager = ShortTermMemoryManager(store, max_short_term=12)
    manager.restore_session("group:200")

    restored = manager.session_short_term_memory["group:200"]
    assert [item["content"] for item in restored] == ["第二个群重启前的内容"]


def test_short_term_from_events_projects_authoritative_dialog_window():
    import modules.advanced_memory as advanced_memory

    scope = SimpleNamespace(conversation_id="local:desktop")

    class EventStore:
        def __init__(self):
            self.calls = []

        def list_dialog_window(self, actual_scope, *, limit, max_age_sec=0):
            self.calls.append((actual_scope, limit, max_age_sec))
            return [
                {
                    "role": "user",
                    "content": "events 权威热窗",
                    "event_id": "event-1",
                }
            ]

    store = EventStore()
    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.short_term_from_events = True
    brain.context_assembler = SimpleNamespace(store=store)
    brain.max_short_term = 12
    brain.short_term_memory = [{"role": "user", "content": "旧缓存"}]
    brain.session_short_term_memory = {
        "local:desktop": [{"role": "user", "content": "旧会话缓存"}]
    }
    brain._restore_session_short_term_from_db = lambda *_args: None

    result = brain._get_short_term_context(
        session_id="local:desktop", conversation_scope=scope
    )

    assert result == [
        {
            "role": "user",
            "content": "events 权威热窗",
            "event_id": "event-1",
        }
    ]
    assert store.calls == [(scope, 12, 86400)]


def test_short_term_events_empty_does_not_fall_back_to_legacy_cache():
    import modules.advanced_memory as advanced_memory

    class EventStore:
        def list_dialog_window(self, _scope, *, limit, max_age_sec=0):
            assert limit == 12
            return []

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.short_term_from_events = True
    brain.context_assembler = SimpleNamespace(store=EventStore())
    brain.max_short_term = 12
    brain.short_term_memory = [{"role": "user", "content": "旧缓存"}]
    brain.session_short_term_memory = {}

    result = brain._get_short_term_context(
        conversation_scope=SimpleNamespace(conversation_id="local:desktop")
    )

    assert result == []


def test_short_term_projection_failure_falls_back_to_legacy_cache():
    """Projection exceptions must not wipe the dialog window; use RAM/transcript cache."""
    import modules.advanced_memory as advanced_memory

    class BrokenEventStore:
        def list_dialog_window(self, _scope, *, limit, max_age_sec=0):
            raise RuntimeError("sqlite locked")

    class _Logger:
        def __init__(self):
            self.warnings = []

        def warning(self, msg, *args, **kwargs):
            self.warnings.append(str(msg))

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.short_term_from_events = True
    brain.context_assembler = SimpleNamespace(store=BrokenEventStore())
    brain.max_short_term = 12
    brain._logger = _Logger()
    brain.short_term_memory = [{"role": "user", "content": "全局旧缓存"}]
    brain.session_short_term_memory = {
        "local:desktop": [
            {"role": "user", "content": "会话旧缓存", "event_id": "legacy-1"}
        ]
    }
    brain._restore_session_short_term_from_db = lambda *_args: None

    result = brain._get_short_term_context(
        session_id="local:desktop",
        conversation_scope=SimpleNamespace(conversation_id="local:desktop"),
    )

    assert result == [
        {"role": "user", "content": "会话旧缓存", "event_id": "legacy-1"}
    ]
    assert any("falling back" in w for w in brain._logger.warnings)


def test_advanced_memory_runtime_wires_shared_vector_index(tmp_path, monkeypatch):
    import modules.advanced_memory as advanced_memory
    from modules.memory_sqlite import MemorySQLite

    class Collection:
        def __init__(self, metadata=None):
            self.metadata = dict(metadata or {})

        def count(self):
            return 0

    class Client:
        def __init__(self):
            self.collections = {}

        def get_or_create_collection(
            self,
            name,
            embedding_function=None,
            metadata=None,
        ):
            del embedding_function
            collection = self.collections.get(name)
            if collection is None:
                collection = Collection(metadata)
                self.collections[name] = collection
            return collection

    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    client = Client()
    monkeypatch.setattr(advanced_memory, "get_memory_store", lambda: store)
    monkeypatch.setattr(advanced_memory.chromadb, "PersistentClient", lambda path: client)
    monkeypatch.setattr(
        advanced_memory,
        "load_runtime_settings_strict",
        lambda: {},
    )
    monkeypatch.setattr(
        advanced_memory,
        "EMBEDDING_CONFIG",
        {
            "enabled": False,
            "provider": "test",
            "api_url": "",
            "api_key": "",
            "model_name": "bge-m3",
            "timeout": 1,
            "expected_dimension": 1024,
        },
    )

    brain = advanced_memory.AdvancedMemorySystem()

    assert brain.memory_core.vector_search == brain._query_memory_vector
    assert brain.embedding_fn.service is brain.embedding_service
    assert brain.get_memory_vector_status()["model"] == "bge-m3"
    assert brain.get_memory_vector_status()["dimension"] == 1024


def test_advanced_memory_vector_error_short_circuits_until_connection_test():
    import modules.advanced_memory as advanced_memory
    from modules.embeddings import EmbeddingUnavailableError

    class Embedding:
        def status(self):
            return {"state": "error", "last_error": "embedding offline"}

    class Index:
        def query(self, *_args, **_kwargs):
            raise AssertionError("query must not run while the circuit is open")

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.embedding_service = Embedding()
    brain.memory_vector_index = Index()
    brain._vector_lock = threading.Lock()
    brain._schedule_memory_vector_sync = lambda limit=10: False

    with pytest.raises(EmbeddingUnavailableError, match="embedding offline"):
        brain._query_memory_vector("上次会议", person_id="owner", session_id="", limit=3)


def test_advanced_memory_does_not_schedule_incompatible_vector_index():
    import modules.advanced_memory as advanced_memory

    class Embedding:
        enabled = True

        def status(self):
            return {"state": "ready"}

    class Index:
        def status(self):
            return {"rebuild_required": True}

    class Executor:
        def submit(self, *_args, **_kwargs):
            raise AssertionError("incompatible index must not be processed")

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.embedding_service = Embedding()
    brain.memory_vector_index = Index()
    brain._vector_schedule_lock = threading.Lock()
    brain._vector_sync_future = None
    brain._vector_executor = Executor()

    assert brain._schedule_memory_vector_sync(limit=10) is False


def test_empty_knowledge_collection_adopts_missing_embedding_metadata():
    import modules.advanced_memory as advanced_memory

    class Collection:
        metadata = {}

        def count(self):
            return 0

        def modify(self, *, metadata):
            self.metadata = dict(metadata)

    collection = Collection()

    compatibility = (
        advanced_memory.AdvancedMemorySystem._knowledge_collection_compatibility(
            collection,
            model="bge-m3",
            dimension=1024,
        )
    )

    assert compatibility["rebuild_required"] is False
    assert collection.metadata["embedding_model"] == "bge-m3"
    assert collection.metadata["embedding_dimension"] == 1024


def test_nonempty_metadata_less_knowledge_collection_requires_rebuild():
    import modules.advanced_memory as advanced_memory

    class Collection:
        metadata = {}

        def count(self):
            return 3

    compatibility = (
        advanced_memory.AdvancedMemorySystem._knowledge_collection_compatibility(
            Collection(),
            model="bge-m3",
            dimension=1024,
        )
    )

    assert compatibility["rebuild_required"] is True


def test_knowledge_collection_rejects_changed_embedding_model():
    import modules.advanced_memory as advanced_memory

    class Collection:
        metadata = {
            "embedding_model": "bge-m3",
            "embedding_dimension": 1024,
        }

        def count(self):
            return 3

    compatibility = (
        advanced_memory.AdvancedMemorySystem._knowledge_collection_compatibility(
            Collection(),
            model="new-embedding",
            dimension=768,
        )
    )

    assert compatibility["rebuild_required"] is True
    assert compatibility["collection_model"] == "bge-m3"


def test_metadata_less_knowledge_collection_requires_rebuild_for_new_model():
    import modules.advanced_memory as advanced_memory

    class Collection:
        metadata = {}

        def count(self):
            return 3

    compatibility = (
        advanced_memory.AdvancedMemorySystem._knowledge_collection_compatibility(
            Collection(),
            model="new-embedding",
            dimension=768,
        )
    )

    assert compatibility["rebuild_required"] is True


def test_rebuild_knowledge_collection_reports_delete_failure():
    import modules.advanced_memory as advanced_memory

    class Client:
        def delete_collection(self, _name):
            raise RuntimeError("database locked")

        def get_or_create_collection(self, **_kwargs):
            return object()

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.chroma_client = Client()
    brain.embedding_fn = object()
    brain.knowledge_collection_metadata = {}

    assert brain.rebuild_knowledge_collection() is False


def test_memory_editor_reuses_injected_live_core(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_core import MemoryCoreService
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(store)
    core.initialize()
    created = {"count": 0}
    original_init = core.initialize

    def _count_init():
        created["count"] += 1
        return original_init()

    core.initialize = _count_init
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(
        embedded=True, memory_core=core
    )
    assert dialog.memory_core is core
    assert created["count"] == 0
    dialog.close()


def test_memory_editor_constructs_without_loading_chromadb(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)

    dialog = memory_editor.MemoryEditorDialog(embedded=True)

    tab_names = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
    assert any("档案概览" in name for name in tab_names)
    assert any("记忆记录" in name for name in tab_names)
    assert any("原始对话" in name for name in tab_names)
    assert any("向量与检索" in name for name in tab_names)
    assert not hasattr(dialog, "_mem_collection")
    dialog.close()


def test_memory_editor_profile_overview_groups_preferences(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(embedded=True)
    dialog.memory_core.upsert_memory_record(
        kind="preference",
        key="likes.music.0",
        content="MyGO",
        subject_id="owner",
        source_type="test",
        source_id="profile-music",
    )
    dialog.memory_core.upsert_memory_record(
        kind="preference",
        key="dislikes.food.0",
        content="不喜欢香菜",
        subject_id="owner",
        source_type="test",
        source_id="profile-dislike",
    )

    dialog._reload_memory_core_records()
    dialog._reload_profile_overview()

    top_labels = [
        dialog.profile_overview_tree.topLevelItem(index).text(0)
        for index in range(dialog.profile_overview_tree.topLevelItemCount())
    ]
    assert any("喜欢" in label for label in top_labels)
    assert any("不喜欢" in label for label in top_labels)
    tree_text = []
    iterator = QtWidgets.QTreeWidgetItemIterator(dialog.profile_overview_tree)
    while iterator.value() is not None:
        tree_text.append(iterator.value().text(0))
        iterator += 1
    assert any("音乐" in text for text in tree_text)
    assert "MyGO" in tree_text
    assert "不喜欢香菜" in tree_text
    dialog.close()


def test_memory_editor_shows_current_vector_runtime_status(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    class Brain:
        def get_memory_vector_status(self):
            return {
                "collection_count": 12,
                "jobs": {"pending": 2, "processing": 0, "indexed": 12, "failed": 1},
                "embedding": {
                    "enabled": True,
                    "available": False,
                    "model": "bge-m3",
                    "dimension": 1024,
                    "calls": 8,
                    "failures": 2,
                    "last_error": "embedding offline",
                },
            }

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(embedded=True, brain=Brain())

    dialog._refresh_vector_status()

    status_text = dialog.vector_status_label.text()
    assert "bge-m3" in status_text
    assert "1024" in status_text
    assert "索引 12" in status_text
    assert "待处理 2" in status_text
    assert "调用 8" in status_text
    assert "失败调用 2" in status_text
    assert "embedding offline" in status_text
    assert dialog._vector_initialized is False
    dialog.close()


def test_memory_editor_rebuilds_current_vector_index_after_confirmation(
    tmp_path,
    monkeypatch,
):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    class Brain:
        calls = 0

        def get_memory_vector_status(self):
            return {"collection_count": 0, "jobs": {}, "embedding": {}}

        def rebuild_memory_vector_index(self):
            self.calls += 1
            return {"queued": 3}

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    messages = []
    monkeypatch.setattr(
        memory_editor,
        "_msg",
        lambda _parent, title, text, _icon=QtWidgets.QMessageBox.Icon.Information: messages.append(
            (title, text)
        ),
    )
    brain = Brain()
    dialog = memory_editor.MemoryEditorDialog(embedded=True, brain=brain)

    dialog._rebuild_vector_index()

    assert brain.calls == 1
    assert any("3" in text for _title, text in messages)
    dialog.close()


def test_memory_editor_searches_current_vector_index(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    class Brain:
        query = None

        def get_memory_vector_status(self):
            return {"collection_count": 1, "jobs": {}, "embedding": {}}

        def query_memory_vector(self, text, *, person_id, limit):
            self.query = (text, person_id, limit)
            return [
                {
                    "id": "mr_meeting",
                    "document": "content: 发布会议持续四十分钟",
                    "vector_score": 0.91,
                    "metadata": {"subject_id": "owner"},
                }
            ]

        def test_embedding_connection(self):
            return {"state": "ready", "model": "bge-m3", "dimension": 1024}

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    brain = Brain()
    dialog = memory_editor.MemoryEditorDialog(embedded=True, brain=brain)
    dialog.current_vector_query.setText("上次会议多久")

    dialog._search_current_vector()

    assert brain.query == ("上次会议多久", "owner", 10)
    assert dialog.current_vector_results.count() == 1
    assert "0.910" in dialog.current_vector_results.item(0).text()
    assert "发布会议持续四十分钟" in dialog.current_vector_results.item(0).text()
    dialog.close()


def test_memory_editor_hides_diary_archive_transcripts(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    store.add_transcript("assistant", "【日记 2026-07-09】日记正文")
    store.add_transcript("user", "普通聊天消息")
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)

    dialog = memory_editor.MemoryEditorDialog(embedded=True)

    contents = [str(row.get("content") or "") for row in dialog._transcript_rows]
    assert "普通聊天消息" in contents
    assert all(not content.startswith("【日记 ") for content in contents)
    dialog.close()


def test_memory_editor_filters_records_with_semantic_tree(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(embedded=True)
    dialog.memory_core.upsert_memory_record(
        kind="preference",
        key="likes.music.0",
        content="MyGO",
        subject_id="owner",
        source_type="test",
        source_id="music",
    )
    dialog.memory_core.upsert_memory_record(
        kind="preference",
        key="likes.games.0",
        content="宝可梦",
        subject_id="owner",
        source_type="test",
        source_id="game",
    )
    dialog.memory_core.upsert_memory_record(
        kind="profile",
        key="status.0",
        content="正在工作",
        subject_id="owner",
        source_type="test",
        source_id="status",
    )

    dialog._reload_memory_core_records()
    dialog._select_memory_category("likes.music")

    assert dialog.memory_category_tree.topLevelItemCount() >= 8
    assert [row["content"] for row in dialog._memory_core_rows] == ["MyGO"]
    assert dialog.memory_category_count("likes") == 2
    assert dialog.memory_category_count("likes.music") == 1
    dialog.close()


def test_memory_editor_prioritizes_record_list_height(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    app = _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(embedded=True)
    dialog.resize(1100, 750)
    dialog.show()
    record_tab = next(
        index
        for index in range(dialog.tabs.count())
        if "记忆记录" in dialog.tabs.tabText(index)
    )
    dialog.tabs.setCurrentIndex(record_tab)
    app.processEvents()

    upper_height, lower_height = dialog.memory_content_splitter.sizes()
    assert upper_height >= lower_height * 1.75
    dialog.close()


def test_memory_editor_filters_records_with_person_dropdown(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(embedded=True)
    dialog.memory_core.upsert_memory_record(
        kind="fact",
        key="owner.fact",
        content="owner memory",
        subject_id="owner",
        source_type="test",
        source_id="owner",
    )
    dialog.memory_core.upsert_memory_record(
        kind="fact",
        key="qq.fact",
        content="qq memory",
        subject_id="qq:123",
        source_type="test",
        source_id="qq",
    )

    dialog._reload_memory_core_records()
    assert dialog.memory_core_person_filter.findData("") >= 0
    assert dialog.memory_core_person_filter.findData("owner") >= 0
    qq_index = dialog.memory_core_person_filter.findData("qq:123")
    assert qq_index >= 0

    dialog.memory_core_person_filter.setCurrentIndex(qq_index)
    assert [row["content"] for row in dialog._memory_core_rows] == ["qq memory"]
    dialog.close()


def test_memory_editor_lists_registered_people_and_zero_record_characters(
    tmp_path,
    monkeypatch,
):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    monkeypatch.setattr(
        memory_editor,
        "get_character_catalog",
        lambda: {"char_tomori": {"name": "高松灯", "aliases": []}},
        raising=False,
    )
    dialog = memory_editor.MemoryEditorDialog(embedded=True)
    dialog.memory_core.repository.upsert_person(
        person_id="qq:42",
        platform="qq",
        user_id="42",
        display_name="测试用户",
    )

    dialog._reload_memory_core_records()

    character_index = dialog.memory_core_person_filter.findData(
        "character:char_tomori"
    )
    qq_index = dialog.memory_core_person_filter.findData("qq:42")
    assert character_index >= 0
    assert "高松灯" in dialog.memory_core_person_filter.itemText(character_index)
    assert qq_index >= 0
    assert "测试用户" in dialog.memory_core_person_filter.itemText(qq_index)
    dialog.close()


def test_memory_editor_manual_category_override_moves_record(tmp_path, monkeypatch):
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)
    dialog = memory_editor.MemoryEditorDialog(embedded=True)
    record_id = dialog.memory_core.upsert_memory_record(
        kind="preference",
        key="likes.general.0",
        content="work-life balance",
        subject_id="owner",
        source_type="test",
        source_id="manual-category",
        metadata={"keep": "value"},
    )
    dialog._reload_memory_core_records()
    dialog._select_memory_category("likes.other")
    row_index = next(
        index
        for index, row in enumerate(dialog._memory_core_rows)
        if row["id"] == record_id
    )
    dialog.memory_core_table.setCurrentCell(row_index, 0)
    dialog.memory_category_combo.setCurrentIndex(
        dialog.memory_category_combo.findData("likes.art")
    )

    dialog._save_memory_core_record()

    row = dialog.memory_core.get_memory_record(record_id)
    assert classify_memory_record(row) == "likes.art"
    assert row["metadata"] == {"keep": "value", "category_override": "likes.art"}
    dialog._select_memory_category("likes.art")
    assert [item["id"] for item in dialog._memory_core_rows] == [record_id]

    dialog.memory_core_table.setCurrentCell(0, 0)
    dialog._reset_memory_category_override()
    row = dialog.memory_core.get_memory_record(record_id)
    assert classify_memory_record(row) == "likes.other"
    assert row["metadata"] == {"keep": "value"}
    dialog.close()
