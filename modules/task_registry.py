from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Optional


CALLER_TASK_REGISTRY: dict[str, str] = {
    "vision": "vision",
    "chat_default_reply": "default",
    "chat_stream_reply": "default",
    "chat_tool_reasoning": "tool_reasoning",
    "chat_tool_deferred_reasoning": "tool_reasoning",
    "chat_tool_finalize": "default",
    "chat_delegate_reasoning": "tool_reasoning",
    "chat_delegate_finalize": "default",
    "chat_gatekeeper": "gatekeeper",
    "reply_emotion_fallback": "reply_polish",
    "hardware_status_polish": "default",
    "active_alert": "default",
    "send_active_alert": "default",
    "sensor_gatekeeper": "gatekeeper",
    "sensor_self_talk": "default",
    "sensor_vision_talk": "sensor_vision_talk",
    "sensor_text_talk": "default",
    "sensor_template_rescue": "default",
    "qq_image_describe": "vision",
    "observe_image_describe": "vision",
    "sensor_vision_direct": "vision",
    "sensor_vision_describe": "vision",
    "tts_translate": "translation",
    "daily_summary": "summary",
    "profile_extract": "summary",
    "memory_rerank": "summary",
    "memory_selector": "gatekeeper",
    "agently_mail_intent": "gatekeeper",
    "screen_classify": "screen_classify",
    "meme_pack_selector": "default",
    "chat_record_knowledge_import": "default",
    "chat_record_expression_import": "default",
}

CALLER_TASK_PATTERNS: dict[str, str] = {
    "natural_reply_polish_*": "reply_polish",
}

CALLER_DESCRIPTIONS: dict[str, str] = {
    "vision": "通用视觉模型调用。",
    "chat_default_reply": "普通聊天回复的非流式兜底调用。",
    "chat_stream_reply": "普通聊天回复的流式主调用。",
    "chat_tool_reasoning": "工具调用前的推理与工具选择。",
    "chat_tool_deferred_reasoning": "延迟工具任务的后续推理。",
    "chat_tool_finalize": "工具返回结果后的最终回复整理。",
    "chat_delegate_reasoning": "副脑委托任务的推理阶段。",
    "chat_delegate_finalize": "副脑委托任务完成后的回复整理。",
    "chat_gatekeeper": "主聊天是否需要进入工具/特殊流程的轻量判断。",
    "reply_emotion_fallback": "回复情绪标签缺失时的兜底润色。",
    "hardware_status_polish": "硬件状态文本发送前的自然语言润色。",
    "active_alert": "主动提醒内容生成。",
    "send_active_alert": "主动提醒发送前的内容整理。",
    "sensor_gatekeeper": "屏幕/传感器事件是否需要回应的轻量判断。",
    "sensor_self_talk": "屏幕/传感器触发的自言自语生成。",
    "sensor_vision_talk": "屏幕视觉理解后的吐槽回复生成。",
    "sensor_text_talk": "屏幕文本事件的吐槽回复生成。",
    "sensor_template_rescue": "屏幕吐槽模板失败时的兜底生成。",
    "qq_image_describe": "QQ 图片消息的视觉描述。",
    "observe_image_describe": "观察图片命令的视觉描述。",
    "sensor_vision_direct": "传感器视觉直连理解。",
    "sensor_vision_describe": "传感器截图的视觉描述。",
    "tts_translate": "TTS 前的翻译处理。",
    "daily_summary": "日记/每日总结生成。",
    "profile_extract": "用户画像与记忆信息抽取。",
    "memory_rerank": "长期记忆候选重排。",
    "memory_selector": "记忆是否相关的轻量筛选。",
    "agently_mail_intent": "Agent Mail 自然语言意图解析。",
    "screen_classify": "屏幕内容分类。",
    "meme_pack_selector": "表情包插件的候选选择。",
    "chat_record_knowledge_import": "聊天记录导入为知识库时的整理。",
    "chat_record_expression_import": "聊天记录导入为表达库时的整理。",
}

CALLER_PATTERN_DESCRIPTIONS: dict[str, str] = {
    "natural_reply_polish_*": "不同场景下的自然回复润色。",
}


@dataclass(frozen=True)
class CallerTaskCheck:
    caller: str
    task_type: str
    expected_task_type: Optional[str]
    known: bool
    matched_pattern: str = ""

    @property
    def ok(self) -> bool:
        return (not self.known) or self.expected_task_type == self.task_type


def resolve_expected_task_type(caller: str) -> tuple[Optional[str], str]:
    name = str(caller or "").strip()
    if not name:
        return None, ""
    expected = CALLER_TASK_REGISTRY.get(name)
    if expected:
        return expected, name
    for pattern, task_type in CALLER_TASK_PATTERNS.items():
        if fnmatchcase(name, pattern):
            return task_type, pattern
    return None, ""


def get_caller_description(caller: str) -> str:
    name = str(caller or "").strip()
    if not name:
        return ""
    desc = CALLER_DESCRIPTIONS.get(name)
    if desc:
        return desc
    for pattern, pattern_desc in CALLER_PATTERN_DESCRIPTIONS.items():
        if fnmatchcase(name, pattern):
            return pattern_desc
    return ""


def check_caller_task(caller: str, task_type: str) -> CallerTaskCheck:
    name = str(caller or "unknown").strip() or "unknown"
    actual = str(task_type or "default").strip() or "default"
    expected, matched = resolve_expected_task_type(name)
    return CallerTaskCheck(
        caller=name,
        task_type=actual,
        expected_task_type=expected,
        known=bool(expected),
        matched_pattern=matched if matched != name else "",
    )
