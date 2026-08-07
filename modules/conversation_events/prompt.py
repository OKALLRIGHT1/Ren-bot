"""Format selected conversation events into a recent-context block."""

from __future__ import annotations

from typing import Iterable, Sequence

from modules.conversation_events.models import ConversationEvent, ConversationEventType

RECENT_BLOCK_TITLE = "【最近发生的事｜内部参考】"
CROSS_CHANNEL_BLOCK_TITLE = "【另一通道近史｜内部参考｜时间邻近】"
LEGACY_SENSOR_EVIDENCE_TITLE = "【最近屏幕/视觉观察证据】"
LEGACY_SENSOR_ROAST_TITLE = "【你刚才的屏幕吐槽/主动发言】"

_USAGE_RULES = (
    "使用规则：\n"
    "- 这些内容只是可用背景，不是当前必须讨论的话题。\n"
    "- 仅在当前消息存在指代、因果或明确语义关联时使用。\n"
    "- 不要复述“内部参考”“事件日志”等系统概念。"
)

_CROSS_CHANNEL_USAGE_RULES = (
    "使用规则：\n"
    "- 这是主人另一通道（桌面/QQ）时间较近的对话摘要，不是当前会话正文。\n"
    "- 仅当用户明显在延续那一侧的话题、指代或未完结事项时再使用。\n"
    "- 不要主动把两侧会话混成同一条时间线，也不要复述“内部参考”等系统概念。"
)


def format_recent_event_block(events: Sequence[ConversationEvent]) -> str:
    if not events:
        return ""

    lines: list[str] = [RECENT_BLOCK_TITLE]
    by_id = {e.event_id: e for e in events}

    # Prefer speak events with parent evidence.
    speak_types = {
        ConversationEventType.ASSISTANT_MESSAGE,
        ConversationEventType.PROACTIVE_UTTERANCE,
        ConversationEventType.CARE_REMINDER,
    }
    rendered_ids: set[str] = set()

    for event in events:
        if event.event_type not in speak_types:
            continue
        quote = str(event.exact_text or "").strip()
        if not quote:
            continue
        lines.append(f'- 你刚才说：“{quote}”')
        rendered_ids.add(event.event_id)
        for parent_id in event.causal_parent_ids or ():
            parent = by_id.get(parent_id)
            if parent is None:
                continue
            evidence = str(parent.evidence_summary or parent.exact_text or "").strip()
            if not evidence:
                continue
            if parent.event_type is ConversationEventType.SCREEN_OBSERVATION:
                lines.append(f"  依据：屏幕观察到 {evidence}")
            elif parent.event_type is ConversationEventType.TOOL_RESULT:
                lines.append(f"  依据：工具结果 {evidence}")
            else:
                lines.append(f"  依据：{evidence}")
            rendered_ids.add(parent.event_id)

    for event in events:
        if event.event_id in rendered_ids:
            continue
        if event.event_type is ConversationEventType.SCREEN_OBSERVATION:
            evidence = str(event.evidence_summary or event.exact_text or "").strip()
            if evidence:
                lines.append(f"- 屏幕观察：{evidence}")
                rendered_ids.add(event.event_id)
        elif event.event_type is ConversationEventType.TOOL_RESULT:
            evidence = str(event.evidence_summary or event.exact_text or "").strip()
            if evidence:
                lines.append(f"- 工具结果：{evidence}")
                rendered_ids.add(event.event_id)
        elif event.event_type is ConversationEventType.USER_MESSAGE:
            text = str(event.exact_text or "").strip()
            if text:
                lines.append(f"- 用户刚说：{text}")
                rendered_ids.add(event.event_id)
        elif event.event_type in speak_types:
            text = str(event.exact_text or "").strip()
            if text:
                lines.append(f'- 你刚才说：“{text}”')
                rendered_ids.add(event.event_id)

    if len(lines) <= 1:
        return ""
    lines.append("")
    lines.append(_USAGE_RULES)
    return "\n".join(lines).strip()


def format_cross_channel_recent_block(events: Sequence[ConversationEvent]) -> str:
    """Format time-nearby owner dialog from the other channel (soft inject)."""
    if not events:
        return ""
    lines: list[str] = [
        CROSS_CHANNEL_BLOCK_TITLE,
        "说明：仅供衔接主人跨通道话题；默认会话仍按通道隔离。",
    ]
    for event in events:
        text = str(event.exact_text or event.evidence_summary or "").strip()
        if not text:
            continue
        if len(text) > 140:
            text = text[:137] + "..."
        scope = event.scope
        channel = str(getattr(scope, "channel", "") or "")
        cid = str(getattr(scope, "conversation_id", "") or "")
        if event.event_type is ConversationEventType.USER_MESSAGE:
            who = "用户"
        elif event.event_type in {
            ConversationEventType.ASSISTANT_MESSAGE,
            ConversationEventType.PROACTIVE_UTTERANCE,
            ConversationEventType.CARE_REMINDER,
        }:
            who = "你"
        else:
            who = "事件"
        lines.append(f"- [{channel}/{cid}] {who}：{text}")
    if len(lines) <= 2:
        return ""
    lines.append("")
    lines.append(_CROSS_CHANNEL_USAGE_RULES)
    return "\n".join(lines).strip()


def detect_dual_inject(system_text: str) -> bool:
    """Return True if both legacy sensor titles and new recent block appear."""
    text = str(system_text or "")
    has_new = RECENT_BLOCK_TITLE in text
    has_legacy = LEGACY_SENSOR_EVIDENCE_TITLE in text or LEGACY_SENSOR_ROAST_TITLE in text
    return has_new and has_legacy
