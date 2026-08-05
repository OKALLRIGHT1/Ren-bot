"""Mid-term segments + short-term eviction provenance tests (Task 8–9)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from modules.conversation_events.mid_term import (
    MidTermSegmentBuilder,
    MidTermSegmentStore,
    build_stub_summary,
    validate_summary,
)
from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)
from modules.conversation_events.store import ConversationEventStore
from modules.memory.short_term import ShortTermMemoryManager
from modules.memory_sqlite import MemorySQLite


def _scope(channel="desktop", cid="local:desktop"):
    return ConversationScope("suzu", "owner", channel, cid)


def _append_event(
    store: ConversationEventStore,
    etype: ConversationEventType,
    text: str,
    *,
    scope: ConversationScope | None = None,
    parents=(),
    occurred_at=None,
    metadata=None,
    eid="",
):
    scope = scope or _scope()
    event = ConversationEvent(
        event_id=eid or "",
        scope=scope,
        event_type=etype,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        exact_text=text,
        evidence_summary=text,
        causal_parent_ids=tuple(parents),
        status="active",
        metadata=dict(metadata or {}),
    )
    return store.append(event)


@pytest.fixture
def event_env(tmp_path: Path):
    sqlite = MemorySQLite(str(tmp_path / "mid.sqlite"))
    store = ConversationEventStore(sqlite)
    return sqlite, store


def test_append_returns_evicted_message_for_session():
    manager = ShortTermMemoryManager(None, max_short_term=2)
    assert manager.append("user", "one", session_id="local:desktop") is None
    manager.append("assistant", "two", session_id="local:desktop")
    evicted = manager.append("user", "three", session_id="local:desktop")
    assert evicted == {"role": "user", "content": "one"}


def test_append_evicted_item_preserves_event_id():
    manager = ShortTermMemoryManager(None, max_short_term=1)
    assert manager.append("user", "a", event_id="e1") is None
    evicted = manager.append("user", "b", event_id="e2")
    assert evicted is not None
    assert evicted["content"] == "a"
    assert evicted["event_id"] == "e1"


def test_list_dialog_window_projects_user_assistant_with_event_ids(event_env):
    _, store = event_env
    scope = _scope()
    u = _append_event(
        store, ConversationEventType.USER_MESSAGE, "我在改登录页", scope=scope
    )
    a = _append_event(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "听起来快收尾了",
        scope=scope,
        parents=(u.event_id,),
    )
    _append_event(
        store,
        ConversationEventType.SCREEN_OBSERVATION,
        "屏幕在刷短视频",
        scope=scope,
    )
    turns = store.list_dialog_window(scope, limit=12)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "我在改登录页"
    assert turns[0]["event_id"] == u.event_id
    assert turns[1]["event_id"] == a.event_id


def test_pending_mid_term_bucket_collects_event_ids_on_evict():
    from modules.advanced_memory import AdvancedMemorySystem
    import threading

    brain = AdvancedMemorySystem.__new__(AdvancedMemorySystem)
    brain.mid_term_enabled = True
    # Floor is max(4, source_items); set 4 so the third+1 eviction yields a batch.
    brain.mid_term_segment_source_items = 4
    brain._pending_mid_term_event_ids = {}
    brain._pending_mid_term_lock = threading.Lock()

    for i in range(1, 4):
        assert (
            AdvancedMemorySystem.note_evicted_for_mid_term(
                brain,
                {"role": "user", "content": str(i), "event_id": f"e{i}"},
                session_id="c1",
            )
            is None
        )
    ready = AdvancedMemorySystem.note_evicted_for_mid_term(
        brain, {"role": "user", "content": "4", "event_id": "e4"}, session_id="c1"
    )
    assert ready == ["e1", "e2", "e3", "e4"]


def test_stub_summary_keeps_user_assistant_and_tool_failure(event_env):
    _, store = event_env
    scope = _scope()
    t0 = datetime.now(timezone.utc)
    u = _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "我在做项目重构",
        scope=scope,
        occurred_at=t0,
    )
    a = _append_event(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "好，稍后提醒你提交 PR",
        scope=scope,
        parents=(u.event_id,),
        occurred_at=t0 + timedelta(seconds=1),
    )
    tool = _append_event(
        store,
        ConversationEventType.TOOL_RESULT,
        "部署失败：权限不足",
        scope=scope,
        parents=(a.event_id,),
        occurred_at=t0 + timedelta(seconds=2),
        metadata={"success": False, "tool_name": "deploy"},
    )
    events = [store.get(u.event_id), store.get(a.event_id), store.get(tool.event_id)]
    stub = build_stub_summary(events)
    ok, err = validate_summary(stub, events)
    assert ok, err
    assert any("提醒" in c or "PR" in c for c in stub["assistant_commitments"])
    assert stub["unresolved_threads"], "tool failure must surface as unresolved"
    assert u.event_id in stub["source_event_ids"]
    assert a.event_id in stub["source_event_ids"]
    assert "我在做项目重构" in stub["summary"]
    assert "稍后提醒" in stub["summary"]


def test_validate_summary_rejects_hallucinated_date(event_env):
    _, store = event_env
    u = _append_event(
        store, ConversationEventType.USER_MESSAGE, "明天再继续改登录"
    )
    events = [store.get(u.event_id)]
    payload = {
        "source_event_ids": [u.event_id],
        "summary": "用户说 2024-01-15 要上线",
        "topics": [],
        "assistant_commitments": [],
        "unresolved_threads": [],
        "entities": ["2024-01-15"],
        "confidence": 0.9,
    }
    ok, err = validate_summary(payload, events)
    assert not ok
    assert "precise token" in err or "2024" in err


def test_validate_summary_rejects_unknown_source_ids(event_env):
    _, store = event_env
    u = _append_event(store, ConversationEventType.USER_MESSAGE, "你好")
    events = [store.get(u.event_id)]
    payload = {
        "source_event_ids": [u.event_id, "not-real"],
        "summary": "你好",
        "confidence": 0.5,
    }
    ok, err = validate_summary(payload, events)
    assert not ok
    assert "subset" in err


def test_builder_does_not_persist_invalid_llm_json(event_env):
    sqlite, store = event_env
    scope = _scope()
    u = _append_event(
        store, ConversationEventType.USER_MESSAGE, "继续昨天的登录页", scope=scope
    )
    a = _append_event(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "好的",
        scope=scope,
        parents=(u.event_id,),
    )

    def bad_llm(_events):
        return "not-json{{"

    builder = MidTermSegmentBuilder(
        store=store, sqlite_store=sqlite, llm_callable=bad_llm
    )
    # allow stub: should still save stub, not hallucinated high-confidence segment
    seg = builder.build_from_event_ids([u.event_id, a.event_id], allow_stub_on_failure=True)
    assert seg is not None
    assert seg.status == "stub"
    assert seg.confidence <= 0.5
    assert u.event_id in seg.source_event_ids

    # without stub: failed, nothing high-trust
    builder2 = MidTermSegmentBuilder(
        store=store, sqlite_store=sqlite, llm_callable=bad_llm
    )
    seg2 = builder2.build_from_event_ids(
        [u.event_id, a.event_id], allow_stub_on_failure=False
    )
    assert seg2 is None
    assert builder2.last_status == "failed"


def test_builder_rejects_llm_hallucination_and_falls_back_to_stub(event_env):
    sqlite, store = event_env
    scope = _scope()
    u = _append_event(
        store, ConversationEventType.USER_MESSAGE, "晚饭吃面", scope=scope
    )

    def lying_llm(events):
        return {
            "source_event_ids": [events[0].event_id],
            "summary": "用户说 2099-12-31 要飞月球",
            "topics": ["月球"],
            "assistant_commitments": [],
            "unresolved_threads": [],
            "entities": ["2099-12-31"],
            "confidence": 0.95,
            "status": "active",
        }

    builder = MidTermSegmentBuilder(
        store=store, sqlite_store=sqlite, llm_callable=lying_llm
    )
    seg = builder.build_from_event_ids([u.event_id], allow_stub_on_failure=True)
    assert seg is not None
    assert seg.status == "stub"
    assert "2099" not in seg.summary
    assert "晚饭吃面" in seg.summary


def test_mid_term_segments_hard_isolation_by_conversation(event_env):
    sqlite, store = event_env
    desktop = _scope("desktop", "local:desktop")
    qq = _scope("qq", "qq:private:1001")
    d_u = _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "桌面在改代码",
        scope=desktop,
    )
    q_u = _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "QQ 在聊游戏",
        scope=qq,
    )
    builder = MidTermSegmentBuilder(store=store, sqlite_store=sqlite, llm_callable=None)
    d_seg = builder.build_from_event_ids([d_u.event_id])
    q_seg = builder.build_from_event_ids([q_u.event_id])
    assert d_seg and q_seg

    seg_store = MidTermSegmentStore(sqlite)
    desktop_hits = seg_store.list_for_scope(desktop, limit=5)
    qq_hits = seg_store.list_for_scope(qq, limit=5)
    assert all(s.scope.conversation_id == "local:desktop" for s in desktop_hits)
    assert all(s.scope.conversation_id == "qq:private:1001" for s in qq_hits)
    assert not any("游戏" in (s.summary or "") for s in desktop_hits)
    assert not any("改代码" in (s.summary or "") for s in qq_hits)


def test_validate_summary_rejects_marking_success_tool_as_failed(event_env):
    _, store = event_env
    tool = _append_event(
        store,
        ConversationEventType.TOOL_RESULT,
        "搜索成功：找到 3 条结果",
        metadata={"success": True},
    )
    events = [store.get(tool.event_id)]
    payload = {
        "source_event_ids": [tool.event_id],
        "summary": "工具调用",
        "unresolved_threads": ["搜索失败了"],
        "confidence": 0.8,
    }
    ok, err = validate_summary(payload, events)
    assert not ok
    assert "successful" in err or "failed" in err
