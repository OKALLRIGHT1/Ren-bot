from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)
from modules.conversation_events.prompt import (
    LEGACY_SENSOR_EVIDENCE_TITLE,
    LEGACY_SENSOR_ROAST_TITLE,
    RECENT_BLOCK_TITLE,
    detect_dual_inject,
)
from modules.conversation_events.store import ConversationEventStore
from modules.memory_sqlite import MemorySQLite
from services.chat_support.context_assembler import ContextAssembler


def _scope(cid="local:desktop"):
    return ConversationScope("suzu", "owner", "desktop", cid)


def _ev(store, etype, text, parents=(), eid=None):
    event = ConversationEvent(
        event_id=eid or "",
        scope=_scope(),
        event_type=etype,
        occurred_at=datetime.now(timezone.utc),
        exact_text=text,
        evidence_summary=text,
        causal_parent_ids=parents,
        status="active",
        metadata={},
    )
    return store.append(event)


@pytest.fixture
def assembler(tmp_path: Path):
    sqlite = MemorySQLite(str(tmp_path / "a.sqlite"))
    store = ConversationEventStore(sqlite)
    return ContextAssembler(store=store, max_events=3, max_chars=900), store


def test_assembler_followup_without_legacy_keywords(assembler):
    asm, store = assembler
    obs = _ev(
        store,
        ConversationEventType.SCREEN_OBSERVATION,
        "DeepSeek 页面里出现了原神",
    )
    utt = _ev(
        store,
        ConversationEventType.PROACTIVE_UTTERANCE,
        "原神又肝起来了？",
        parents=(obs.event_id,),
    )
    for user_text in ("你这结论哪来的", "怎么突然这么讲", "嗯？依据呢"):
        result = asm.assemble(
            current_user_text=user_text,
            scope=_scope(),
        )
        assert result.selected_event_ids
        assert utt.event_id in result.selected_event_ids or obs.event_id in result.selected_event_ids
        assert RECENT_BLOCK_TITLE in result.recent_event_block
        assert "原神" in result.recent_event_block
        assert result.trace.get("selected_event_ids")


def test_assembler_suppresses_irrelevant_screen(assembler):
    asm, store = assembler
    _ev(store, ConversationEventType.SCREEN_OBSERVATION, "用户正在看代码")
    result = asm.assemble(current_user_text="晚饭吃什么", scope=_scope())
    assert "代码" not in result.recent_event_block
    assert result.selected_event_ids == ()


def test_assembler_no_dual_inject_titles(assembler):
    asm, store = assembler
    obs = _ev(
        store,
        ConversationEventType.SCREEN_OBSERVATION,
        "DeepSeek 页面里出现了原神",
    )
    _ev(
        store,
        ConversationEventType.PROACTIVE_UTTERANCE,
        "原神又肝起来了？",
        parents=(obs.event_id,),
    )
    result = asm.assemble(current_user_text="看到了什么", scope=_scope())
    block = result.recent_event_block
    assert RECENT_BLOCK_TITLE in block
    assert LEGACY_SENSOR_EVIDENCE_TITLE not in block
    assert LEGACY_SENSOR_ROAST_TITLE not in block
    assert not detect_dual_inject(block)


def test_short_term_observation_dedup(assembler):
    asm, store = assembler
    roast = "原神又肝起来了？"
    obs = _ev(
        store,
        ConversationEventType.SCREEN_OBSERVATION,
        "DeepSeek 页面里出现了原神",
    )
    _ev(
        store,
        ConversationEventType.PROACTIVE_UTTERANCE,
        roast,
        parents=(obs.event_id,),
    )
    short = (
        {"role": "assistant", "content": f"[视觉观察] {roast}"},
        {"role": "user", "content": "你好"},
    )
    result = asm.assemble(
        current_user_text="你刚吐槽什么",
        scope=_scope(),
        short_term_messages=short,
    )
    # Dialog user turn kept; tagged observation duplicate dropped when selected.
    contents = [m.get("content") for m in result.short_term_messages]
    assert "你好" in contents


def test_short_term_dialog_is_not_rendered_again_in_recent_block(assembler):
    asm, store = assembler
    user_event = _ev(
        store,
        ConversationEventType.USER_MESSAGE,
        "我今天在改登录页",
    )
    _ev(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "听起来快收尾了",
        parents=(user_event.event_id,),
    )
    short = (
        {"role": "user", "content": "我今天在改登录页"},
        {"role": "assistant", "content": "听起来快收尾了"},
    )

    result = asm.assemble(
        current_user_text="你刚才说什么",
        scope=_scope(),
        short_term_messages=short,
    )

    assert result.recent_event_block == ""
    assert result.short_term_messages == short
