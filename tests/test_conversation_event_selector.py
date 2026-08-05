from __future__ import annotations

from datetime import datetime, timedelta, timezone

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
    EventBudget,
)
from modules.conversation_events.prompt import (
    RECENT_BLOCK_TITLE,
    detect_dual_inject,
    format_recent_event_block,
)
from modules.conversation_events.selector import RecentEventSelector


def _scope(cid: str = "local:desktop") -> ConversationScope:
    return ConversationScope("suzu", "owner", "desktop", cid)


def _event(
    eid: str,
    etype: ConversationEventType,
    text: str,
    *,
    parents=(),
    minutes_ago: float = 0,
    status: str = "active",
    expires_in=None,
    metadata=None,
    evidence: str = "",
):
    now = datetime.now(timezone.utc)
    return ConversationEvent(
        event_id=eid,
        scope=_scope(),
        event_type=etype,
        occurred_at=now - timedelta(minutes=minutes_ago),
        exact_text=text,
        evidence_summary=evidence or text,
        causal_parent_ids=tuple(parents),
        expires_at=(now + timedelta(seconds=expires_in)) if expires_in is not None else None,
        status=status,
        metadata=dict(metadata or {}),
    )


def test_hard_filter_drops_expired_system_inactive_empty():
    selector = RecentEventSelector()
    now = datetime.now(timezone.utc)
    candidates = [
        _event("ok", ConversationEventType.PROACTIVE_UTTERANCE, "原神又肝起来了？"),
        _event(
            "exp",
            ConversationEventType.SCREEN_OBSERVATION,
            "expired",
            expires_in=-10,
        ),
        _event(
            "sys",
            ConversationEventType.SYSTEM_NOTICE,
            "debug",
            metadata={"visibility": "system"},
        ),
        _event("dead", ConversationEventType.USER_MESSAGE, "x", status="archived"),
        _event("empty", ConversationEventType.USER_MESSAGE, "   ", evidence="  "),
    ]
    # Fix expired event expires_at in the past
    candidates[1] = ConversationEvent(
        event_id="exp",
        scope=_scope(),
        event_type=ConversationEventType.SCREEN_OBSERVATION,
        occurred_at=now - timedelta(minutes=5),
        exact_text="expired",
        evidence_summary="expired",
        expires_at=now - timedelta(seconds=1),
        status="active",
        metadata={},
    )
    result = selector.select("你刚说啥", candidates, now=now)
    assert "ok" in result.event_ids
    assert "exp" not in result.event_ids
    assert "sys" not in result.event_ids
    assert "dead" not in result.event_ids
    assert "empty" not in result.event_ids


def test_direct_followup_selects_utterance_and_its_parent():
    selector = RecentEventSelector()
    observation = _event(
        "obs",
        ConversationEventType.SCREEN_OBSERVATION,
        "DeepSeek 页面里出现了原神",
        minutes_ago=2,
    )
    utterance = _event(
        "utt",
        ConversationEventType.PROACTIVE_UTTERANCE,
        "原神又肝起来了？",
        parents=("obs",),
        minutes_ago=1,
    )
    unrelated = _event(
        "other",
        ConversationEventType.USER_MESSAGE,
        "今天天气不错",
        minutes_ago=0.5,
    )
    result = selector.select(
        "你这结论哪来的",
        [unrelated, observation, utterance],
        budget=EventBudget(max_events=3, max_chars=900),
    )
    assert "utt" in result.event_ids
    assert "obs" in result.event_ids


def test_topic_switch_suppresses_screen_noise():
    selector = RecentEventSelector()
    observation = _event(
        "obs",
        ConversationEventType.SCREEN_OBSERVATION,
        "用户正在看代码",
        minutes_ago=1,
    )
    result = selector.select(
        "晚饭吃什么",
        [observation],
        budget=EventBudget(max_events=3, max_chars=900),
    )
    assert result.event_ids == ()


def test_unrelated_requests_do_not_select_fresh_screen_chain():
    selector = RecentEventSelector()
    observation = _event(
        "obs",
        ConversationEventType.SCREEN_OBSERVATION,
        "用户正在看代码",
        minutes_ago=1,
    )
    utterance = _event(
        "utt",
        ConversationEventType.PROACTIVE_UTTERANCE,
        "还在和报错较劲呀？",
        parents=("obs",),
    )

    for user_text in ("推荐一首歌", "我有点困", "讲个笑话", "帮我翻译这句话"):
        result = selector.select(user_text, [observation, utterance])
        assert result.event_ids == (), user_text


def test_short_term_dialog_texts_are_not_selected_again():
    selector = RecentEventSelector()
    user_event = _event(
        "user",
        ConversationEventType.USER_MESSAGE,
        "我今天在改登录页",
    )
    assistant_event = _event(
        "assistant",
        ConversationEventType.ASSISTANT_MESSAGE,
        "听起来快收尾了",
        parents=("user",),
    )

    result = selector.select(
        "你刚才说什么",
        [user_event, assistant_event],
        short_term_texts={"我今天在改登录页", "听起来快收尾了"},
    )

    assert result.event_ids == ()


def test_budget_cuts_whole_events():
    selector = RecentEventSelector()
    events = [
        _event(
            f"e{i}",
            ConversationEventType.ASSISTANT_MESSAGE,
            "字" * 200,
            minutes_ago=i,
        )
        for i in range(5)
    ]
    result = selector.select(
        "你刚才说",
        events,
        budget=EventBudget(max_events=2, max_chars=500),
    )
    assert len(result.event_ids) <= 2
    assert result.total_chars <= 500 or len(result.event_ids) <= 1


def test_prompt_format_uses_quote_and_evidence():
    observation = _event(
        "obs",
        ConversationEventType.SCREEN_OBSERVATION,
        "DeepSeek 页面中出现了原神",
    )
    utterance = _event(
        "utt",
        ConversationEventType.PROACTIVE_UTTERANCE,
        "原神又肝起来了？",
        parents=("obs",),
    )
    block = format_recent_event_block([observation, utterance])
    assert RECENT_BLOCK_TITLE in block
    assert "原神又肝起来了？" in block
    assert "DeepSeek" in block
    assert "依据" in block


def test_detect_dual_inject():
    assert detect_dual_inject(
        f"{RECENT_BLOCK_TITLE}\n【最近屏幕/视觉观察证据】\nx"
    )
    assert not detect_dual_inject(f"{RECENT_BLOCK_TITLE}\n- x")
