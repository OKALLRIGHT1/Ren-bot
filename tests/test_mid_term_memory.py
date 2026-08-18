"""Mid-term segments + short-term eviction provenance tests (Task 8–9)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from modules.conversation_events.mid_term import (
    MidTermRecallService,
    MidTermSegmentBuilder,
    MidTermSegmentStore,
    build_stub_summary,
    validate_summary,
)
from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
    MidTermSegment,
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


def _save_segment(
    segment_store: MidTermSegmentStore,
    *,
    scope: ConversationScope,
    summary: str,
    start: datetime,
    source_id: str,
    segment_id: str,
    recall_cues=(),
    commitments=(),
):
    return segment_store.save(
        MidTermSegment(
            segment_id=segment_id,
            scope=scope,
            range_start=start,
            range_end=start + timedelta(seconds=1),
            recall_cues=tuple(recall_cues),
            assistant_commitments=tuple(commitments),
            source_event_ids=(source_id,),
            summary=summary,
            confidence=0.8,
            status="active",
        )
    )


class _KeywordEmbedding:
    def embed(self, documents):
        vectors = []
        for document in documents:
            text = str(document or "")
            if "登录" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif "晚饭" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class _CountingKeywordEmbedding(_KeywordEmbedding):
    def __init__(self):
        self.batches = []

    def embed(self, documents):
        items = list(documents)
        self.batches.append(items)
        return super().embed(items)


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


def test_list_dialog_window_drops_turns_older_than_max_age(event_env):
    _, store = event_env
    scope = _scope()
    now = datetime.now(timezone.utc)
    _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "在我眼里你就是最完美的哦",
        scope=scope,
        occurred_at=now - timedelta(days=11),
    )
    _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "上海最近会有什么台风吗",
        scope=scope,
        occurred_at=now,
    )

    turns = store.list_dialog_window(scope, now=now, limit=12, max_age_sec=86400)

    assert [item["content"] for item in turns] == ["上海最近会有什么台风吗"]


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


def test_mid_term_bucket_prefers_conversation_over_shared_memory_session():
    from modules.advanced_memory import AdvancedMemorySystem

    brain = AdvancedMemorySystem.__new__(AdvancedMemorySystem)
    key = AdvancedMemorySystem._bucket_key_for_mid_term(
        brain,
        session_id="private:1001",
        meta={
            "context_session_id": "private:1001",
            "session_id": "owner_shared",
        },
    )

    assert key == "private:1001"


def test_advanced_memory_mid_term_summary_uses_summary_llm_route(monkeypatch):
    import modules.advanced_memory as advanced_memory
    from modules.advanced_memory import AdvancedMemorySystem

    calls = []

    def fake_chat(messages, *, task_type, caller):
        calls.append((messages, task_type, caller))
        return '{"source_event_ids":["e1"],"summary":"用户在改登录页","confidence":0.8}'

    monkeypatch.setattr(advanced_memory, "chat_with_ai", fake_chat)
    event = ConversationEvent(
        event_id="e1",
        scope=_scope(),
        event_type=ConversationEventType.USER_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        exact_text="我在改登录页",
        evidence_summary="",
    )
    brain = AdvancedMemorySystem.__new__(AdvancedMemorySystem)

    raw = AdvancedMemorySystem._summarize_mid_term_events(brain, [event])

    assert "用户在改登录页" in raw
    assert calls[0][1:] == ("summary", "mid_term_segment")
    assert "e1" in calls[0][0][0]["content"]


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


def test_validate_summary_rejects_hallucinated_entity_without_numbers(event_env):
    _, store = event_env
    user = _append_event(
        store, ConversationEventType.USER_MESSAGE, "我在修改登录页"
    )
    events = [store.get(user.event_id)]
    payload = {
        "source_event_ids": [user.event_id],
        "summary": "用户在修改登录页",
        "entities": ["火星基地"],
        "confidence": 0.8,
    }

    ok, err = validate_summary(payload, events)

    assert not ok
    assert "entity" in err


def test_validate_summary_only_uses_claimed_source_events(event_env):
    _, store = event_env
    login = _append_event(
        store, ConversationEventType.USER_MESSAGE, "我在修改登录页"
    )
    dinner = _append_event(
        store, ConversationEventType.USER_MESSAGE, "晚饭决定吃面"
    )
    events = [store.get(login.event_id), store.get(dinner.event_id)]
    payload = {
        "source_event_ids": [login.event_id],
        "summary": "用户在修改登录页，晚饭决定吃面",
        "entities": ["晚饭"],
        "confidence": 0.8,
    }

    ok, err = validate_summary(payload, events)

    assert not ok
    assert "entity" in err or "claimed" in err


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


def test_mid_term_recall_is_hard_scoped_by_conversation(event_env):
    sqlite, store = event_env
    desktop = _scope("desktop", "local:desktop")
    qq = _scope("qq", "qq:private:1001")
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    _save_segment(
        segment_store,
        scope=desktop,
        summary="桌面登录页使用蓝色按钮",
        start=now - timedelta(hours=2),
        source_id="desktop-source",
        segment_id="desktop-old",
        recall_cues=("登录页",),
    )
    _save_segment(
        segment_store,
        scope=qq,
        summary="QQ 登录讨论使用红色按钮",
        start=now - timedelta(hours=2),
        source_id="qq-source",
        segment_id="qq-old",
        recall_cues=("登录",),
    )
    latest = _save_segment(
        segment_store,
        scope=desktop,
        summary="刚才讨论晚饭吃面",
        start=now - timedelta(minutes=10),
        source_id="desktop-latest-source",
        segment_id="desktop-latest",
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=_KeywordEmbedding(),
        relevance_threshold=0.7,
    ).recall(current_text="登录页按钮是什么颜色", scope=desktop)

    assert result.active_segment_id == latest.segment_id
    assert result.recalled_segment_ids == ("desktop-old",)
    assert "蓝色按钮" in result.mid_term_block
    assert "红色按钮" not in result.mid_term_block


def test_latest_segment_is_resident_and_older_segment_is_semantic_only(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    old = _save_segment(
        segment_store,
        scope=scope,
        summary="登录页决定使用蓝色按钮",
        start=now - timedelta(hours=3),
        source_id="old-source",
        segment_id="old-login",
        recall_cues=("登录页",),
    )
    latest = _save_segment(
        segment_store,
        scope=scope,
        summary="晚饭决定吃面",
        start=now - timedelta(minutes=20),
        source_id="latest-source",
        segment_id="latest-dinner",
        commitments=("稍后提醒喝水",),
    )
    after = _append_event(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "我答应十分钟后提醒你喝水",
        scope=scope,
        occurred_at=now - timedelta(minutes=5),
    )

    service = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=_KeywordEmbedding(),
        relevance_threshold=0.7,
    )
    result = service.recall(
        current_text="登录页按钮定了什么",
        scope=scope,
        available_events=[after],
    )

    assert result.active_segment_id == latest.segment_id
    assert latest.summary in result.active_session_block
    assert "十分钟后提醒你喝水" in result.active_session_block
    assert result.recalled_segment_ids == (old.segment_id,)
    assert old.summary in result.mid_term_block

    unrelated = service.recall(current_text="晚饭吃什么", scope=scope)
    assert unrelated.active_segment_id == latest.segment_id
    assert unrelated.recalled_segment_ids == ()
    assert old.summary not in unrelated.mid_term_block


def test_embedding_unavailable_keeps_active_state_but_skips_history(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    _save_segment(
        segment_store,
        scope=scope,
        summary="登录页历史决定",
        start=now - timedelta(hours=2),
        source_id="old-source",
        segment_id="old",
    )
    _save_segment(
        segment_store,
        scope=scope,
        summary="晚饭当前状态",
        start=now - timedelta(minutes=5),
        source_id="latest-source",
        segment_id="latest",
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=None,
    ).recall(current_text="登录页决定", scope=scope)

    assert "晚饭当前状态" in result.active_session_block
    assert result.mid_term_block == ""
    assert result.recalled_segment_ids == ()
    assert result.error == "embedding_unavailable"


def test_recall_does_not_repeat_segments_already_present_as_raw_events(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    old = _save_segment(
        segment_store,
        scope=scope,
        summary="登录页决定使用蓝色按钮",
        start=now - timedelta(hours=2),
        source_id="raw-old",
        segment_id="old-login",
        recall_cues=("登录页",),
    )
    latest = _save_segment(
        segment_store,
        scope=scope,
        summary="晚饭决定吃面",
        start=now - timedelta(minutes=5),
        source_id="raw-latest",
        segment_id="latest-dinner",
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=_KeywordEmbedding(),
        relevance_threshold=0.7,
    ).recall(
        current_text="登录页按钮是什么颜色",
        scope=scope,
        excluded_event_ids={"raw-old", "raw-latest"},
    )

    assert old.segment_id not in result.recalled_segment_ids
    assert old.summary not in result.mid_term_block
    assert latest.summary not in result.active_session_block


def test_excluding_latest_segment_does_not_promote_older_segment_to_active(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    old = _save_segment(
        segment_store,
        scope=scope,
        summary="登录页旧决定",
        start=now - timedelta(hours=2),
        source_id="old-source",
        segment_id="old",
    )
    latest = _save_segment(
        segment_store,
        scope=scope,
        summary="晚饭当前状态",
        start=now - timedelta(minutes=5),
        source_id="latest-source",
        segment_id="latest",
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=None,
    ).recall(
        current_text="继续",
        scope=scope,
        excluded_event_ids={"latest-source"},
    )

    assert result.active_segment_id == latest.segment_id
    assert result.active_session_block == ""
    assert old.summary not in result.active_session_block


def test_active_state_does_not_repeat_hot_raw_events(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    latest = _save_segment(
        segment_store,
        scope=scope,
        summary="当前在做登录页",
        start=now - timedelta(minutes=10),
        source_id="segment-source",
        segment_id="latest",
    )
    hot = _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "这句已经在短期原文里",
        scope=scope,
        occurred_at=latest.range_end + timedelta(seconds=1),
        eid="hot-event",
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=None,
    ).recall(
        current_text="继续",
        scope=scope,
        available_events=[hot],
        excluded_event_ids={hot.event_id},
    )

    assert "当前在做登录页" in result.active_session_block
    assert "这句已经在短期原文里" not in result.active_session_block


def test_active_state_keeps_post_segment_events_under_budget(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    latest = _save_segment(
        segment_store,
        scope=scope,
        summary="旧摘要" * 500,
        start=now - timedelta(minutes=10),
        source_id="segment-source",
        segment_id="latest",
    )
    newest = _append_event(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "最新承诺：十分钟后提醒喝水",
        scope=scope,
        occurred_at=latest.range_end + timedelta(seconds=1),
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=None,
        active_max_chars=300,
    ).recall(
        current_text="继续",
        scope=scope,
        available_events=[newest],
    )

    assert len(result.active_session_block) <= 300
    assert "最新承诺：十分钟后提醒喝水" in result.active_session_block


def test_active_state_budget_keeps_newest_post_segment_event(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    latest = _save_segment(
        segment_store,
        scope=scope,
        summary="当前摘要",
        start=now - timedelta(minutes=10),
        source_id="segment-source",
        segment_id="latest",
    )
    older = _append_event(
        store,
        ConversationEventType.USER_MESSAGE,
        "较早原文" * 80,
        scope=scope,
        occurred_at=latest.range_end + timedelta(seconds=1),
    )
    newest = _append_event(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "真正最新的承诺",
        scope=scope,
        occurred_at=latest.range_end + timedelta(seconds=2),
    )

    result = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=None,
        active_max_chars=240,
    ).recall(
        current_text="继续",
        scope=scope,
        available_events=[older, newest],
    )

    assert len(result.active_session_block) <= 240
    assert "真正最新的承诺" in result.active_session_block


def test_mid_term_recall_caches_immutable_segment_embeddings(event_env):
    sqlite, store = event_env
    scope = _scope()
    segment_store = MidTermSegmentStore(sqlite)
    now = datetime.now(timezone.utc)
    _save_segment(
        segment_store,
        scope=scope,
        summary="登录页决定使用蓝色按钮",
        start=now - timedelta(hours=2),
        source_id="old-source",
        segment_id="old-login",
        recall_cues=("登录页",),
    )
    _save_segment(
        segment_store,
        scope=scope,
        summary="晚饭决定吃面",
        start=now - timedelta(minutes=5),
        source_id="latest-source",
        segment_id="latest-dinner",
    )
    embedding = _CountingKeywordEmbedding()
    service = MidTermRecallService(
        segment_store=segment_store,
        event_store=store,
        embedding_service=embedding,
        relevance_threshold=0.7,
    )

    first = service.recall(current_text="登录页按钮", scope=scope)
    second = service.recall(current_text="登录页颜色", scope=scope)

    assert first.recalled_segment_ids == ("old-login",)
    assert second.recalled_segment_ids == ("old-login",)
    assert [len(batch) for batch in embedding.batches] == [2, 1]
