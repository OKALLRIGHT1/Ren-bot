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
from pathlib import Path
from typing import Optional, Dict, Any, Awaitable, Callable, List

from modules.llm import chat_complete, chat_with_ai

try:
    from modules.llm import chat_with_ai_stream
except ImportError:
    chat_with_ai_stream = None

from modules.live2d import trigger_motion
from modules.delegate_task_state import set_task_state as set_delegate_task_state
from modules.delegate_session import add_event as delegate_add_event
from modules.personality_system import get_personality_system
from modules.runtime_settings import load_runtime_settings
from core.message_source import REMOTE_CHAT_SOURCES, build_output_profile
from services.chat_support import (
    active_alert_service,
    diary_service,
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
    sensor_reply_service,
    sensor_utils,
    search_flow_service,
    text_utils,
    tool_flow_service,
    tool_result_formatter,
)
from services.chat_support.direct_flow_service import DirectFlowHooks, run_direct_turn
from services.chat_support.followup_service import FollowupService
from services.chat_support.reply_gatekeeper import should_reply as gatekeeper_should_reply
from services.chat_support.flow_result import STATUS_REPLIED
from services.chat_support.qq_input_service import QqInputConfig, QqInputService
from services.chat_support.qq_link_enrichment import QqLinkEnrichmentService
from services.chat_support.qq_private_buffer import QqPrivateMessageBuffer
from services.chat_support.natural_chat_pipeline import (
    NaturalChatConfig,
    PlainChatHooks,
    apply_forbidden_phrase_guard,
    assemble_chat_context_messages,
    build_character_thought_block,
    build_scene_prompt,
    chat_message_type,
    format_expression_block,
    looks_like_chat_command,
    retry_reply_without_forbidden,
    run_plain_chat,
    scope_matches,
    should_run_plain_chat,
)
from services.agent_runtime import AgentRuntime
from services.plugin_ports import AppPort, BrainKnowledgePort, ChatGatewayPort, TtsPort
from services.capability_gatekeeper import resolve_ambiguous_capability
from services.chat_support.tool_invocation import (
    get_tool_invocation_mode,
    should_attach_deferred_tool_catalog,
    should_attach_mcp_tool_prompt,
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
        DEFAULT_PERSONA,
        VISION_MODE,
        WAKE_KEYWORDS,
        CHARACTER_SHARING_ENABLED,
        CHARACTER_NATURAL_CHAT,
        CHAT_DEBUG_PRINTS,
        NAPCAT_OWNER_USER_IDS,
        QQ_PRIVATE_CONTINUOUS_COMMAND_PREFIXES,
        QQ_PRIVATE_CONTINUOUS_DEBOUNCE_SEC,
        QQ_PRIVATE_CONTINUOUS_ENABLE_FORWARD_CONTEXT,
        QQ_PRIVATE_CONTINUOUS_ENABLE_REPLY_CONTEXT,
        QQ_PRIVATE_CONTINUOUS_ENABLE_RECALL,
        QQ_PRIVATE_CONTINUOUS_ENABLE_TYPING,
        QQ_PRIVATE_CONTINUOUS_MAX_ITEMS,
        QQ_PRIVATE_CONTINUOUS_MAX_TEXT_CHARS,
        QQ_PRIVATE_CONTINUOUS_MAX_TYPING_WAIT_SEC,
        QQ_PRIVATE_CONTINUOUS_MESSAGE_ENABLED,
        QQ_PRIVATE_CONTINUOUS_SHORT_DEBOUNCE_SEC,
        QQ_PRIVATE_LINK_ENRICHMENT_ENABLED,
        QQ_PRIVATE_LINK_ENRICHMENT_MAX_LINKS,
        QQ_PRIVATE_LINK_ENRICHMENT_TIMEOUT_SEC,
        SCREEN_GLOBAL_COOLDOWN,
        MEMORY_SETTINGS,
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
    CHARACTER_NATURAL_CHAT = {
        "character_thought_enabled": True,
        "character_thought_scope": "desktop_and_qq_private",
        "group_chat_natural_enabled": False,
        "character_thought_timeout_ms": 4000,
        "character_thought_max_tokens": 220,
        "expression_inject_in_main_reply": True,
        "expression_inject_max_items": 1,
        "character_thought_on_error": "skip_thought",
        "forbidden_phrase_max_retries": 1,
        "detail_intent_bypass_short_shell": True,
    }
    CHAT_DEBUG_PRINTS = False
    NAPCAT_OWNER_USER_IDS = []
    QQ_PRIVATE_CONTINUOUS_COMMAND_PREFIXES = ["/", "!", "！", "#"]
    QQ_PRIVATE_CONTINUOUS_DEBOUNCE_SEC = 3.2
    QQ_PRIVATE_CONTINUOUS_ENABLE_FORWARD_CONTEXT = True
    QQ_PRIVATE_CONTINUOUS_ENABLE_REPLY_CONTEXT = True
    QQ_PRIVATE_CONTINUOUS_ENABLE_RECALL = True
    QQ_PRIVATE_CONTINUOUS_ENABLE_TYPING = True
    QQ_PRIVATE_CONTINUOUS_MAX_ITEMS = 12
    QQ_PRIVATE_CONTINUOUS_MAX_TEXT_CHARS = 2400
    MEMORY_SETTINGS = {}
    QQ_PRIVATE_CONTINUOUS_MAX_TYPING_WAIT_SEC = 12.0
    QQ_PRIVATE_CONTINUOUS_MESSAGE_ENABLED = True
    QQ_PRIVATE_CONTINUOUS_SHORT_DEBOUNCE_SEC = 2.2
    QQ_PRIVATE_LINK_ENRICHMENT_ENABLED = False
    QQ_PRIVATE_LINK_ENRICHMENT_MAX_LINKS = 3
    QQ_PRIVATE_LINK_ENRICHMENT_TIMEOUT_SEC = 8.0
    SCREEN_GLOBAL_COOLDOWN = 120

QQ_REMOTE_SOURCES = gateway_sender.QQ_REMOTE_SOURCES
OWNER_SHARED_SESSION_ID = "owner_shared"
OWNER_SHARED_LOCAL_SOURCES = {"text_input", "voice"}
LEGACY_OWNER_PRIVATE_SESSION_IDS = {
    f"private:{str(item).strip()}"
    for item in (NAPCAT_OWNER_USER_IDS or [])
    if str(item).strip()
}


# ── ASR 纠错「修正：原词→改成词」解析 ──────────────────────────────

_CORRECTION_RE = re.compile(
    r"^\s*修正[:：]\s*(.+?)\s*(?:→|->|=>|➝|➔)\s*(.+?)\s*$"
)


def _parse_correction_pair(text: str) -> Optional[tuple[str, str]]:
    """Parse ``修正：原词→改成词`` (full/half-width colon, →/->/=> accepted)."""
    if not text:
        return None
    match = _CORRECTION_RE.match(str(text).strip())
    if not match:
        return None
    src = match.group(1).strip()
    dst = match.group(2).strip()
    if not src or not dst or src == dst:
        return None
    return src, dst


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
        self.agent_runtime = AgentRuntime(
            plugin_manager=plugin_manager,
            mcp_bridge_getter=lambda: self.mcp_bridge,
        )
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
        self.gateway_voice_reply_enabled = False
        self.gateway_voice_reply_probability = 0
        self.gateway_voice_renderer: Optional[
            Callable[..., Awaitable[Optional[str]]]
        ] = None
        self.qq_private_message_buffer = QqPrivateMessageBuffer(
            enabled=QQ_PRIVATE_CONTINUOUS_MESSAGE_ENABLED,
            debounce_sec=QQ_PRIVATE_CONTINUOUS_DEBOUNCE_SEC,
            short_debounce_sec=QQ_PRIVATE_CONTINUOUS_SHORT_DEBOUNCE_SEC,
            max_typing_wait_sec=QQ_PRIVATE_CONTINUOUS_MAX_TYPING_WAIT_SEC,
            max_items=QQ_PRIVATE_CONTINUOUS_MAX_ITEMS,
            max_text_chars=QQ_PRIVATE_CONTINUOUS_MAX_TEXT_CHARS,
            command_prefixes=QQ_PRIVATE_CONTINUOUS_COMMAND_PREFIXES,
            enable_reply_context=QQ_PRIVATE_CONTINUOUS_ENABLE_REPLY_CONTEXT,
        )
        self.qq_link_enrichment = QqLinkEnrichmentService(
            enabled=QQ_PRIVATE_LINK_ENRICHMENT_ENABLED,
            max_links=QQ_PRIVATE_LINK_ENRICHMENT_MAX_LINKS,
            timeout_sec=QQ_PRIVATE_LINK_ENRICHMENT_TIMEOUT_SEC,
        )
        self.qq_input_service = QqInputService(
            gateway_getter=lambda: self.chat_gateway,
            message_buffer=self.qq_private_message_buffer,
            link_enrichment=self.qq_link_enrichment,
            config=QqInputConfig(
                enable_forward_context=QQ_PRIVATE_CONTINUOUS_ENABLE_FORWARD_CONTEXT,
                enable_recall=QQ_PRIVATE_CONTINUOUS_ENABLE_RECALL,
                enable_typing=QQ_PRIVATE_CONTINUOUS_ENABLE_TYPING,
            ),
            logger=self.logger,
        )
        self.tool_trigger_store = tool_flow_service.ToolTriggerStore()
        self.search_topic_store = search_flow_service.SearchTopicStore()
        self._recent_sensor_replies: List[str] = []
        self._sensor_event_lock = asyncio.Lock()
        self.gateway_context_service = self._create_gateway_context_service()
        self.conversation_event_service = self._create_conversation_event_service()
        self.gateway_sender = self._create_gateway_sender()
        self.gateway_port = ChatGatewayPort(
            gateway_getter=lambda: self.chat_gateway,
            send_text=self.gateway_sender.send_reply,
            send_image=self.gateway_sender.send_image_reply,
        )
        self.tts_port = TtsPort(presenter_getter=lambda: self.presenter)
        self.app_port = AppPort(
            app_getter=lambda: self._app_ref,
            app_root_getter=lambda: str(getattr(self, "app_root", "") or ""),
            music_handler=self.handle_music_event,
        )
        self.idle_status_service = self._create_idle_status_service()
        self.reply_style_service = self._create_reply_style_service()
        self.emotion_reply_service = self._create_emotion_reply_service()
        self.tool_result_formatter = self._create_tool_result_formatter()
        self.hardware_status_service = self._create_hardware_status_service()
        self.active_alert_service = self._create_active_alert_service()
        self.sensor_event_service = self._create_sensor_event_service()
        self.sensor_reply_service = self._create_sensor_reply_service()
        self.diary_service = self._create_diary_service()
        self.followup_service = FollowupService(
            memory_store_getter=self._get_memory_store,
            add_memory_safe=self._add_memory_safe,
            logger=self.logger,
        )

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
            memory_store_getter=self._get_memory_store,
            logger=self.logger,
        )

    def _create_conversation_event_service(self):
        from services.chat_support.conversation_event_service import (
            ConversationEventService,
        )
        from modules.conversation_events.store import ConversationEventStore

        settings = dict(MEMORY_SETTINGS or {})
        enabled = bool(settings.get("conversation_events_enabled", True))
        store = None
        sqlite_store = getattr(self.brain, "sqlite_store", None)
        if enabled and sqlite_store is not None:
            try:
                store = ConversationEventStore(sqlite_store)
            except Exception as exc:
                if self.logger:
                    self.logger.warning(
                        f"[ConversationEvents] store init failed: {exc}"
                    )
                store = None
        return ConversationEventService(
            store=store,
            gateway_context_service=self.gateway_context_service,
            enabled=enabled and store is not None,
            default_persona_id="suzu",
            screen_event_ttl_sec=int(settings.get("screen_event_ttl_sec", 1800) or 1800),
            logger=self.logger,
        )

    async def _record_message_pair_events(
        self,
        *,
        ctx: Optional[Dict[str, Any]],
        user_text: str,
        assistant_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        existing_user_event_id: str = "",
        assistant_parent_event_id: str = "",
    ) -> tuple[str, str]:
        """T1 dual-write: events = near-history authority; transcript via add_memory_safe."""
        service = getattr(self, "conversation_event_service", None)
        if service is None or not getattr(service, "is_ready", False):
            return "", ""
        try:
            return await service.record_message_pair(
                ctx=ctx,
                user_text=user_text,
                assistant_text=assistant_text,
                metadata=metadata,
                existing_user_event_id=existing_user_event_id,
                assistant_parent_event_id=assistant_parent_event_id,
            )
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    f"[ConversationEvents] record_message_pair failed: {exc}"
                )
            return "", ""

    async def _record_tool_execution_events(
        self,
        *,
        ctx: Dict[str, Any],
        user_text: str,
        event_state: Dict[str, str],
        command_text: str,
        triggered: bool,
        outputs: list[Any],
        used_triggers: list[str],
    ) -> None:
        service = getattr(self, "conversation_event_service", None)
        if service is None or not getattr(service, "is_ready", False):
            return
        triggers = [
            str(item or "").strip()
            for item in used_triggers
            if str(item or "").strip()
        ]
        if not triggers:
            return

        user_event_id = str(event_state.get("user_event_id") or "")
        if not user_event_id:
            user_event = service.record_user_message(
                ctx=ctx,
                text=user_text,
                metadata={"path": "tool_use", "role": "user"},
            )
            user_event_id = user_event.event_id if user_event else ""
            if user_event_id:
                event_state["user_event_id"] = user_event_id

        parent_event_id = str(
            event_state.get("assistant_parent_event_id") or user_event_id
        )
        result_rows = [str(item or "") for item in outputs]
        for index, tool_name in enumerate(triggers):
            call_event = service.record_tool_call(
                ctx=ctx,
                tool_name=tool_name,
                arguments_summary=str(command_text or "")[:1000],
                parent_event_id=parent_event_id,
                metadata={"path": "tool_loop"},
            )
            if call_event is None:
                continue
            parent_event_id = call_event.event_id
            result_summary = result_rows[index] if index < len(result_rows) else ""
            result_event = service.record_tool_result(
                ctx=ctx,
                tool_name=tool_name,
                success=bool(triggered),
                result_summary=result_summary[:1500],
                parent_event_id=call_event.event_id,
                metadata={"path": "tool_loop"},
            )
            if result_event is not None:
                parent_event_id = result_event.event_id
        if parent_event_id:
            event_state["assistant_parent_event_id"] = parent_event_id

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
            personality_state_getter=self._get_personality_reply_state,
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
            apply_character_catchphrase=self._apply_character_catchphrase,
            logger=self.logger,
            conversation_event_service=getattr(
                self, "conversation_event_service", None
            ),
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
            conversation_event_service=getattr(
                self, "conversation_event_service", None
            ),
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
            remember_sensor_reply=self._remember_sensor_reply,
            update_active_time=self._update_active_time,
            infer_reply_emotion_with_llm=self._infer_reply_emotion_with_llm,
            get_current_live2d_emotion=self._get_current_live2d_emotion,
            add_memory_safe=self._add_memory_safe,
            last_reply_time_getter=lambda: self._last_reply_time,
            conversation_event_service=getattr(
                self, "conversation_event_service", None
            ),
            clean_text_for_tts=self._clean_text_for_tts,
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
            owner_ids=[str(item).strip() for item in (NAPCAT_OWNER_USER_IDS or [])],
            owner_shared_session_id=OWNER_SHARED_SESSION_ID,
            legacy_owner_private_session_ids=LEGACY_OWNER_PRIVATE_SESSION_IDS,
            owner_shared_local_sources=OWNER_SHARED_LOCAL_SOURCES,
            qq_remote_sources=QQ_REMOTE_SOURCES,
            get_active_character_context=self._get_active_character_context,
            chat_gateway_getter=lambda: self.chat_gateway,
            app_getter=lambda: self.app,
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
        clean = self._strip_internal_tags(
            self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(clean))
        ).strip()
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

    async def _maybe_buffer_qq_private_message(
        self, user_text: str, ctx: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        return await self.qq_input_service.prepare_private_message(user_text, ctx)

    async def handle_external_chat_notice(self, event: Any) -> None:
        await self.qq_input_service.handle_notice(event)

    def _make_plain_chat_hooks(
        self, *, ctx: Optional[Dict[str, Any]], user_text: str
    ) -> PlainChatHooks:
        return PlainChatHooks(
            brain=self.brain,
            plugin_manager=self.plugin_manager,
            chat_with_ai=chat_with_ai,
            thought_chat_fn=chat_with_ai,
            natural_config=self._get_natural_chat_config(),
            get_active_character_context=self._get_active_character_context,
            consume_character_switch=self._consume_character_switch_flag,
            wants_detailed_answer=self._wants_detailed_answer,
            looks_like_command=self._looks_like_chat_command,
            clean_retry_text=self._clean_forbidden_retry_text,
            normalize_qq_reply=self._normalize_qq_reply_style,
            build_expression=self._build_expression_inject_block,
            get_short_term_messages=self._get_short_term_messages,
            build_user_address_context=self._build_user_address_context,
            build_external_sender_context=self._build_external_sender_context,
            build_live2d_self_awareness_hint=self._build_live2d_self_awareness_hint,
            build_current_emotion_context=self._build_current_emotion_context,
            build_reply_style_context=self._build_reply_style_context,
            get_memory_person_id=self._get_memory_person_id,
            extract_emo_tag=self._extract_emo_tag,
            clean_text_for_tts=self._clean_text_for_tts,
            strip_internal_tags=self._strip_internal_tags,
            strip_cmd_anywhere=self._strip_cmd_anywhere,
            strip_emo_tags_anywhere=self._strip_emo_tags_anywhere,
            should_suppress_followup_preface=self._should_suppress_followup_preface,
            merge_preface_texts=self._merge_preface_texts,
            apply_character_catchphrase=self._apply_character_catchphrase,
            prepare_reply_for_output=self._prepare_reply_for_output,
            infer_reply_emotion_with_llm=self._infer_reply_emotion_with_llm,
            contains_cmd=self._contains_cmd,
            looks_like_uncertain_answer=self._looks_like_uncertain_answer,
            is_searchworthy_question=self._is_searchworthy_question,
            skill_manager=getattr(self, "skill_manager", None),
            personality=self.personality,
            logger=self.logger,
            record_tool_execution=lambda event_state, **event: self._record_tool_execution_events(
                ctx=ctx or {},
                user_text=user_text,
                event_state=event_state,
                **event,
            ),
            trace_process=self._trace_process,
        )

    def _make_direct_flow_hooks(self) -> DirectFlowHooks:
        return DirectFlowHooks(
            send_text=self._send_gateway_reply,
            send_image=self._send_gateway_image_reply,
            send_file=self._send_gateway_file_reply,
            send_voice=self._send_gateway_voice_reply,
            cleanup_image=self.gateway_sender.cleanup_image_file,
            present_direct_tool_text=self._present_direct_tool_text,
            presenter=self.presenter,
            event_bus=self.event_bus,
            add_memory_safe=self._add_memory_safe,
            update_active_time=self._update_active_time,
            set_codex_task_state=self._set_codex_task_state,
            emit_idle_status_when_safe=self._emit_idle_status_when_safe,
            schedule_app_restart=self._schedule_app_restart,
            logger=self.logger,
        )

    @property
    def _last_tool_triggers_by_session(self) -> Dict[str, List[str]]:
        store = getattr(self, "tool_trigger_store", None)
        if store is None:
            self.tool_trigger_store = tool_flow_service.ToolTriggerStore()
            store = self.tool_trigger_store
        return store.by_session

    @_last_tool_triggers_by_session.setter
    def _last_tool_triggers_by_session(self, value: Dict[str, List[str]]) -> None:
        store = getattr(self, "tool_trigger_store", None)
        if store is None:
            self.tool_trigger_store = tool_flow_service.ToolTriggerStore()
            store = self.tool_trigger_store
        store.by_session = dict(value or {})

    @property
    def _last_search_topic_by_session(self) -> Dict[str, str]:
        store = getattr(self, "search_topic_store", None)
        if store is None:
            self.search_topic_store = search_flow_service.SearchTopicStore()
            store = self.search_topic_store
        return store.by_session

    @_last_search_topic_by_session.setter
    def _last_search_topic_by_session(self, value: Dict[str, str]) -> None:
        store = getattr(self, "search_topic_store", None)
        if store is None:
            self.search_topic_store = search_flow_service.SearchTopicStore()
            store = self.search_topic_store
        store.by_session = dict(value or {})

    def _make_tool_turn_hooks(self, *, ctx: Dict[str, Any], user_text: str):
        from services.chat_support.tool_turn import ToolTurnHooks

        return ToolTurnHooks(
            plugin_manager=self.plugin_manager,
            loop_chat=chat_with_ai,
            plain_chat=chat_with_ai,
            contains_cmd=self._contains_cmd,
            strip_cmd_anywhere=self._strip_cmd_anywhere,
            strip_emo_tags_anywhere=self._strip_emo_tags_anywhere,
            clean_text_for_tts=self._clean_text_for_tts,
            extract_emo_tag=self._extract_emo_tag,
            try_share=self.personality.try_share,
            looks_like_uncertain_answer=self._looks_like_uncertain_answer,
            is_searchworthy_question=self._is_searchworthy_question,
            is_search_delegate=self._is_search_delegate_route,
            extract_workspace_read_path=self._extract_workspace_read_path,
            record_tool_execution=lambda event_state, **event: self._record_tool_execution_events(
                ctx=ctx,
                user_text=user_text,
                event_state=event_state,
                **event,
            ),
            set_delegate_task_state=self._set_delegate_task_state,
            add_delegate_session_event=self._add_delegate_session_event,
            queue_background_delegate=lambda **kwargs: asyncio.create_task(
                self._run_background_delegate_task(**kwargs)
            ),
            add_codex_session_event=self._add_codex_session_event,
            add_memory_safe=self._add_memory_safe,
            apply_forbidden_guard=self._apply_forbidden_phrase_guard,
            reply_start_emotion=self._reply_start_emotion,
            looks_like_upstream_error=self._looks_like_upstream_error_reply,
            strip_internal_tags=self._strip_internal_tags,
            should_suppress_followup_preface=self._should_suppress_followup_preface,
            merge_preface_texts=self._merge_preface_texts,
            apply_character_catchphrase=self._apply_character_catchphrase,
            prepare_reply_for_output=self._prepare_reply_for_output,
            infer_reply_emotion_with_llm=self._infer_reply_emotion_with_llm,
            remember_search_topic=self._remember_search_topic,
            trace_process=self._trace_process,
            logger=self.logger,
        )

    def _make_stream_turn_hooks(self):
        from services.chat_support.stream_turn import StreamTurnHooks

        return StreamTurnHooks(
            event_bus=self.event_bus,
            add_codex_session_event=self._add_codex_session_event,
            reply_start_emotion=self._reply_start_emotion,
            strip_complete_cmd=lambda buffer: self._cmd_re.sub("", buffer),
            emo_tag_re=self._emo_tag_re,
            normalize_emo=self._normalize_emo,
            clean_text_for_tts=self._clean_text_for_tts,
            strip_internal_tags=self._strip_internal_tags,
            strip_cmd_anywhere=self._strip_cmd_anywhere,
            strip_emo_tags_anywhere=self._strip_emo_tags_anywhere,
            strip_model_catchphrase=self._strip_model_catchphrase,
            apply_forbidden_guard=self._apply_forbidden_phrase_guard,
            try_share=self.personality.try_share,
            apply_character_catchphrase=self._apply_character_catchphrase,
            prepare_reply_for_output=self._prepare_reply_for_output,
            presenter_output_controls_idle=self._presenter_output_controls_idle,
            sensor_emotion_intensity=sensor_utils.sensor_emotion_intensity,
            trace_process=self._trace_process,
            logger=self.logger,
        )

    def _clean_forbidden_retry_text(self, text: str) -> str:
        return self._strip_model_catchphrase(
            self._clean_text_for_tts(
                self._strip_wrapping_quotes(
                    self._strip_internal_tags(
                        self._strip_cmd_anywhere(
                            self._strip_emo_tags_anywhere(text or "")
                        )
                    )
                )
            )
        ).strip()

    def _get_natural_chat_config(self) -> NaturalChatConfig:
        try:
            raw = CHARACTER_NATURAL_CHAT
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return NaturalChatConfig.from_mapping(raw)

    def _natural_chat_message_type(self, ctx: Optional[Dict[str, Any]]) -> str:
        return chat_message_type(ctx)

    def _looks_like_chat_command(self, user_text: str) -> bool:
        return looks_like_chat_command(user_text)

    def _consume_character_switch_flag(self) -> bool:
        """True once after active character id changes (first natural-chat turn)."""
        try:
            _name, char_id, _prompt = self._get_active_character_context()
        except Exception:
            char_id = ""
        prev = getattr(self, "_natural_chat_last_character_id", None)
        self._natural_chat_last_character_id = char_id
        if prev is None:
            return False
        return bool(char_id) and str(prev) != str(char_id)

    async def _maybe_build_character_thought_block(
        self,
        *,
        user_text: str,
        ctx: Optional[Dict[str, Any]],
        need_tools: bool = False,
        codex_mode: bool = False,
        recent: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Run Character Thought when gate allows; returns (prompt_block, trace)."""
        return await build_character_thought_block(
            user_text=user_text,
            ctx=ctx,
            config=self._get_natural_chat_config(),
            need_tools=need_tools,
            codex_mode=codex_mode,
            recent=recent,
            get_active_character_context=self._get_active_character_context,
            consume_character_switch=self._consume_character_switch_flag,
            wants_detailed_answer=self._wants_detailed_answer,
            looks_like_command=self._looks_like_chat_command,
            chat_fn=chat_with_ai,
            get_short_term_messages=self._get_short_term_messages,
            logger=self.logger,
        )

    def _build_expression_inject_block(
        self,
        user_text: str,
        ctx: Optional[Dict[str, Any]] = None,
        *,
        config: Optional[NaturalChatConfig] = None,
        recent: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, List[str]]:
        cfg = config or self._get_natural_chat_config()
        if not cfg.expression_inject_in_main_reply:
            return "", []
        limit = max(1, int(cfg.expression_inject_max_items or 1))
        hints = self._load_expression_library_hints(
            user_text, "chat", ctx, recent=recent, limit=limit
        )
        hints = [h for h in hints if str(h).strip()][:limit]
        return format_expression_block(hints, max_items=limit), hints

    async def _retry_reply_without_forbidden(
        self,
        *,
        user_text: str,
        draft_text: str,
        constraint: str,
        ctx: Optional[Dict[str, Any]] = None,
    ) -> str:
        """One-shot constrained rewrite only when forbidden phrases hit (not polish mode)."""
        return await retry_reply_without_forbidden(
            user_text=user_text,
            draft_text=draft_text,
            constraint=constraint,
            ctx=ctx,
            get_active_character_context=self._get_active_character_context,
            chat_fn=chat_with_ai,
            clean_retry_text=self._clean_forbidden_retry_text,
            normalize_qq_reply=self._normalize_qq_reply_style,
        )

    async def _apply_forbidden_phrase_guard(
        self,
        *,
        text: str,
        user_text: str,
        ctx: Optional[Dict[str, Any]],
        regenerate: Optional[Callable[..., Awaitable[str]]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        return await apply_forbidden_phrase_guard(
            text=text,
            user_text=user_text,
            ctx=ctx,
            config=self._get_natural_chat_config(),
            retry_reply=self._retry_reply_without_forbidden,
            logger=self.logger,
            regenerate=regenerate,
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

    def _build_mcp_tool_prompt(self, max_tools: int = 16, invocation_mode: str = "") -> str:
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
        from services.chat_support.tool_invocation import normalize_invocation_mode

        mode = normalize_invocation_mode(invocation_mode or get_tool_invocation_mode())
        if mode in {"dual", "function"}:
            how = (
                "这些工具需要通过副脑委托 mcp_tools。"
                "需要时调用 mcp_tools，并说明目标工具和参数。不要在正文写工具协议。\n"
            )
        else:
            how = (
                "这些工具需要通过副脑委托调用。需要使用时，先委托 mcp_tools，并说明目标工具和参数。\n"
                "委托示例：\n"
                "[CMD: mcp_tools | call_tool ||| 工具名 ||| JSON参数]\n"
                "如需查看当前可用远程工具，可委托：\n"
                "[CMD: mcp_tools | list_tools]\n"
            )
        return "\n\n【远程MCP工具】\n" + how + "\n".join(lines)

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
        memory = self._get_short_term_messages(ctx)
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
            session_id, _user_id = self.gateway_context_service.reply_effect_identity(ctx)
            memory_core = getattr(self.brain, "memory_core", None)
            if memory_core is None:
                return
            record = memory_core.observe_followup(
                session_id=session_id,
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
            session_id, _user_id = self.gateway_context_service.reply_effect_identity(ctx)
            memory_core = getattr(self.brain, "memory_core", None)
            if memory_core is None:
                return
            profile = self._get_active_character_profile()
            memory_core.record_reply(
                session_id=session_id,
                person_id=self._get_memory_person_id(ctx) or "owner",
                character_name=str(profile.get("name") or "").strip(),
                text=reply_text,
                source=source or str((ctx or {}).get("source") or ""),
            )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"Reply effect record failed: {exc}")

    async def _backfill_napcat_history_for_day(self, date_str: str) -> int:
        return await self.diary_service.backfill_napcat_history_for_day(date_str)

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
        await self.gateway_context_service.sync_qq_user_profile(ctx)

    def _build_external_sender_context(self, ctx: Optional[Dict[str, Any]]) -> str:
        return self.gateway_context_service.build_external_sender_context(ctx)

    def _get_active_user_address(self, ctx: Optional[Dict[str, Any]] = None) -> str:
        return self.gateway_context_service.get_active_user_address(ctx)

    def _build_user_address_context(self, ctx: Optional[Dict[str, Any]] = None) -> str:
        return self.gateway_context_service.build_user_address_context(ctx)

    def _looks_like_explicit_code_agent_request(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if not re.search(r"codex|claude\s*code|(^|[^\w])cc([^\w]|$)", raw, re.IGNORECASE):
            return False
        return bool(
            re.search(
                r"(分析|检查|查看|看一下|看看|读一下|审查|排查|修复|修改|重构|改一下|处理|接手|帮我|画|画图|绘图|生图|生成图片)",
                raw,
                re.IGNORECASE,
            )
        )

    def _runtime_bool_setting(self, *keys: str) -> bool:
        app = self.app
        if app is None:
            return False
        try:
            loader = getattr(app, "_load_runtime_settings", None)
            normalizer = getattr(app, "_normalize_external_runtime_settings", None)
            if not callable(loader):
                return False
            runtime = loader()
            if callable(normalizer):
                runtime = normalizer(runtime)
            if not isinstance(runtime, dict):
                return False
            for key in keys:
                if bool(runtime.get(key)):
                    return True
        except Exception:
            return False
        return False

    def _get_memory_session_id(self, ctx: Optional[Dict[str, Any]]) -> str:
        return self.gateway_context_service.memory_session_id(ctx)

    def _get_memory_person_id(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not self.gateway_context_service.is_qq_source(ctx):
            return "owner"
        channel_meta = (ctx or {}).get("channel_meta") or {}
        if bool(channel_meta.get("is_owner")):
            return "owner"
        user_id = str(channel_meta.get("user_id") or "").strip()
        return f"qq:{user_id}" if user_id else ""

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
        """Scene prompt: shared core for desktop + QQ; shell differs only lightly."""
        source = str((ctx or {}).get("source") or "").strip().lower()
        message_type = self._natural_chat_message_type(ctx)
        detail_requested = self._wants_detailed_answer(user_text)
        config = self._get_natural_chat_config()
        parts_text = build_scene_prompt(
            source,
            message_type,
            detail_intent=detail_requested,
            bypass_short_shell=bool(
                detail_requested and config.detail_intent_bypass_short_shell
            ),
        )
        effect_hint = self._build_reply_effect_style_hint(ctx)
        if effect_hint:
            # 反馈提示本身已是一行，直接挂上；不重复系统规则里的记忆说明
            parts_text = parts_text + "\n" + effect_hint
        return parts_text

    def _build_reply_effect_style_hint(self, ctx: Optional[Dict[str, Any]]) -> str:
        memory_core = getattr(self.brain, "memory_core", None)
        if memory_core is None:
            return ""
        try:
            session_id, _user_id = self.gateway_context_service.reply_effect_identity(ctx)
            stats = memory_core.feedback_stats(session_id=session_id, limit=80)
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

    def _get_short_term_messages(
        self, ctx: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        brain = getattr(self, "brain", None)
        if brain is None:
            return []
        session_id = self.gateway_context_service.conversation_session_key(ctx)
        if session_id:
            manager = getattr(brain, "short_term_manager", None)
            get_context = getattr(manager, "get_context", None)
            if callable(get_context):
                try:
                    return list(get_context(session_id=session_id) or [])
                except Exception as exc:
                    self.logger.warning(
                        f"Load session short-term context failed ({session_id}): {exc}"
                    )
                    return []
            buckets = getattr(brain, "session_short_term_memory", None)
            if isinstance(buckets, dict):
                return list(buckets.get(session_id, []) or [])
            return []
        memory = getattr(brain, "short_term_memory", None)
        return list(memory or []) if isinstance(memory, list) else []

    def _build_recent_chat_tone_context(
        self,
        max_items: int = 4,
        ctx: Optional[Dict[str, Any]] = None,
    ) -> str:
        memory = self._get_short_term_messages(ctx)
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

    def _load_expression_library_hints(
        self,
        user_text: str,
        scene: str = "chat",
        ctx: Optional[Dict[str, Any]] = None,
        recent: Optional[List[Dict[str, Any]]] = None,
        limit: Optional[int] = None,
    ) -> List[str]:
        runtime = self._load_expression_library_runtime()
        if not runtime.get("expression_library_enabled", True):
            return []
        if scene == "sensor" and not runtime.get("expression_library_use_in_screen", True):
            return []
        if scene != "sensor" and not runtime.get("expression_library_use_in_chat", True):
            return []
        memory_core = getattr(self.brain, "memory_core", None)
        if memory_core is None:
            return []
        profile = self._get_active_character_profile()
        character_name = str(profile.get("name") or "").strip()
        _active_name, character_id, _base_prompt = self._get_active_character_context()
        try:
            recent_messages = list(
                recent if recent is not None else self._get_short_term_messages(ctx)
            )[-6:]
            runtime_limit = min(3, runtime.get("expression_library_max_prompt_items", 4))
            select_limit = runtime_limit
            if limit is not None:
                select_limit = max(1, min(runtime_limit, int(limit)))
            return memory_core.select_expressions(
                user_text=user_text,
                character_id=character_id,
                character_name=character_name,
                scene=scene,
                recent_messages=recent_messages,
                limit=select_limit,
                session_id=self._get_memory_session_id(ctx),
                person_id=self._get_memory_person_id(ctx) or "owner",
                use_llm=scene != "chat",
            )
        except Exception:
            return []

    def _build_natural_habits_block(
        self,
        user_text: str,
        scene: str = "chat",
        ctx: Optional[Dict[str, Any]] = None,
    ) -> str:
        profile = self._get_active_character_profile()
        name = str(profile.get("name") or "").strip()
        custom_habits = profile.get("expression_habits")
        habits: List[str] = []
        if isinstance(custom_habits, dict):
            scene_items = custom_habits.get(scene)
            if isinstance(scene_items, list):
                habits.extend(str(item).strip() for item in scene_items if str(item).strip())
        habits.extend(self._load_expression_library_hints(user_text, scene, ctx))
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
        # 闲聊不再二次 polish；传感器只在草稿已经像观察报告时才改写。
        if scene != "sensor":
            return False
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
        recent_context = self._build_recent_chat_tone_context(ctx=ctx)
        recent_sensor_context = (
            self._format_recent_sensor_reply_block() if scene == "sensor" else ""
        )
        habits_block = self._build_natural_habits_block(user_text, scene, ctx)
        source = str((ctx or {}).get("source") or "").strip().lower()
        if scene == "sensor":
            scene_prompt = (
                f"屏幕感知临场句。像「{char_name}」低声接一句，不要再解释画面；最多 36 字。"
            )
        elif source in QQ_REMOTE_SOURCES:
            scene_prompt = (
                f"QQ 消息。像「{char_name}」随手回，1～2 短句，少用句号。"
                "不要拔高成价值判断，不要复述原句。"
            )
        else:
            scene_prompt = "日常闲聊改写：1～2 短句，不要扩写。"

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
            "7) 只跟随当前角色气质，不要套用其他固定角色的说话方式。\n"
            f"{scene_prompt}"
        )
        if scene == "sensor":
            system_prompt += "\n不要写成观察报告或助手口吻。"

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
                image_base64 = await asyncio.to_thread(
                    load_image_base64, image_meta, source="remote"
                )
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

    def _looks_like_image_reference_request(self, user_text: str) -> bool:
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        if any(
            marker in text
            for marker in ("画图", "绘图", "生成图片", "图片接口", "图片设置", "图像模型")
        ):
            return False
        has_image_reference = any(
            marker in text
            for marker in ("这张图", "这幅图", "图上", "图里", "图片", "照片", "截图", "画面")
        )
        has_image_action = any(
            marker in text
            for marker in (
                "总结",
                "分析",
                "看看",
                "看下",
                "描述",
                "识别",
                "读取",
                "效果",
                "内容",
                "是什么",
                "怎么样",
            )
        )
        return has_image_reference and has_image_action

    def _detect_feedback(self, user_text: str) -> tuple[str, str]:
        return self.reply_style_service.detect_feedback(
            user_text,
            negative_keywords=self.NEGATIVE_FEEDBACK_KEYWORDS,
            positive_keywords=self.POSITIVE_FEEDBACK_KEYWORDS,
        )

    def _looks_like_user_file_read_request(self, user_text: str) -> bool:
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        has_place = any(
            hint in text
            for hint in ("下载目录", "文档目录", "桌面", "documents", "downloads", "desktop")
        )
        has_read_action = any(
            hint in text for hint in ("看看", "查看", "读取", "读一下", "列出", "打开")
        )
        has_file_name = bool(
            re.search(r"\.(txt|md|json|py|log|csv|zip|png|jpe?g|pdf|docx?)\b", text)
        )
        return has_place and (has_read_action or has_file_name)

    def _should_force_capability_route(
        self,
        *,
        route_reason: str,
        normal_triggers: List[str],
        used_triggers: List[str],
    ) -> bool:
        from services.chat_support.tool_turn import should_force_capability_route

        return should_force_capability_route(
            route_reason=route_reason,
            normal_triggers=normal_triggers,
            used_triggers=used_triggers,
        )

    def _looks_like_preserved_tool_output(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return True
        if raw.startswith("# ") or raw.startswith("```"):
            return True
        if "\n" in raw and (
            re.search(r"^[A-Za-z]:\\", raw)
            or re.search(r"^/[^\n]+", raw)
            or raw.count("\n") >= 3
        ):
            return True
        return False

    def _looks_like_direct_tool_error(self, text: str) -> bool:
        raw = str(text or "").strip()
        return raw.startswith(
            (
                "文件不存在:",
                "路径越界:",
                "读取权限已关闭",
                "写入权限已关闭",
                "未知用户文件根目录:",
                "代码代理需要允许执行命令",
                "未找到 Codex 命令",
                "未找到 Claude Code 命令",
                "MCP 调用失败",
                "工具超时",
                "⚠️",
            )
        )

    async def _present_direct_tool_text(
        self,
        *,
        user_text: str,
        direct_text: str,
        ctx: Dict[str, Any],
    ) -> str:
        raw = str(direct_text or "").strip()
        if not raw or self._looks_like_preserved_tool_output(raw):
            return raw
        if self._looks_like_direct_tool_error(raw):
            if raw.startswith("……那个"):
                return raw
            return "……那个，工具那边是这样回的：\n" + raw
        try:
            return await self._polish_natural_reply(
                user_text=user_text,
                draft_text=raw,
                ctx=ctx,
                scene="direct_tool",
            )
        except Exception:
            return raw

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
            from modules.sensor_prompts import format_sensor_prompt, join_prompt_parts

            hint = format_sensor_prompt(
                "self_awareness",
                active_name=active_name,
                costume_line=costume_line,
            )
            if str((ctx or {}).get("sensor_category") or "") != "self":
                hint = join_prompt_parts(
                    hint, format_sensor_prompt("self_awareness_screenshot")
                )
            return hint
        return ""

    def _get_current_live2d_emotion(self) -> tuple[str, float]:
        return self.emotion_reply_service.get_current_live2d_emotion()

    def _reply_start_emotion(self, ctx: Optional[Dict[str, Any]] = None) -> tuple[str, float]:
        return self.emotion_reply_service.reply_start_emotion(ctx)

    def _get_personality_reply_state(self) -> Dict[str, Any]:
        personality = getattr(self, "personality", None)
        if personality is None:
            return {}
        getter = getattr(personality, "get_reply_state", None)
        if callable(getter):
            return dict(getter() or {})
        getter = getattr(personality, "get_state", None)
        if callable(getter):
            return dict(getter() or {})
        return {}

    def _observe_final_reply_emotion(self, emotion: str) -> None:
        normalized = self._normalize_emo(emotion)
        if not normalized:
            return
        adjust = getattr(getattr(self, "personality", None), "adjust_emotion", None)
        if not callable(adjust):
            return
        intensity = max(0.0, min(0.5, sensor_utils.sensor_emotion_intensity(normalized) * 0.5))
        adjust(normalized, intensity)

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
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if active_char:
                base_prompt = active_char.get("prompt", DEFAULT_PERSONA)
        except Exception:
            pass

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = [
            f"【当前时间】{current_time}",
            str(base_prompt or "").strip(),
            self._build_current_emotion_context(ctx),
        ]
        from modules.sensor_prompts import format_sensor_prompt

        persona_extra = format_sensor_prompt("persona_extra")
        if persona_extra:
            parts.append(persona_extra)
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

    def _looks_like_question_or_recall(self, text: str) -> bool:
        return FollowupService.looks_like_question_or_recall(text)

    def _extract_task_candidates(self, text: str) -> list[str]:
        return self.followup_service.extract_task_candidates(text)

    async def _update_task_agent(self, user_text: str):
        return await self.followup_service.update_task_agent(user_text)

    def _should_suppress_followup_preface(self, user_text: str) -> bool:
        return FollowupService.should_suppress_followup_preface(user_text)

    async def _maybe_send_task_followup(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        return await self.followup_service.maybe_send_task_followup(user_text, ctx)

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

    def _has_search_delegate(self) -> bool:
        delegate_map = getattr(self.plugin_manager, "delegate_map", None) or {}
        for key, plugin in dict(delegate_map).items():
            names = {
                str(key or "").strip().lower(),
                str(getattr(plugin, "plugin_trigger", "") or "").strip().lower(),
                str(getattr(plugin, "llm_command", "") or "").strip().lower(),
            }
            if names & {"search", "search_web"}:
                return True
        return False

    def _is_search_delegate_route(self, triggers, raw_text: str) -> bool:
        normalized = [
            str(trigger or "").strip()
            for trigger in (triggers or [])
            if str(trigger or "").strip()
        ]
        if self.tool_result_formatter.is_search_delegate(normalized, raw_text):
            return True
        delegate_map = getattr(self.plugin_manager, "delegate_map", {}) or {}
        return any(
            str(getattr(delegate_map.get(trigger), "plugin_trigger", "") or "")
            .strip()
            .lower()
            == "search_web"
            for trigger in normalized
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
        record_chat_log: bool = True,
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
        if record_chat_log:
            await self.event_bus.emit(
                "chat.log",
                role="assistant",
                content=final_text,
                meta=assistant_log_meta,
            )
        pace_ui = output_coordinator.should_pace_assistant_ui(output_profile)
        if output_profile.get("ui_append", True) and not pace_ui:
            await output_coordinator.emit_assistant_ui_parts(self.event_bus, final_text)
        await self.presenter.present(
            final_text,
            emotion,
            interrupt=interrupt,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
            append_ui=pace_ui,
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
        invocation_mode = get_tool_invocation_mode()
        await delegate_flow_service.run_background_delegate_task(
            user_text=user_text,
            ctx=ctx,
            context_messages=context_messages,
            delegate_triggers=delegate_triggers,
            task_reasoning=task_reasoning,
            transcript_meta=transcript_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            plugin_manager=self.plugin_manager,
            chat_with_ai=(
                chat_complete
                if invocation_mode in {"dual", "function"}
                else chat_with_ai
            ),
            set_delegate_task_state=self._set_delegate_task_state,
            add_delegate_session_event=self._add_delegate_session_event,
            should_fallback_from_moegirl=self.tool_result_formatter.should_fallback_from_moegirl,
            polish_background_delegate_reply=self.tool_result_formatter.polish_background_delegate_reply,
            emit_assistant_text=self._emit_assistant_text,
            logger=self.logger,
            invocation_mode=invocation_mode,
        )

    def _update_active_time(self):
        """更新活跃时间戳"""
        self._last_reply_time = time.time()

    def _schedule_app_restart(self, result: Dict[str, Any]) -> None:
        restart = getattr(self.app, "restart_app", None)
        if not callable(restart):
            self.logger.warning("App restart requested but restart_app is unavailable")
            return
        try:
            delay_sec = float((result or {}).get("delay_sec", 1.0))
        except Exception:
            delay_sec = 1.0
        delay_sec = max(0.0, min(10.0, delay_sec))

        def _restart() -> None:
            try:
                restart()
            except SystemExit:
                raise
            except Exception as exc:
                self.logger.error(f"App restart failed: {exc}")

        try:
            loop = asyncio.get_running_loop()
            loop.call_later(delay_sec, _restart)
        except RuntimeError:
            _restart()

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
        memory_session_id = str(safe_meta.get("session_id") or "").strip() or None
        session_id = (
            str(safe_meta.get("context_session_id", "") or "").strip()
            or memory_session_id
        )

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
                memory_session_id=memory_session_id,
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

    async def _should_reply(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> bool:
        return await gatekeeper_should_reply(
            user_text,
            ctx=ctx,
            last_reply_time=self._last_reply_time,
            get_short_term_messages=self._get_short_term_messages,
            is_searchworthy_question=self._is_searchworthy_question,
            chat_with_ai=chat_with_ai,
            logger=self.logger,
            enabled=bool(GATEKEEPER_ENABLED),
            wake_keywords=WAKE_KEYWORDS or (),
            whitelist=GATEKEEPER_WHITELIST or (),
            blacklist=GATEKEEPER_BLACKLIST or (),
            prompt_template=GATEKEEPER_PROMPT_TEMPLATE or "",
            active_session_window=int(GATEKEEPER_ACTIVE_SESSION_WINDOW or 20),
        )

    async def _record_proactive_followup(
        self, followup: Optional[Dict[str, Any]]
    ) -> None:
        await self.followup_service.record_proactive_followup(followup)

    async def _record_task_followup(self, followup: Optional[Dict[str, Any]]) -> None:
        await self.followup_service.record_task_followup(followup)

    async def _maybe_send_proactive_followup(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        return await self.followup_service.maybe_send_proactive_followup(user_text, ctx)

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

    async def _handle_asr_correction_learn(
        self, src: str, dst: str, ctx: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """Record a manual ASR fix (「修正：src→dst」) and reply with a confirmation.

        Returns the reply text on success (caller should stop here).  Falls back
        to a freshly-built corrector (persisting to the same JSON files) when the
        voice sensor is not running, so fixes are recorded regardless.
        """
        corrector = None
        app = getattr(self, "app", None)
        sensor = getattr(app, "voice_sensor", None)
        if sensor is not None:
            corrector = getattr(sensor, "corrector", None)
        if corrector is None:
            try:
                from modules.asr.adapter import build_corrector

                corrector = build_corrector()
            except Exception as exc:
                self.logger.warning(f"ASR 纠错学习失败（无法构造 corrector）: {exc}")
                return None
        try:
            entry = corrector.learn(src, dst)
        except Exception as exc:
            self.logger.warning(f"ASR 纠错学习失败: {exc}")
            return None
        count = entry.get("count", 0)
        promoted = entry.get("promoted")
        if promoted:
            reply = f"记住了，以后把「{src}」纠正成「{dst}」（第 {count} 次，已设为规则）。"
        else:
            reply = f"已记下这次修改：{src} → {dst}（第 {count} 次，再记一次就固定成规则）。"
        try:
            from services.chat_support.output_coordinator import emit_assistant_ui_parts

            await emit_assistant_ui_parts(self.event_bus, reply)
        except Exception as exc:
            self.logger.warning(f"ASR 纠错确认回复发送失败: {exc}")
        return reply

    async def process(self, user_text: str, ctx: Optional[Dict[str, Any]] = None):
        """
        处理用户输入 (主入口)
        包含：Direct指令 -> 每日总结拦截 -> Gatekeeper拦截 -> 观察插件 -> 工具路由 -> LLM回复
        """
        self._dbg(f"process() 开始，用户输入: {user_text}")

        # 0. ASR 纠错学习：解析「修正：原词→改成词」并记录，不进入 LLM。
        pair = _parse_correction_pair(user_text)
        if pair is not None:
            src, dst = pair
            confirm = await self._handle_asr_correction_learn(src, dst, ctx)
            if confirm is not None:
                return

        # 1. 初始化上下文
        if ctx is None:
            ctx = {}
        ctx["brain"] = self.brain
        ctx["knowledge"] = BrainKnowledgePort(self.brain)
        ctx["gateway"] = getattr(self, "gateway_port", None) or ChatGatewayPort.from_chat_service(
            self
        )
        ctx["tts"] = getattr(self, "tts_port", None) or TtsPort.from_chat_service(self)
        ctx["app"] = getattr(self, "app_port", None) or AppPort.from_chat_service(self)
        ctx["plugin_manager"] = self.plugin_manager
        ctx["agent_runtime"] = self.agent_runtime
        ctx["mcp_bridge"] = self.mcp_bridge
        ctx["skills"] = getattr(self, "skill_manager", None)
        # Explicit for on-demand tools (e.g. owner_channel_bridge); keeps hard isolation elsewhere.
        if getattr(self, "conversation_event_service", None) is not None:
            ctx["conversation_event_service"] = self.conversation_event_service
        ctx.setdefault("send_bubble", None)
        ctx.setdefault("trigger_motion", trigger_motion)
        ctx.setdefault("user_text", user_text)
        app_root = str(Path.cwd().resolve())
        ctx.setdefault("app_root", app_root)
        ctx.setdefault("cwd", app_root)
        ctx.setdefault("code_path", app_root)

        input_ctx = input_context.build_chat_input_context(
            ctx,
            is_qq_source=self.gateway_context_service.is_qq_source,
            get_memory_session_id=self._get_memory_session_id,
            remote_chat_sources=set(REMOTE_CHAT_SOURCES or set()),
        )
        ctx["memory_session_id"] = input_ctx.memory_session_id
        ctx["memory_person_id"] = self._get_memory_person_id(ctx) or "owner"
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
        conversation_session_id = self.gateway_context_service.conversation_session_key(ctx)
        ctx["conversation_session_id"] = conversation_session_id
        try:
            ctx["conversation_scope"] = self.conversation_event_service.resolve_scope(
                ctx,
                person_id=self._get_memory_person_id(ctx) or "owner",
            )
        except Exception as exc:
            if self.logger:
                self.logger.warning(
                    f"[ConversationEvents] resolve_scope failed: {exc}"
                )
            ctx["conversation_scope"] = None
        transcript_channel_meta = dict(input_ctx.transcript_channel_meta)
        if conversation_session_id:
            transcript_channel_meta["context_session_id"] = conversation_session_id
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
        explicit_code_agent_request = self._looks_like_explicit_code_agent_request(user_text)
        configured_code_agent_exec = self._runtime_bool_setting(
            "code_agent_allow_exec", "codex_allow_exec"
        )
        # 代码助手权限默认仅在 codex_mode 下开放；delegate 只读权限会在副脑上下文单独注入
        ctx["allow_read"] = bool(ctx.get("allow_read", False)) and (
            codex_mode or explicit_code_agent_request
        )
        ctx["allow_write"] = bool(ctx.get("allow_write", False)) and (
            codex_mode or explicit_code_agent_request
        )
        ctx["allow_exec"] = (
            bool(ctx.get("allow_exec", False)) or configured_code_agent_exec
        ) and (codex_mode or explicit_code_agent_request)
        if not codex_mode and self._looks_like_user_file_read_request(user_text):
            ctx["allow_read"] = True
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
        # 2. Direct 模式：处理“控制类”硬指令 / 待确认操作（确认/取消）
        # =========================================================================
        self._dbg("检查是否为direct命令")
        # Pending ActionGate/agent confirm is handled inside AgentRuntime before plugins
        direct_outcome = await self.agent_runtime.handle_direct_text(user_text, ctx)
        is_direct = direct_outcome.handled
        direct_result = direct_outcome.reply
        if str(user_text or "").strip().startswith(("/", "!", "！")):
            preview = str(user_text or "").replace("\n", " ").strip()
            if len(preview) > 80:
                preview = preview[:80] + "..."
            if is_direct:
                self.logger.info(f"[Direct] 已处理命令: {preview}")
            else:
                self.logger.info(
                    f"[Direct] 未命中命令(可能插件未加载/权限不足/别名不匹配): {preview}"
                )

        if is_direct:
            self._dbg("进入 Direct 命令处理流程")
            await run_direct_turn(
                user_text=user_text,
                direct_result=direct_result,
                ctx=ctx,
                output_profile=output_profile,
                transcript_channel_meta=transcript_channel_meta,
                chat_log_source=chat_log_source,
                memory_session_id=memory_session_id,
                codex_mode=codex_mode,
                hooks=self._make_direct_flow_hooks(),
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
        channel_meta = ctx.get("channel_meta") if isinstance(ctx, dict) else {}
        current_images = (
            channel_meta.get("images")
            if isinstance(channel_meta, dict)
            and isinstance(channel_meta.get("images"), list)
            else []
        )
        has_external_images = bool(current_images)
        if (
            self.gateway_context_service.is_qq_source(ctx)
            and self._looks_like_image_reference_request(user_text)
            and not has_external_images
        ):
            await output_coordinator.emit_short_reaction(
                text="我这边没收到你指的图片，引用那张图再发一次吧",
                emotion="confused",
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
                record_message_pair=self._record_message_pair_events,
            )
            return
        self._remember_search_topic(user_text, ctx)

        direct_chat_sources = input_ctx.direct_chat_sources
        source_key = input_ctx.source_key
        should_reply = (
            True
            if input_ctx.should_bypass_gatekeeper
            else await self._should_reply(user_text, ctx)
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
        # Fuzzy capability matches: ask the shared gatekeeper model chain.
        if (
            not route.need_tools
            and bool(getattr(route, "capability_ambiguous", False))
            and list(getattr(route, "capability_candidates", None) or [])
        ):
            try:
                gate_pick = await resolve_ambiguous_capability(
                    user_text=user_text,
                    candidates=list(route.capability_candidates or []),
                    chat_with_ai=chat_with_ai,
                )
            except Exception as exc:
                self.logger.warning("capability gatekeeper resolve failed: %s", exc)
                gate_pick = None
            if gate_pick is not None and gate_pick.approved and gate_pick.plugin:
                from modules.tool_router import ToolRouteResult

                route = ToolRouteResult(
                    True,
                    [gate_pick.plugin],
                    f"capability:{gate_pick.capability_id}",
                    capability_id=gate_pick.capability_id,
                    capability_args=dict(gate_pick.args or {}),
                    capability_score=float(gate_pick.confidence or 0.0),
                    capability_match_reason=str(
                        gate_pick.reason or "gatekeeper_selected"
                    ),
                )
                self.logger.info(
                    "[Gatekeeper] 模糊能力裁决: %s -> %s (%.2f)",
                    gate_pick.capability_id,
                    gate_pick.plugin,
                    float(gate_pick.confidence or 0.0),
                )
            else:
                self.logger.info(
                    "[Gatekeeper] 模糊能力未放行: %s",
                    getattr(gate_pick, "reason", None) or "no_decision",
                )
        self._dbg(
            f"路由结果: need_tools={route.need_tools}, triggers={route.tool_triggers}"
        )
        effective_triggers = list(route.tool_triggers or [])
        if (
            not route.need_tools
            and not effective_triggers
            and not codex_mode
            and text_utils.is_direct_fact_search_question(user_text)
            and self._has_search_delegate()
        ):
            effective_triggers.append("search_web")
            self.logger.info(
                "[SearchRoute] 事实问句直接搜索: %s",
                str(user_text or "")[:120],
            )
        if codex_mode and "workspace_ops" not in effective_triggers:
            effective_triggers.append("workspace_ops")
        normal_triggers, delegate_triggers = self._split_delegate_triggers(
            effective_triggers
        )
        search_delegate_requested = self._is_search_delegate_route(
            delegate_triggers,
            user_text,
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
        need_tools = bool(route.need_tools or effective_triggers)
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
            record_message_pair=self._record_message_pair_events,
        ):
            return

        deferred_tool_flow = False
        if not need_tools:
            try:
                deferred_tool_flow = bool(
                    self.plugin_manager.should_use_deferred_tool_flow(user_text)
                )
            except Exception:
                deferred_tool_flow = False

        skip_prompt_and_first_pass = False
        react_first_pass = None
        tool_event_state: Dict[str, str] = {}
        if should_run_plain_chat(
            plain_direct_chat_candidate=plain_direct_chat_candidate,
            deferred_tool_flow=deferred_tool_flow,
            delegate_triggers=delegate_triggers,
            search_delegate_requested=search_delegate_requested,
        ):
            invocation_mode = get_tool_invocation_mode()
            loop_chat = (
                chat_complete
                if invocation_mode in {"dual", "function"}
                else chat_with_ai
            )
            start_emo, start_intensity = self._reply_start_emotion(ctx)
            hooks = self._make_plain_chat_hooks(ctx=ctx, user_text=user_text)
            hooks.chat_with_ai = loop_chat
            plain_outcome = await run_plain_chat(
                user_text=user_text,
                ctx=ctx,
                hooks=hooks,
                conversation_session_id=conversation_session_id,
                memory_session_id=memory_session_id,
                source_key=source_key,
                route_reason=str(getattr(route, "reason", "") or ""),
                capability_match_reason=str(
                    getattr(route, "capability_match_reason", "") or ""
                ),
                preface_text=preface_text,
                followup_search_query=followup_search_query,
                invocation_mode=invocation_mode,
                task_default=task_default,
                task_reasoning=task_reasoning,
                character_sharing_enabled=CHARACTER_SHARING_ENABLED,
                start_emo=start_emo,
            )
            if plain_outcome.flow.status == STATUS_REPLIED:
                final_reply = plain_outcome.flow.reply_text
                final_emo = plain_outcome.flow.emotion or "neutral"
                self._observe_final_reply_emotion(final_emo)
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
                    triggered=False,
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
                    record_message_pair=self._record_message_pair_events,
                )
                return
            skip_prompt_and_first_pass = True
            react_first_pass = plain_outcome.react_first_pass
            context_messages = list(plain_outcome.context_messages)
            tool_event_state = dict(plain_outcome.tool_event_state or {})

        if skip_prompt_and_first_pass:
            use_non_stream_flow = True
        else:
            natural_reply_candidate = plain_direct_chat_candidate
            invocation_mode = get_tool_invocation_mode()
            tool_prompt = ""
            if need_tools:
                tool_prompt = self.plugin_manager.get_tool_prompt_for_triggers(
                    list(normal_triggers),
                    compact=True,
                    invocation_mode=invocation_mode,
                )
            elif should_attach_deferred_tool_catalog(
                need_tools=need_tools, deferred_tool_flow=deferred_tool_flow
            ):
                tool_prompt = self.plugin_manager.get_system_prompt_addition(
                    invocation_mode=invocation_mode
                )
            delegate_prompt = self.plugin_manager.get_delegate_prompt_for_triggers(
                list(delegate_triggers),
                compact=True,
                invocation_mode=invocation_mode,
            )
            mcp_prompt = ""
            if should_attach_mcp_tool_prompt(
                list(effective_triggers or []) + list(delegate_triggers or [])
            ):
                mcp_prompt = self._build_mcp_tool_prompt(invocation_mode=invocation_mode)
            codex_hint = ""
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

            prompt_hooks = self._make_plain_chat_hooks(ctx=ctx, user_text=user_text)
            assembled = await assemble_chat_context_messages(
                user_text=user_text,
                ctx=ctx,
                hooks=prompt_hooks,
                conversation_session_id=conversation_session_id,
                memory_session_id=memory_session_id,
                route_reason=str(getattr(route, "reason", "") or ""),
                capability_match_reason=str(
                    getattr(route, "capability_match_reason", "") or ""
                ),
                invocation_mode=invocation_mode,
                need_tools=need_tools,
                codex_mode=codex_mode,
                extra_runtime_sections=(codex_hint,),
                tool_sections=(tool_prompt, delegate_prompt, mcp_prompt),
                tool_intent=list(effective_triggers),
            )
            context_messages = list(assembled.context_messages)
            natural_cfg = prompt_hooks.natural_config

            guard_requires_non_stream = bool(
                (
                    output_profile.get("speak", True)
                    or output_profile.get("show_bubble", True)
                )
                and scope_matches(
                    source_key,
                    self._natural_chat_message_type(ctx),
                    natural_cfg,
                )
            )
            use_non_stream_flow = reply_flow_service.should_use_non_stream_flow(
                need_tools=need_tools,
                deferred_tool_flow=deferred_tool_flow,
                stream_available=chat_with_ai_stream is not None,
                natural_reply_candidate=natural_reply_candidate,
                guard_requires_non_stream=guard_requires_non_stream,
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
            invocation_mode = get_tool_invocation_mode()
            tool_hooks = self._make_tool_turn_hooks(ctx=ctx, user_text=user_text)
            tool_hooks.loop_chat = (
                chat_complete
                if invocation_mode in {"dual", "function"}
                else chat_with_ai
            )
            from services.chat_support.tool_turn import run_non_stream_tool_turn

            turn = await run_non_stream_tool_turn(
                user_text=user_text,
                ctx=ctx,
                context_messages=context_messages,
                route=route,
                need_tools=need_tools,
                deferred_tool_flow=deferred_tool_flow,
                normal_triggers=normal_triggers,
                delegate_triggers=delegate_triggers,
                effective_triggers=effective_triggers,
                followup_search_query=followup_search_query,
                search_delegate_requested=search_delegate_requested,
                task_reasoning=task_reasoning,
                task_default=task_default,
                invocation_mode=invocation_mode,
                session_key=session_key,
                memory_session_id=memory_session_id,
                preface_text=preface_text,
                transcript_channel_meta=transcript_channel_meta,
                chat_log_source=chat_log_source,
                output_profile=output_profile,
                hooks=tool_hooks,
                tool_trigger_store=self.tool_trigger_store,
                character_sharing_enabled=CHARACTER_SHARING_ENABLED,
                existing_first_pass=(
                    react_first_pass
                    if skip_prompt_and_first_pass and react_first_pass is not None
                    else None
                ),
                existing_tool_event_state=(
                    tool_event_state if skip_prompt_and_first_pass else None
                ),
            )
            self._observe_final_reply_emotion(turn.emotion)
            await output_coordinator.emit_non_stream_reply(
                final_reply=turn.reply,
                final_emo=turn.emotion,
                user_text=user_text,
                ctx=ctx,
                output_profile=output_profile,
                transcript_meta=transcript_channel_meta,
                chat_log_source=chat_log_source,
                memory_session_id=memory_session_id,
                feedback_type=feedback_type,
                feedback_reaction=feedback_reaction,
                triggered=turn.triggered,
                learning=self.learning,
                live2d_enabled=live2d_enabled,
                start_emo=turn.start_emo,
                start_intensity=turn.start_intensity,
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
                record_message_pair=lambda **message: self._record_message_pair_events(
                    existing_user_event_id=str(
                        turn.tool_event_state.get("user_event_id") or ""
                    ),
                    assistant_parent_event_id=str(
                        turn.tool_event_state.get("assistant_parent_event_id") or ""
                    ),
                    **message,
                ),
            )

        # =========================================================================
        # 7. 分支 B: 流式对话 (Stream) - 🟢 已修复重复写入问题 & 加回分享欲
        # =========================================================================
        else:
            self._dbg("进入流式对话流程")
            from services.chat_support.stream_turn import run_stream_turn

            stream_turn = await run_stream_turn(
                user_text=user_text,
                ctx=ctx,
                context_messages=context_messages,
                chat_with_ai_stream=chat_with_ai_stream,
                task_default=task_default,
                preface_text=preface_text,
                output_profile=output_profile,
                live2d_enabled=live2d_enabled,
                character_sharing_enabled=CHARACTER_SHARING_ENABLED,
                effective_triggers=effective_triggers,
                hooks=self._make_stream_turn_hooks(),
            )
            await output_coordinator.emit_stream_reply(
                full_reply=stream_turn.reply,
                emotion=stream_turn.emotion,
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
                record_message_pair=self._record_message_pair_events,
                presenter=self.presenter,
            )

    # 🟢 [新增] 主动关怀提醒
    async def send_active_alert(self, app_name: str, minutes: int):
        """处理久坐提醒"""
        await self.active_alert_service.send_active_alert(app_name, minutes)

    # ==================== 屏幕感知事件处理 (文本 + 视觉) ====================

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
        from modules.llm import analyze_image, chat_with_ai

        return await self.sensor_event_service.handle_window_event(
            window_title=window_title,
            category=category,
            count=count,
            use_vision=use_vision,
            app_name=app_name,
            reason=reason,
            app_duration_sec=app_duration_sec,
            current_stay_sec=current_stay_sec,
            last_reply_time=self._last_reply_time,
            min_reply_interval_sec=self._sensor_min_reply_interval_sec,
            vision_mode=VISION_MODE,
            chat_with_ai=chat_with_ai,
            analyze_image=analyze_image,
            send_sensor_reply=self.sensor_reply_service.send_sensor_reply,
        )

    async def _reset_sensor_motion_after(
        self, delay_s: float, *, reply_started_at: float
    ) -> None:
        await self.sensor_reply_service.reset_motion_after(
            delay_s, reply_started_at=reply_started_at
        )

    async def _rescue_sensor_template_reply(
        self, text: str, *, title: str, category: str
    ) -> str:
        return await self.sensor_reply_service.rescue_template_reply(
            text, title=title, category=category
        )

    async def handle_music_event(self, title: str, artist: str):
        await sensor_event_service.run_music_event(
            title=title,
            artist=artist,
            last_reply_time=self._last_reply_time,
            min_reply_interval_sec=self._sensor_min_reply_interval_sec,
            event_bus=self.event_bus,
            presenter=self.presenter,
            logger=self.logger,
            extract_emo_tag=self._extract_emo_tag,
            apply_character_catchphrase=self._apply_character_catchphrase,
            observe_final_reply_emotion=self._observe_final_reply_emotion,
            update_active_time=self._update_active_time,
            add_memory_safe=self._add_memory_safe,
            default_persona=DEFAULT_PERSONA,
            chat_with_ai=chat_with_ai,
        )

    def _get_runtime_owner_label(self) -> str:
        return self.diary_service.get_runtime_owner_label()

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
        return self.diary_service.load_day_transcript_rows(date_str)

