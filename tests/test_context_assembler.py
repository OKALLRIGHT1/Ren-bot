from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)
from modules.conversation_events.mid_term import MidTermRecallResult
from modules.conversation_events.prompt import (
    LEGACY_SENSOR_EVIDENCE_TITLE,
    LEGACY_SENSOR_ROAST_TITLE,
    RECENT_BLOCK_TITLE,
    detect_dual_inject,
)
from modules.conversation_events.store import ConversationEventStore
from modules.memory_sqlite import MemorySQLite
from services.chat_support.context_assembler import ContextAssembler


def _scope(cid="local:desktop", channel="desktop", person_id="owner"):
    return ConversationScope("suzu", person_id, channel, cid)


def _ev(store, etype, text, parents=(), eid=None, scope=None, at=None):
    event = ConversationEvent(
        event_id=eid or "",
        scope=scope or _scope(),
        event_type=etype,
        occurred_at=at or datetime.now(timezone.utc),
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


def test_assembler_recalls_mid_term_with_raw_event_dedup(assembler):
    _, store = assembler
    user_event = _ev(
        store,
        ConversationEventType.USER_MESSAGE,
        "我今天在改登录页",
    )

    class RecallService:
        def __init__(self):
            self.calls = []

        def recall(self, **kwargs):
            self.calls.append(dict(kwargs))
            return MidTermRecallResult(
                active_session_block="【当前会话状态｜内部参考】\n晚饭决定吃面",
                mid_term_block="【中期会话摘要】\n登录页决定使用蓝色按钮",
                active_segment_id="active-segment",
                recalled_segment_ids=("history-segment",),
            )

    recall = RecallService()
    asm = ContextAssembler(
        store=store,
        max_events=3,
        max_chars=900,
        mid_term_enabled=True,
        mid_term_recall_service=recall,
    )
    short = (
        {
            "role": "user",
            "content": user_event.exact_text,
            "event_id": user_event.event_id,
        },
    )

    result = asm.assemble(
        current_user_text="登录页按钮是什么颜色",
        scope=_scope(),
        short_term_messages=short,
    )

    assert result.active_session_block.startswith("【当前会话状态")
    assert "蓝色按钮" in result.mid_term_block
    assert result.selected_segment_ids == (
        "active-segment",
        "history-segment",
    )
    assert recall.calls[0]["excluded_event_ids"] == {user_event.event_id}
    assert result.trace["selected_segment_ids"] == [
        "active-segment",
        "history-segment",
    ]


def test_mid_term_failure_does_not_drop_recent_context(assembler):
    _, store = assembler
    obs = _ev(
        store,
        ConversationEventType.SCREEN_OBSERVATION,
        "屏幕上出现原神",
    )
    _ev(
        store,
        ConversationEventType.PROACTIVE_UTTERANCE,
        "又在看原神？",
        parents=(obs.event_id,),
    )

    class BrokenRecall:
        def recall(self, **kwargs):
            raise RuntimeError("mid-term database unavailable")

    asm = ContextAssembler(
        store=store,
        mid_term_enabled=True,
        mid_term_recall_service=BrokenRecall(),
    )

    result = asm.assemble(current_user_text="你从哪看到的", scope=_scope())

    assert "原神" in result.recent_event_block
    assert result.active_session_block == ""
    assert result.mid_term_block == ""
    assert result.trace["mid_term_error"] == "recall_exception:RuntimeError"
    assert "mid-term database unavailable" not in str(result.trace)


def test_assembler_enforces_final_layer_budgets_without_partial_units(assembler):
    _, store = assembler
    obs = _ev(
        store,
        ConversationEventType.SCREEN_OBSERVATION,
        "登录页按钮是蓝色",
    )
    _ev(
        store,
        ConversationEventType.PROACTIVE_UTTERANCE,
        "蓝色按钮挺显眼的",
        parents=(obs.event_id,),
    )

    class OversizedRecall:
        def recall(self, **kwargs):
            return MidTermRecallResult(
                active_session_block=(
                    "【当前会话状态｜内部参考】\n"
                    "原始事件优先。\n"
                    + "- ACTIVE_OLDER_" + "甲" * 180 + "\n"
                    + "- ACTIVE_NEWEST 完整最新事件"
                ),
                mid_term_block=(
                    "【中期会话摘要】\n"
                    "（压缩承托）\n"
                    "- 段 first\nMID_FIRST 完整片段\n"
                    "- 段 second\n" + "MID_SECOND_" + "乙" * 180
                ),
                active_segment_id="active-segment",
                recalled_segment_ids=("first", "second"),
            )

    asm = ContextAssembler(
        store=store,
        max_events=3,
        max_chars=260,
        active_max_chars=120,
        mid_term_max_chars=100,
        long_term_max_chars=45,
        mid_term_enabled=True,
        mid_term_recall_service=OversizedRecall(),
    )
    result = asm.assemble(
        current_user_text="你刚才为什么这么说",
        scope=_scope(),
        long_term_block="LONG_FIRST 完整记忆\nLONG_SECOND_" + "丙" * 80,
    )

    assert len(result.recent_event_block) <= 260
    assert len(result.active_session_block) <= 120
    assert len(result.mid_term_block) <= 100
    assert len(result.long_term_block) <= 45
    assert "ACTIVE_NEWEST 完整最新事件" in result.active_session_block
    assert "ACTIVE_OLDER_" not in result.active_session_block
    assert "MID_FIRST 完整片段" in result.mid_term_block
    assert "MID_SECOND_" not in result.mid_term_block
    assert "LONG_FIRST 完整记忆" in result.long_term_block
    assert "LONG_SECOND_" not in result.long_term_block


def test_active_budget_prioritizes_newest_raw_event_over_summary(assembler):
    _, store = assembler

    class SummaryHeavyRecall:
        def recall(self, **kwargs):
            return MidTermRecallResult(
                active_session_block=(
                    "【当前会话状态｜内部参考】\n"
                    "以下是压缩状态及段后原始事件。\n"
                    + "很长的旧摘要" * 45
                    + "\n- [assistant_message] 最新承诺：十分钟后提醒喝水"
                ),
                active_segment_id="active-segment",
            )

    result = ContextAssembler(
        store=store,
        active_max_chars=140,
        mid_term_enabled=True,
        mid_term_recall_service=SummaryHeavyRecall(),
    ).assemble(current_user_text="继续", scope=_scope())

    assert len(result.active_session_block) <= 140
    assert "最新承诺：十分钟后提醒喝水" in result.active_session_block
    assert "很长的旧摘要" not in result.active_session_block


def test_assembler_deduplicates_lower_layers_before_budgeting(assembler):
    _, store = assembler

    class DuplicateRecall:
        def recall(self, **kwargs):
            return MidTermRecallResult(
                active_session_block=(
                    "【当前会话状态｜内部参考】\n共享事实：按钮是蓝色\n当前未决：检查对比度"
                ),
                mid_term_block=(
                    "【中期会话摘要】\n共享事实：按钮是蓝色\n历史决定：保留圆角"
                ),
                active_segment_id="active-segment",
                recalled_segment_ids=("history-segment",),
            )

    asm = ContextAssembler(
        store=store,
        mid_term_enabled=True,
        mid_term_recall_service=DuplicateRecall(),
    )
    result = asm.assemble(
        current_user_text="继续",
        scope=_scope(),
        long_term_block="共享事实：按钮是蓝色\n长期偏好：喜欢简洁界面",
    )
    combined = "\n".join(
        (
            result.active_session_block,
            result.mid_term_block,
            result.long_term_block,
        )
    )

    assert combined.count("共享事实：按钮是蓝色") == 1
    assert result.trace["deduplicated_items"] == [
        "mid_term:line:2",
        "long_term:line:1",
    ]


def test_assembler_trace_has_bounded_privacy_safe_contract(assembler):
    asm, store = assembler
    private_text = "这是不能复制进 trace 的 QQ 私聊正文"
    qq_scope = _scope("qq:private:1001", channel="qq")
    event = _ev(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        private_text,
        scope=qq_scope,
    )

    result = asm.assemble(
        current_user_text="你刚才说什么",
        scope=qq_scope,
        candidates=[event],
    )
    trace = result.trace

    assert trace["source"] == "events"
    assert trace["conversation_id"] == "qq:private:1001"
    assert trace["candidate_event_ids"] == [event.event_id]
    assert trace["selected_event_ids"] == [event.event_id]
    assert trace["selection_reasons"][event.event_id]
    assert trace["selected_segment_ids"] == []
    assert trace["layer_chars"] == {
        "recent": len(result.recent_event_block),
        "active": len(result.active_session_block),
        "mid_term": len(result.mid_term_block),
        "long_term": len(result.long_term_block),
        "cross_channel": len(result.cross_channel_recent_block),
    }
    assert trace["planner_triggered"] is False
    assert private_text not in str(trace)


def test_assembler_filters_explicit_candidates_by_scope(assembler):
    asm, store = assembler
    desktop_scope = _scope("local:desktop")
    qq_scope = _scope("qq:private:1001", channel="qq")
    desktop_event = _ev(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "桌面端刚才说的话",
        scope=desktop_scope,
    )
    qq_event = _ev(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "QQ 私聊里不能泄漏的话",
        scope=qq_scope,
    )

    result = asm.assemble(
        current_user_text="你刚才说什么",
        scope=desktop_scope,
        candidates=[desktop_event, qq_event],
    )

    assert desktop_event.event_id in result.selected_event_ids
    assert qq_event.event_id not in result.trace["candidate_event_ids"]
    # Current-session recent stays hard-isolated.
    assert "QQ 私聊里不能泄漏的话" not in result.recent_event_block
    # Owner soft bridge may still surface the opposite channel separately.
    assert "QQ 私聊里不能泄漏的话" in result.cross_channel_recent_block


def test_owner_cross_channel_recent_auto_injects_opposite_side(assembler):
    """Desktop owner prompt can soft-see recent QQ private dialog by time."""
    from modules.conversation_events.prompt import CROSS_CHANNEL_BLOCK_TITLE

    asm, store = assembler
    desktop = _scope("local:desktop", channel="desktop")
    qq = _scope("qq:private:owner", channel="qq")
    _ev(
        store,
        ConversationEventType.USER_MESSAGE,
        "QQ上说晚饭吃面",
        scope=qq,
    )
    _ev(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "桌面这边在改登录",
        scope=desktop,
    )

    result = asm.assemble(
        current_user_text="那晚饭呢",
        scope=desktop,
    )
    assert CROSS_CHANNEL_BLOCK_TITLE in result.cross_channel_recent_block
    assert "晚饭吃面" in result.cross_channel_recent_block
    assert "改登录" not in result.cross_channel_recent_block
    assert result.trace.get("cross_channel_injected") is True


def test_owner_cross_channel_skips_group_and_non_owner(assembler):
    asm, store = assembler
    desktop = _scope("local:desktop", channel="desktop")
    _ev(
        store,
        ConversationEventType.USER_MESSAGE,
        "本地秘密话题",
        scope=desktop,
    )

    group_scope = _scope("group:9", channel="qq", person_id="owner")
    group_result = asm.assemble(
        current_user_text="本地刚才聊了啥",
        scope=group_scope,
    )
    assert group_result.cross_channel_recent_block == ""
    assert not group_result.trace.get("cross_channel_injected")

    other = _scope("qq:private:2", channel="qq", person_id="qq:2")
    other_result = asm.assemble(
        current_user_text="本地刚才聊了啥",
        scope=other,
    )
    assert other_result.cross_channel_recent_block == ""


def test_owner_cross_channel_respects_max_age(assembler):
    from datetime import timedelta

    asm, store = assembler
    asm.owner_cross_channel_max_age_sec = 600
    desktop = _scope("local:desktop", channel="desktop")
    qq = _scope("qq:private:owner", channel="qq")
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    _ev(
        store,
        ConversationEventType.USER_MESSAGE,
        "很久以前的QQ话题",
        scope=qq,
        at=old,
    )
    result = asm.assemble(current_user_text="继续说", scope=desktop)
    assert result.cross_channel_recent_block == ""


def test_assembler_rejects_explicit_candidates_without_scope(assembler):
    asm, store = assembler
    event = _ev(
        store,
        ConversationEventType.ASSISTANT_MESSAGE,
        "不能在缺少 scope 时进入上下文",
    )

    result = asm.assemble(
        current_user_text="你刚才说什么",
        candidates=[event],
    )

    assert result.selected_event_ids == ()
    assert result.recent_event_block == ""
    assert result.trace["reason"] == "missing_scope"
    assert result.trace["candidate_event_ids"] == []
