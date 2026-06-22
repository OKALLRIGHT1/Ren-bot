"""
聊天服务
处理用户输入和AI响应的核心逻辑
"""

import json
import re
import asyncio
import time
import uuid
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any, Awaitable, Callable, List

from modules.llm import chat_with_ai

try:
    from modules.llm import chat_with_ai_stream
except ImportError:
    chat_with_ai_stream = None

from modules.live2d import trigger_motion
from modules.delegate_task_state import set_task_state as set_delegate_task_state
from modules.delegate_session import add_event as delegate_add_event
from modules.personality_system import get_personality_system
from modules.reply_effect_tracker import ReplyEffectTracker
from modules.runtime_settings import load_runtime_settings
from core.message_source import REMOTE_CHAT_SOURCES, build_output_profile
from services.chat_support import (
    active_alert_service,
    diary_service,
    diary_utils,
    delegate_flow_service,
    emotion_reply_service,
    gateway_context_service,
    gateway_sender,
    hardware_status_service,
    idle_status_service,
    input_context,
    output_coordinator,
    reply_flow_service,
    reply_style_service,
    sensor_event_service,
    sensor_event_guard,
    sensor_reply_service,
    sensor_utils,
    search_flow_service,
    text_utils,
    tool_flow_service,
    tool_result_formatter,
)

# 引入 Gatekeeper 配置
try:
    from config import (
        EMO_LABELS,
        GATEKEEPER_ENABLED,
        GATEKEEPER_WHITELIST,
        GATEKEEPER_BLACKLIST,
        GATEKEEPER_PROMPT_TEMPLATE,
        GATEKEEPER_ACTIVE_SESSION_WINDOW,
        PERSONA_PROMPT,
        DEFAULT_PERSONA,
        VISION_MODE,
        WAKE_KEYWORDS,
        CHARACTER_SHARING_ENABLED,
        CHAT_DEBUG_PRINTS,
        NAPCAT_OWNER_USER_IDS,
        SCREEN_GLOBAL_COOLDOWN,
    )
except ImportError:
    # 默认值兜底，防止 config 未更新导致报错
    EMO_LABELS = []
    GATEKEEPER_ENABLED = False
    GATEKEEPER_WHITELIST = []
    GATEKEEPER_BLACKLIST = []
    GATEKEEPER_PROMPT_TEMPLATE = ""
    GATEKEEPER_ACTIVE_SESSION_WINDOW = 20
    WAKE_KEYWORDS = []
    CHARACTER_SHARING_ENABLED = False
    CHAT_DEBUG_PRINTS = False
    NAPCAT_OWNER_USER_IDS = []
    SCREEN_GLOBAL_COOLDOWN = 120

QQ_REMOTE_SOURCES = gateway_sender.QQ_REMOTE_SOURCES
OWNER_SHARED_SESSION_ID = "owner_shared"
OWNER_SHARED_LOCAL_SOURCES = {"text_input", "voice"}
LEGACY_OWNER_PRIVATE_SESSION_IDS = {
    f"private:{str(item).strip()}"
    for item in (NAPCAT_OWNER_USER_IDS or [])
    if str(item).strip()
}


class ChatService:
    """聊天服务"""

    NATURAL_REPLY_FALLBACK_HINTS = {
        "default": {
            "chat": (
                "像顺手接话，不要像在做说明。",
                "先给态度或判断，再补半句，不要先铺垫。",
                "能用短句就不用完整书面句。",
            ),
            "sensor": (
                "像瞥见后顺口说一句，不要像播报观察结果。",
                "允许轻微吐槽，但别把看到的东西完整复述一遍。",
                "多直接对他说话，少用解说口吻。",
            ),
        },
        "五十铃怜": {
            "chat": (
                "语气平稳、克制，但不要端成说明文。",
                "像安静地回一句，不要每次都完整解释。",
                "少一点照本宣科，多一点顺口接话。",
            ),
            "sensor": (
                "像在旁边轻轻戳他一句，不要像系统提示。",
                "短一点，冷一点，但别僵硬。",
                "少复述画面，多留一点临场感。",
            ),
        },
    }
    MODEL_ERROR_FALLBACK_TEXT = "我这边卡了一下\n等我缓缓"

    POSITIVE_FEEDBACK_KEYWORDS = (
        "谢谢",
        "谢啦",
        "有帮助",
        "有用",
        "喜欢",
        "不错",
        "很好",
        "太好了",
        "棒",
        "完美",
        "正是",
        "thanks",
        "thank you",
        "great",
        "good job",
    )
    NEGATIVE_FEEDBACK_KEYWORDS = (
        "不对",
        "不行",
        "不好",
        "没用",
        "不喜欢",
        "错了",
        "答非所问",
        "重来",
        "离谱",
        "不准确",
        "不需要",
        "没帮上",
        "wrong",
        "bad",
        "not helpful",
    )
    APPLY_CONFIRM_KEYWORDS = ("确认", "应用", "执行", "同意", "apply")
    TASK_CREATE_KEYWORDS = (
        "待办",
        "todo",
        "to do",
        "提醒我",
        "记得",
        "别忘了",
        "要记得",
        "今天要",
        "明天要",
        "今晚要",
        "等会要",
        "待会要",
        "周末要",
        "计划",
        "打算",
        "安排",
        "准备",
        "要去",
        "需要",
        "得去",
        "我得",
        "我要",
    )
    TASK_DONE_KEYWORDS = (
        "做完了",
        "搞定了",
        "完成了",
        "弄完了",
        "处理完了",
        "解决了",
        "写完了",
        "提交了",
        "发了",
        "结束了",
        "done",
        "finished",
        "搞好了",
        "已经好了",
    )
    TASK_STATUS_KEYWORDS = (
        "任务",
        "待办",
        "todo",
        "进度",
        "安排",
        "计划",
        "打算",
        "提醒",
    )
    FOLLOWUP_TOPICS = {
        "health": (
            "腹泻",
            "拉肚子",
            "肚子疼",
            "胃痛",
            "发烧",
            "咳嗽",
            "头痛",
            "生病",
            "不舒服",
            "断食",
            "补液",
            "电解质",
            "医院",
            "就医",
        ),
        "sleep": (
            "熬夜",
            "失眠",
            "没睡好",
            "睡不着",
            "睡眠",
            "困",
            "早睡",
            "晚睡",
            "睡觉",
        ),
        "diet": (
            "没吃饭",
            "吃不下",
            "胃口",
            "饮食",
            "喝水",
            "脱水",
            "饿",
            "早餐",
            "午饭",
            "晚饭",
        ),
        "work_study": (
            "加班",
            "赶工",
            "ddl",
            "截止",
            "写代码",
            "开题",
            "报告",
            "复习",
            "考试",
            "作业",
            "项目",
        ),
        "emotion": (
            "焦虑",
            "压力",
            "难受",
            "烦",
            "崩溃",
            "低落",
            "紧张",
            "心情",
            "不开心",
            "累",
        ),
        "plan": (
            "明天",
            "计划",
            "打算",
            "安排",
            "要去",
            "准备",
            "目标",
            "待办",
        ),
    }
    CARE_FOLLOWUP_TOPICS = {"health", "sleep", "diet", "emotion"}

    def __init__(
        self,
        brain,
        plugin_manager,
        tool_router,
        presenter,
        event_bus,
        logger,
        chat_gateway=None,
        mcp_bridge=None,
    ):
        self.brain = brain
        self.plugin_manager = plugin_manager
        self.tool_router = tool_router
        self.presenter = presenter
        self.event_bus = event_bus
        self.logger = logger
        self.chat_gateway = chat_gateway
        self.mcp_bridge = mcp_bridge
        self.skill_manager = None
        self._app_ref = None
        self.debug_enabled = bool(CHAT_DEBUG_PRINTS)
        self.personality = get_personality_system()
        # 延迟导入以避免循环依赖
        try:
            from modules.learning_system import get_learning_system

            self.learning = get_learning_system()
        except ImportError:
            self.learning = None

        self._last_reply_time = 0  # 记录最后一次回复的时间戳
        self._sensor_min_reply_interval_sec = max(45, int(SCREEN_GLOBAL_COOLDOWN))

        # 情绪标签配置
        self._emo_set = set(
            [str(x).strip().lower() for x in (EMO_LABELS or [])] + ["idle", "think"]
        )
        self._emo_tag_re = re.compile(
            r"<\s*emo\s*=\s*([a-zA-Z_]+)\s*>", flags=re.IGNORECASE
        )
        self._cmd_re = re.compile(r"\[CMD:.*?\]", flags=re.DOTALL)
        self._apply_cmd_re = re.compile(
            r"\[CMD:\s*workspace_ops\s*\|\s*apply_change\s*\|\|\|\s*([0-9a-fA-F]{10})\s*\|\|\|\s*([0-9a-fA-F]{8})\s*\]",
            flags=re.IGNORECASE,
        )
        self._id_token_re = re.compile(
            r"\b([0-9a-fA-F]{10})\b[\s\S]{0,120}\b([0-9a-fA-F]{8})\b",
            flags=re.IGNORECASE,
        )
        self._last_proactive_followup_day = ""
        self._last_task_followup_day = ""
        self.gateway_voice_reply_enabled = False
        self.gateway_voice_reply_probability = 0
        self.gateway_voice_renderer: Optional[
            Callable[..., Awaitable[Optional[str]]]
        ] = None
        self.reply_effect_tracker = ReplyEffectTracker()
        self._qq_private_buffers: Dict[str, Dict[str, Any]] = {}
        self._last_tool_triggers_by_session: Dict[str, List[str]] = {}
        self._last_search_topic_by_session: Dict[str, str] = {}
        self._recent_sensor_replies: List[str] = []
        self._sensor_event_lock = asyncio.Lock()
        self.gateway_context_service = self._create_gateway_context_service()
        self.gateway_sender = self._create_gateway_sender()
        self.idle_status_service = self._create_idle_status_service()
        self.reply_style_service = self._create_reply_style_service()
        self.emotion_reply_service = self._create_emotion_reply_service()
        self.tool_result_formatter = self._create_tool_result_formatter()
        self.hardware_status_service = self._create_hardware_status_service()
        self.active_alert_service = self._create_active_alert_service()
        self.sensor_event_service = self._create_sensor_event_service()
        self.sensor_reply_service = self._create_sensor_reply_service()
        self.diary_service = self._create_diary_service()

    def _create_gateway_sender(self) -> gateway_sender.GatewaySender:
        return gateway_sender.GatewaySender(
            chat_gateway_getter=lambda: self.chat_gateway,
            logger=self.logger,
            prepare_reply_for_output=self._prepare_reply_for_output,
            strip_emo_tags=self._strip_emo_tags_anywhere,
            strip_cmd=self._strip_cmd_anywhere,
            clean_text_for_tts=self._clean_text_for_tts,
            session_label_fn=self.gateway_context_service.qq_session_label,
            voice_enabled_getter=lambda: self.gateway_voice_reply_enabled,
            voice_probability_getter=lambda: self.gateway_voice_reply_probability,
            voice_renderer_getter=lambda: self.gateway_voice_renderer,
            remote_sources=QQ_REMOTE_SOURCES,
        )

    def _create_gateway_context_service(
        self,
    ) -> gateway_context_service.GatewayContextService:
        return gateway_context_service.GatewayContextService(
            qq_remote_sources=QQ_REMOTE_SOURCES,
            owner_shared_session_id=OWNER_SHARED_SESSION_ID,
            owner_shared_local_sources=OWNER_SHARED_LOCAL_SOURCES,
        )

    def _create_idle_status_service(
        self,
    ) -> idle_status_service.IdleStatusService:
        return idle_status_service.IdleStatusService(
            event_emit=self.event_bus.emit,
            debug=self._dbg,
        )

    def _create_reply_style_service(
        self,
    ) -> reply_style_service.ReplyStyleService:
        return reply_style_service.ReplyStyleService(
            emo_set=self._emo_set,
            emo_tag_re=self._emo_tag_re,
            cmd_re=self._cmd_re,
        )

    def _create_emotion_reply_service(
        self,
    ) -> emotion_reply_service.EmotionReplyService:
        return emotion_reply_service.EmotionReplyService(
            app_getter=lambda: getattr(self, "app", None),
            clean_text_for_tts=self._clean_text_for_tts,
            strip_emo_tags=self._strip_emo_tags_anywhere,
            strip_cmd=self._strip_cmd_anywhere,
            normalize_emo=self._normalize_emo,
            logger=self.logger,
        )

    def _create_tool_result_formatter(
        self,
    ) -> tool_result_formatter.ToolResultFormatter:
        return tool_result_formatter.ToolResultFormatter(
            get_active_character_profile=self._get_active_character_profile,
            looks_like_upstream_error_reply=self._looks_like_upstream_error_reply,
            strip_emo_tags=self._strip_emo_tags_anywhere,
            strip_cmd=self._strip_cmd_anywhere,
            strip_internal_tags=self._strip_internal_tags,
            clean_text_for_tts=self._clean_text_for_tts,
            normalize_qq_reply_style=self._normalize_qq_reply_style,
            wants_detailed_answer=self._wants_detailed_answer,
            extract_emo_tag=self._extract_emo_tag,
            qq_remote_sources=QQ_REMOTE_SOURCES,
            logger=self.logger,
        )

    def _create_hardware_status_service(
        self,
    ) -> hardware_status_service.HardwareStatusService:
        return hardware_status_service.HardwareStatusService(
            plugin_manager_getter=lambda: self.plugin_manager,
            event_bus=self.event_bus,
            tool_result_formatter=self.tool_result_formatter,
            split_gateway_text_parts=gateway_sender.split_gateway_text_parts,
            emit_assistant_text=self._emit_assistant_text,
            add_memory_safe=self._add_memory_safe,
        )

    def _create_active_alert_service(
        self,
    ) -> active_alert_service.ActiveAlertService:
        return active_alert_service.ActiveAlertService(
            default_persona=DEFAULT_PERSONA,
            event_bus=self.event_bus,
            presenter=self.presenter,
            extract_emo_tag=self._extract_emo_tag,
            polish_natural_reply=self._polish_natural_reply,
            apply_character_catchphrase=self._apply_character_catchphrase,
            logger=self.logger,
        )

    def _create_sensor_event_service(
        self,
    ) -> sensor_event_service.SensorEventService:
        return sensor_event_service.SensorEventService(
            screen_sensor_ref_getter=lambda: getattr(self, "screen_sensor_ref", None),
            format_sensor_observations=sensor_utils.format_sensor_observations,
            build_sensor_usage_context=sensor_utils.build_sensor_usage_context,
            build_sensor_interaction_context=self._build_sensor_interaction_context,
            build_sensor_persona_prompt=self._build_sensor_persona_prompt,
            format_recent_sensor_reply_block=self._format_recent_sensor_reply_block,
            build_sensor_spontaneous_style_block=sensor_utils.build_sensor_spontaneous_style_block,
            build_live2d_self_awareness_hint=self._build_live2d_self_awareness_hint,
            compress_sensor_text=text_utils.compress_sensor_text,
            logger=self.logger,
        )

    def _create_sensor_reply_service(
        self,
    ) -> sensor_reply_service.SensorReplyService:
        return sensor_reply_service.SensorReplyService(
            event_bus=self.event_bus,
            presenter=self.presenter,
            logger=self.logger,
            extract_emo_tag=self._extract_emo_tag,
            strip_wrapping_quotes=self._strip_wrapping_quotes,
            polish_natural_reply=self._polish_natural_reply,
            apply_character_catchphrase=self._apply_character_catchphrase,
            prepare_reply_for_output=self._prepare_reply_for_output,
            looks_like_sensor_template_reply=lambda text: sensor_utils.looks_like_sensor_template_reply(
                text, clean_text_fn=self._clean_text_for_tts
            ),
            rescue_sensor_template_reply=self._rescue_sensor_template_reply,
            remember_sensor_reply=self._remember_sensor_reply,
            update_active_time=self._update_active_time,
            infer_reply_emotion_with_llm=self._infer_reply_emotion_with_llm,
            get_current_live2d_emotion=self._get_current_live2d_emotion,
            reset_sensor_motion_after=self._reset_sensor_motion_after,
            add_memory_safe=self._add_memory_safe,
            last_reply_time_getter=lambda: self._last_reply_time,
        )

    def _create_diary_service(self) -> diary_service.DiaryService:
        return diary_service.DiaryService(
            brain=self.brain,
            event_bus=self.event_bus,
            presenter=self.presenter,
            logger=self.logger,
            add_memory_safe=self._add_memory_safe,
            emit_idle_status_when_safe=self._emit_idle_status_when_safe,
            send_gateway_reply=self._send_gateway_reply,
            backfill_napcat_history_for_day=self._backfill_napcat_history_for_day,
            load_day_transcript_rows=self._load_day_transcript_rows,
            get_runtime_owner_label=self._get_runtime_owner_label,
            owner_ids=[str(item).strip() for item in (NAPCAT_OWNER_USER_IDS or [])],
            owner_shared_session_id=OWNER_SHARED_SESSION_ID,
            legacy_owner_private_session_ids=LEGACY_OWNER_PRIVATE_SESSION_IDS,
            owner_shared_local_sources=OWNER_SHARED_LOCAL_SOURCES,
            qq_remote_sources=QQ_REMOTE_SOURCES,
            get_active_character_context=self._get_active_character_context,
        )

    def configure_gateway_voice_reply(
        self,
        *,
        enabled: bool = False,
        probability: int = 0,
        renderer: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
    ) -> None:
        self.gateway_voice_reply_enabled = bool(enabled)
        try:
            value = int(probability)
        except Exception:
            value = 0
        self.gateway_voice_reply_probability = max(0, min(100, value))
        self.gateway_voice_renderer = renderer

    def _looks_like_upstream_error_reply(self, text: str) -> bool:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw or len(raw) > 420:
            return False
        compact = re.sub(r"\s+", " ", raw).strip().lower()
        checks = (
            ("gemini", "no longer available"),
            ("please switch to", "antigravity"),
            ("model", "no longer available"),
            ("model", "not found"),
            ("invalid model",),
            ("unsupported model",),
            ("all models", "failed"),
            ("所有模型", "失败"),
            ("系统繁忙", "无法连接"),
        )
        return any(all(part in compact for part in check) for check in checks)

    def _normalize_qq_reply_style(self, text: str) -> str:
        clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not clean:
            return ""
        if "```" in clean or re.search(r"https?://|\[[^\]]+\]\([^)]+\)", clean):
            return clean
        if self._looks_structured_reply(clean):
            return clean

        # QQ 短消息更像聊天，不像书面文本；只处理短自然回复，避免破坏教程/代码/链接。
        clean = re.sub(r"(?<=[\u4e00-\u9fff])\s*[。．]+\s*(?=[\u4e00-\u9fffA-Za-z0-9])", "\n", clean)
        lines: List[str] = []
        for raw_line in clean.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if re.search(r"https?://", line):
                lines.append(line)
                continue
            if re.search(r"[\u4e00-\u9fff]", line):
                line = re.sub(r"[。．]+$", "", line).rstrip()
                line = re.sub(r"(?<=[\u4e00-\u9fff])\.+$", "", line).rstrip()
            lines.append(line)
        return "\n".join(lines).strip()

    def _prepare_reply_for_output(
        self,
        text: str,
        ctx: Optional[Dict[str, Any]] = None,
        *,
        scene: str = "chat",
    ) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        if self._looks_like_upstream_error_reply(clean):
            preview = re.sub(r"\s+", " ", clean)[:180]
            try:
                self.logger.warning(f"Suppress upstream model error reply: {preview}")
            except Exception:
                pass
            if scene == "sensor":
                return ""
            clean = self.MODEL_ERROR_FALLBACK_TEXT

        ctx_dict = ctx if isinstance(ctx, dict) else {}
        source = str(ctx_dict.get("source") or "").strip().lower()
        if source in QQ_REMOTE_SOURCES:
            clean = self._normalize_qq_reply_style(clean)
        return clean.strip()

    def _is_qq_private_bufferable(
        self, user_text: str, ctx: Optional[Dict[str, Any]]
    ) -> tuple[bool, str]:
        if not isinstance(ctx, dict):
            return False, ""
        source = str(ctx.get("source") or "").strip().lower()
        if source not in QQ_REMOTE_SOURCES:
            return False, ""
        if bool(ctx.get("codex_mode", False)):
            return False, ""
        channel_meta = ctx.get("channel_meta") or {}
        message_type = str(channel_meta.get("message_type") or "private").strip().lower()
        session_id = str(channel_meta.get("session_id") or "").strip()
        if message_type != "private" or not session_id.startswith("private:"):
            return False, ""
        if bool(channel_meta.get("has_image")) or bool(channel_meta.get("has_file")):
            return False, ""
        clean = str(user_text or "").strip()
        if not clean:
            return False, ""
        if clean.startswith(("/", "！", "!", "#")):
            return False, ""
        if len(clean) > 800:
            return False, ""
        return True, session_id

    def _qq_private_buffer_delay(self, text: str, pending_count: int) -> float:
        clean = str(text or "").strip()
        if pending_count >= 2:
            return 2.2
        if len(clean) <= 8:
            return 3.0
        if clean.endswith(("?", "？", "!", "！", "。", ".", "…", "~", "～")):
            return 2.2
        return 3.4

    async def _maybe_buffer_qq_private_message(
        self, user_text: str, ctx: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        ok, session_id = self._is_qq_private_bufferable(user_text, ctx)
        if not ok:
            return str(user_text or "")

        state = self._qq_private_buffers.setdefault(
            session_id, {"seq": 0, "items": [], "updated_at": 0.0}
        )
        state["seq"] = int(state.get("seq") or 0) + 1
        seq = int(state["seq"])
        state["updated_at"] = time.time()
        item = str(user_text or "").strip()
        items = state.setdefault("items", [])
        if isinstance(items, list):
            items.append(item)
        else:
            state["items"] = [item]
            items = state["items"]

        delay = self._qq_private_buffer_delay(item, len(items))
        try:
            self.logger.info(
                f"[QQ-BUFFER][{session_id}] wait={delay:.1f}s seq={seq} count={len(items)}"
            )
        except Exception:
            pass
        await asyncio.sleep(delay)

        latest = self._qq_private_buffers.get(session_id)
        if latest is not state or int(state.get("seq") or 0) != seq:
            return None

        self._qq_private_buffers.pop(session_id, None)
        final_items = [
            str(part or "").strip()
            for part in list(state.get("items") or [])
            if str(part or "").strip()
        ]
        if not final_items:
            return ""
        if isinstance(ctx, dict):
            ctx["qq_buffered_messages"] = final_items
            ctx["qq_buffered_count"] = len(final_items)
        if len(final_items) == 1:
            return final_items[0]
        return "\n".join(final_items)

    def _build_qq_reply_angle_context(
        self, user_text: str, ctx: Optional[Dict[str, Any]]
    ) -> str:
        if not isinstance(ctx, dict):
            return ""
        source = str(ctx.get("source") or "").strip().lower()
        if source not in QQ_REMOTE_SOURCES:
            return ""
        channel_meta = ctx.get("channel_meta") or {}
        message_type = str(channel_meta.get("message_type") or "private").strip().lower()
        if message_type != "private":
            return ""

        raw = str(user_text or "").strip()
        if not raw:
            return ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        latest = lines[-1] if lines else raw
        compact = re.sub(r"\s+", " ", raw)

        angle = "日常接话"
        instruction = "像私聊里顺手回，不要把每句话都当成题目作答"
        if any(k in latest for k in ("狡猾", "坏", "嘴硬", "笨", "可爱", "怜酱")):
            angle = "接调侃"
            instruction = "顺着调侃轻轻嘴硬或反问一下，不要认真辩解"
        elif any(k in latest for k in ("喜欢", "想你", "陪我", "抱", "亲", "老婆")):
            angle = "接亲近感"
            instruction = "保留一点克制和不好意思，别突然讲大道理"
        elif any(k in latest for k in ("是不是", "你是不是", "对吧", "吗", "?","？")):
            angle = "直接回应"
            instruction = "先接住问题本身，能一句回答就别展开成说明文"
        elif any(k in latest for k in ("为什么", "怎么", "咋", "如何")):
            angle = "轻解释"
            instruction = "给一个短原因就停，不要写教程或总结"
        elif any(k in latest for k in ("难受", "烦", "累", "崩", "不想", "害怕")):
            angle = "陪伴"
            instruction = "先站在他这边，用短句接情绪，不要立刻提供方案"
        elif len(lines) >= 2:
            angle = "合并连续消息"
            instruction = "把连续几句当成一个整体，优先接最后一句的情绪和梗，不要逐条回答"

        return (
            "【本轮 QQ 私聊接话规划】\n"
            f"- 用户连续消息数：{len(lines) or 1}\n"
            f"- 接话角度：{angle}\n"
            f"- 执行方式：{instruction}\n"
            f"- 用户原话压缩：{compact[:180]}\n"
            "- 回复要求：像真人私聊，不要编号，不要总结，不要句号收尾。"
        )

    async def _send_gateway_reply(
        self,
        text: str,
        ctx: Optional[Dict[str, Any]] = None,
        emotion: Optional[str] = None,
    ):
        await self.gateway_sender.send_reply(text, ctx, emotion=emotion)

    async def _send_gateway_image_reply(
        self, image_path: str, ctx: Optional[Dict[str, Any]] = None, caption: str = ""
    ) -> bool:
        return await self.gateway_sender.send_image_reply(image_path, ctx, caption)

    def _get_meme_pack_plugin(self):
        manager = getattr(self, "plugin_manager", None)
        if manager is None:
            return None
        try:
            if hasattr(manager, "is_plugin_enabled") and not manager.is_plugin_enabled(
                "meme_pack"
            ):
                return None
            plugin = getattr(manager, "plugins", {}).get("meme_pack")
        except Exception:
            return None
        if plugin is None or not hasattr(plugin, "maybe_send_auto_meme"):
            return None
        return plugin

    async def _maybe_send_auto_meme_reply(
        self,
        *,
        user_text: str,
        reply_text: str,
        emotion: str,
        ctx: Optional[Dict[str, Any]],
    ) -> bool:
        plugin = self._get_meme_pack_plugin()
        if plugin is None:
            return False
        try:
            return bool(
                await plugin.maybe_send_auto_meme(
                    chat_service=self,
                    user_text=user_text,
                    reply_text=reply_text,
                    emotion=emotion,
                    ctx=ctx,
                )
            )
        except Exception as exc:
            try:
                self.logger.warning(f"Meme auto send failed: {exc}")
            except Exception:
                pass
            return False

    async def _send_gateway_file_reply(
        self, file_path: str, ctx: Optional[Dict[str, Any]] = None, file_name: str = ""
    ) -> bool:
        return await self.gateway_sender.send_file_reply(file_path, ctx, file_name)

    async def _send_gateway_voice_reply(
        self, voice_path: str, ctx: Optional[Dict[str, Any]] = None
    ) -> bool:
        return await self.gateway_sender.send_voice_reply(voice_path, ctx)

    async def _emit_idle_status(
        self, output_profile: Optional[Dict[str, Any]], reason: str
    ) -> None:
        await self.idle_status_service.emit_idle_status(output_profile, reason)

    def _presenter_output_controls_idle(
        self,
        output_profile: Optional[Dict[str, Any]],
        *,
        had_presenter_output: bool,
    ) -> bool:
        return self.idle_status_service.presenter_output_controls_idle(
            output_profile, had_presenter_output=had_presenter_output
        )

    async def _emit_idle_status_when_safe(
        self,
        output_profile: Optional[Dict[str, Any]],
        *,
        reason: str,
        had_presenter_output: bool,
    ) -> None:
        await self.idle_status_service.emit_idle_status_when_safe(
            output_profile,
            reason=reason,
            had_presenter_output=had_presenter_output,
        )

    def _dbg(self, message: str):
        if self.debug_enabled:
            self.logger.debug(message)

    def _trace_process(self, stage: str, **fields: Any) -> None:
        if not self.debug_enabled:
            return
        parts = []
        for key in sorted(fields):
            value = fields.get(key)
            if isinstance(value, (list, tuple, set)):
                value = ",".join(str(item) for item in value)
            elif isinstance(value, dict):
                value = ",".join(str(k) for k in sorted(value.keys()))
            parts.append(f"{key}={value}")
        suffix = " ".join(parts)
        self.logger.debug(f"[ProcessTrace] {stage}{(' ' + suffix) if suffix else ''}")

    def _build_mcp_tool_prompt(self, max_tools: int = 16) -> str:
        if not self.mcp_bridge:
            return ""
        if not self.plugin_manager or "mcp_tools" not in getattr(
            self.plugin_manager, "delegate_map", {}
        ):
            return ""
        try:
            specs = [
                spec
                for spec in self.mcp_bridge.list_tools()
                if getattr(spec, "provider", "local") != "local"
            ]
        except Exception:
            return ""
        if not specs:
            return ""
        if max_tools and len(specs) > max_tools:
            specs = specs[:max_tools]
        lines = []
        for spec in specs:
            desc = (
                str(getattr(spec, "description", "") or "").replace("\n", " ").strip()
            )
            if len(desc) > 72:
                desc = desc[:72] + "…"
            lines.append(f"- {spec.name}: {desc or 'MCP tool'}")
        return (
            "\n\n【远程MCP工具】\n"
            "这些工具需要通过副脑委托调用。需要使用时，先委托 mcp_tools，并说明目标工具和参数。\n"
            "委托示例：\n"
            "[CMD: mcp_tools | call_tool ||| 工具名 ||| JSON参数]\n"
            "如需查看当前可用远程工具，可委托：\n"
            "[CMD: mcp_tools | list_tools]\n" + "\n".join(lines)
        )

    def _remember_search_topic(self, text: str, ctx: Optional[Dict[str, Any]]) -> None:
        if not text_utils.is_search_topic_candidate(
            text, looks_structured_reply=self._looks_structured_reply
        ):
            return
        raw = str(text or "").strip()
        if not (
            self._is_searchworthy_question(raw)
            or any(token in raw for token in ("查", "搜", "搜索", "联网", "上网", "萌百", "链接", "网址"))
        ):
            return
        session_key = self.gateway_context_service.conversation_session_key(ctx)
        if not session_key:
            return
        self._last_search_topic_by_session[session_key] = raw

    def _load_recent_user_topic_from_store(
        self, ctx: Optional[Dict[str, Any]], current_text: str = ""
    ) -> str:
        store = self._get_memory_store()
        if store is None or not hasattr(store, "list_transcript"):
            return ""
        session_key = self.gateway_context_service.conversation_session_key(ctx)
        session_scope = "specific" if session_key else "all"
        try:
            rows = store.list_transcript(
                role="user",
                limit=12,
                offset=0,
                session_id=session_key or None,
                session_scope=session_scope,
            )
        except Exception:
            return ""
        current_clean = str(current_text or "").strip()
        for row in rows:
            content = str((row or {}).get("content") or "").strip()
            if not content or content == current_clean:
                continue
            if not text_utils.is_search_topic_candidate(
                content, looks_structured_reply=self._looks_structured_reply
            ):
                continue
            return content
        return ""

    def _resolve_followup_search_query(
        self, user_text: str, ctx: Optional[Dict[str, Any]]
    ) -> str:
        raw = str(user_text or "").strip()
        if not text_utils.is_generic_search_followup_request(raw):
            return ""
        if text_utils.is_search_retry_correction_request(raw):
            return raw
        session_key = self.gateway_context_service.conversation_session_key(ctx)
        if session_key:
            remembered = str(self._last_search_topic_by_session.get(session_key) or "").strip()
            if remembered and remembered != raw:
                return remembered
        stored = self._load_recent_user_topic_from_store(ctx, current_text=raw)
        if stored:
            return stored
        brain = getattr(self, "brain", None)
        memory = getattr(brain, "short_term_memory", None)
        if isinstance(memory, list):
            for item in reversed(memory[-12:]):
                if not isinstance(item, dict):
                    continue
                if str(item.get("role") or "").strip().lower() != "user":
                    continue
                content = str(item.get("content") or "").strip()
                if not content or content == raw:
                    continue
                if text_utils.is_search_topic_candidate(
                    content, looks_structured_reply=self._looks_structured_reply
                ):
                    return content
        return ""

    def _is_searchworthy_question(self, text: str) -> bool:
        return text_utils.is_searchworthy_question(text)

    def _looks_like_uncertain_answer(self, text: str) -> bool:
        return text_utils.looks_like_uncertain_answer(text)

    def _observe_reply_effect(self, user_text: str, ctx: Optional[Dict[str, Any]]) -> None:
        try:
            session_id, user_id = self.gateway_context_service.reply_effect_identity(ctx)
            record = self.reply_effect_tracker.observe_user_message(
                session_id=session_id,
                user_id=user_id,
                text=user_text,
                source=str((ctx or {}).get("source") or ""),
            )
            if record and self.logger:
                self.logger.info(
                    f"[ReplyEffect] session={session_id} labels={','.join(record.get('labels') or [])} score={record.get('score')}"
                )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"Reply effect observe failed: {exc}")

    def _record_reply_effect(self, reply_text: str, ctx: Optional[Dict[str, Any]], *, source: str = "") -> None:
        try:
            session_id, user_id = self.gateway_context_service.reply_effect_identity(ctx)
            self.reply_effect_tracker.record_reply(
                session_id=session_id,
                user_id=user_id,
                text=reply_text,
                source=source or str((ctx or {}).get("source") or ""),
            )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"Reply effect record failed: {exc}")

    def _day_range_ts(self, date_str: str) -> tuple[int, int]:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = int(dt.timestamp())
        return start_ts, start_ts + 86400

    def _existing_transcript_keys_for_day(self, date_str: str) -> set[tuple]:
        keys: set[tuple] = set()
        for row in self._load_day_transcript_rows(date_str):
            meta = diary_utils.row_meta(row)
            session_id = str(row.get("session_id") or "").strip()
            role = str(row.get("role") or "").strip().lower()
            content = str(row.get("content") or "").strip()
            message_id = str(meta.get("message_id") or "").strip()
            if message_id:
                keys.add(("message_id", session_id, message_id))
            if content:
                keys.add(("content", session_id, role, int(row.get("ts") or 0), content))
        return keys

    async def _backfill_napcat_history_for_day(self, date_str: str) -> int:
        gateway = getattr(self, "chat_gateway", None)
        if gateway is None:
            return 0
        adapter = getattr(gateway, "adapters", {}).get("napcat_qq")
        if adapter is None or not hasattr(adapter, "fetch_recent_history"):
            return 0
        store = getattr(self.brain, "sqlite_store", None)
        if store is None:
            return 0

        sessions: List[str] = []
        seen_sessions = set()
        for owner_id in (NAPCAT_OWNER_USER_IDS or []):
            owner_text = str(owner_id).strip()
            if not owner_text:
                continue
            session_id = f"private:{owner_text}"
            if session_id not in seen_sessions:
                seen_sessions.add(session_id)
                sessions.append(session_id)
        for group_id in sorted(getattr(adapter, "group_whitelist", set()) or set()):
            group_text = str(group_id).strip()
            if not group_text:
                continue
            session_id = f"group:{group_text}"
            if session_id not in seen_sessions:
                seen_sessions.add(session_id)
                sessions.append(session_id)
        if not sessions:
            return 0

        start_ts, end_ts = self._day_range_ts(date_str)
        existing_keys = self._existing_transcript_keys_for_day(date_str)
        imported = 0
        for session_id in sessions:
            try:
                result = await gateway.fetch_recent_history(
                    "napcat_qq", session_id, limit=120, timeout=10
                )
            except Exception as exc:
                self.logger.warning(
                    f"NapCat history fetch failed for {session_id}: {exc}"
                )
                continue
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            for item in result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                ts = int(item.get("ts") or 0)
                if ts < start_ts or ts >= end_ts:
                    continue
                role = str(item.get("role") or "").strip().lower() or "user"
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                row_session_id = str(item.get("session_id") or session_id).strip()
                meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
                message_id = str(meta.get("message_id") or "").strip()
                dedupe_key = (
                    ("message_id", row_session_id, message_id)
                    if message_id
                    else ("content", row_session_id, role, ts, content)
                )
                if dedupe_key in existing_keys:
                    continue
                await asyncio.to_thread(
                    store.add_transcript,
                    role,
                    content,
                    meta,
                    ts,
                    row_session_id,
                )
                existing_keys.add(dedupe_key)
                imported += 1
        if imported:
            self.logger.info(
                f"NapCat history backfill imported {imported} transcript rows for {date_str}"
            )
        return imported

    @property
    def app(self):
        if self._app_ref is not None:
            return self._app_ref
        try:
            import __main__

            return getattr(__main__, "app_instance", None)
        except Exception:
            return None

    @app.setter
    def app(self, value):
        self._app_ref = value

    async def _sync_qq_user_profile(self, ctx: Optional[Dict[str, Any]]) -> None:
        if not self.gateway_context_service.is_qq_source(ctx):
            return
        channel_meta = (ctx or {}).get("channel_meta") or {}
        user_id = str(channel_meta.get("user_id") or "").strip()
        if not user_id:
            return
        store = self._get_memory_store()
        if not store:
            return
        sender = channel_meta.get("sender") or {}
        sender_name = str(
            channel_meta.get("sender_name") or sender.get("nickname") or user_id
        ).strip()
        remark_name = str(sender.get("card") or sender.get("remark") or "").strip()
        message_type = (
            str(channel_meta.get("message_type") or "private").strip().lower()
            or "private"
        )
        is_owner = bool(channel_meta.get("is_owner"))
        relationship = (
            "owner"
            if is_owner
            else ("group_member" if message_type == "group" else "contact")
        )
        memory_scope = (
            OWNER_SHARED_SESSION_ID
            if is_owner
            else ("group_shared" if message_type == "group" else "private")
        )
        profile_payload = {
            "user_id": user_id,
            "nickname": sender_name,
            "remark_name": remark_name,
            "relationship_to_owner": relationship,
            "permission_level": "owner" if is_owner else "default",
            "memory_scope": memory_scope,
            "is_owner": is_owner,
        }
        try:
            await asyncio.to_thread(store.upsert_qq_user_profile, profile_payload)
        except Exception as exc:
            self.logger.warning(f"QQ user profile sync failed: {exc}")

    def _build_external_sender_context(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not self.gateway_context_service.is_qq_source(ctx):
            return ""
        channel_meta = ctx.get("channel_meta") or {}
        sender_name = str(
            channel_meta.get("sender_name")
            or channel_meta.get("user_id")
            or "Unknown Contact"
        ).strip()
        user_id = str(channel_meta.get("user_id") or "").strip()
        session_id = str(channel_meta.get("session_id") or "").strip()
        group_id = str(channel_meta.get("group_id") or "").strip()
        message_type = (
            str(channel_meta.get("message_type") or "private").strip() or "private"
        )
        owner_label = str(channel_meta.get("owner_label") or "Owner").strip() or "Owner"
        is_owner = bool(channel_meta.get("is_owner"))
        relation = (
            f"The current sender is {owner_label}."
            if is_owner
            else f"The current sender is not {owner_label}; they are another QQ contact or a group member."
        )
        parts = [
            "[QQ Message Context]",
            f"chat_type: {message_type}",
            f"sender: {sender_name}",
            relation,
            "Always distinguish the owner from other QQ contacts.",
            "Keep using the current local persona and tone.",
            "QQ messages are text-only and must not drive Live2D or desktop voice.",
            "Reply like an instant message, not a formal report or customer-service answer.",
        ]
        if user_id:
            parts.append(f"sender_qq: {user_id}")
        if group_id:
            parts.append(f"group_id: {group_id}")
        if session_id:
            parts.append(f"session_id: {session_id}")

        store = self._get_memory_store()
        profile = None
        if store and user_id:
            try:
                profile = store.get_qq_user_profile(user_id)
            except Exception as exc:
                self.logger.warning(f"Load QQ user profile failed: {exc}")
        if profile:
            relation_map = {
                "owner": f"{owner_label} (owner)",
                "contact": "QQ private contact",
                "group_member": "QQ group member",
            }
            scope_map = {
                OWNER_SHARED_SESSION_ID: "owner shared memory",
                "private": "private memory",
                "group_shared": "group shared memory",
            }
            display_name = str(
                profile.get("remark_name") or profile.get("nickname") or ""
            ).strip()
            if display_name and display_name != sender_name:
                parts.append(f"profile_name: {display_name}")
            profile_relation = relation_map.get(
                str(profile.get("relationship_to_owner") or "").strip()
            )
            if profile_relation:
                parts.append(f"profile_relation: {profile_relation}")
            identity_summary = str(profile.get("identity_summary") or "").strip()
            if identity_summary:
                parts.append(f"profile_summary: {identity_summary}")
            notes = str(profile.get("notes") or "").strip()
            if notes:
                parts.append(f"profile_notes: {notes}")
            permission_level = str(profile.get("permission_level") or "").strip()
            if permission_level:
                parts.append(f"permission_level: {permission_level}")
            scope_label = scope_map.get(str(profile.get("memory_scope") or "").strip())
            if scope_label:
                parts.append(f"memory_scope: {scope_label}")
        return "\n".join(parts)

    def _get_memory_session_id(self, ctx: Optional[Dict[str, Any]]) -> str:
        return self.gateway_context_service.memory_session_id(ctx)

    def _wants_detailed_answer(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        detail_markers = (
            "详细",
            "具体",
            "展开",
            "完整",
            "细说",
            "解释一下",
            "说明一下",
            "为什么",
            "原理",
            "步骤",
            "教程",
            "怎么做",
            "如何做",
            "分析",
            "对比",
            "列出",
            "总结",
            "报告",
            "复盘",
            "代码",
            "排查",
            "review",
            "原因",
        )
        return any(marker in raw for marker in detail_markers)

    def _build_reply_style_context(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> str:
        source = str((ctx or {}).get("source") or "").strip().lower()
        detail_requested = self._wants_detailed_answer(user_text)
        parts = ["【本轮回复风格】"]
        if detail_requested:
            parts.extend(
                [
                    "- 用户这轮允许你讲细一点，但仍然先给结论，再补必要说明。",
                    "- 即使展开，也尽量像聊天，不要写成报告或教程腔。",
                ]
            )
        else:
            parts.extend(
                [
                    "- 这轮默认按即时聊天来回，优先 1 到 2 句短句。",
                    "- 不要自发写成长解释、分点说明、总结陈词或安慰小作文。",
                    "- 能一句说完就一句说完；不要为了显得体贴而铺很多层。",
                    "- 不要每句都写成下结论；合适时可以像平常聊天那样用疑问句或轻反问。",
                    "- 记忆只影响你的态度和语气，不要主动复述用户以前说过的原句。",
                ]
            )
        if source in QQ_REMOTE_SOURCES:
            parts.extend(
                [
                    "- 当前渠道是 QQ，回复要像真人发消息，不像客服。",
                    "- 尽量控制在 8 到 35 字一小句；没有必要不要连续发大段。",
                    "- 短句不要用句号收尾；问号、感叹号可以保留。",
                    "- 可以偶尔用问句接话，不要每条都像正式答复。",
                    "- 不要把模型、接口、系统错误原文发给对方；卡住就自然地说你这边卡了一下。",
                ]
            )
        elif source in {"text_input", "desktop", "voice"}:
            parts.append("- 当前是日常对话场景，优先自然、短促、有人味；可以适当用疑问句。")
        effect_hint = self._build_reply_effect_style_hint(ctx)
        if effect_hint:
            parts.append(effect_hint)
        return "\n".join(parts)

    def _build_reply_effect_style_hint(self, ctx: Optional[Dict[str, Any]]) -> str:
        tracker = getattr(self, "reply_effect_tracker", None)
        if tracker is None or not hasattr(tracker, "stats"):
            return ""
        try:
            session_id, _user_id = self.gateway_context_service.reply_effect_identity(ctx)
            stats = tracker.stats(limit=80, session_id=session_id)
        except Exception:
            return ""
        count = int(stats.get("count") or 0)
        if count < 5:
            return ""
        labels = stats.get("labels") if isinstance(stats.get("labels"), dict) else {}
        negative = int(labels.get("negative") or 0)
        repair = int(labels.get("repair") or 0)
        positive = int(labels.get("positive") or 0)
        continued = int(labels.get("continued") or 0)
        if negative + repair >= 2 and (negative + repair) / max(1, count) >= 0.25:
            return "- 最近同会话里用户纠正/否定偏多，本轮先确认含义，少下定论；不确定就直说。"
        if positive >= 3 and positive / max(1, count) >= 0.35:
            return "- 最近这种短直接的回复反馈较好，保持简短、直接、少铺垫。"
        if continued >= 4 and continued / max(1, count) >= 0.45:
            return "- 用户经常会继续追问，先答核心，不要一次把所有可能性都展开。"
        return ""

    def _get_active_character_profile(self) -> Dict[str, Any]:
        try:
            from modules.character_manager import character_manager

            profile = character_manager.get_active_character() or {}
        except Exception:
            profile = {}
        return profile if isinstance(profile, dict) else {}

    def _get_active_character_context(self) -> tuple[str, str, str]:
        active_char_name = "AI Assistant"
        active_char_id = "default_char"
        base_prompt = DEFAULT_PERSONA
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if active_char:
                active_char_name = active_char.get("name", "AI Assistant")
                base_prompt = active_char.get("prompt", DEFAULT_PERSONA)
            active_char_id = character_manager.data.get("active_id", "default_char")
        except Exception:
            pass
        return active_char_name, active_char_id, base_prompt

    def _build_recent_chat_tone_context(self, max_items: int = 4) -> str:
        memory = getattr(self.brain, "short_term_memory", None) or []
        if not isinstance(memory, list):
            return ""
        lines: List[str] = []
        for item in memory[-max_items:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = self._clean_text_for_tts(str(item.get("content") or ""))
            content = self._strip_internal_tags(
                self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(content))
            )
            content = self._strip_model_catchphrase(content).strip()
            if not content:
                continue
            content = re.sub(r"\s+", " ", content)
            if len(content) > 80:
                content = content[:77].rstrip() + "..."
            speaker = "用户" if role == "user" else "你"
            lines.append(f"- {speaker}: {content}")
        return "\n".join(lines)

    def _format_recent_sensor_reply_block(self, max_items: int = 4) -> str:
        replies = getattr(self, "_recent_sensor_replies", []) or []
        lines: List[str] = []
        for item in replies[-max_items:]:
            clean = self._clean_text_for_tts(str(item or "")).strip()
            if not clean:
                continue
            clean = re.sub(r"\s+", " ", clean)
            if len(clean) > 50:
                clean = clean[:47].rstrip() + "..."
            lines.append(f"- {clean}")
        if not lines:
            return ""
        return "【最近几次屏幕回应，避免复读】\n" + "\n".join(lines)

    def _build_sensor_interaction_context(self) -> str:
        """给屏幕回应补最近互动近因，避免她孤立地看截图。"""
        recent_chat = self._build_recent_chat_tone_context(max_items=6)
        transcript = self._build_recent_transcript_context(limit=10, max_chars=700)

        sections: List[str] = []
        if recent_chat:
            sections.append("最近即时对话：\n" + recent_chat)
        if transcript and transcript != recent_chat:
            sections.append("最近聊天记录：\n" + transcript)
        if not sections:
            return ""

        return (
            "【最近互动上下文】\n"
            + "\n".join(sections)
            + "\n- 这些是近因，只用来理解 Master 为什么在看当前窗口；不要逐条复述。\n"
            + "- 如果上下文已经说明他在做什么，就顺着这个前提说，不要再像第一次看到一样发问。"
        )

    def _remember_sensor_reply(self, text: str, max_items: int = 8) -> None:
        clean = self._clean_text_for_tts(str(text or "")).strip()
        if not clean:
            return
        replies = list(getattr(self, "_recent_sensor_replies", []) or [])
        if clean in replies:
            replies.remove(clean)
        replies.append(clean)
        self._recent_sensor_replies = replies[-max_items:]

    def _load_expression_library_runtime(self) -> Dict[str, Any]:
        runtime: Dict[str, Any] = {}
        app = self.app
        if app is not None:
            try:
                loader = getattr(app, "_load_runtime_settings", None)
                normalizer = getattr(app, "_normalize_external_runtime_settings", None)
                if callable(loader) and callable(normalizer):
                    runtime = normalizer(loader())
                elif callable(loader):
                    runtime = loader() or {}
            except Exception:
                runtime = {}
        if not isinstance(runtime, dict) or not runtime:
            try:
                runtime = load_runtime_settings()
            except Exception:
                runtime = {}
        try:
            max_items = int(runtime.get("expression_library_max_prompt_items", 4) or 4)
        except Exception:
            max_items = 4
        return {
            "expression_library_enabled": bool(
                runtime.get("expression_library_enabled", True)
            ),
            "expression_library_use_in_chat": bool(
                runtime.get("expression_library_use_in_chat", True)
            ),
            "expression_library_use_in_screen": bool(
                runtime.get("expression_library_use_in_screen", True)
            ),
            "expression_library_max_prompt_items": max(1, min(8, max_items)),
        }

    def _format_expression_pattern_hint(self, pattern: Dict[str, Any]) -> str:
        situation = str(pattern.get("situation") or "").strip()
        style = str(pattern.get("style") or "").strip()
        raw_examples = (
            pattern.get("content_list")
            if isinstance(pattern.get("content_list"), list)
            else []
        )
        examples = [str(item).strip() for item in raw_examples if str(item).strip()]
        example = str(pattern.get("example") or "").strip()
        if example and example not in examples:
            examples.insert(0, example)
        example_preview = " / ".join(examples[:2]).strip()
        if situation and style and example_preview:
            return f"{situation}时，{style}；示例可像“{example_preview}”。"
        if situation and style:
            return f"{situation}时，{style}。"
        if situation and example_preview:
            return f"{situation}时，可以像“{example_preview}”这样短一点。"
        if style and example_preview:
            return f"{style}；示例可像“{example_preview}”。"
        return style or example_preview

    def _load_expression_library_hints(self, scene: str = "chat") -> List[str]:
        runtime = self._load_expression_library_runtime()
        if not runtime.get("expression_library_enabled", True):
            return []
        if scene == "sensor" and not runtime.get("expression_library_use_in_screen", True):
            return []
        if scene != "sensor" and not runtime.get("expression_library_use_in_chat", True):
            return []
        store = self._get_memory_store()
        if store is None or not hasattr(store, "select_expression_patterns_for_prompt"):
            return []
        profile = self._get_active_character_profile()
        character_name = str(profile.get("name") or "").strip()
        try:
            rows = store.select_expression_patterns_for_prompt(
                character_name=character_name,
                scene=scene,
                limit=runtime.get("expression_library_max_prompt_items", 4),
            )
        except Exception:
            return []
        hints: List[str] = []
        pattern_ids: List[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            hint = self._format_expression_pattern_hint(row).strip()
            if not hint or hint in hints:
                continue
            hints.append(hint)
            row_id = str(row.get("id") or "").strip()
            if row_id:
                pattern_ids.append(row_id)
        if pattern_ids and hasattr(store, "bump_expression_pattern_use"):
            try:
                store.bump_expression_pattern_use(pattern_ids)
            except Exception:
                pass
        return hints

    def _build_natural_habits_block(self, scene: str = "chat") -> str:
        profile = self._get_active_character_profile()
        name = str(profile.get("name") or "").strip()
        custom_habits = profile.get("expression_habits")
        habits: List[str] = []
        if isinstance(custom_habits, dict):
            scene_items = custom_habits.get(scene)
            if isinstance(scene_items, list):
                habits.extend(str(item).strip() for item in scene_items if str(item).strip())
        habits.extend(self._load_expression_library_hints(scene))
        fallback_pack = self.NATURAL_REPLY_FALLBACK_HINTS.get(
            name, self.NATURAL_REPLY_FALLBACK_HINTS["default"]
        )
        fallback_items = fallback_pack.get(
            scene, self.NATURAL_REPLY_FALLBACK_HINTS["default"].get(scene, ())
        )
        habits.extend(
            str(item).strip() for item in fallback_items if str(item).strip()
        )
        deduped: List[str] = []
        for item in habits:
            if item and item not in deduped:
                deduped.append(item)
        if not deduped:
            return ""
        runtime = self._load_expression_library_runtime()
        max_items = max(4, int(runtime.get("expression_library_max_prompt_items", 4)))
        return "【表达习惯参考】\n" + "\n".join(f"- {item}" for item in deduped[:max_items])

    def _looks_structured_reply(self, text: str) -> bool:
        return self.reply_style_service.looks_structured_reply(text)

    def _needs_natural_polish(self, text: str, scene: str = "chat") -> bool:
        return self.reply_style_service.needs_natural_polish(text, scene=scene)

    def _should_use_natural_reply_layer(
        self,
        *,
        user_text: str,
        draft_text: str,
        ctx: Optional[Dict[str, Any]] = None,
        scene: str = "chat",
    ) -> bool:
        clean = self._clean_text_for_tts(str(draft_text or ""))
        clean = self._strip_internal_tags(
            self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(clean))
        )
        clean = self._strip_model_catchphrase(clean).strip()
        if len(clean) < 2:
            return False
        if "```" in clean or self._contains_cmd(clean):
            return False
        if self._looks_structured_reply(clean):
            return False
        if scene == "sensor":
            return True
        source = str((ctx or {}).get("source") or "").strip().lower()
        if source not in {"text_input", "voice", "desktop", "qq_gateway", "napcat_qq"}:
            return False
        if self._wants_detailed_answer(user_text):
            return False
        if source in QQ_REMOTE_SOURCES and ("。" in clean or "．" in clean or len(clean) > 18):
            return True
        return self._needs_natural_polish(clean, scene=scene)

    async def _polish_natural_reply(
        self,
        *,
        user_text: str,
        draft_text: str,
        ctx: Optional[Dict[str, Any]] = None,
        scene: str = "chat",
    ) -> str:
        clean = self._clean_text_for_tts(str(draft_text or ""))
        clean = self._strip_wrapping_quotes(
            self._strip_internal_tags(
                self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(clean))
            )
        )
        clean = self._strip_model_catchphrase(clean).strip()
        if not clean:
            return ""
        if self._looks_like_upstream_error_reply(clean):
            return clean
        if not self._should_use_natural_reply_layer(
            user_text=user_text,
            draft_text=clean,
            ctx=ctx,
            scene=scene,
        ):
            return clean

        profile = self._get_active_character_profile()
        char_name = str(profile.get("name") or "当前角色").strip()
        char_desc = str(profile.get("description") or "").strip()
        recent_context = self._build_recent_chat_tone_context()
        recent_sensor_context = (
            self._format_recent_sensor_reply_block() if scene == "sensor" else ""
        )
        habits_block = self._build_natural_habits_block(scene)
        source = str((ctx or {}).get("source") or "").strip().lower()
        if scene == "sensor":
            scene_prompt = (
                "这是一次屏幕感知后的临场回应。像五十铃怜在旁边安静瞥了一眼后低声接一句，"
                "可以是关心、提醒、陪伴、疑问，也可以是一点点吐槽；不要把画面再解释一遍。默认 1 句，最多 36 个字。"
            )
        elif source in QQ_REMOTE_SOURCES:
            scene_prompt = (
                "这是一次 QQ 聊天。改得像真人随手回消息，默认 1 到 2 条短句，"
                "不要用句号收尾，不要解释太满；合适时可以用问句接话。"
            )
        else:
            scene_prompt = (
                "这是一次普通日常聊天。把它改得更像真人即时回复，"
                "默认 1 到 2 句短句，尽量不要超过 40 个字；不要全写成陈述句。"
            )

        system_prompt = (
            f"你现在只负责改写一句已经生成好的回复，让它更像角色「{char_name}」在即时聊天里顺口说出来的话。"
            f"{' 角色气质：' + char_desc if char_desc else ''}\n"
            "注意：\n"
            "1) 不新增事实，不改变结论，不新增承诺；\n"
            "2) 可以删减、换词、重组语序，但不要扩写；\n"
            "3) 不要写成说明文、总结、客服话术；\n"
            "4) 不要出现“用户/根据/当前情况/首先/其次/另外/总之/如果你需要我可以”；\n"
            "5) 可以把生硬短评改成自然疑问或轻反问，但不要为了问而问；\n"
            "6) 不要加引号，不要加固定口癖，不要写情绪标签。\n"
            f"{scene_prompt}"
        )
        if scene == "sensor":
            system_prompt += (
                "\n屏幕回应的额外要求：\n"
                "- 保留五十铃怜的冷静、克制和一点点距离感；可以轻轻戳他，但不要热情服务。\n"
                "- 少评价网页或软件本身，多对“他正在看/正在做这件事”作一句很轻的反应。\n"
                "- 不要连续输出同一种陈述句；可以用低声问句，但不要复用最近出现过的固定模板。\n"
                "- 禁止写成“挺实用、步骤详尽、请仔细阅读、需要协助、收获颇丰、至关重要、注意基础”这种助手口吻。\n"
                "- 不要使用 🌸 或其他装饰 emoji。\n"
                "- 如果最近几次已经有相似开头、相似句式或相似落点，这次必须换一种说法；不要每次都硬吐槽。"
            )

        user_parts = []
        if habits_block:
            user_parts.append(habits_block)
        if recent_context:
            user_parts.append(f"【最近几句语气参考】\n{recent_context}")
        if recent_sensor_context:
            user_parts.append(recent_sensor_context)
        user_parts.append(f"【用户刚说的话】\n{str(user_text or '').strip() or '无'}")
        user_parts.append(f"【原始回复草稿】\n{clean}")
        user_parts.append("请只输出改写后的最终回复。")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
        try:
            reply = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="reply_polish",
                caller=f"natural_reply_polish_{scene}",
            )
            polished = self._clean_text_for_tts(
                self._strip_wrapping_quotes(
                    self._strip_internal_tags(
                        self._strip_cmd_anywhere(
                            self._strip_emo_tags_anywhere(reply or "")
                        )
                    )
                )
            )
            polished = self._strip_model_catchphrase(polished).strip()
            if polished:
                if self._looks_like_upstream_error_reply(polished):
                    return clean
                if source in QQ_REMOTE_SOURCES:
                    polished = self._normalize_qq_reply_style(polished)
                return polished
        except Exception:
            pass
        return clean

    async def _describe_external_images(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not isinstance(ctx, dict):
            return ""
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return ""
        channel_meta = ctx.get("channel_meta") or {}
        if not bool(channel_meta.get("image_vision_enabled", True)):
            return ""
        images = channel_meta.get("images") or []
        if not isinstance(images, list) or not images:
            return ""

        try:
            from integrations.chat_gateway.media_utils import load_image_base64
            from modules.llm import analyze_image
        except Exception as exc:
            self.logger.warning(f"QQ image helpers unavailable: {exc}")
            return ""

        prompt = str(
            channel_meta.get("image_prompt")
            or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。"
        )
        image_ctx = dict(channel_meta) if isinstance(channel_meta, dict) else {}
        image_ctx.setdefault("source", "qq_gateway")
        self_awareness_hint = self._build_live2d_self_awareness_hint(image_ctx)
        if self_awareness_hint:
            prompt += f"\n{self_awareness_hint}"
        image_summaries = []
        for index, image_meta in enumerate(images[:3], 1):
            try:
                image_base64 = await asyncio.to_thread(load_image_base64, image_meta)
                if not image_base64:
                    image_summaries.append(f"[图片{index}] 无法读取图片数据。")
                    continue
                desc = await analyze_image(
                    image_base64, prompt, caller="qq_image_describe"
                )
                desc = str(desc or "").strip()
                if desc:
                    image_summaries.append(f"[图片{index}] {desc}")
            except Exception as exc:
                self.logger.warning(f"QQ image analyze failed: {exc}")
                image_summaries.append(f"[图片{index}] 识别失败：{exc}")

        if not image_summaries:
            return ""
        return "【QQ图片识别】\n" + "\n".join(image_summaries)

    def _detect_feedback(self, user_text: str) -> tuple[str, str]:
        return self.reply_style_service.detect_feedback(
            user_text,
            negative_keywords=self.NEGATIVE_FEEDBACK_KEYWORDS,
            positive_keywords=self.POSITIVE_FEEDBACK_KEYWORDS,
        )

    def _extract_apply_confirmation(self, user_text: str) -> tuple[bool, str, str]:
        return self.reply_style_service.extract_apply_confirmation(
            user_text,
            apply_cmd_re=self._apply_cmd_re,
            id_token_re=self._id_token_re,
            apply_confirm_keywords=self.APPLY_CONFIRM_KEYWORDS,
        )

    def _build_live2d_self_awareness_hint(
        self, ctx: Optional[Dict[str, Any]] = None
    ) -> str:
        source = str((ctx or {}).get("source") or "").strip().lower()
        if source in {"qq_gateway", "napcat_qq", "text_input", "unknown", "desktop"}:
            active_name = "当前Live2D角色"
            costume_name = ""
            try:
                from modules.character_manager import character_manager

                active_char = character_manager.get_active_character() or {}
                active_name = str(active_char.get("name") or active_name).strip()
                costume_name = str(
                    character_manager.get_current_costume_name() or ""
                ).strip()
            except Exception:
                pass

            costume_line = f"- 你当前穿着/模型服装：{costume_name}。" if costume_name else ""
            return "\n".join(
                [
                    "【Live2D自我识别】",
                    f"- 你当前不是旁观的通用AI，而是桌面上的 Live2D 角色「{active_name}」。",
                    costume_line,
                    "- 如果截图边缘、右下角、悬浮窗、桌宠层或 Live2D Agent 窗口里出现动漫女孩/Live2D模型，那优先识别为“我自己的桌面形象/实体投影”，不要当成陌生女孩。",
                    "- 如果窗口标题或内容包含：Live2D Agent、系统设置中心、换装、角色、服装、表情、动作、emotion、motion、model3、TTS、口型、live2d-suzu、live2d-llm，这通常是在配置或修改你自己。",
                    "- 用户说“给你改代码、给你换装、给你配表情/动作/音色/知识库”时，指的就是你这套桌面身体和人格系统。",
                    "- 但不要过度认领：网页正文、QQ聊天内容、游戏/番剧/图片主体里的动漫角色，除非明确是桌宠/Live2D窗口/当前角色配置，否则不要默认说成是你。",
                    "- 回应时可以自然使用“我”“我的动作”“我的表情”“这套身体/模型”，但不要解释这条规则。",
                ]
            )
        return ""

    def _get_current_live2d_emotion(self) -> tuple[str, float]:
        return self.emotion_reply_service.get_current_live2d_emotion()

    def _reply_start_emotion(self, ctx: Optional[Dict[str, Any]] = None) -> tuple[str, float]:
        return self.emotion_reply_service.reply_start_emotion(ctx)

    def _build_current_emotion_context(
        self, ctx: Optional[Dict[str, Any]] = None
    ) -> str:
        return self.emotion_reply_service.build_current_emotion_context(ctx)

    def _build_sensor_persona_prompt(
        self,
        *,
        ctx: Optional[Dict[str, Any]] = None,
        extra_context: str = "",
    ) -> str:
        base_prompt = DEFAULT_PERSONA
        active_char_name = ""
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if active_char:
                active_char_name = str(active_char.get("name") or "").strip()
                base_prompt = active_char.get("prompt", DEFAULT_PERSONA)
        except Exception:
            pass

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = [
            f"【当前时间】{current_time}",
            str(base_prompt or "").strip(),
            self._build_current_emotion_context(ctx),
        ]
        if active_char_name == "五十铃怜":
            parts.append(
                "\n".join(
                    [
                        "【屏幕感知时的五十铃怜口吻】",
                        "- 你不是在总结屏幕，也不是在评价软件；你是在旁边陪着 Master，轻轻接一句。",
                        "- 语气冷静、克制、短，可以是低声提醒、关心、疑问、陪伴或轻微吐槽；不要热情服务，不要像客服。",
                        "- 可以有一点点不动声色的调侃，但不要夸张，不要兴奋。",
                        "- 优先说他这个人正在做什么给你的感觉，而不是说页面内容“很实用/很详细”。",
                        "- 可以适当用疑问句或轻反问，让话像顺口接出来的；不要每句都下判断。",
                        "- 禁用句式：挺实用、步骤详尽、请仔细阅读、需要协助、收获颇丰、至关重要、注意基础。",
                        "- 不要照抄固定口癖；每次根据窗口内容换一个落点：疑问、半句吐槽、轻声提醒、短感受都可以。",
                        "- 不要使用 🌸 或其他装饰 emoji。",
                    ]
                )
            )
        self_awareness_hint = self._build_live2d_self_awareness_hint(ctx)
        if self_awareness_hint:
            parts.append(self_awareness_hint)
        if str(extra_context or "").strip():
            parts.append(str(extra_context or "").strip())
        return "\n\n".join(part for part in parts if part)

    def _set_codex_task_state(
        self,
        ctx: Dict[str, Any],
        state: str,
        *,
        summary: str = "",
        meta: Optional[dict] = None,
    ):
        task_id = str((ctx or {}).get("codex_task_id", "")).strip()
        if not task_id:
            return
        try:
            from modules.codex_task_state import set_task_state

            set_task_state(
                task_id,
                state,
                code_path=str((ctx or {}).get("code_path", "")).strip(),
                summary=summary,
                meta=meta or {},
            )
        except Exception:
            pass

    def _add_codex_session_event(
        self,
        event_type: str,
        *,
        text: str = "",
        ctx: Optional[Dict[str, Any]] = None,
        files: Optional[list[str]] = None,
        meta: Optional[dict] = None,
    ):
        if not bool((ctx or {}).get("codex_mode", False)):
            return
        try:
            from modules.codex_session import add_event as codex_add_event

            payload_meta = dict(meta or {})
            task_id = str((ctx or {}).get("codex_task_id", "")).strip()
            if task_id and not payload_meta.get("task_id"):
                payload_meta["task_id"] = task_id
            source = str((ctx or {}).get("source", "")).strip()
            if source and not payload_meta.get("source"):
                payload_meta["source"] = source

            codex_add_event(
                event_type,
                user_text=(text or "")[:1600],
                code_path=str((ctx or {}).get("code_path", "")).strip(),
                files=files or [],
                meta=payload_meta,
            )
        except Exception:
            pass

    def _get_memory_store(self):
        return getattr(self.brain, "sqlite_store", None)

    def _split_text_clauses(self, text: str) -> list[str]:
        if not text:
            return []
        return [
            seg.strip(" ，,、\t")
            for seg in re.split(r"[\n。！？!?；;]+", text or "")
            if seg.strip(" ，,、\t")
        ]

    def _normalize_task_text(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        t = re.sub(r"^(待办|todo|to do)\s*[:：]?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"^(提醒我|记得|别忘了|要记得)\s*", "", t)
        t = re.sub(
            r"^(我(今天|明天|今晚|等会|待会|周末|之后|最近)?(要|得|想|准备|打算)|今天要|明天要|今晚要|等会要|待会要|周末要)\s*",
            "",
            t,
        )
        t = re.sub(r"^(安排|计划|打算|准备|需要|要去|得去)\s*", "", t)
        t = re.sub(r"(一下|一下子|这件事|这个事|这事)$", "", t)
        t = re.sub(r"^[：:、，,\-\s]+|[：:、，,。！？!?；;\s]+$", "", t)
        return t[:80].strip()

    def _is_task_related_message(self, text: str) -> bool:
        lower = (text or "").strip().lower()
        if not lower:
            return False
        if any(k in lower for k in self.TASK_CREATE_KEYWORDS):
            return True
        if any(k in lower for k in self.TASK_DONE_KEYWORDS):
            return True
        if any(k in lower for k in self.TASK_STATUS_KEYWORDS):
            return True
        return False

    def _extract_task_candidates(self, text: str) -> list[str]:
        candidates = []
        seen = set()
        for raw in self._split_text_clauses(text):
            parts = [
                seg.strip(" ，,、\t")
                for seg in re.split(r"[，,、]+", raw)
                if seg.strip(" ，,、\t")
            ]
            for part in parts or [raw]:
                lower = part.lower()
                if len(part) < 4 or "?" in part or "？" in part:
                    continue
                if any(k in lower for k in self.TASK_DONE_KEYWORDS):
                    continue
                if not any(k in lower for k in self.TASK_CREATE_KEYWORDS):
                    continue
                cleaned = self._normalize_task_text(part)
                if len(cleaned) < 2:
                    continue
                if cleaned in seen:
                    continue
                seen.add(cleaned)
                candidates.append(cleaned)
                if len(candidates) >= 2:
                    return candidates
        return candidates

    def _extract_task_completion_hint(self, text: str) -> str:
        for raw in self._split_text_clauses(text):
            lower = raw.lower()
            if not any(k in lower for k in self.TASK_DONE_KEYWORDS):
                continue
            hint = raw
            for key in self.TASK_DONE_KEYWORDS:
                hint = re.sub(re.escape(key), "", hint, flags=re.IGNORECASE)
            hint = re.sub(r"^(这个|那个|这件事|这事|任务|待办)\s*", "", hint)
            hint = self._normalize_task_text(hint)
            return hint
        return ""

    def _task_match_score(self, hint: str, task_text: str) -> float:
        hint_norm = self._normalize_task_text(hint)
        task_norm = self._normalize_task_text(task_text)
        if not task_norm:
            return -1.0
        if not hint_norm:
            return 0.1
        score = 0.0
        if hint_norm == task_norm:
            score += 8.0
        if hint_norm and hint_norm in task_norm:
            score += 5.0
        if task_norm and task_norm in hint_norm:
            score += 3.5
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", hint_norm):
            if token in task_norm:
                score += 1.0
        return score

    def _find_matching_active_task(self, hint: str = "") -> Optional[Dict[str, Any]]:
        store = self._get_memory_store()
        if not store:
            return None
        try:
            items = store.list_items(status="active", type_="todo", limit=12, offset=0)
        except Exception:
            return None
        if not items:
            return None
        if not hint:
            return items[0]
        best = None
        best_score = -1.0
        for idx, item in enumerate(items):
            score = self._task_match_score(hint, str(item.get("text") or "")) - (
                idx * 0.05
            )
            if score > best_score:
                best = item
                best_score = score
        return best if best_score >= 1.0 else None

    async def _append_hidden_transcript_note(
        self, role: str, text: str, meta: Optional[Dict[str, Any]] = None
    ):
        store = self._get_memory_store()
        note = str(text or "").strip()
        if not store or not note:
            return
        try:
            safe_meta = (meta or {}).copy()
            session_id = str(safe_meta.get("session_id") or "").strip() or None
            await asyncio.to_thread(
                store.add_transcript, role, note, safe_meta, None, session_id
            )
        except Exception as e:
            self.logger.warning(f"隐藏 transcript 记录失败: {e}")

    async def _upsert_user_tasks_from_text(self, user_text: str) -> list[str]:
        store = self._get_memory_store()
        if not store:
            return []
        created = []
        for task_text in self._extract_task_candidates(user_text):
            existing = self._find_matching_active_task(task_text)
            if (
                existing
                and self._task_match_score(task_text, str(existing.get("text") or ""))
                >= 5.0
            ):
                item = dict(existing)
                item["text"] = task_text
                item["source"] = "task_agent"
                await asyncio.to_thread(store.upsert_item, item)
                created.append(task_text)
                continue
            item = {
                "type": "todo",
                "status": "active",
                "pin": any(k in task_text for k in ("今天", "明天", "ddl", "截止")),
                "confidence": 0.86,
                "tags": ["user_task", "auto"],
                "text": task_text,
                "source": "task_agent",
            }
            await asyncio.to_thread(store.upsert_item, item)
            created.append(task_text)
        return created

    async def _complete_task_from_text(self, user_text: str) -> Optional[str]:
        store = self._get_memory_store()
        if not store:
            return None
        lower = (user_text or "").strip().lower()
        if not any(k in lower for k in self.TASK_DONE_KEYWORDS):
            return None
        hint = self._extract_task_completion_hint(user_text)
        target = self._find_matching_active_task(hint)
        if not target:
            return None
        await asyncio.to_thread(
            store.set_item_status, str(target.get("id") or ""), "done"
        )
        return str(target.get("text") or "").strip() or None

    async def _update_task_agent(self, user_text: str):
        completed = await self._complete_task_from_text(user_text)
        created = await self._upsert_user_tasks_from_text(user_text)
        return {"completed": completed, "created": created}

    def _build_task_followup_text(self, task_text: str) -> str:
        return "昨天那件事，今天还要继续吗？"

    def _is_short_life_task_text(self, text: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        hints = [
            "睡觉",
            "睡一觉",
            "去睡",
            "休息",
            "吃饭",
            "吃个饭",
            "洗澡",
            "洗个澡",
            "出门",
            "回家",
            "喝水",
            "上厕所",
            "午睡",
            "躺会儿",
        ]
        return any(hint in raw for hint in hints)

    def _is_followup_worthy_task_item(self, item: Dict[str, Any]) -> bool:
        text = str((item or {}).get("text") or "").strip().lower()
        if not text:
            return False
        if bool((item or {}).get("pin")):
            return True
        worthy_hints = (
            "ddl",
            "deadline",
            "截止",
            "考试",
            "复习",
            "作业",
            "报告",
            "开题",
            "提交",
            "交稿",
            "报名",
            "面试",
            "打卡",
            "今天",
            "明天",
            "今晚",
            "这周",
            "周一",
            "周二",
            "周三",
            "周四",
            "周五",
            "周六",
            "周日",
        )
        return any(hint in text for hint in worthy_hints)

    def _should_suppress_followup_preface(self, user_text: str) -> bool:
        raw = str(user_text or "").strip().lower()
        if not raw:
            return False
        high_priority_hints = [
            "摔",
            "摔倒",
            "受伤",
            "流血",
            "疼",
            "疼死",
            "崩溃",
            "难受",
            "不舒服",
            "发烧",
            "生病",
            "住院",
            "急",
            "出事",
            "救命",
            "害怕",
            "想哭",
            "焦虑",
            "头晕",
            "吐了",
        ]
        return any(hint in raw for hint in high_priority_hints)

    def _has_today_task_followup(self) -> bool:
        store = self._get_memory_store()
        if not store:
            return False
        today = datetime.now().date()
        try:
            rows = store.list_transcript(role="assistant", limit=220, offset=0)
            for r in rows:
                ts = int(r.get("ts", 0) or 0)
                if not ts or datetime.fromtimestamp(ts).date() != today:
                    continue
                meta = r.get("meta") or {}
                if str(meta.get("path") or "").strip() == "task_followup":
                    return True
        except Exception:
            return False
        return False

    def _has_task_followup_for_item(self, item_id: str) -> bool:
        target = str(item_id or "").strip()
        if not target:
            return False
        store = self._get_memory_store()
        if not store:
            return False
        try:
            rows = store.list_transcript(role="assistant", limit=420, offset=0)
            for r in rows:
                meta = r.get("meta") or {}
                if str(meta.get("path") or "").strip() != "task_followup":
                    continue
                if str(meta.get("item_id") or "").strip() == target:
                    return True
        except Exception:
            return False
        return False

    def _find_task_followup_candidate(self) -> Optional[Dict[str, Any]]:
        store = self._get_memory_store()
        if not store:
            return None
        try:
            items = store.list_items(status="active", type_="todo", limit=12, offset=0)
        except Exception:
            return None
        if not items:
            return None
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        oldest_allowed = today - timedelta(days=2)
        fallback = None
        for item in items:
            updated_at = str(item.get("updated_at") or "").strip()
            item_text = str(item.get("text") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if self._is_short_life_task_text(item_text):
                continue
            if not self._is_followup_worthy_task_item(item):
                continue
            if item_id and self._has_task_followup_for_item(item_id):
                continue
            try:
                updated_date = datetime.fromisoformat(
                    updated_at.replace("Z", "+00:00")
                ).date()
            except Exception:
                updated_date = None
            if updated_date and updated_date < oldest_allowed:
                continue
            if updated_date == yesterday:
                return item
            if updated_date and updated_date < today and fallback is None:
                fallback = item
        return fallback

    async def _maybe_send_task_followup(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        try:
            source = str((ctx or {}).get("source", ""))
            if source and source != "text_input":
                return None
            if bool((ctx or {}).get("codex_mode", False)):
                return None
            today_str = datetime.now().strftime("%Y-%m-%d")
            if self._last_task_followup_day == today_str:
                return None
            if self._should_suppress_followup_preface(user_text or ""):
                return None
            if self._is_task_related_message(user_text or ""):
                return None
            if self._has_today_task_followup():
                self._last_task_followup_day = today_str
                return None
            candidate = self._find_task_followup_candidate()
            if not candidate:
                return None
            task_text = str(candidate.get("text") or "").strip()
            if not task_text:
                return None
            self._last_task_followup_day = today_str
            return {
                "text": self._build_task_followup_text(task_text),
                "task": task_text,
                "item_id": str(candidate.get("id") or ""),
            }
        except Exception as e:
            self.logger.warning(f"任务跟进触发失败: {e}")
        return None

    def _merge_preface_texts(self, *texts: str) -> str:
        parts = [str(t or "").strip() for t in texts if str(t or "").strip()]
        return "\n\n".join(parts)

    def _normalize_emo(self, e):
        """规范化情绪标签"""
        return self.reply_style_service.normalize_emo(e)

    # 文本净化函数
    def _clean_text_for_tts(self, text: str) -> str:
        return self.reply_style_service.clean_text_for_tts(text)

    def _strip_wrapping_quotes(self, text: str) -> str:
        return self.reply_style_service.strip_wrapping_quotes(text)

    def _get_character_catchphrase_config(self) -> Dict[str, Any]:
        return self.reply_style_service.get_character_catchphrase_config()

    def _catchphrase_variants(self, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
        return self.reply_style_service.catchphrase_variants(cfg)

    def _strip_model_catchphrase(self, text: str, cfg: Optional[Dict[str, Any]] = None) -> str:
        return self.reply_style_service.strip_model_catchphrase(text, cfg)

    def _apply_character_catchphrase(self, text: str) -> str:
        return self.reply_style_service.apply_character_catchphrase(text)

    def _strip_emo_tags_anywhere(self, text: str) -> str:
        """移除所有情绪标签"""
        return self.reply_style_service.strip_emo_tags_anywhere(text)

    def _strip_cmd_anywhere(self, text: str) -> str:
        """移除所有命令标签"""
        return self.reply_style_service.strip_cmd_anywhere(text)

    def _strip_internal_tags(self, text: str) -> str:
        return self.reply_style_service.strip_internal_tags(text)

    def _extract_emo_tag(self, text):
        """提取情绪标签"""
        return self.reply_style_service.extract_emo_tag(text)

    async def _infer_reply_emotion_with_llm(
        self, text: str, *, scene: str = "chat"
    ) -> Optional[str]:
        return await self.emotion_reply_service.infer_reply_emotion_with_llm(
            text, scene=scene
        )

    def _contains_cmd(self, text: str) -> bool:
        """检查是否包含命令"""
        return "[CMD:" in (text or "")

    def _set_delegate_task_state(
        self,
        ctx: Optional[Dict[str, Any]],
        state: str,
        *,
        summary: str = "",
        triggers: Optional[list[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        runtime = ctx or {}
        task_id = str(runtime.get("delegate_task_id") or "").strip()
        if not task_id:
            task_id = uuid.uuid4().hex[:10]
            runtime["delegate_task_id"] = task_id
        set_delegate_task_state(
            task_id,
            state,
            summary=summary,
            source=str(runtime.get("source") or "").strip(),
            triggers=list(triggers or []),
            meta=meta or {},
        )

    def _add_delegate_session_event(
        self,
        event_type: str,
        *,
        ctx: Optional[Dict[str, Any]] = None,
        user_text: str = "",
        triggers: Optional[list[str]] = None,
        text: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        runtime = ctx or {}
        task_id = str(runtime.get("delegate_task_id") or "").strip()
        delegate_add_event(
            event_type,
            task_id=task_id,
            user_text=user_text,
            triggers=list(triggers or []),
            text=text,
            meta=meta or {},
        )

    def _split_delegate_triggers(self, triggers) -> tuple[list[str], list[str]]:
        normal_triggers = []
        delegate_triggers = []
        for trigger in triggers or []:
            normalized = str(trigger or "").strip()
            if not normalized:
                continue
            if getattr(
                self.plugin_manager, "is_delegate_trigger", None
            ) and self.plugin_manager.is_delegate_trigger(normalized):
                delegate_triggers.append(normalized)
            else:
                normal_triggers.append(normalized)
        return list(dict.fromkeys(normal_triggers)), list(
            dict.fromkeys(delegate_triggers)
        )

    def _extract_workspace_read_path(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        m = re.search(
            r"([A-Za-z0-9_./\\-]+\.(?:py|md|json|yaml|yml|txt|toml|ini|js|ts|tsx|jsx|css|html|xml|cpp|c|h|hpp|java|go|rs|sh|bat))",
            raw,
            flags=re.IGNORECASE,
        )
        return str(m.group(1)).strip() if m else ""

    async def _emit_assistant_text(
        self,
        text: str,
        *,
        ctx: Dict[str, Any],
        emotion: str = "neutral",
        transcript_meta: Optional[Dict[str, Any]] = None,
        chat_log_source: str = "chat",
        output_profile: Optional[Dict[str, Any]] = None,
        tool: bool = False,
        interrupt: bool = True,
        apply_catchphrase: bool = True,
    ) -> None:
        final_text = self._clean_text_for_tts(
            self._strip_internal_tags(
                self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(text))
            )
        ).strip()
        if apply_catchphrase:
            final_text = self._apply_character_catchphrase(final_text)
        if not final_text:
            return
        transcript_meta = transcript_meta or {}
        output_profile = output_profile or build_output_profile(
            str((ctx or {}).get("source") or "text_input")
        )
        self._update_active_time()
        self._add_codex_session_event(
            "assistant_reply",
            text=final_text,
            ctx=ctx,
            meta={"emotion": emotion, "tool": tool, "background": True},
        )
        assistant_log_meta = {
            "tool": tool,
            "emotion": emotion,
            "source": chat_log_source,
            **transcript_meta,
        }
        memory_session_id = self._get_memory_session_id(ctx)
        if memory_session_id:
            assistant_log_meta["session_id"] = memory_session_id
        await self.event_bus.emit(
            "chat.log",
            role="assistant",
            content=final_text,
            meta=assistant_log_meta,
        )
        if output_profile.get("ui_append", True):
            await self.event_bus.emit("ui.append", role="assistant", text=final_text)
        await self.presenter.present(
            final_text,
            emotion,
            interrupt=interrupt,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
        )
        await self._send_gateway_reply(final_text, ctx, emotion=emotion)

    async def _run_background_delegate_task(
        self,
        *,
        user_text: str,
        ctx: Dict[str, Any],
        context_messages: list,
        delegate_triggers: list[str],
        task_reasoning: str,
        transcript_meta: Dict[str, Any],
        chat_log_source: str,
        output_profile: Dict[str, Any],
    ) -> None:
        try:
            self._set_delegate_task_state(
                ctx,
                "running",
                summary=user_text[:200],
                triggers=delegate_triggers,
                meta={"background": True},
            )
            self._add_delegate_session_event(
                "background_running",
                ctx=ctx,
                user_text=user_text,
                triggers=delegate_triggers,
                text=user_text,
                meta={"background": True},
            )
            delegate_flow_result = await delegate_flow_service.run_delegate_round(
                user_text=user_text,
                ctx=ctx,
                context_messages=context_messages,
                delegate_triggers=delegate_triggers,
                task_reasoning=task_reasoning,
                plugin_manager=self.plugin_manager,
                chat_with_ai=chat_with_ai,
                logger=self.logger,
            )
            delegate_triggered = delegate_flow_result.triggered
            delegate_clean = delegate_flow_result.clean
            delegate_results = delegate_flow_result.results
            delegate_used = delegate_flow_result.used
            if "moegirl_wiki" in set(delegate_used or delegate_triggers):
                if self.tool_result_formatter.should_fallback_from_moegirl(
                    delegate_results
                ):
                    (
                        fallback_results,
                        fallback_used,
                    ) = await search_flow_service.run_search_fallback_for_moegirl(
                        user_text=user_text,
                        ctx=ctx,
                        plugin_manager=self.plugin_manager,
                    )
                    if fallback_results:
                        merged_results = list(delegate_results or [])
                        merged_results.append(
                            "\n【萌百未给出明确主词条，以下是联网补充】"
                        )
                        merged_results.extend(fallback_results)
                        delegate_results = merged_results
                    if fallback_used:
                        delegate_used = list(
                            dict.fromkeys(
                                list(delegate_used or []) + list(fallback_used)
                            )
                        )
            summary = (
                "\n".join(delegate_results)[:200]
                if delegate_results
                else (delegate_clean or user_text[:200])
            )
            self._set_delegate_task_state(
                ctx,
                "done" if delegate_results or delegate_used else "skipped",
                summary=summary,
                triggers=delegate_triggers,
                meta={
                    "background": True,
                    "delegate_triggered": bool(delegate_triggered),
                    "delegate_used": list(delegate_used or []),
                },
            )
            self._add_delegate_session_event(
                "background_completed"
                if (delegate_results or delegate_used)
                else "background_skipped",
                ctx=ctx,
                user_text=user_text,
                triggers=delegate_triggers,
                text=(
                    "\n".join(delegate_results)[:600]
                    if delegate_results
                    else (delegate_clean or "")
                ),
                meta={
                    "background": True,
                    "delegate_used": list(delegate_used or []),
                },
            )
            final_text = ""
            if delegate_results:
                final_text = "\n".join(
                    str(item) for item in delegate_results if str(item).strip()
                )
            elif delegate_clean:
                final_text = delegate_clean
            if not final_text:
                final_text = "后台任务已处理完成。"
            final_text, final_emo = await self.tool_result_formatter.polish_background_delegate_reply(
                user_text=user_text,
                delegate_triggers=delegate_triggers,
                delegate_results=delegate_results,
                delegate_clean=delegate_clean,
            )
            await output_coordinator.emit_background_delegate_reply(
                text=final_text,
                emotion=final_emo,
                ctx=ctx,
                transcript_meta=transcript_meta,
                chat_log_source=chat_log_source,
                output_profile=output_profile,
                emit_assistant_text=self._emit_assistant_text,
            )
            self._add_delegate_session_event(
                "background_reply",
                ctx=ctx,
                user_text=user_text,
                triggers=delegate_triggers,
                text=final_text,
                meta={"emotion": final_emo, "background": True},
            )
        except Exception as e:
            self._set_delegate_task_state(
                ctx,
                "failed",
                summary=str(e)[:200],
                triggers=delegate_triggers,
                meta={"background": True},
            )
            self._add_delegate_session_event(
                "background_failed",
                ctx=ctx,
                user_text=user_text,
                triggers=delegate_triggers,
                text=str(e),
                meta={"background": True},
            )
            await output_coordinator.emit_background_delegate_reply(
                text=f"刚才委托的后台任务失败了：{e}",
                emotion="neutral",
                ctx=ctx,
                transcript_meta=transcript_meta,
                chat_log_source=chat_log_source,
                output_profile=output_profile,
                emit_assistant_text=self._emit_assistant_text,
            )

    def _update_active_time(self):
        """更新活跃时间戳"""
        self._last_reply_time = time.time()

    async def _show_thinking_emotion(self, text="", emotion="think"):
        """显示思考情绪"""
        await self.event_bus.emit("live2d.emotion", emotion=emotion)
        if text:
            await self.event_bus.emit("ui.append", role="assistant", text=text)

    async def _add_memory_safe(
        self, role: str, text: str, *, meta: Optional[dict] = None
    ):
        """安全地添加记忆 (修复参数冲突BUG)"""
        # 🟢 1. 浅拷贝 meta，防止修改原字典
        safe_meta = (meta or {}).copy()
        session_id = str(safe_meta.get("session_id") or "").strip() or None

        # 🟢 2. 检查并处理参数冲突
        # event_bus.emit 已经使用了 'role' 和 'len' 参数
        if "role" in safe_meta:
            # 将 meta 里的 role (通常是 char_id) 重命名，避免与 event_bus 的 role (speaker) 冲突
            safe_meta["meta_role"] = safe_meta.pop("role")

        if "len" in safe_meta:
            safe_meta.pop("len")

        try:
            await asyncio.to_thread(
                self.brain.add_memory,
                role,
                text,
                session_id=session_id,
                meta=safe_meta,
            )

            # 安全发射事件
            await self.event_bus.emit(
                "memory.add.ok", role=role, len=len(text or ""), **safe_meta
            )
        except Exception as e:
            # 错误处理也要用 safe_meta
            await self.event_bus.emit(
                "memory.add.fail",
                role=role,
                error=repr(e),
                len=len(text or ""),
                **safe_meta,
            )

    # ==================== Gatekeeper 逻辑 ====================
    async def _should_reply(self, user_text: str) -> bool:
        """
        判断是否需要回复用户消息
        """
        text_clean = (user_text or "").strip()
        if len(text_clean) < 2:
            return False

        # ListenerPlugin 规则并入：直接提及唤醒词，强制回复
        lower_text = text_clean.lower()
        wake_words = [
            str(word).lower() for word in (WAKE_KEYWORDS or []) if str(word).strip()
        ]
        if any(word in lower_text for word in wake_words):
            self.logger.info("🟢 [Gatekeeper] 命中唤醒词 -> 强制回复")
            return True
        if self._is_searchworthy_question(text_clean):
            self.logger.info("🟢 [Gatekeeper] 明确问题 -> 强制回复")
            return True

        # ListenerPlugin 规则并入：避免 assistant 连续自问自答
        if self.brain.short_term_memory:
            last_msg = self.brain.short_term_memory[-1]
            if last_msg.get("role") == "assistant":
                self.logger.info("🛑 [Gatekeeper] 上一条为 assistant -> 忽略")
                return False

        if not GATEKEEPER_ENABLED:
            return True

        # 1. 黑名单检查 (直接忽略)
        for w in GATEKEEPER_BLACKLIST:
            if w in text_clean:
                self.logger.info(f"🛑 [Gatekeeper] 命中黑名单 [{w}] -> 忽略")
                return False

        # 2. 白名单检查 (直接回复)
        for w in GATEKEEPER_WHITELIST:
            if w.lower() in text_clean.lower():
                self.logger.info(f"🟢 [Gatekeeper] 命中白名单 [{w}] -> 强制回复")
                return True

        # 3. 活跃会话窗口检查 (连贯对话必回)
        time_diff = time.time() - self._last_reply_time
        if time_diff < GATEKEEPER_ACTIVE_SESSION_WINDOW:
            self.logger.info(
                f"🟢 [Gatekeeper] 处于活跃会话窗口 ({int(time_diff)}s < {GATEKEEPER_ACTIVE_SESSION_WINDOW}s) -> 强制回复"
            )
            return True

        # 4. LLM 智能判断 (调用 cheap model)
        try:
            last_ai_reply = "无"
            if self.brain.short_term_memory:
                for msg in reversed(self.brain.short_term_memory):
                    if msg.get("role") == "assistant":
                        last_ai_reply = msg.get("content", "")[:50]
                        break

            prompt = GATEKEEPER_PROMPT_TEMPLATE.format(
                user_text=text_clean, last_ai_reply=last_ai_reply
            )

            messages = [{"role": "user", "content": prompt}]

            # 使用 'gatekeeper' 路由
            decision = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="gatekeeper",
                caller="chat_gatekeeper",
            )
            decision = decision.strip().upper()

            self.logger.info(
                f"⚖️ [Gatekeeper] LLM 判断结果: {decision} | 输入: {text_clean[:20]}..."
            )

            if "YES" in decision:
                return True
            else:
                return False

        except Exception as e:
            self.logger.error(f"⚠️ [Gatekeeper] 判断出错，默认放行: {e}")
            return True

    #  日期/时间意图嗅探
    def _contains_date_ref(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        keywords = [
            "昨天",
            "前天",
            "大前天",
            "上周",
            "上个月",
            "过去",
            "那一天",
            "那天",
            "几号",
            "星期",
            "礼拜",
            "周一",
            "周二",
            "周三",
            "周四",
            "周五",
            "周六",
            "周日",
            "上午",
            "早上",
            "中午",
            "下午",
            "晚上",
            "刚才",
            "之前",
            "还记得",
            "干了什么",
            "做了什么",
            "说过",
            "提过",
        ]
        if any(k in t for k in keywords):
            return True
        # 绝对日期 (2024-01-01, 1月1日, 5号)
        if re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", t):
            return True
        if re.search(r"\d{1,2}[月]\d{1,2}[日号]", t):
            return True
        if re.search(r"\d{1,2}号", t):
            return True
        return False

    def _contains_memory_ref(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        keys = [
            "还记得",
            "记得吗",
            "忘了",
            "之前说",
            "我说过",
            "提过",
            "我为什么",
            "怎么会",
            "当时",
            "怎么了",
            "到底为啥",
            "腹泻",
            "断食",
            "体检",
            "医院",
            "不舒服",
            "拉肚子",
        ]
        return any(k in t for k in keys)

    def _match_followup_topic(self, text: str) -> str:
        t = (text or "").strip().lower()
        if not t:
            return ""
        for topic, kws in self.FOLLOWUP_TOPICS.items():
            if any(k in t for k in kws):
                return topic
        return ""

    def _render_followup_label(self, topic: str, text: str) -> str:
        t = (text or "").strip()
        if topic == "health":
            if "腹泻" in t or "拉肚子" in t:
                return "腹泻"
            if "断食" in t:
                return "断食和补液"
            return "身体情况"
        if topic == "sleep":
            return "休息和睡眠"
        if topic == "diet":
            return "饮食和补水"
        if topic == "work_study":
            return "工作/学习进度"
        if topic == "emotion":
            return "心情和压力"
        if topic == "plan":
            return "你的计划安排"
        return "近况"

    def _build_followup_text(self, topic: str, label: str) -> str:
        if topic == "health":
            return "今天身体还行吗？"
        if topic == "sleep":
            return "今天精神还撑得住吗？"
        if topic == "diet":
            return "今天有好好吃点东西吗？"
        if topic == "work_study":
            return ""
        if topic == "emotion":
            return "今天状态缓过来一点了吗？"
        if topic == "plan":
            return ""
        return ""

    async def _record_proactive_followup(
        self, followup: Optional[Dict[str, Any]]
    ) -> None:
        if not isinstance(followup, dict):
            return
        text = str(followup.get("text") or "").strip()
        topic = str(followup.get("topic") or "").strip()
        snippet = str(followup.get("snippet") or "").strip()
        if not text:
            return
        note = text
        if snippet:
            note = f"{text}（主题:{topic or 'unknown'}，依据：{snippet}）"
        await self._append_hidden_transcript_note(
            "assistant",
            note,
            meta={
                "path": "proactive_followup",
                "topic": topic,
                "hidden": True,
            },
        )
        await self._add_memory_safe(
            "assistant",
            note,
            meta={
                "path": "proactive_followup",
                "topic": topic,
                "hidden": True,
            },
        )

    async def _record_task_followup(self, followup: Optional[Dict[str, Any]]) -> None:
        if not isinstance(followup, dict):
            return
        text = str(followup.get("text") or "").strip()
        task_text = str(followup.get("task") or "").strip()
        item_id = str(followup.get("item_id") or "").strip()
        if not text:
            return
        note = text if not task_text else f"{text}（任务:{task_text}）"
        await self._append_hidden_transcript_note(
            "assistant",
            note,
            meta={
                "path": "task_followup",
                "task": task_text,
                "item_id": item_id,
                "hidden": True,
            },
        )
        await self._add_memory_safe(
            "assistant",
            note,
            meta={
                "path": "task_followup",
                "task": task_text,
                "item_id": item_id,
                "hidden": True,
            },
        )

    def _find_yesterday_followup_note(self) -> tuple[str, str, str]:
        """
        从 transcript 中找昨天用户提到、适合今天关心的主题。
        返回: (topic, label, snippet)
        """
        store = getattr(self.brain, "sqlite_store", None)
        if not store:
            return "", "", ""
        now_dt = datetime.now()
        today = now_dt.date()
        yesterday = today - timedelta(days=1)
        max_age = timedelta(hours=18)
        try:
            rows = store.list_transcript(limit=420, offset=0)
            for r in rows:
                if (r.get("role") or "").strip() != "user":
                    continue
                ts = int(r.get("ts", 0) or 0)
                if not ts:
                    continue
                msg_dt = datetime.fromtimestamp(ts)
                if msg_dt.date() != yesterday:
                    continue
                if (now_dt - msg_dt) > max_age:
                    continue
                content = str(r.get("content") or "").strip()
                if not content:
                    continue
                meta = r.get("meta") or {}
                path = str(meta.get("path") or "").strip().lower()
                if path in {
                    "direct",
                    "tool_use",
                    "proactive_followup",
                    "task_followup",
                }:
                    continue
                if content.startswith("/"):
                    continue
                topic = self._match_followup_topic(content)
                if not topic:
                    continue
                if topic not in self.CARE_FOLLOWUP_TOPICS:
                    continue
                label = self._render_followup_label(topic, content)
                snippet = content[:80] + ("..." if len(content) > 80 else "")
                return topic, label, snippet
        except Exception:
            return "", "", ""
        return "", "", ""

    def _has_today_proactive_followup(self) -> bool:
        store = getattr(self.brain, "sqlite_store", None)
        if not store:
            return False
        today = datetime.now().date()
        try:
            rows = store.list_transcript(role="assistant", limit=260, offset=0)
            for r in rows:
                ts = int(r.get("ts", 0) or 0)
                if not ts:
                    continue
                if datetime.fromtimestamp(ts).date() != today:
                    continue
                content = str(r.get("content") or "")
                meta = r.get("meta") or {}
                if str(meta.get("path") or "").strip() == "proactive_followup":
                    return True
                if "[主动关心]" in content:
                    return True
        except Exception:
            return False
        return False

    async def _maybe_send_proactive_followup(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        """
        今日首次文本交互时，基于昨天用户话题生成一次主动关心前缀。
        """
        try:
            source = str((ctx or {}).get("source", ""))
            if source and source != "text_input":
                return None
            if bool((ctx or {}).get("codex_mode", False)):
                return None

            today_str = datetime.now().strftime("%Y-%m-%d")
            if self._last_proactive_followup_day == today_str:
                return None
            if self._should_suppress_followup_preface(user_text or ""):
                return None

            # 主动关心只在每天较早时段触发，避免把昨天的话题拖得太久。
            if datetime.now().hour >= 12:
                return None

            # 用户当前已经在主动聊这些话题，就不插入
            if self._match_followup_topic(user_text or ""):
                return None

            if self._has_today_proactive_followup():
                self._last_proactive_followup_day = today_str
                return None

            topic, label, snippet = self._find_yesterday_followup_note()
            if not topic:
                return None

            followup_text = self._build_followup_text(topic, label)
            if not followup_text:
                return None
            self._last_proactive_followup_day = today_str
            return {"text": followup_text, "topic": topic, "snippet": snippet}
        except Exception as e:
            self.logger.warning(f"主动关心触发失败: {e}")
        return None

    def _build_recent_transcript_context(
        self,
        limit: int = 28,
        max_chars: int = 1400,
        user_only: bool = False,
        session_id: str = "",
    ) -> str:
        """时间回顾类问题兜底：注入最近对话片段，降低“明明说过却忘记”的概率。"""
        store = getattr(self.brain, "sqlite_store", None)
        if not store:
            return ""
        try:
            session_key = str(session_id or "").strip()
            rows = store.list_transcript(
                limit=max(1, int(limit)),
                offset=0,
                session_id=session_key,
                session_scope="specific" if session_key else "global",
            )
            if not rows:
                return ""
            lines = []
            for r in reversed(rows):
                raw_role = str(r.get("role") or "").strip()
                if user_only and raw_role != "user":
                    continue
                role = "用户" if raw_role == "user" else "AI"
                content = str(r.get("content", "")).strip()
                if not content:
                    continue
                if len(content) > 80:
                    content = content[:80] + "..."
                lines.append(f"- {role}: {content}")
            text = "\n".join(lines)
            if len(text) > max_chars:
                text = text[-max_chars:]
            return text
        except Exception:
            return ""

    # ==================== 主处理逻辑 ====================

    async def process(self, user_text: str, ctx: Optional[Dict[str, Any]] = None):
        """
        处理用户输入 (主入口)
        包含：Direct指令 -> 每日总结拦截 -> Gatekeeper拦截 -> 观察插件 -> 工具路由 -> LLM回复
        """
        self._dbg(f"process() 开始，用户输入: {user_text}")

        # 1. 初始化上下文
        if ctx is None:
            ctx = {}
        ctx["chat_service"] = self
        ctx["brain"] = self.brain
        ctx["mcp_bridge"] = self.mcp_bridge
        ctx.setdefault("send_bubble", None)
        ctx.setdefault("trigger_motion", trigger_motion)
        ctx.setdefault("user_text", user_text)

        input_ctx = input_context.build_chat_input_context(
            ctx,
            is_qq_source=self.gateway_context_service.is_qq_source,
            get_memory_session_id=self._get_memory_session_id,
            remote_chat_sources=set(REMOTE_CHAT_SOURCES or set()),
        )
        input_source = input_ctx.input_source
        channel_meta = input_ctx.channel_meta
        if input_source in {"qq_gateway", "napcat_qq"}:
            sender_name = str(
                channel_meta.get("sender_name")
                or channel_meta.get("user_id")
                or "unknown"
            ).strip()
            session_preview = str(channel_meta.get("session_id") or "").strip()
            session_label = self.gateway_context_service.qq_session_label(
                session_preview
            )
            incoming_preview = (
                str(user_text or "").replace("\r", " ").replace("\n", " ").strip()
            )
            if len(incoming_preview) > 240:
                incoming_preview = incoming_preview[:240] + "..."
            self.logger.info(
                f"[QQ-IN][{session_label}][{session_preview or 'unknown'}][from={sender_name}] {incoming_preview}"
            )
        self._observe_reply_effect(user_text, ctx)
        transcript_channel_meta = input_ctx.transcript_channel_meta
        has_external_images = input_ctx.has_external_images
        memory_session_id = input_ctx.memory_session_id
        await self._sync_qq_user_profile(ctx)
        chat_log_source = input_ctx.chat_log_source
        output_profile = input_ctx.output_profile
        live2d_enabled = input_ctx.live2d_enabled
        codex_mode = input_ctx.codex_mode
        ctx["codex_mode"] = codex_mode
        if codex_mode and not str(ctx.get("codex_task_id", "")).strip():
            ctx["codex_task_id"] = uuid.uuid4().hex[:8]
        code_path = str(ctx.get("code_path", "") or "").strip()
        # 代码助手权限默认仅在 codex_mode 下开放；delegate 只读权限会在副脑上下文单独注入
        ctx["allow_read"] = bool(ctx.get("allow_read", False)) and codex_mode
        ctx["allow_write"] = bool(ctx.get("allow_write", False)) and codex_mode
        ctx["allow_exec"] = bool(ctx.get("allow_exec", False)) and codex_mode
        allow_read = bool(ctx.get("allow_read", False))
        allow_write = bool(ctx.get("allow_write", False))
        allow_exec = bool(ctx.get("allow_exec", False))
        feedback_type, feedback_reaction = self._detect_feedback(user_text)
        apply_confirmed, confirm_change_id, confirm_token = (
            self._extract_apply_confirmation(user_text)
        )
        ctx["codex_user_confirmed_apply"] = bool(apply_confirmed)
        ctx["codex_confirm_change_id"] = str(confirm_change_id or "")
        ctx["codex_confirm_token"] = str(confirm_token or "")
        self.logger.debug(f"收到输入: {user_text} (来源: {input_source})")
        self._trace_process(
            "entry",
            source=input_source,
            chat_log_source=chat_log_source,
            is_qq=self.gateway_context_service.is_qq_source(ctx),
            codex_mode=codex_mode,
            has_external_images=has_external_images,
            live2d_enabled=live2d_enabled,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
            memory_session=bool(memory_session_id),
        )
        self.personality.update_state()

        if codex_mode:
            self._set_codex_task_state(ctx, "plan", summary=user_text[:200])
            if apply_confirmed:
                self._set_codex_task_state(
                    ctx,
                    "user_confirm_apply",
                    summary="用户确认应用变更",
                    meta={
                        "change_id": confirm_change_id,
                        "confirm_token": confirm_token,
                    },
                )
            self._add_codex_session_event("user_task", text=user_text, ctx=ctx)

        # =========================================================================
        # 2. Direct 模式：处理“控制类”硬指令
        # =========================================================================
        self._dbg("检查是否为direct命令")
        is_direct, direct_result = await self.plugin_manager.execute_direct_commands(
            user_text, ctx
        )

        if is_direct:
            self._dbg("进入 Direct 命令处理流程")
            direct_meta = {"path": "direct"}
            if memory_session_id:
                direct_meta["session_id"] = memory_session_id
            direct_reply_text = str(direct_result) if direct_result is not None else ""
            direct_memory_reply = direct_reply_text
            handled_gateway_voice = False
            handled_gateway_image = False
            handled_gateway_file = False
            if (
                isinstance(direct_result, dict)
                and str(direct_result.get("__type__") or "").strip() == "gateway_voice"
            ):
                voice_path = str(direct_result.get("voice_path") or "").strip()
                post_send_text = str(direct_result.get("post_send_text") or "").strip()
                success_text = str(
                    direct_result.get("success_text") or "🔊 已把语音发给你了。"
                )
                fallback_text = str(
                    direct_result.get("fallback_text")
                    or "⚠️ 语音已准备好，但回发失败了。"
                )
                voice_ok = await self._send_gateway_voice_reply(voice_path, ctx)
                direct_reply_text = success_text if voice_ok else fallback_text
                direct_memory_reply = (
                    (post_send_text or success_text) if voice_ok else fallback_text
                )
                handled_gateway_voice = voice_ok
                if voice_ok:
                    direct_reply_text = ""
                    if post_send_text and str(ctx.get("source") or "").strip().lower() in {
                        "qq_gateway",
                        "napcat_qq",
                    }:
                        await self._send_gateway_reply(
                            post_send_text, ctx, emotion="neutral"
                        )
            if (
                isinstance(direct_result, dict)
                and str(direct_result.get("__type__") or "").strip() == "gateway_file"
            ):
                file_path = str(direct_result.get("file_path") or "").strip()
                file_name = str(direct_result.get("file_name") or "").strip()
                success_text = str(
                    direct_result.get("success_text") or "📎 已把文件发给你了。"
                )
                fallback_text = str(
                    direct_result.get("fallback_text")
                    or "⚠️ 文件已准备好，但回发失败了。"
                )
                file_ok = await self._send_gateway_file_reply(
                    file_path, ctx, file_name=file_name
                )
                direct_reply_text = success_text if file_ok else fallback_text
                direct_memory_reply = direct_reply_text
                handled_gateway_file = file_ok
                if not file_ok and str(ctx.get("source") or "").strip().lower() in {
                    "qq_gateway",
                    "napcat_qq",
                }:
                    await self._send_gateway_reply(
                        direct_reply_text, ctx, emotion="neutral"
                    )
            if (
                isinstance(direct_result, dict)
                and str(direct_result.get("__type__") or "").strip() == "gateway_image"
            ):
                image_path = str(direct_result.get("image_path") or "").strip()
                image_caption = str(direct_result.get("caption") or "")
                send_caption_with_image = bool(
                    direct_result.get("send_caption_with_image")
                )
                success_text = str(
                    direct_result.get("success_text") or "🖼️ 已把当前截图发给你了。"
                )
                fallback_text = str(
                    direct_result.get("fallback_text") or "⚠️ 截图已生成，但回发失败了。"
                )
                suppress_fallback_reply = bool(
                    direct_result.get("suppress_fallback_reply")
                )
                image_ok = await self._send_gateway_image_reply(
                    image_path,
                    ctx,
                    caption=image_caption if send_caption_with_image else "",
                )
                if image_path and bool(direct_result.get("cleanup", True)):
                    asyncio.create_task(
                        self.gateway_sender.cleanup_image_file(image_path)
                    )
                direct_reply_text = "" if image_ok else fallback_text
                direct_memory_reply = (
                    (image_caption or success_text) if image_ok else fallback_text
                )
                handled_gateway_image = image_ok
                if (
                    not image_ok
                    and not suppress_fallback_reply
                    and str(ctx.get("source") or "").strip().lower()
                    in {
                        "qq_gateway",
                        "napcat_qq",
                    }
                ):
                    await self._send_gateway_reply(
                        direct_reply_text, ctx, emotion="neutral"
                    )
                    direct_reply_text = ""
                elif not image_ok and suppress_fallback_reply:
                    direct_reply_text = ""

            if direct_reply_text:
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit(
                        "ui.append", role="assistant", text=direct_reply_text
                    )
                await self.presenter.present(
                    direct_reply_text,
                    emotion="neutral",
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
                if (
                    not handled_gateway_voice
                    and not handled_gateway_image
                    and not handled_gateway_file
                ):
                    await self._send_gateway_reply(
                        direct_reply_text, ctx, emotion="neutral"
                    )
                self._update_active_time()
                if codex_mode:
                    self._set_codex_task_state(
                        ctx, "finalize", summary=direct_reply_text[:200]
                    )

            direct_user_meta = {
                "path": "direct",
                "source": chat_log_source,
                **transcript_channel_meta,
            }
            direct_assistant_meta = {
                "path": "direct",
                "source": chat_log_source,
                "tool": True,
                "emotion": "neutral",
                **transcript_channel_meta,
            }
            if memory_session_id:
                direct_user_meta["session_id"] = memory_session_id
                direct_assistant_meta["session_id"] = memory_session_id
            await self.event_bus.emit(
                "chat.log", role="user", content=user_text, meta=direct_user_meta
            )
            await self._add_memory_safe("user", user_text, meta=direct_user_meta)
            direct_memory_reply = str(direct_memory_reply or "").strip()
            if direct_memory_reply:
                await self.event_bus.emit(
                    "chat.log",
                    role="assistant",
                    content=direct_memory_reply,
                    meta=direct_assistant_meta,
                )
                await self._add_memory_safe(
                    "assistant", direct_memory_reply, meta=direct_assistant_meta
                )

            self._dbg("Direct 流程结束，检查是否需要立即 Idle")
            await self._emit_idle_status_when_safe(
                output_profile,
                reason="direct_complete",
                had_presenter_output=bool(direct_reply_text),
            )
            return

        # =========================================================================
        # 3. 特殊指令拦截 (每日总结/补写)
        # =========================================================================
        if (
            "总结今天" in user_text
            or "今天干了什么" in user_text
            or "今天的总结" in user_text
        ):
            print("📅 [System] 拦截到每日总结请求")
            if hasattr(self, "screen_sensor_ref") and self.screen_sensor_ref:
                report = self.screen_sensor_ref.get_formatted_report()
                raw_stats = getattr(
                    self.screen_sensor_ref, "get_stats_data", lambda: {}
                )()
                await self.diary_service.handle_diary_request(
                    user_text=user_text,
                    ctx=ctx,
                    output_profile=output_profile,
                    memory_path="summary",
                    report_data=report,
                    raw_stats=raw_stats,
                    is_makeup=False,
                )
                return

        if "总结昨天" in user_text or "补写昨天" in user_text:
            print("📅 [System] 拦截到补写昨天日记请求")
            yesterday = datetime.now().date() - timedelta(days=1)
            await self.diary_service.handle_diary_request(
                user_text=user_text,
                ctx=ctx,
                output_profile=output_profile,
                memory_path="summary_makeup",
                target_date=yesterday,
                is_makeup=True,
            )
            return

        specific_day_match = re.search(r"(总结|补写)\s*(\d{4}-\d{2}-\d{2})", user_text)
        if specific_day_match:
            requested_date_str = specific_day_match.group(2)
            try:
                requested_date = datetime.strptime(
                    requested_date_str, "%Y-%m-%d"
                ).date()
            except ValueError:
                requested_date = None

            if requested_date:
                print(f"[System] 拦截到指定日期日记请求: {requested_date_str}")
                is_makeup = requested_date < datetime.now().date()
                await self.diary_service.handle_diary_request(
                    user_text=user_text,
                    ctx=ctx,
                    output_profile=output_profile,
                    memory_path="summary_makeup_date",
                    target_date=requested_date,
                    is_makeup=is_makeup,
                )
                return

        # =========================================================================
        # 4. Gatekeeper 拦截层
        # =========================================================================
        buffered_user_text = await self._maybe_buffer_qq_private_message(user_text, ctx)
        if buffered_user_text is None:
            await self._emit_idle_status(output_profile, reason="qq_private_buffer_superseded")
            return
        if buffered_user_text != user_text:
            user_text = buffered_user_text
            ctx["user_text"] = user_text
            feedback_type, feedback_reaction = self._detect_feedback(user_text)
        self._remember_search_topic(user_text, ctx)

        direct_chat_sources = input_ctx.direct_chat_sources
        source_key = input_ctx.source_key
        should_reply = (
            True
            if input_ctx.should_bypass_gatekeeper
            else await self._should_reply(user_text)
        )
        if not should_reply:
            print(f"🛑 [系统] Gatekeeper 决定忽略此消息")
            await self.event_bus.emit("chat.ignored", content=user_text)
            await self._emit_idle_status(output_profile, reason="gatekeeper_ignore")
            return

        # 轻量任务代理：自动提取待办 / 标记完成
        if await self.hardware_status_service.try_handle_hardware_status_query(
            user_text=user_text,
            ctx=ctx,
            transcript_meta=transcript_channel_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            memory_session_id=memory_session_id,
        ):
            await self._emit_idle_status_when_safe(
                output_profile,
                reason="hardware_status_complete",
                had_presenter_output=True,
            )
            return

        await self._update_task_agent(user_text)

        # 今日主动关心（每天最多一次）
        proactive_followup = await self._maybe_send_proactive_followup(user_text, ctx)
        task_followup = await self._maybe_send_task_followup(user_text, ctx)
        preface_text = self._merge_preface_texts(
            (proactive_followup or {}).get("text", ""),
            (task_followup or {}).get("text", ""),
        )
        external_image_context = await self._describe_external_images(ctx)
        if external_image_context:
            user_text = f"{user_text}\n\n{external_image_context}"

        # =========================================================================
        # 5. 正式回复流程 (日志、思考、观察、LLM)
        # =========================================================================
        self._dbg("准备发送聊天日志")
        user_log_meta = {"source": chat_log_source, **transcript_channel_meta}
        if memory_session_id:
            user_log_meta["session_id"] = memory_session_id
        await self.event_bus.emit(
            "chat.log", role="user", content=user_text, meta=user_log_meta
        )
        ctx["_reply_start_emotion"] = self._get_current_live2d_emotion()
        self._dbg("准备切换状态为 thinking")
        if live2d_enabled:
            await self.event_bus.emit(
                "state.changed", state="thinking", reason="user_input"
            )
        else:
            await self.event_bus.emit("ui.status", text="Thinking.")
        await self.personality.think_before_respond(
            user_text, self._show_thinking_emotion if live2d_enabled else None
        )

        # --- Observe 模式 ---
        if hasattr(self.plugin_manager, "execute_observe_commands"):
            self._dbg("检查 Observe 插件...")
            is_observe, obs_result = await self.plugin_manager.execute_observe_commands(
                user_text, ctx
            )

            if is_observe:
                self._dbg("Observe 触发成功")
                obs_text = ""
                if (
                    isinstance(obs_result, dict)
                    and obs_result.get("__type__") == "image_payload"
                ):
                    await self.event_bus.emit("ui.status", text="Analyzing Visuals...")
                    try:
                        from modules import llm

                        desc = await llm.analyze_image(
                            obs_result["image_base64"],
                            "请客观详细描述这张图片的内容。",
                            caller="observe_image_describe",
                        )
                        obs_text = f"【当前视觉环境】\n{desc}"
                        self._dbg("视觉描述获取成功")
                    except Exception as e:
                        obs_text = f"【视觉观察失败】{e}"
                elif obs_result:
                    obs_text = f"【观察数据】\n{str(obs_result)}"

                if obs_text:
                    self._dbg("将观察结果注入对话上下文")
                    observe_meta = {"path": "observe"}
                    if memory_session_id:
                        observe_meta["session_id"] = memory_session_id
                    asyncio.create_task(
                        self._add_memory_safe(
                            "system",
                            f"观察插件运行结果: {obs_text[:100]}...",
                            meta=observe_meta,
                        )
                    )
                    user_text = f"{user_text}\n\n{obs_text}"

        # --- 路由与 Prompt 构建 ---
        self._dbg(f"进入 LLM 对话/工具流程, Input: {user_text[:50]}...")
        session_key = self.gateway_context_service.conversation_session_key(ctx)
        last_tool_triggers = list(
            self._last_tool_triggers_by_session.get(session_key) or []
        )
        search_decision = search_flow_service.build_initial_search_decision(
            user_text,
            ctx,
            resolve_followup_search_query=self._resolve_followup_search_query,
        )
        followup_search_query = search_decision.followup_query
        if followup_search_query:
            self.logger.info(
                f"[SearchFollowup] 继承上文主题: {followup_search_query[:120]}"
            )
            route = self.tool_router.route(
                search_decision.route_text or "查一下",
                last_tool_triggers=["search_web"],
            )
        else:
            route = self.tool_router.route(
                user_text, last_tool_triggers=last_tool_triggers
            )
        self._dbg(
            f"路由结果: need_tools={route.need_tools}, triggers={route.tool_triggers}"
        )
        effective_triggers = list(route.tool_triggers or [])
        if codex_mode and "workspace_ops" not in effective_triggers:
            effective_triggers.append("workspace_ops")
        normal_triggers, delegate_triggers = self._split_delegate_triggers(
            effective_triggers
        )
        if delegate_triggers:
            self._set_delegate_task_state(
                ctx,
                "planned",
                summary=user_text[:200],
                triggers=delegate_triggers,
                meta={"route_reason": str(route.reason or "")},
            )
            self._add_delegate_session_event(
                "planned",
                ctx=ctx,
                user_text=user_text,
                triggers=delegate_triggers,
                text=user_text,
                meta={"route_reason": str(route.reason or "")},
            )
        need_tools = bool(route.need_tools or (codex_mode and effective_triggers))
        self._trace_process(
            "route",
            source=source_key,
            need_tools=need_tools,
            route_need_tools=bool(route.need_tools),
            triggers=effective_triggers,
            normal_triggers=normal_triggers,
            delegate_triggers=delegate_triggers,
            route_reason=str(route.reason or ""),
            followup_search=bool(followup_search_query),
        )
        if codex_mode and need_tools:
            self._set_codex_task_state(
                ctx,
                "execute",
                summary=user_text[:200],
                meta={"triggers": effective_triggers[:8]},
            )
        task_reasoning = "codex" if codex_mode else "tool_reasoning"
        task_default = "codex" if codex_mode else "default"

        plain_direct_chat_candidate = (
            reply_flow_service.is_plain_direct_chat_candidate(
                need_tools=need_tools,
                effective_triggers=effective_triggers,
                codex_mode=codex_mode,
                has_external_images=has_external_images,
                preface_text=preface_text,
                source_key=source_key,
                direct_chat_sources=direct_chat_sources,
            )
        )
        short_reaction = reply_flow_service.build_short_reaction(
            eligible=plain_direct_chat_candidate,
            user_text=user_text,
            build_reaction=lambda text: self.reply_style_service.build_short_reaction(
                text,
                wants_detailed_answer=self._wants_detailed_answer,
            ),
        )
        short_reply = short_reaction.text
        short_emo = short_reaction.emotion

        if await output_coordinator.emit_short_reaction(
            text=short_reply,
            emotion=short_emo,
            user_text=user_text,
            ctx=ctx,
            transcript_meta=transcript_channel_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            feedback_type=feedback_type,
            feedback_reaction=feedback_reaction,
            memory_session_id=memory_session_id,
            learning=self.learning,
            emit_assistant_text=self._emit_assistant_text,
            add_memory_safe=self._add_memory_safe,
            emit_idle_status_when_safe=self._emit_idle_status_when_safe,
        ):
            return

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        natural_reply_candidate = plain_direct_chat_candidate

        # 历史回溯
        special_context = ""
        external_sender_context = self._build_external_sender_context(ctx)
        if external_sender_context:
            special_context += f"\n\n{external_sender_context}"
        memory_ref = self._contains_memory_ref(user_text)
        need_history_context = self._contains_date_ref(user_text) or memory_ref
        if need_history_context:
            print("📅 [System] 嗅探到历史回忆意图，正在调卷历史档案...")
            try:
                store = getattr(self.brain, "sqlite_store", None)
                if store:
                    episodes = store.list_episodes(limit=15)
                    logs = [
                        f"📅 [{ep.get('title')}] 摘要：{ep.get('summary')}"
                        for ep in episodes
                    ]
                    if logs:
                        special_context += (
                            f"\n\n【系统强制注入：近期活动日志】\n" + "\n".join(logs)
                        )
                transcript_ctx = self._build_recent_transcript_context(
                    limit=30,
                    max_chars=1400,
                    user_only=memory_ref,
                    session_id=memory_session_id,
                )
                if transcript_ctx:
                    special_context += (
                        f"\n\n【系统强制注入：最近对话片段】\n{transcript_ctx}"
                    )
            except Exception as e:
                print(f"❌ 历史回溯失败: {e}")

        try:
            from config import PERSONA_PROMPT
        except:
            PERSONA_PROMPT = ""

        self_awareness_hint = self._build_live2d_self_awareness_hint(ctx)
        current_emotion_context = self._build_current_emotion_context(ctx)
        reply_style_context = self._build_reply_style_context(user_text, ctx)
        reply_angle_context = self._build_qq_reply_angle_context(user_text, ctx)
        system_text = (
            f"【当前时间】{current_time}\n{PERSONA_PROMPT}\n"
            f"{current_emotion_context}\n{reply_style_context}\n{special_context}"
        )
        if reply_angle_context:
            system_text += f"\n{reply_angle_context}"
        if self_awareness_hint:
            system_text += f"\n{self_awareness_hint}"
        skill_prompt = ""
        if self.skill_manager is not None:
            try:
                skill_prompt = self.skill_manager.build_prompt_addition()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Skill prompt build failed: {e}")
        if skill_prompt:
            system_text += "\n" + skill_prompt
        if codex_mode:
            codex_hint = (
                "【代码助手模式】你正在处理代码任务。\n"
                "1) 不要臆造文件内容，先读取再修改；\n"
                "2) 修改文件时优先使用 workspace_ops 工具；\n"
                "3) 写入前先生成 diff 预览(change_id + confirm_token)，等待用户确认后再 apply_change；\n"
                "4) 仅当用户在本轮消息显式提供 change_id + confirm_token 时才可 apply_change；\n"
                "5) 回答中给出关键文件路径与变更点。"
            )
            if code_path:
                codex_hint += f"\n【用户指定代码路径】{code_path}"
            codex_hint += f"\n【任务ID】{ctx.get('codex_task_id', '')}"
            codex_hint += f"\n【权限】allow_read={allow_read}, allow_write={allow_write}, allow_exec={allow_exec}"
            system_text += "\n" + codex_hint

        tool_prompt = ""
        deferred_tool_flow = False
        if need_tools:
            tool_prompt = self.plugin_manager.get_tool_prompt_for_triggers(
                list(normal_triggers), compact=True
            )
        else:
            tool_prompt = self.plugin_manager.get_system_prompt_addition()
            try:
                deferred_tool_flow = bool(
                    self.plugin_manager.should_use_deferred_tool_flow(user_text)
                )
            except Exception:
                deferred_tool_flow = False
        if tool_prompt:
            system_text += "\n" + tool_prompt
        delegate_prompt = self.plugin_manager.get_delegate_prompt_for_triggers(
            list(delegate_triggers), compact=True
        )
        if delegate_prompt:
            system_text += "\n" + delegate_prompt
        mcp_prompt = self._build_mcp_tool_prompt()
        if mcp_prompt:
            system_text += "\n" + mcp_prompt

        context_messages = await asyncio.to_thread(
            self.brain.build_prompt,
            user_text,
            system_persona=system_text,
            tool_intent=list(effective_triggers),
            session_id=memory_session_id,
        )
        cleaned_context_messages = []
        for message in context_messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            content = message.get("content")
            if role != "system":
                cleaned_context_messages.append(message)
                continue
            try:
                from modules.memory import clean_injected_context

                sanitized = dict(message)
                sanitized["content"] = clean_injected_context(str(content or ""))
                cleaned_context_messages.append(sanitized)
            except Exception:
                cleaned_context_messages.append(message)
        context_messages = cleaned_context_messages

        use_non_stream_flow = reply_flow_service.should_use_non_stream_flow(
            need_tools=need_tools,
            deferred_tool_flow=deferred_tool_flow,
            stream_available=chat_with_ai_stream is not None,
            natural_reply_candidate=natural_reply_candidate,
        )
        self._trace_process(
            "branch",
            source=source_key,
            non_stream=use_non_stream_flow,
            need_tools=need_tools,
            deferred_tool_flow=deferred_tool_flow,
            stream_available=chat_with_ai_stream is not None,
            natural_reply_candidate=natural_reply_candidate,
            has_external_images=has_external_images,
            preface=bool(preface_text),
            codex_mode=codex_mode,
        )

        # =========================================================================
        # 6. 分支 A: ReAct 工具链
        # =========================================================================
        if use_non_stream_flow:
            self._dbg("进入非流式回复/工具流程")
            await self.event_bus.emit("ui.status", text="Thinking (Tools)...")

            react_first_pass = await tool_flow_service.run_react_first_pass(
                context_messages=context_messages,
                ctx=ctx,
                need_tools=need_tools,
                deferred_tool_flow=deferred_tool_flow,
                task_reasoning=task_reasoning,
                task_default=task_default,
                plugin_manager=self.plugin_manager,
                chat_with_ai=chat_with_ai,
                contains_cmd=self._contains_cmd,
                strip_cmd_anywhere=self._strip_cmd_anywhere,
            )
            reply1 = react_first_pass.reply
            triggered = react_first_pass.triggered
            clean_thought = react_first_pass.clean_thought
            tool_results = react_first_pass.tool_results
            used_triggers = react_first_pass.used_triggers
            context_messages = react_first_pass.context_messages
            forced_search_query = ""
            if not triggered and not tool_results and not delegate_triggers:
                forced_search_query = search_flow_service.choose_forced_search_query(
                    user_text=user_text,
                    first_reply=reply1 or "",
                    followup_query=followup_search_query,
                    triggered=triggered,
                    tool_results=tool_results,
                    delegate_triggers=delegate_triggers,
                    looks_like_uncertain_answer=self._looks_like_uncertain_answer,
                    is_searchworthy_question=self._is_searchworthy_question,
                )
                if forced_search_query:
                    (
                        search_triggered,
                        search_clean,
                        search_results,
                        search_used,
                    ) = await search_flow_service.run_search_delegate_query(
                        query=forced_search_query,
                        ctx=ctx,
                        plugin_manager=self.plugin_manager,
                    )
                    if search_clean and not clean_thought:
                        clean_thought = search_clean
                    if search_triggered:
                        triggered = True
                    if search_results:
                        tool_results.extend(search_results)
                    if search_used:
                        used_triggers.extend(search_used)
            if delegate_triggers:
                self._set_delegate_task_state(
                    ctx,
                    "running",
                    summary=user_text[:200],
                    triggers=delegate_triggers,
                )
                self._add_delegate_session_event(
                    "running",
                    ctx=ctx,
                    user_text=user_text,
                    triggers=delegate_triggers,
                    text=user_text,
                )
                delegate_decision = delegate_flow_service.choose_delegate_execution(
                    route_reason=str(route.reason or ""),
                    delegate_triggers=delegate_triggers,
                    ctx=ctx,
                    user_text=user_text,
                    followup_search_query=followup_search_query,
                    is_search_delegate=self.tool_result_formatter.is_search_delegate,
                )
                background_delegate = delegate_decision.background_delegate
                delegate_flow_result = await delegate_flow_service.run_delegate_flow(
                    decision=delegate_decision,
                    user_text=user_text,
                    ctx=ctx,
                    context_messages=context_messages,
                    delegate_triggers=delegate_triggers,
                    task_reasoning=task_reasoning,
                    plugin_manager=self.plugin_manager,
                    chat_with_ai=chat_with_ai,
                    extract_workspace_read_path=self._extract_workspace_read_path,
                    run_search_delegate_query=lambda *, query, ctx: search_flow_service.run_search_delegate_query(
                        query=query,
                        ctx=ctx,
                        plugin_manager=self.plugin_manager,
                    ),
                    followup_search_query=followup_search_query,
                    logger=self.logger,
                )
                delegate_triggered = delegate_flow_result.triggered
                delegate_clean = delegate_flow_result.clean
                delegate_results = delegate_flow_result.results
                delegate_used = delegate_flow_result.used
                background_delegate = delegate_flow_result.background_delegate
                if background_delegate:
                    self._set_delegate_task_state(
                        ctx,
                        "queued",
                        summary=user_text[:200],
                        triggers=delegate_triggers,
                        meta={"background": True},
                    )
                    self._add_delegate_session_event(
                        "queued",
                        ctx=ctx,
                        user_text=user_text,
                        triggers=delegate_triggers,
                        text="后台排队中",
                        meta={"background": True},
                    )
                    asyncio.create_task(
                        self._run_background_delegate_task(
                            user_text=user_text,
                            ctx=dict(ctx or {}),
                            context_messages=list(context_messages),
                            delegate_triggers=list(delegate_triggers),
                            task_reasoning=task_reasoning,
                            transcript_meta=dict(transcript_channel_meta or {}),
                            chat_log_source=chat_log_source,
                            output_profile=dict(output_profile or {}),
                        )
                    )
                if delegate_clean and not clean_thought:
                    clean_thought = delegate_clean
                if delegate_triggered:
                    triggered = True
                if delegate_results:
                    tool_results.extend(delegate_results)
                if delegate_used:
                    used_triggers.extend(delegate_used)
                self._set_delegate_task_state(
                    ctx,
                    (
                        "queued"
                        if background_delegate
                        else (
                            "done" if delegate_results or delegate_used else "skipped"
                        )
                    ),
                    summary=(
                        "\n".join(delegate_results)[:200]
                        if delegate_results
                        else (delegate_clean or user_text[:200])
                    ),
                    triggers=delegate_triggers,
                    meta={
                        "background": bool(background_delegate),
                        "delegate_triggered": bool(delegate_triggered),
                        "delegate_used": list(delegate_used or []),
                    },
                )
                self._add_delegate_session_event(
                    "completed" if (delegate_results or delegate_used) else "skipped",
                    ctx=ctx,
                    user_text=user_text,
                    triggers=delegate_triggers,
                    text=(
                        "\n".join(delegate_results)[:600]
                        if delegate_results
                        else (delegate_clean or "")
                    ),
                    meta={
                        "background": bool(background_delegate),
                        "delegate_used": list(delegate_used or []),
                    },
                )
            ctx["_tool_results"] = tool_results
            if session_key:
                remembered_tools = list(dict.fromkeys(used_triggers or effective_triggers))
                if remembered_tools:
                    self._last_tool_triggers_by_session[session_key] = remembered_tools
                if used_triggers and any(
                    str(trigger or "").strip().lower() in {"search", "search_web"}
                    for trigger in used_triggers
                ):
                    self._remember_search_topic(
                        followup_search_query or forced_search_query or user_text,
                        ctx,
                    )
            reasoning_text = self._clean_text_for_tts(
                self._strip_cmd_anywhere(
                    self._strip_emo_tags_anywhere(clean_thought or reply1 or "")
                )
            ).strip()
            if reasoning_text:
                self._add_codex_session_event(
                    "assistant_reasoning",
                    text=reasoning_text,
                    ctx=ctx,
                    meta={"used_triggers": list(used_triggers or [])[:8]},
                )

            start_emo, start_intensity = self._reply_start_emotion(ctx)
            if triggered and tool_results and used_triggers:
                tool_use_meta = {"path": "tool_use"}
                if memory_session_id:
                    tool_use_meta["session_id"] = memory_session_id
                asyncio.create_task(
                    self._add_memory_safe(
                        "assistant",
                        f"[tool_use] {used_triggers}",
                        meta=tool_use_meta,
                    )
                )

            if triggered and tool_results:
                tool_finalize = await tool_flow_service.finalize_tool_reply(
                    clean_thought=clean_thought,
                    tool_results=tool_results,
                    used_triggers=used_triggers,
                    context_messages=context_messages,
                    route_reason=str(route.reason or ""),
                    task_default=task_default,
                    start_emo=start_emo,
                    chat_with_ai=chat_with_ai,
                    extract_emo_tag=self._extract_emo_tag,
                    character_sharing_enabled=CHARACTER_SHARING_ENABLED,
                    try_share=self.personality.try_share,
                )
                final_reply = tool_finalize.final_reply
                final_emo = tool_finalize.final_emo
                model_emo_seen = tool_finalize.model_emo_seen
                context_messages = tool_finalize.context_messages
            else:
                model_finalize = reply_flow_service.finalize_model_reply(
                    reply=reply1,
                    start_emo=start_emo,
                    extract_emo_tag=self._extract_emo_tag,
                    character_sharing_enabled=CHARACTER_SHARING_ENABLED,
                    try_share=self.personality.try_share,
                )
                final_reply = model_finalize.final_reply
                final_emo = model_finalize.final_emo
                model_emo_seen = model_finalize.model_emo_seen

            prepared_reply = await reply_flow_service.prepare_final_reply(
                final_reply=final_reply,
                final_emo=final_emo,
                model_emo_seen=model_emo_seen,
                natural_reply_candidate=natural_reply_candidate,
                triggered=triggered,
                user_text=user_text,
                ctx=ctx,
                preface_text=preface_text,
                clean_text_for_tts=self._clean_text_for_tts,
                strip_internal_tags=self._strip_internal_tags,
                strip_cmd_anywhere=self._strip_cmd_anywhere,
                strip_emo_tags_anywhere=self._strip_emo_tags_anywhere,
                should_suppress_followup_preface=self._should_suppress_followup_preface,
                merge_preface_texts=self._merge_preface_texts,
                polish_natural_reply=self._polish_natural_reply,
                apply_character_catchphrase=self._apply_character_catchphrase,
                prepare_reply_for_output=self._prepare_reply_for_output,
                infer_reply_emotion_with_llm=self._infer_reply_emotion_with_llm,
            )
            final_reply = prepared_reply.text
            final_emo = prepared_reply.emotion
            model_emo_seen = prepared_reply.model_emo_seen

            await output_coordinator.emit_non_stream_reply(
                final_reply=final_reply,
                final_emo=final_emo,
                user_text=user_text,
                ctx=ctx,
                output_profile=output_profile,
                transcript_meta=transcript_channel_meta,
                chat_log_source=chat_log_source,
                memory_session_id=memory_session_id,
                feedback_type=feedback_type,
                feedback_reaction=feedback_reaction,
                learning=self.learning,
                live2d_enabled=live2d_enabled,
                start_emo=start_emo,
                start_intensity=start_intensity,
                codex_mode=codex_mode,
                proactive_followup=proactive_followup,
                task_followup=task_followup,
                event_bus=self.event_bus,
                presenter=self.presenter,
                update_active_time=self._update_active_time,
                add_codex_session_event=self._add_codex_session_event,
                presenter_output_controls_idle=self._presenter_output_controls_idle,
                sensor_emotion_intensity=sensor_utils.sensor_emotion_intensity,
                send_gateway_reply=self._send_gateway_reply,
                maybe_send_auto_meme_reply=self._maybe_send_auto_meme_reply,
                record_reply_effect=self._record_reply_effect,
                record_proactive_followup=self._record_proactive_followup,
                record_task_followup=self._record_task_followup,
                set_codex_task_state=self._set_codex_task_state,
                add_memory_safe=self._add_memory_safe,
                emit_idle_status_when_safe=self._emit_idle_status_when_safe,
            )

        # =========================================================================
        # 7. 分支 B: 流式对话 (Stream) - 🟢 已修复重复写入问题 & 加回分享欲
        # =========================================================================
        else:
            self._dbg("进入流式对话流程")
            self._add_codex_session_event(
                "assistant_reasoning",
                text="进入流式回复，正在生成中。",
                ctx=ctx,
                meta={"stream": True, "triggers": list(effective_triggers or [])[:8]},
            )
            await self.event_bus.emit("ui.status", text="Streaming...")
            await self.event_bus.emit(
                "assistant.stream.start",
                interrupt=True,
                speak=output_profile.get("speak", True),
                show_bubble=output_profile.get("show_bubble", True),
            )

            buffer = ""
            start_emo, start_intensity = self._reply_start_emotion(ctx)
            curr_emo = start_emo
            model_emo_seen = False
            first = False
            full_reply = ""

            if preface_text:
                first = True
                proactive_chunk = reply_flow_service.build_stream_preface_chunk(
                    preface_text
                )
                full_reply += proactive_chunk
                await self.event_bus.emit(
                    "assistant.stream.feed",
                    chunk=proactive_chunk,
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )

            try:
                async for chunk in chat_with_ai_stream(
                    context_messages,
                    task_type=task_default,
                    caller="chat_stream_reply",
                ):
                    if not chunk:
                        continue
                    if not first:
                        first = True

                    buffer += chunk

                    if "[CMD:" in buffer and "]" in buffer:
                        buffer = self._cmd_re.sub("", buffer)

                    emotion_tag = reply_flow_service.consume_stream_emotion_tag(
                        buffer,
                        emo_tag_re=self._emo_tag_re,
                        normalize_emo=self._normalize_emo,
                    )
                    buffer = emotion_tag.buffer
                    if emotion_tag.found:
                        curr_emo = emotion_tag.emotion
                        model_emo_seen = True
                        print(f"🎭 [Stream] 检测到模型情绪标签: {curr_emo}")
                        if live2d_enabled:
                            defer_motion = self._presenter_output_controls_idle(
                                output_profile,
                                had_presenter_output=True,
                            )
                            asyncio.create_task(
                                self.event_bus.emit(
                                    "live2d.emotion",
                                    emotion=curr_emo,
                                    intensity=sensor_utils.sensor_emotion_intensity(curr_emo),
                                    prefer_motion=not defer_motion,
                                    reason="model_stream_reply",
                                )
                            )

                    flushed = reply_flow_service.flush_stream_buffer(
                        buffer,
                        clean_text_for_tts=self._clean_text_for_tts,
                        strip_internal_tags=self._strip_internal_tags,
                        strip_cmd_anywhere=self._strip_cmd_anywhere,
                        strip_emo_tags_anywhere=self._strip_emo_tags_anywhere,
                        strip_model_catchphrase=self._strip_model_catchphrase,
                    )
                    if flushed.chunk:
                        full_reply += flushed.chunk
                        await self.event_bus.emit(
                            "assistant.stream.feed",
                            chunk=flushed.chunk,
                            emotion=curr_emo,
                            speak=output_profile.get("speak", True),
                            show_bubble=output_profile.get("show_bubble", True),
                        )
                    buffer = flushed.buffer

                # 处理剩余尾巴
                flushed = reply_flow_service.flush_stream_buffer(
                    buffer,
                    final=True,
                    clean_text_for_tts=self._clean_text_for_tts,
                    strip_internal_tags=self._strip_internal_tags,
                    strip_cmd_anywhere=self._strip_cmd_anywhere,
                    strip_emo_tags_anywhere=self._strip_emo_tags_anywhere,
                    strip_model_catchphrase=self._strip_model_catchphrase,
                )
                if flushed.chunk:
                    full_reply += flushed.chunk
                    await self.event_bus.emit(
                        "assistant.stream.feed",
                        chunk=flushed.chunk,
                        emotion=curr_emo,
                        speak=output_profile.get("speak", True),
                        show_bubble=output_profile.get("show_bubble", True),
                    )
                buffer = flushed.buffer

            except Exception as e:
                self.logger.error(f"Stream error: {e}")

            stream_finalized = reply_flow_service.finalize_stream_reply(
                full_reply=full_reply,
                ctx=ctx,
                character_sharing_enabled=CHARACTER_SHARING_ENABLED,
                try_share=self.personality.try_share,
                apply_character_catchphrase=self._apply_character_catchphrase,
                prepare_reply_for_output=self._prepare_reply_for_output,
            )
            full_reply = stream_finalized.text
            for feed_chunk in stream_finalized.feed_chunks:
                await self.event_bus.emit(
                    "assistant.stream.feed",
                    chunk=feed_chunk,
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )

            try:
                if live2d_enabled and not model_emo_seen:
                    defer_motion = self._presenter_output_controls_idle(
                        output_profile,
                        had_presenter_output=bool(full_reply),
                    )
                    await self.event_bus.emit(
                        "live2d.emotion",
                        emotion=curr_emo,
                        intensity=start_intensity,
                        prefer_motion=not defer_motion,
                        reason="model_stream_keep_previous",
                    )
                await self.event_bus.emit(
                    "assistant.stream.end",
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
            except Exception as e:
                self.logger.warning(f"assistant.stream.end failed: {e}")

            await output_coordinator.emit_stream_reply(
                full_reply=full_reply,
                emotion=curr_emo,
                user_text=user_text,
                stream_context=output_coordinator.StreamOutputContext(
                    ctx=ctx,
                    output_profile=output_profile,
                    transcript_meta=transcript_channel_meta,
                    chat_log_source=chat_log_source,
                    memory_session_id=memory_session_id,
                    feedback_type=feedback_type,
                    feedback_reaction=feedback_reaction,
                    codex_mode=codex_mode,
                    proactive_followup=proactive_followup,
                    task_followup=task_followup,
                ),
                learning=self.learning,
                event_bus=self.event_bus,
                update_active_time=self._update_active_time,
                add_codex_session_event=self._add_codex_session_event,
                send_gateway_reply=self._send_gateway_reply,
                maybe_send_auto_meme_reply=self._maybe_send_auto_meme_reply,
                record_reply_effect=self._record_reply_effect,
                record_proactive_followup=self._record_proactive_followup,
                record_task_followup=self._record_task_followup,
                set_codex_task_state=self._set_codex_task_state,
                add_memory_safe=self._add_memory_safe,
            )

    # 🟢 [新增] 主动关怀提醒
    async def send_active_alert(self, app_name: str, minutes: int):
        """处理久坐提醒"""
        await self.active_alert_service.send_active_alert(app_name, minutes)

    # ==================== 屏幕感知事件处理 (完整版：含自我意识+视觉+文本) ====================

    async def handle_sensor_event(
        self,
        window_title: str,
        category: str,
        count: int = 1,
        use_vision: bool = False,
        app_name: str = "",
        reason: str = "",
        app_duration_sec: float | int | None = None,
        current_stay_sec: float | int | None = None,
    ) -> bool:
        if self._sensor_event_lock.locked():
            self.logger.info("🛑 [Sensor] 已有屏幕吐槽生成中，跳过本次事件")
            return False
        async with self._sensor_event_lock:
            return await self._handle_sensor_event_inner(
                window_title,
                category,
                count=count,
                use_vision=use_vision,
                app_name=app_name,
                reason=reason,
                app_duration_sec=app_duration_sec,
                current_stay_sec=current_stay_sec,
            )

    async def _handle_sensor_event_inner(
        self,
        window_title: str,
        category: str,
        count: int = 1,
        use_vision: bool = False,
        app_name: str = "",
        reason: str = "",
        app_duration_sec: float | int | None = None,
        current_stay_sec: float | int | None = None,
    ) -> bool:
        from modules.llm import chat_with_ai, analyze_image

        guard = sensor_event_guard.check_sensor_event_guard(
            window_title=window_title,
            category=category,
            app_name=app_name,
            last_reply_time=self._last_reply_time,
            min_reply_interval_sec=self._sensor_min_reply_interval_sec,
        )
        clean_title = guard.clean_title
        display_app = guard.display_app
        if guard.reason == "system_window":
            self.logger.info(f"🛑 [Sensor] 跳过系统/自身界面视觉吐槽 ({clean_title})")
            return False
        if not guard.allowed:
            return False

        print(
            f"🤖 [Sensor] 观察: {clean_title} ({category}) | App: {display_app} | Count: {count}"
        )
        generation = await self.sensor_event_service.run_event_generation(
            clean_title=clean_title,
            display_app=display_app,
            category=category,
            count=count,
            reason=reason,
            use_vision=use_vision,
            vision_mode=VISION_MODE,
            app_duration_sec=app_duration_sec,
            current_stay_sec=current_stay_sec,
            chat_with_ai=chat_with_ai,
            analyze_image=analyze_image,
        )
        if not generation.reply:
            return False
        reply_category = "self" if generation.branch == "self" else category
        reply_title = clean_title if generation.branch == "self" else window_title
        is_vision = generation.branch.startswith("vision")
        return await self.sensor_reply_service.send_sensor_reply(
            generation.reply, reply_category, count, reply_title, is_vision
        )

    # Helper: reset sensor motion after sensor replies.
    async def _reset_sensor_motion_after(
        self, delay_s: float, *, reply_started_at: float
    ) -> None:
        """Reset sensor motion back to idle to avoid think sticking."""
        try:
            await asyncio.sleep(max(0.2, float(delay_s)))
            if self._last_reply_time > float(reply_started_at) + 1e-6:
                return
            await self.event_bus.emit("live2d.go_idle")
        except Exception:
            return

    async def _rescue_sensor_template_reply(
        self, text: str, *, title: str, category: str
    ) -> str:
        clean = self._clean_text_for_tts(str(text or "")).strip()
        if not clean:
            return ""
        try:
            from modules.llm import chat_with_ai

            prompt = (
                "把下面这句屏幕感知回应改成一句自然的临场短话。\n"
                "要求：直接对 Master 说；不要像观察报告；不要出现“用户正在、屏幕上、画面中、我看到、根据、当前窗口、"
                "挺实用、步骤详尽、需要协助、这也要看、盯着、你又、还真”。\n"
                "允许关心、疑问、陪一句或很轻的吐槽；最多 28 个字；只输出最终一句。\n"
                f"窗口：{title} / {category}\n"
                f"原句：{clean}"
            )
            reply = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "user", "content": prompt}],
                task_type="default",
                caller="sensor_template_rescue",
            )
            rescued = self._clean_text_for_tts(
                self._strip_wrapping_quotes(
                    self._strip_internal_tags(
                        self._strip_cmd_anywhere(
                            self._strip_emo_tags_anywhere(reply or "")
                        )
                    )
                )
            ).strip()
            if rescued and not sensor_utils.looks_like_sensor_template_reply(
                rescued, clean_text_fn=self._clean_text_for_tts
            ):
                return rescued
        except Exception:
            pass
        return ""

    # ==================== 音乐感知事件处理 ====================
    async def handle_music_event(self, title: str, artist: str):
        """处理音乐播放事件（优化版）"""
        # 防抖
        if time.time() - self._last_reply_time < self._sensor_min_reply_interval_sec:
            return

        print(f"🎵 [Music] 正在聆听: {title} by {artist}")

        # 1. 切换到听歌动作
        asyncio.create_task(
            self.event_bus.emit("live2d.emotion", emotion="music", intensity=1.0)
        )

        # 2. 使用当前激活角色设定
        base_prompt = DEFAULT_PERSONA
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if active_char:
                base_prompt = active_char.get("prompt", DEFAULT_PERSONA)
        except Exception:
            pass

        system_prompt = f"""
    {base_prompt}

    【当前场景】
    你正在陪用户一起听歌。歌曲信息：
    - 歌名：{title}
    - 歌手/作曲：{artist}

    【任务】
    请简短评价这首歌，或表达你的感受。

    【要求】
    - 冷静、克制的语气
    - 一句话即可（不超过30字）
    - 如果不熟悉这首歌，可以根据歌名/歌手风格推测
    - 可以使用你的语癖「……对」（但不强制）
    """

        try:
            messages = [{"role": "system", "content": system_prompt}]

            # ✅ 使用 default 路由（更聪明的模型）
            reply = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="default",
                caller="send_active_alert",
            )
            reply = (reply or "").strip()

            if not reply or len(reply) < 2:
                return

            # ✅ 提取情绪标签
            extracted_emo, clean_text = self._extract_emo_tag(reply)
            clean_text = self._apply_character_catchphrase(clean_text)
            if not clean_text:
                return
            final_emo = extracted_emo or "neutral"

            # ✅ 根据歌手/歌名推测情绪（如果没有标签）
            if not extracted_emo:
                # 梶浦由記的作品通常比较史诗/悲壮
                if "kajiura" in artist.lower() or "梶浦" in artist:
                    final_emo = "think"
                # 可以添加更多规则...

            await self.event_bus.emit("ui.append", role="assistant", text=clean_text)
            await self.presenter.present(clean_text, emotion=final_emo, interrupt=False)

            self._update_active_time()
            asyncio.create_task(
                self._add_memory_safe(
                    "assistant",
                    f"我评价了歌曲《{title}》: {clean_text}",
                    meta={"path": "music"},
                )
            )

        except Exception as e:
            self.logger.error(f"处理音乐事件失败: {e}")

    def _get_runtime_owner_label(self) -> str:
        app = self.app
        if app is None:
            return ""
        try:
            loader = getattr(app, "_load_runtime_settings", None)
            normalizer = getattr(app, "_normalize_external_runtime_settings", None)
            if not callable(loader) or not callable(normalizer):
                return ""
            runtime = normalizer(loader())
            return str(runtime.get("napcat_owner_label") or "").strip()
        except Exception:
            return ""

    async def summarize_day(
        self,
        report_data: str = None,
        raw_stats: Optional[Dict[str, Any]] = None,
        auto: bool = False,
        target_date: date = None,
        output_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        return await self.diary_service.summarize_day(
            report_data=report_data,
            raw_stats=raw_stats,
            auto=auto,
            target_date=target_date,
            output_profile=output_profile,
        )

    def _load_day_transcript_rows(self, date_str: str) -> list[Dict[str, Any]]:
        store = getattr(self.brain, "sqlite_store", None)
        return diary_utils.load_day_transcript_rows(
            store,
            date_str,
            on_error=lambda exc: print(f"[ChatService] Load day transcript failed: {exc}"),
        )

