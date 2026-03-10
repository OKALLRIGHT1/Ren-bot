"""
聊天服务
处理用户输入和AI响应的核心逻辑
"""
import os
import json
import re
import asyncio
import time
import uuid
import random  # ✅ 需要导入 random
from datetime import datetime,timedelta, date
from typing import Optional, Dict, Any, Awaitable, Callable

from modules.llm import chat_with_ai
try:
    from modules.llm import chat_with_ai_stream
except ImportError:
    chat_with_ai_stream = None

from modules.live2d import trigger_motion
from modules.personality_system import get_personality_system
from core.message_source import REMOTE_CHAT_SOURCES, build_output_profile

# 引入 Gatekeeper 配置
try:
    from config import (
        EMO_LABELS,
        GATEKEEPER_ENABLED,
        GATEKEEPER_WHITELIST,
        GATEKEEPER_BLACKLIST,
        GATEKEEPER_PROMPT_TEMPLATE,
        GATEKEEPER_ACTIVE_SESSION_WINDOW, PERSONA_PROMPT, DEFAULT_PERSONA, VISION_MODE, WAKE_KEYWORDS,
        CHARACTER_SHARING_ENABLED, CHAT_DEBUG_PRINTS, NAPCAT_OWNER_USER_IDS
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

QQ_REMOTE_SOURCES = {"qq_gateway", "napcat_qq"}
OWNER_SHARED_SESSION_ID = "owner_shared"
OWNER_SHARED_LOCAL_SOURCES = {"text_input", "voice"}
LEGACY_OWNER_PRIVATE_SESSION_IDS = {
    f"private:{str(item).strip()}"
    for item in (NAPCAT_OWNER_USER_IDS or [])
    if str(item).strip()
}


class ChatService:
    """聊天服务"""
    POSITIVE_FEEDBACK_KEYWORDS = (
        "谢谢", "谢啦", "有帮助", "有用", "喜欢", "不错", "很好", "太好了",
        "棒", "完美", "正是", "thanks", "thank you", "great", "good job",
    )
    NEGATIVE_FEEDBACK_KEYWORDS = (
        "不对", "不行", "不好", "没用", "不喜欢", "错了", "答非所问", "重来",
        "离谱", "不准确", "不需要", "没帮上", "wrong", "bad", "not helpful",
    )
    APPLY_CONFIRM_KEYWORDS = ("确认", "应用", "执行", "同意", "apply")
    TASK_CREATE_KEYWORDS = (
        "待办", "todo", "to do", "提醒我", "记得", "别忘了", "要记得",
        "今天要", "明天要", "今晚要", "等会要", "待会要", "周末要",
        "计划", "打算", "安排", "准备", "要去", "需要", "得去", "我得", "我要",
    )
    TASK_DONE_KEYWORDS = (
        "做完了", "搞定了", "完成了", "弄完了", "处理完了", "解决了", "写完了",
        "提交了", "发了", "结束了", "done", "finished", "搞好了", "已经好了",
    )
    TASK_STATUS_KEYWORDS = (
        "任务", "待办", "todo", "进度", "安排", "计划", "打算", "提醒",
    )
    FOLLOWUP_TOPICS = {
        "health": (
            "腹泻", "拉肚子", "肚子疼", "胃痛", "发烧", "咳嗽", "头痛", "生病", "不舒服",
            "断食", "补液", "电解质", "医院", "就医",
        ),
        "sleep": (
            "熬夜", "失眠", "没睡好", "睡不着", "睡眠", "困", "早睡", "晚睡", "睡觉",
        ),
        "diet": (
            "没吃饭", "吃不下", "胃口", "饮食", "喝水", "脱水", "饿", "早餐", "午饭", "晚饭",
        ),
        "work_study": (
            "加班", "赶工", "ddl", "截止", "写代码", "开题", "报告", "复习", "考试", "作业", "项目",
        ),
        "emotion": (
            "焦虑", "压力", "难受", "烦", "崩溃", "低落", "紧张", "心情", "不开心", "累",
        ),
        "plan": (
            "明天", "计划", "打算", "安排", "要去", "准备", "目标", "待办",
        ),
    }

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
        self.debug_enabled = bool(CHAT_DEBUG_PRINTS)
        self.personality = get_personality_system()
        # 延迟导入以避免循环依赖
        try:
            from modules.learning_system import get_learning_system
            self.learning = get_learning_system()
        except ImportError:
            self.learning = None

        self._last_reply_time = 0  # 记录最后一次回复的时间戳
        self._sensor_min_reply_interval_sec = 45

        # 情绪标签配置
        self._emo_set = set([str(x).strip().lower() for x in (EMO_LABELS or [])] + ["idle", "think"])
        self._emo_tag_re = re.compile(r"<\s*emo\s*=\s*([a-zA-Z_]+)\s*>", flags=re.IGNORECASE)
        self._cmd_re = re.compile(r"\[CMD:.*?\]", flags=re.DOTALL)
        self._apply_cmd_re = re.compile(
            r"\[CMD:\s*workspace_ops\s*\|\s*apply_change\s*\|\|\|\s*([0-9a-fA-F]{10})\s*\|\|\|\s*([0-9a-fA-F]{8})\s*\]",
            flags=re.IGNORECASE,
        )
        self._id_token_re = re.compile(r"\b([0-9a-fA-F]{10})\b[\s\S]{0,120}\b([0-9a-fA-F]{8})\b", flags=re.IGNORECASE)
        self._last_proactive_followup_day = ""
        self._last_task_followup_day = ""
        self.gateway_voice_reply_enabled = False
        self.gateway_voice_reply_probability = 0
        self.gateway_voice_renderer: Optional[Callable[..., Awaitable[Optional[str]]]] = None

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

    async def _cleanup_gateway_voice_file(self, path: str, delay_sec: float = 45.0):
        file_path = str(path or "").strip()
        if not file_path:
            return
        try:
            await asyncio.sleep(max(0.0, float(delay_sec)))
        except Exception:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    async def _cleanup_gateway_image_file(self, path: str, delay_sec: float = 45.0):
        file_path = str(path or "").strip()
        if not file_path:
            return
        try:
            await asyncio.sleep(max(0.0, float(delay_sec)))
        except Exception:
            pass
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    def _should_use_gateway_voice_reply(self, source: str, text: str) -> bool:
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        if not self.gateway_voice_reply_enabled:
            return False
        if not callable(self.gateway_voice_renderer):
            return False
        if self.gateway_voice_reply_probability <= 0:
            return False
        clean = self._clean_text_for_tts(self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(text)))
        if len(clean) < 2:
            return False
        return random.random() * 100 < self.gateway_voice_reply_probability

    async def _send_gateway_reply(self, text: str, ctx: Optional[Dict[str, Any]] = None, emotion: Optional[str] = None):
        text = str(text or "").strip()
        if not text or not self.chat_gateway or not isinstance(ctx, dict):
            return
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return
        channel_meta = ctx.get("channel_meta") or {}
        adapter_name = str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            self.logger.warning("Gateway reply skipped: missing session_id")
            return
        voice_path = ""
        if self._should_use_gateway_voice_reply(source, text):
            clean_voice_text = self._clean_text_for_tts(self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(text)))
            try:
                voice_path = str(await self.gateway_voice_renderer(
                    clean_voice_text,
                    emotion=emotion,
                    source=source,
                    channel_meta=channel_meta,
                ) or "").strip()
            except Exception as e:
                self.logger.warning(f"Gateway voice render failed: {e}")
                voice_path = ""
            if voice_path:
                try:
                    result = await self.chat_gateway.send_voice(adapter_name, session_id, voice_path, metadata=channel_meta, source=source)
                    if isinstance(result, dict) and result.get("ok"):
                        return
                    self.logger.warning(f"Gateway voice send fallback to text: {result}")
                except Exception as e:
                    self.logger.warning(f"Gateway voice send failed, fallback to text: {e}")
                finally:
                    asyncio.create_task(self._cleanup_gateway_voice_file(voice_path))
        try:
            result = await self.chat_gateway.send_text(adapter_name, session_id, text, metadata=channel_meta, source=source)
            if isinstance(result, dict) and not result.get("ok"):
                self.logger.warning(f"Gateway text reply failed: {result}")
        except Exception as e:
            self.logger.error(f"Gateway reply failed: {e}")

    async def _send_gateway_image_reply(self, image_path: str, ctx: Optional[Dict[str, Any]] = None, caption: str = "") -> bool:
        path_text = str(image_path or "").strip()
        if not path_text or not self.chat_gateway or not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        channel_meta = ctx.get("channel_meta") or {}
        adapter_name = str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            self.logger.warning("Gateway image reply skipped: missing session_id")
            return False
        try:
            result = await self.chat_gateway.send_image(
                adapter_name,
                session_id,
                path_text,
                caption=caption,
                metadata=channel_meta,
                source=source,
            )
            if isinstance(result, dict) and result.get("ok"):
                return True
            self.logger.warning(f"Gateway image reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway image reply failed: {e}")
            return False

    async def _emit_idle_status(self, output_profile: Optional[Dict[str, Any]], reason: str) -> None:
        live2d_enabled = True
        if isinstance(output_profile, dict):
            live2d_enabled = bool(output_profile.get("live2d_enabled", True))
        if live2d_enabled:
            await self.event_bus.emit("state.changed", state="idle", reason=reason)
        else:
            await self.event_bus.emit("ui.status", text="Idle")

    def _dbg(self, message: str):
        if self.debug_enabled:
            self.logger.debug(message)

    def _build_mcp_tool_prompt(self, max_tools: int = 16) -> str:
        if not self.mcp_bridge:
            return ""
        try:
            specs = [spec for spec in self.mcp_bridge.list_tools() if getattr(spec, "provider", "local") != "local"]
        except Exception:
            return ""
        if not specs:
            return ""
        if max_tools and len(specs) > max_tools:
            specs = specs[:max_tools]
        lines = []
        for spec in specs:
            desc = str(getattr(spec, "description", "") or "").replace("\n", " ").strip()
            if len(desc) > 72:
                desc = desc[:72] + "…"
            lines.append(f"- {spec.name}: {desc or 'MCP tool'}")
        return (
            "\n\n【远程MCP工具】\n"
            "如需调用远程 MCP 工具，可使用独立一行：\n"
            "[CMD: mcp_tools | call_tool ||| 工具名 ||| JSON参数]\n"
            "如需查看当前可用的远程工具，可使用：\n"
            "[CMD: mcp_tools | list_tools]\n"
            + "\n".join(lines)
        )

    def _is_qq_source(self, ctx: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        return source in QQ_REMOTE_SOURCES

    def _build_transcript_channel_meta(self, ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not self._is_qq_source(ctx):
            return {}
        channel_meta = (ctx or {}).get("channel_meta") or {}
        result: Dict[str, Any] = {}
        for key in ("adapter", "user_id", "sender_name", "message_type", "group_id", "is_owner", "owner_label"):
            value = channel_meta.get(key)
            if value in (None, "", [], {}):
                continue
            result[key] = value
        return result

    def _is_owner_shared_context(self, ctx: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        if source in OWNER_SHARED_LOCAL_SOURCES:
            return True
        if source not in QQ_REMOTE_SOURCES:
            return False
        channel_meta = ctx.get("channel_meta") or {}
        return bool(channel_meta.get("is_owner"))

    async def _sync_qq_user_profile(self, ctx: Optional[Dict[str, Any]]) -> None:
        if not self._is_qq_source(ctx):
            return
        channel_meta = (ctx or {}).get("channel_meta") or {}
        user_id = str(channel_meta.get("user_id") or "").strip()
        if not user_id:
            return
        store = self._get_memory_store()
        if not store:
            return
        sender = channel_meta.get("sender") or {}
        sender_name = str(channel_meta.get("sender_name") or sender.get("nickname") or user_id).strip()
        remark_name = str(sender.get("card") or sender.get("remark") or "").strip()
        message_type = str(channel_meta.get("message_type") or "private").strip().lower() or "private"
        is_owner = bool(channel_meta.get("is_owner"))
        relationship = "owner" if is_owner else ("group_member" if message_type == "group" else "contact")
        memory_scope = OWNER_SHARED_SESSION_ID if is_owner else ("group_shared" if message_type == "group" else "private")
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
        if not self._is_qq_source(ctx):
            return ""
        channel_meta = ctx.get("channel_meta") or {}
        sender_name = str(channel_meta.get("sender_name") or channel_meta.get("user_id") or "Unknown Contact").strip()
        user_id = str(channel_meta.get("user_id") or "").strip()
        session_id = str(channel_meta.get("session_id") or "").strip()
        group_id = str(channel_meta.get("group_id") or "").strip()
        message_type = str(channel_meta.get("message_type") or "private").strip() or "private"
        owner_label = str(channel_meta.get("owner_label") or "Owner").strip() or "Owner"
        is_owner = bool(channel_meta.get("is_owner"))
        relation = (
            f"The current sender is {owner_label}."
            if is_owner else
            f"The current sender is not {owner_label}; they are another QQ contact or a group member."
        )
        parts = [
            "[QQ Message Context]",
            f"chat_type: {message_type}",
            f"sender: {sender_name}",
            relation,
            "Always distinguish the owner from other QQ contacts.",
            "Keep using the current local persona and tone.",
            "QQ messages are text-only and must not drive Live2D or desktop voice.",
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
            display_name = str(profile.get("remark_name") or profile.get("nickname") or "").strip()
            if display_name and display_name != sender_name:
                parts.append(f"profile_name: {display_name}")
            profile_relation = relation_map.get(str(profile.get("relationship_to_owner") or "").strip())
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
        if not isinstance(ctx, dict):
            return ""
        source = str(ctx.get("source") or "").strip().lower()
        if source in OWNER_SHARED_LOCAL_SOURCES:
            return OWNER_SHARED_SESSION_ID
        if source not in QQ_REMOTE_SOURCES:
            return ""
        channel_meta = ctx.get("channel_meta") or {}
        if bool(channel_meta.get("is_owner")):
            return OWNER_SHARED_SESSION_ID
        return str(channel_meta.get("session_id") or "").strip()

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

        prompt = str(channel_meta.get("image_prompt") or "请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。")
        image_summaries = []
        for index, image_meta in enumerate(images[:3], 1):
            try:
                image_base64 = await asyncio.to_thread(load_image_base64, image_meta)
                if not image_base64:
                    image_summaries.append(f"[图片{index}] 无法读取图片数据。")
                    continue
                desc = await analyze_image(image_base64, prompt)
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
        text = (user_text or "").strip().lower()
        if not text:
            return "neutral", "neutral"
        if any(k in text for k in self.NEGATIVE_FEEDBACK_KEYWORDS):
            return "explicit_negative", "negative"
        if any(k in text for k in self.POSITIVE_FEEDBACK_KEYWORDS):
            return "explicit", "positive"
        return "neutral", "neutral"

    def _extract_apply_confirmation(self, user_text: str) -> tuple[bool, str, str]:
        text = (user_text or "").strip()
        if not text:
            return False, "", ""
        m = self._apply_cmd_re.search(text)
        if m:
            return True, m.group(1), m.group(2)
        lower = text.lower()
        if not any(k in lower for k in self.APPLY_CONFIRM_KEYWORDS):
            return False, "", ""
        m2 = self._id_token_re.search(text)
        if m2:
            return True, m2.group(1), m2.group(2)
        return False, "", ""

    def _set_codex_task_state(self, ctx: Dict[str, Any], state: str, *, summary: str = "", meta: Optional[dict] = None):
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
        return [seg.strip(" ，,、\t") for seg in re.split(r"[\n。！？!?；;]+", text or "") if seg.strip(" ，,、\t")]

    def _normalize_task_text(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        t = re.sub(r"^(待办|todo|to do)\s*[:：]?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"^(提醒我|记得|别忘了|要记得)\s*", "", t)
        t = re.sub(r"^(我(今天|明天|今晚|等会|待会|周末|之后|最近)?(要|得|想|准备|打算)|今天要|明天要|今晚要|等会要|待会要|周末要)\s*", "", t)
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
            parts = [seg.strip(" ，,、\t") for seg in re.split(r"[，,、]+", raw) if seg.strip(" ，,、\t")]
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
            score = self._task_match_score(hint, str(item.get("text") or "")) - (idx * 0.05)
            if score > best_score:
                best = item
                best_score = score
        return best if best_score >= 1.0 else None

    async def _append_hidden_transcript_note(self, role: str, text: str, meta: Optional[Dict[str, Any]] = None):
        store = self._get_memory_store()
        note = str(text or "").strip()
        if not store or not note:
            return
        try:
            safe_meta = (meta or {}).copy()
            session_id = str(safe_meta.get("session_id") or "").strip() or None
            await asyncio.to_thread(store.add_transcript, role, note, safe_meta, None, session_id)
        except Exception as e:
            self.logger.warning(f"隐藏 transcript 记录失败: {e}")

    async def _upsert_user_tasks_from_text(self, user_text: str) -> list[str]:
        store = self._get_memory_store()
        if not store:
            return []
        created = []
        for task_text in self._extract_task_candidates(user_text):
            existing = self._find_matching_active_task(task_text)
            if existing and self._task_match_score(task_text, str(existing.get("text") or "")) >= 5.0:
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
        await asyncio.to_thread(store.set_item_status, str(target.get("id") or ""), "done")
        return str(target.get("text") or "").strip() or None

    async def _update_task_agent(self, user_text: str):
        completed = await self._complete_task_from_text(user_text)
        created = await self._upsert_user_tasks_from_text(user_text)
        return {"completed": completed, "created": created}

    def _build_task_followup_text(self, task_text: str) -> str:
        task = str(task_text or "").strip()
        if not task:
            return "你之前提到的那件事，今天推进得怎么样了？如果卡住了，我们可以一起拆一下。"
        return f"你之前说要{task}，今天推进得怎么样了？如果卡住了，我们可以一起拆一下。"

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
        fallback = None
        for item in items:
            updated_at = str(item.get("updated_at") or "").strip()
            try:
                updated_date = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).date()
            except Exception:
                updated_date = None
            if updated_date == yesterday:
                return item
            if updated_date and updated_date < today and fallback is None:
                fallback = item
        return fallback

    async def _maybe_send_task_followup(self, user_text: str, ctx: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
        try:
            source = str((ctx or {}).get("source", ""))
            if source and source != "text_input":
                return None
            if bool((ctx or {}).get("codex_mode", False)):
                return None
            today_str = datetime.now().strftime("%Y-%m-%d")
            if self._last_task_followup_day == today_str:
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
        if not e:
            return None
        t = str(e).strip().lower()
        t = t.strip("<>").strip()
        if t.startswith("emo="):
            t = t.split("=", 1)[1].strip()
        return t if t in self._emo_set else None

    # 文本净化函数
    def _clean_text_for_tts(self, text: str) -> str:
        if not text: return ""
        # 1. 暴力去除所有星号 * 和 #
        text = re.sub(r"[\*#]+", "", text)
        # 2. 去除 markdown 链接
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        return text.strip()
    def _strip_emo_tags_anywhere(self, text: str) -> str:
        """移除所有情绪标签"""
        return self._emo_tag_re.sub("", text or "")

    def _strip_cmd_anywhere(self, text: str) -> str:
        """移除所有命令标签"""
        return self._cmd_re.sub("", text or "")

    def _extract_emo_tag(self, text):
        """提取情绪标签"""
        raw = text or ""
        m = self._emo_tag_re.search(raw)
        if m:
            emo = self._normalize_emo(m.group(1))
            clean = self._emo_tag_re.sub("", raw, count=1).strip()
            return emo, clean
        return None, raw

    def _contains_cmd(self, text: str) -> bool:
        """检查是否包含命令"""
        return "[CMD:" in (text or "")

    def _update_active_time(self):
        """更新活跃时间戳"""
        self._last_reply_time = time.time()

    async def _show_thinking_emotion(self, text="", emotion="think"):
        """显示思考情绪"""
        await self.event_bus.emit("live2d.emotion", emotion=emotion)
        if text:
            await self.event_bus.emit("ui.append", role="assistant", text=text)

    async def _add_memory_safe(self, role: str, text: str, *, meta: Optional[dict] = None):
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
            await asyncio.to_thread(self.brain.add_memory, role, text, session_id)

            # 安全发射事件
            await self.event_bus.emit("memory.add.ok", role=role, len=len(text or ""), **safe_meta)
        except Exception as e:
            # 错误处理也要用 safe_meta
            await self.event_bus.emit("memory.add.fail", role=role, error=repr(e), len=len(text or ""), **safe_meta)

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
        wake_words = [str(word).lower() for word in (WAKE_KEYWORDS or []) if str(word).strip()]
        if any(word in lower_text for word in wake_words):
            self.logger.info("🟢 [Gatekeeper] 命中唤醒词 -> 强制回复")
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
            self.logger.info(f"🟢 [Gatekeeper] 处于活跃会话窗口 ({int(time_diff)}s < {GATEKEEPER_ACTIVE_SESSION_WINDOW}s) -> 强制回复")
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
                user_text=text_clean,
                last_ai_reply=last_ai_reply
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

            self.logger.info(f"⚖️ [Gatekeeper] LLM 判断结果: {decision} | 输入: {text_clean[:20]}...")

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
            "昨天", "前天", "大前天", "上周", "上个月", "过去",
            "那一天", "那天", "几号", "星期", "礼拜", "周一", "周二", "周三", "周四", "周五", "周六", "周日",
            "上午", "早上", "中午", "下午", "晚上", "刚才", "之前", "还记得",
            "干了什么", "做了什么", "说过", "提过",
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
            "还记得", "记得吗", "忘了", "之前说", "我说过", "提过",
            "我为什么", "怎么会", "当时", "怎么了", "到底为啥",
            "腹泻", "断食", "体检", "医院", "不舒服", "拉肚子",
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
            return f"你昨天提到{label}，今天好些了吗？如果还不舒服，我们可以一起把节奏放慢一点。"
        if topic == "sleep":
            return "昨晚休息得怎么样？今天精力还顶得住吗？"
        if topic == "diet":
            return f"你昨天提到{label}，今天有好好吃东西、补水吗？"
        if topic == "work_study":
            return f"你昨天在忙{label}，今天推进得还顺利吗？"
        if topic == "emotion":
            return f"你昨天提到{label}，今天心情有没有轻松一点？"
        if topic == "plan":
            return f"关于你昨天说的{label}，今天进展到哪一步了？"
        return "今天状态怎么样？要不要我陪你理一下接下来的安排？"

    def _merge_proactive_followup(self, followup_text: str, reply_text: str) -> str:
        followup = (followup_text or "").strip()
        reply = (reply_text or "").strip()
        if followup and reply:
            return f"{followup}\n\n{reply}"
        return followup or reply

    async def _record_proactive_followup(self, followup: Optional[Dict[str, Any]]) -> None:
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
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        try:
            rows = store.list_transcript(limit=420, offset=0)
            for r in rows:
                if (r.get("role") or "").strip() != "user":
                    continue
                ts = int(r.get("ts", 0) or 0)
                if not ts:
                    continue
                if datetime.fromtimestamp(ts).date() != yesterday:
                    continue
                content = str(r.get("content") or "").strip()
                if not content:
                    continue
                topic = self._match_followup_topic(content)
                if not topic:
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

    async def _maybe_send_proactive_followup(self, user_text: str, ctx: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
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
            self._last_proactive_followup_day = today_str
            return {"text": followup_text, "topic": topic, "snippet": snippet}
        except Exception as e:
            self.logger.warning(f"主动关心触发失败: {e}")
        return None

    def _build_recent_transcript_context(self, limit: int = 28, max_chars: int = 1400, user_only: bool = False, session_id: str = "") -> str:
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
        if ctx is None: ctx = {}
        ctx["chat_service"] = self
        ctx["brain"] = self.brain
        ctx["mcp_bridge"] = self.mcp_bridge
        ctx.setdefault("send_bubble", None)
        ctx.setdefault("trigger_motion", trigger_motion)
        ctx.setdefault("user_text", user_text)

        input_source = str(ctx.get("source", "unknown") or "unknown").strip()
        channel_meta = ctx.get("channel_meta") or {}
        transcript_channel_meta = self._build_transcript_channel_meta(ctx)
        has_external_images = bool(channel_meta.get("has_image"))
        memory_session_id = self._get_memory_session_id(ctx)
        await self._sync_qq_user_profile(ctx)
        chat_log_source = input_source if input_source != "unknown" else "chat"
        output_profile = build_output_profile(str(input_source or "text_input"))
        live2d_enabled = bool(output_profile.get("live2d_enabled", True))
        if "codex_mode" in ctx:
            codex_mode = bool(ctx.get("codex_mode", False))
        else:
            codex_mode = input_source == "codex_input"
        ctx["codex_mode"] = codex_mode
        if codex_mode and not str(ctx.get("codex_task_id", "")).strip():
            ctx["codex_task_id"] = uuid.uuid4().hex[:8]
        code_path = str(ctx.get("code_path", "") or "").strip()
        # 代码助手权限统一受 codex_mode 约束，防止普通对话误触文件能力
        ctx["allow_read"] = bool(ctx.get("allow_read", False)) and codex_mode
        ctx["allow_write"] = bool(ctx.get("allow_write", False)) and codex_mode
        ctx["allow_exec"] = bool(ctx.get("allow_exec", False)) and codex_mode
        allow_read = bool(ctx.get("allow_read", False))
        allow_write = bool(ctx.get("allow_write", False))
        allow_exec = bool(ctx.get("allow_exec", False))
        feedback_type, feedback_reaction = self._detect_feedback(user_text)
        apply_confirmed, confirm_change_id, confirm_token = self._extract_apply_confirmation(user_text)
        ctx["codex_user_confirmed_apply"] = bool(apply_confirmed)
        ctx["codex_confirm_change_id"] = str(confirm_change_id or "")
        ctx["codex_confirm_token"] = str(confirm_token or "")
        self.logger.debug(f"收到输入: {user_text} (来源: {input_source})")
        self.personality.update_state()

        if codex_mode:
            self._set_codex_task_state(ctx, "plan", summary=user_text[:200])
            if apply_confirmed:
                self._set_codex_task_state(
                    ctx,
                    "user_confirm_apply",
                    summary="用户确认应用变更",
                    meta={"change_id": confirm_change_id, "confirm_token": confirm_token},
                )
            self._add_codex_session_event("user_task", text=user_text, ctx=ctx)

        # =========================================================================
        # 2. Direct 模式：处理“控制类”硬指令
        # =========================================================================
        self._dbg("检查是否为direct命令")
        is_direct, direct_result = await self.plugin_manager.execute_direct_commands(user_text, ctx)

        if is_direct:
            self._dbg("进入 Direct 命令处理流程")
            direct_meta = {"path": "direct"}
            if memory_session_id:
                direct_meta["session_id"] = memory_session_id
            await self._add_memory_safe("user", user_text, meta=direct_meta)

            direct_reply_text = str(direct_result) if direct_result is not None else ""
            handled_gateway_image = False
            if isinstance(direct_result, dict) and str(direct_result.get("__type__") or "").strip() == "gateway_image":
                image_path = str(direct_result.get("image_path") or "").strip()
                image_caption = str(direct_result.get("caption") or "").strip()
                success_text = str(direct_result.get("success_text") or "🖼️ 已把当前截图发给你了。")
                fallback_text = str(direct_result.get("fallback_text") or "⚠️ 截图已生成，但回发失败了。")
                image_ok = await self._send_gateway_image_reply(image_path, ctx, caption=image_caption)
                if image_path:
                    asyncio.create_task(self._cleanup_gateway_image_file(image_path))
                direct_reply_text = success_text if image_ok else fallback_text
                handled_gateway_image = image_ok
                if not image_ok and str(ctx.get("source") or "").strip().lower() in {"qq_gateway", "napcat_qq"}:
                    await self._send_gateway_reply(direct_reply_text, ctx, emotion="neutral")

            await self._add_memory_safe("assistant", direct_reply_text, meta=direct_meta)

            if direct_reply_text:
                if self.learning:
                    self.learning.record_interaction(
                        user_text,
                        direct_reply_text,
                        "neutral",
                        feedback_type,
                        feedback_reaction,
                    )
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit("ui.append", role="assistant", text=direct_reply_text)
                await self.presenter.present(
                    direct_reply_text,
                    emotion="neutral",
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
                if not handled_gateway_image:
                    await self._send_gateway_reply(direct_reply_text, ctx, emotion="neutral")
                self._update_active_time()
                if codex_mode:
                    self._set_codex_task_state(ctx, "finalize", summary=direct_reply_text[:200])

            self._dbg("Direct 流程结束，返回 Idle")
            await self._emit_idle_status(output_profile, reason="direct_complete")
            return

        # =========================================================================
        # 3. 特殊指令拦截 (每日总结/补写)
        # =========================================================================
        if "总结今天" in user_text or "今天干了什么" in user_text or "今天的总结" in user_text:
            print("📅 [System] 拦截到每日总结请求")
            if hasattr(self, "screen_sensor_ref") and self.screen_sensor_ref:
                report = self.screen_sensor_ref.get_formatted_report()
                raw_stats = getattr(self.screen_sensor_ref, "get_stats_data", lambda: {})()

                await self.summarize_day(report, raw_stats=raw_stats, auto=False)
                asyncio.create_task(self._add_memory_safe("user", user_text, meta={"path": "summary"}))
                await self._emit_idle_status(output_profile, reason="summary_complete")
                return

        if "总结昨天" in user_text or "补写昨天" in user_text:
            print("📅 [System] 拦截到补写昨天日记请求")
            yesterday = datetime.now().date() - timedelta(days=1)
            await self.summarize_day(report_data=None, auto=False, target_date=yesterday)
            asyncio.create_task(self._add_memory_safe("user", user_text, meta={"path": "summary_makeup"}))
            await self._emit_idle_status(output_profile, reason="summary_complete")
            return

        # =========================================================================
        # 4. Gatekeeper 拦截层
        # =========================================================================
        remote_sources = {str(x).strip().lower() for x in set(REMOTE_CHAT_SOURCES or set()) if str(x).strip()}
        direct_chat_sources = {"text_input", "voice", "codex_input", *remote_sources}
        source_key = str(input_source or "").strip().lower()
        should_reply = True if source_key in direct_chat_sources or codex_mode or has_external_images else await self._should_reply(user_text)
        if not should_reply:
            print(f"🛑 [系统] Gatekeeper 决定忽略此消息")
            await self.event_bus.emit("chat.ignored", content=user_text)
            await self._emit_idle_status(output_profile, reason="gatekeeper_ignore")
            return

        # 轻量任务代理：自动提取待办 / 标记完成
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
        await self.event_bus.emit("chat.log", role="user", content=user_text, meta=user_log_meta)
        self._dbg("准备切换状态为 thinking")
        if live2d_enabled:
            await self.event_bus.emit("state.changed", state="thinking", reason="user_input")
        else:
            await self.event_bus.emit("ui.status", text="Thinking.")
        await self.personality.think_before_respond(user_text, self._show_thinking_emotion if live2d_enabled else None)

        # --- Observe 模式 ---
        if hasattr(self.plugin_manager, "execute_observe_commands"):
            self._dbg("检查 Observe 插件...")
            is_observe, obs_result = await self.plugin_manager.execute_observe_commands(user_text, ctx)

            if is_observe:
                self._dbg("Observe 触发成功")
                obs_text = ""
                if isinstance(obs_result, dict) and obs_result.get("__type__") == "image_payload":
                    await self.event_bus.emit("ui.status", text="Analyzing Visuals...")
                    try:
                        from modules import llm
                        desc = await llm.analyze_image(obs_result["image_base64"], "请客观详细描述这张图片的内容。")
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
                    asyncio.create_task(self._add_memory_safe("system", f"观察插件运行结果: {obs_text[:100]}...",
                                                              meta=observe_meta))
                    user_text = f"{user_text}\n\n{obs_text}"

        # --- 路由与 Prompt 构建 ---
        self._dbg(f"进入 LLM 对话/工具流程, Input: {user_text[:50]}...")
        route = self.tool_router.route(user_text)
        self._dbg(f"路由结果: need_tools={route.need_tools}, triggers={route.tool_triggers}")
        effective_triggers = list(route.tool_triggers or [])
        if codex_mode and "workspace_ops" not in effective_triggers:
            effective_triggers.append("workspace_ops")
        need_tools = bool(route.need_tools or (codex_mode and effective_triggers))
        if codex_mode and need_tools:
            self._set_codex_task_state(ctx, "execute", summary=user_text[:200], meta={"triggers": effective_triggers[:8]})
        task_reasoning = "codex" if codex_mode else "tool_reasoning"
        task_default = "codex" if codex_mode else "default"

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

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
                    logs = [f"📅 [{ep.get('title')}] 摘要：{ep.get('summary')}" for ep in episodes]
                    if logs:
                        special_context += f"\n\n【系统强制注入：近期活动日志】\n" + "\n".join(logs)
                transcript_ctx = self._build_recent_transcript_context(
                    limit=30,
                    max_chars=1400,
                    user_only=memory_ref,
                    session_id=memory_session_id,
                )
                if transcript_ctx:
                    special_context += f"\n\n【系统强制注入：最近对话片段】\n{transcript_ctx}"
            except Exception as e:
                print(f"❌ 历史回溯失败: {e}")

        try:
            from config import PERSONA_PROMPT
        except:
            PERSONA_PROMPT = ""

        system_text = f"【当前时间】{current_time}\n{PERSONA_PROMPT}\n{special_context}"
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
        if need_tools:
            tool_prompt = self.plugin_manager.get_tool_prompt_for_triggers(list(effective_triggers), compact=True)
        else:
            tool_prompt = self.plugin_manager.get_system_prompt_addition()
        if tool_prompt: system_text += "\n" + tool_prompt
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

        # =========================================================================
        # 6. 分支 A: ReAct 工具链
        # =========================================================================
        if need_tools or chat_with_ai_stream is None:
            self._dbg("进入工具 ReAct 流程")
            await self.event_bus.emit("ui.status", text="Thinking (Tools)...")

            reply1 = await asyncio.to_thread(
                chat_with_ai,
                context_messages,
                task_type=task_reasoning,
                caller="chat_tool_reasoning",
            )

            allow_tools = bool(need_tools) or self._contains_cmd(reply1 or "")
            ret = await self.plugin_manager.execute_commands(reply1, ctx, allow_tools=allow_tools)
            triggered, clean_thought, tool_results, used_triggers = ret
            reasoning_text = self._clean_text_for_tts(
                self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(clean_thought or reply1 or ""))
            ).strip()
            if reasoning_text:
                self._add_codex_session_event(
                    "assistant_reasoning",
                    text=reasoning_text,
                    ctx=ctx,
                    meta={"used_triggers": list(used_triggers or [])[:8]},
                )


            final_reply = ""
            final_emo = "neutral"

            if triggered and tool_results:
                _, clean1 = self._extract_emo_tag(clean_thought or "")
                if clean1: context_messages.append({"role": "assistant", "content": clean1})

                feedback = "\n".join([str(r) for r in tool_results])
                if used_triggers:
                    tool_use_meta = {"path": "tool_use"}
                    if memory_session_id:
                        tool_use_meta["session_id"] = memory_session_id
                    asyncio.create_task(
                        self._add_memory_safe("assistant", f"[tool_use] {used_triggers}", meta=tool_use_meta))

                context_messages.append({"role": "system", "content": f"【系统反馈】工具结果：\n{feedback}\n请据此回答。"})
                reply2 = await asyncio.to_thread(
                    chat_with_ai,
                    context_messages,
                    task_type=task_default,
                    caller="chat_tool_finalize",
                )

                emo2, clean2 = self._extract_emo_tag(reply2 or "")
                final_reply = clean2.strip() or clean1
                final_emo = emo2 or "neutral"

                # 尝试追加“分享欲”内容 (工具分支也加了)
                if CHARACTER_SHARING_ENABLED:
                    sharing = self.personality.try_share()
                    if sharing:
                        final_reply = f"{final_reply}\n\n{sharing}"

            else:
                emo, clean = self._extract_emo_tag(reply1 or "")
                final_reply = clean.strip() or "…"
                final_emo = emo or "neutral"
                if CHARACTER_SHARING_ENABLED:
                    sharing = self.personality.try_share()
                    if sharing:
                        final_reply += f"\n\n{sharing}"

            final_reply = self._clean_text_for_tts(self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(final_reply)))
            final_reply = self._merge_preface_texts(preface_text, final_reply)

            if self.learning:
                self.learning.record_interaction(
                    user_text,
                    final_reply,
                    final_emo,
                    feedback_type,
                    feedback_reaction,
                )

            if final_reply:
                self._update_active_time()
                self._add_codex_session_event(
                    "assistant_reply",
                    text=final_reply,
                    ctx=ctx,
                    meta={"emotion": final_emo, "tool": True},
                )
                assistant_log_meta = {"tool": True, "emotion": final_emo, "source": chat_log_source, **transcript_channel_meta}
                if memory_session_id:
                    assistant_log_meta["session_id"] = memory_session_id
                await self.event_bus.emit("chat.log", role="assistant", content=final_reply,
                                          meta=assistant_log_meta)
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit("ui.append", role="assistant", text=final_reply)
                await self.presenter.present(
                    final_reply,
                    final_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
                await self._send_gateway_reply(final_reply, ctx, emotion=final_emo)
                await self._record_proactive_followup(proactive_followup)
                await self._record_task_followup(task_followup)
                if codex_mode:
                    self._set_codex_task_state(ctx, "finalize", summary=final_reply[:200])

                # 写入记忆 (ReAct 分支)
                chat_meta = {"path": "chat"}
                if memory_session_id:
                    chat_meta["session_id"] = memory_session_id
                await self._add_memory_safe("user", user_text, meta=chat_meta)
                await self._add_memory_safe("assistant", final_reply, meta=chat_meta)

            await self._emit_idle_status(output_profile, reason="tool_end")

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
            curr_emo = "neutral"
            first = False
            full_reply = ""

            if preface_text:
                first = True
                curr_emo = "concern"
                proactive_chunk = f"{preface_text}\n\n"
                full_reply += proactive_chunk
                if output_profile.get("speak", True):
                    await self.event_bus.emit("state.changed", state="speaking", reason="proactive_followup")
                await self.event_bus.emit(
                    "assistant.stream.feed",
                    chunk=proactive_chunk,
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )

            try:
                async for chunk in chat_with_ai_stream(context_messages, task_type=task_default):
                    if not chunk: continue
                    if not first:
                        first = True
                        if output_profile.get("speak", True):
                            await self.event_bus.emit("state.changed", state="speaking", reason="stream_start")

                    buffer += chunk

                    if "[CMD:" in buffer and "]" in buffer: buffer = self._cmd_re.sub("", buffer)

                    if "<" in buffer and ">" in buffer:
                        m = self._emo_tag_re.search(buffer)
                        if m:
                            raw = self._normalize_emo(m.group(1)) or "neutral"
                            curr_emo, _ = self.personality.adjust_emotion(raw, 0.8)
                            print(f"🎭 [Stream] 检测到情绪标签: {raw} -> {curr_emo}")
                            if live2d_enabled:
                                asyncio.create_task(
                                    self.event_bus.emit("live2d.emotion", emotion=curr_emo, prefer_motion=False))
                            buffer = self._emo_tag_re.sub("", buffer, count=1)

                    if len(buffer) > 15 and any(p in buffer for p in "，。！？,.!?\n"):
                        safe = self._clean_text_for_tts(self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(buffer)))
                        if safe:
                            full_reply += safe
                            await self.event_bus.emit(
                                "assistant.stream.feed",
                                chunk=safe,
                                emotion=curr_emo,
                                speak=output_profile.get("speak", True),
                                show_bubble=output_profile.get("show_bubble", True),
                            )
                            buffer = ""

                # 处理剩余尾巴
                if buffer:
                    safe = self._clean_text_for_tts(self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(buffer)))
                    if safe:
                        full_reply += safe
                        await self.event_bus.emit(
                            "assistant.stream.feed",
                            chunk=safe,
                            emotion=curr_emo,
                            speak=output_profile.get("speak", True),
                            show_bubble=output_profile.get("show_bubble", True),
                        )

            except Exception as e:
                self.logger.error(f"Stream error: {e}")

            try:
                await self.event_bus.emit(
                    "assistant.stream.end",
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
            except Exception as e:
                self.logger.warning(f"assistant.stream.end failed: {e}")

            await self._emit_idle_status(output_profile, reason="stream_end")

            # 🟢 确保只写入一次记忆
            if full_reply:
                # 🟢 尝试追加“分享欲”内容
                # 因为流式已经结束了，分享欲作为追加内容，可以整块发送
                if CHARACTER_SHARING_ENABLED:
                    sharing = self.personality.try_share()
                    if sharing:
                    # 分享内容追加到最终文本
                        full_reply = f"{full_reply}\n\n{sharing}"
                    # 额外播放分享内容的语音

                self._update_active_time()

                # 1. 更新 UI 和 日志
                self._add_codex_session_event(
                    "assistant_reply",
                    text=full_reply,
                    ctx=ctx,
                    meta={"emotion": curr_emo, "stream": True},
                )
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit("ui.append", role="assistant", text=full_reply)
                stream_log_meta = {"stream": True, "emotion": curr_emo, "source": chat_log_source, **transcript_channel_meta}
                if memory_session_id:
                    stream_log_meta["session_id"] = memory_session_id
                await self.event_bus.emit("chat.log", role="assistant", content=full_reply,
                                          meta=stream_log_meta)
                await self._send_gateway_reply(full_reply, ctx, emotion=curr_emo)
                await self._record_proactive_followup(proactive_followup)
                await self._record_task_followup(task_followup)

                # 2. 学习系统
                if self.learning:
                    self.learning.record_interaction(
                        user_text,
                        full_reply,
                        curr_emo,
                        feedback_type,
                        feedback_reaction,
                    )

                # 3. 写入数据库 (确保只在这里调用一次)
                stream_chat_meta = {"path": "chat"}
                if memory_session_id:
                    stream_chat_meta["session_id"] = memory_session_id
                await self._add_memory_safe("user", user_text, meta=stream_chat_meta)
                await self._add_memory_safe("assistant", full_reply, meta=stream_chat_meta)
                if codex_mode:
                    self._set_codex_task_state(ctx, "finalize", summary=full_reply[:200])

    # 🟢 [新增] 主动关怀提醒
    async def send_active_alert(self, app_name: str, minutes: int):
        """处理久坐提醒"""
        print(f"⏰ [Chat] 收到久坐提醒请求: {app_name} ({minutes}m)")

        # 1. 获取人设
        base_prompt = DEFAULT_PERSONA
        try:
            from modules.character_manager import character_manager
            c = character_manager.get_active_character()
            if c: base_prompt = c.get("prompt", DEFAULT_PERSONA)
        except:
            pass

        # 2. 生成关心的话
        system_prompt = f"""
{base_prompt}

【当前情况】
用户已经在 [{app_name}] 上连续专注了 {minutes} 分钟，一直没动过。

【任务】
请主动弹窗提醒他休息、喝水或活动一下。
语气要温柔、体贴，像家人一样。
字数限制：30字以内。
"""
        try:
            reply = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "system", "content": system_prompt}],
                task_type="default",
                caller="active_alert",
            )

            if reply:
                # 3. 触发弹窗和语音
                # 发送给 UI 显示弹窗 (需 UI 支持 'ui.popup' 事件，或者直接用 append)
                await self.event_bus.emit("ui.append", role="assistant", text=f"【温馨提醒】{reply}")
                await self.presenter.present(reply, emotion="concern", interrupt=True)

        except Exception as e:
            self.logger.error(f"Active alert failed: {e}")

    # ==================== 屏幕感知事件处理 (完整版：含自我意识+视觉+文本) ====================

    async def handle_sensor_event(self, window_title: str, category: str, count: int = 1, use_vision: bool = False):
        import time
        import random
        import asyncio
        from modules.llm import chat_with_ai, analyze_image

        def clean_garbage(text):
            if not text:
                return ""
            return "".join(ch for ch in text if ch.isprintable())

        clean_title = clean_garbage(window_title)
        if not clean_title.strip():
            clean_title = category

        if time.time() - self._last_reply_time < self._sensor_min_reply_interval_sec:
            return

        print(f"🤖 [Sensor] 观察: {clean_title} ({category}) | Count: {count}")

        # 获取人设
        base_prompt = DEFAULT_PERSONA
        try:
            from modules.character_manager import character_manager
            c = character_manager.get_active_character()
            if c:
                base_prompt = c.get("prompt", DEFAULT_PERSONA)
        except:
            pass

        # ================= [分支 A] 自我意识 =================
        if category == "self":
            if count > 1 and random.random() > 0.7:
                return

            sys_prompt = f"""{base_prompt}
    用户正在盯着【你的】程序窗口({clean_title})看。
    请打破第四面墙，对他简短说一句话。
    【警告】绝不能超过 15 个字！"""

            try:
                reply = await asyncio.to_thread(
                    chat_with_ai,
                    [{"role": "system", "content": sys_prompt}],
                    task_type="default",
                    caller="sensor_self_talk",
                )
                if reply:
                    await self._send_sensor_reply(reply, "self", count, clean_title, False)
            except:
                pass
            return

        # ================= [分支：看门人 (Gatekeeper) 判断] =================
        if not use_vision:
            if count <= 2 and category not in {"self", "work"}:
                self.logger.info(f"🛑 [Sensor Gatekeeper] 低强度事件跳过 ({clean_title})")
                return

            gk_prompt = f"""
【场景】
用户刚切换到窗口: [{clean_title}] (分类: {category})，今天第 {count} 次。

【判断任务】
你是一个性格高冷、话少、克制的 AI 助手。你不需要对用户的每一次无聊操作做出反应。
只有出现以下情况才输出 YES：
1. 极度频繁的摸鱼/切屏（让人忍不住想吐槽）。
2. 连续高强度工作很久，需要打断并关心。
3. 软件名字极其特别，或者你今天第一次看到这个软件。

如果是普通的网页浏览、正常的切回编辑器、毫无亮点的日常办公，请保持高冷，严格输出 NO。

【输出格式】
仅输出：YES 或 NO
"""
            try:
                gk_decision = await asyncio.to_thread(
                    chat_with_ai,
                    [{"role": "user", "content": gk_prompt}],
                    task_type="gatekeeper",
                    caller="sensor_gatekeeper",
                )
                self.logger.info(f"⚖️ [Sensor Gatekeeper] 判断是否值得吐槽: {gk_decision.strip()}")

                if "YES" not in gk_decision.upper():
                    self.logger.info(f"🛑 [Sensor Gatekeeper] 拦截本次纯文本吐槽 ({clean_title})，保持高冷")
                    return
            except Exception as e:
                self.logger.warning(f"⚠️ [Sensor Gatekeeper] 调用失败，默认放行: {e}")

        # ================= [分支 B] 视觉模式 =================
        if use_vision:
            try:
                from modules.vision.capture import take_screenshot_base64

                print("📸 [Sensor] 正在视觉采样...")
                img_b64 = await asyncio.to_thread(take_screenshot_base64)

                if img_b64:
                    # ===== 模式 1：视觉直接吐槽 =====
                    if VISION_MODE == "direct":
                        v_prompt = f"""{base_prompt}
你正看着用户的屏幕(当前活跃窗口: [{clean_title}])。
【空间自我意识】如果你在画面边缘（如右下角）看到一个动漫女孩、桌宠或悬浮球，那就是你自己（你的实体投影）！切记不要把她当成别人。
请主要结合用户屏幕上的工作/娱乐内容进行吐槽。
【字数限制】极度重要！绝不能超过 20 个字！一句话结束！"""
                        reply = await analyze_image(img_b64, v_prompt)

                        if reply:
                            await self._send_sensor_reply(reply, category, count, window_title, True)
                            return

                    # ===== 模式 2：视觉只描述 → 默认模型吐槽 =====
                    elif VISION_MODE == "separate":
                        # 先让视觉模型客观描述，并强制它识别出助手
                        v_desc_prompt = """请客观详细描述这张图片的内容，重点描述用户正在使用的软件和文字。
【特殊指令】如果你在屏幕右下角或边缘看到一个二次元/动漫风格的女孩、虚拟形象或悬浮球，请在描述中明确标记为“这是AI助手(你)的形象”。"""

                        description = await analyze_image(img_b64, v_desc_prompt)

                        if description:
                            sys_prompt = f"""{base_prompt}

    【场景】用户当前屏幕内容如下：
    {description}

    【重要设定】描述中提到的“AI助手的形象”、“动漫女孩”就是你自己。你正隔着屏幕陪伴用户。

    【任务】
    结合用户屏幕上的主要内容（忽略你自己，关注用户在干嘛）进行一次毒舌或关心的吐槽。
    【字数限制】绝对不能超过 20 个字！用最精简的一句话表达，绝对不要像机器人一样罗列画面内容。
    """
                            reply = await asyncio.to_thread(
                                chat_with_ai,
                                [{"role": "system", "content": sys_prompt}],
                                task_type="default",
                                caller="sensor_vision_talk",
                            )

                            if reply:
                                await self._send_sensor_reply(reply, category, count, window_title, True)
                                return

            except Exception as e:
                self.logger.warning(f"Vision failed: {e}")

        # ================= [分支 C] 文本模式 =================
        sys_prompt = f"""{base_prompt}

    用户刚切换到窗口: [{clean_title}] ({category})，这是今天第 {count} 次。

    【任务】直接对他说话进行吐槽。
    【字数限制】极度重要！最多绝对不能超过 20 个字！用符合你高冷/克制人设的一句话表达即可。多余的话一个字都不要说！
    """

        try:
            reply = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "system", "content": sys_prompt}],
                task_type="default",
                caller="sensor_text_talk",
            )
            if reply:
                await self._send_sensor_reply(reply, category, count, window_title, False)
        except Exception as e:
            self.logger.error(f"Sensor Gen failed: {e}")

    # 辅助方法：统一发送 (只提取情绪，不暴力改内容的版本)
    async def _send_sensor_reply(self, reply: str, category: str, count: int, title: str, is_vision: bool):
        """统一发送传感器回复"""
        extracted_emo, clean_text = self._extract_emo_tag(reply)

        if not clean_text or len(clean_text) < 2:
            return

        self.logger.info(f"🤖 [Sensor] 发言: {clean_text[:50]}...")
        self._update_active_time()

        await self.event_bus.emit("ui.append", role="assistant", text=clean_text)

        final_emo = "neutral"
        if category == "gaming":
            final_emo = "angry" if count > 8 else "happy"
        elif category == "coding":
            final_emo = "sad" if count > 12 else "think"
        if is_vision:
            final_emo = "think"
        if extracted_emo:
            final_emo = extracted_emo

        await self.presenter.present(clean_text, emotion=final_emo, interrupt=False)

        # ✅ 改为串行写入
        tag = "[视觉观察]" if is_vision else "[屏幕观察]"
        await self._add_memory_safe(
            "assistant",
            f"{tag} {clean_text}",
            meta={"path": "sensor", "emotion": final_emo}
        )

    # ==================== 音乐感知事件处理 ====================
    async def handle_music_event(self, title: str, artist: str):
        """处理音乐播放事件（优化版）"""
        # 防抖
        if time.time() - self._last_reply_time < self._sensor_min_reply_interval_sec:
            return

        print(f"🎵 [Music] 正在聆听: {title} by {artist}")

        # 1. 切换到听歌动作
        asyncio.create_task(self.event_bus.emit("live2d.emotion", emotion="music", intensity=1.0))

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
                    meta={"path": "music"}
                )
            )

        except Exception as e:
            self.logger.error(f"处理音乐事件失败: {e}")



    async def summarize_day(self, report_data: str = None, raw_stats: Optional[Dict[str, Any]] = None, auto: bool = False, target_date: date = None):
        if not target_date:
            target_date = datetime.now().date()

        date_str = target_date.strftime("%Y-%m-%d")
        is_makeup = (target_date < datetime.now().date())

        print(f"[Diary] Build summary ({date_str}) | makeup={is_makeup}")

        store = getattr(self.brain, "sqlite_store", None)

        stats_payload = raw_stats if isinstance(raw_stats, dict) else (report_data if isinstance(report_data, dict) else None)
        if store and isinstance(stats_payload, dict):
            try:
                normalized_stats = dict(stats_payload)
                if "summary_text" not in normalized_stats:
                    normalized_stats["summary_text"] = json.dumps(stats_payload, ensure_ascii=False)
                await asyncio.to_thread(store.save_daily_screen_stats, date_str, normalized_stats)
                print(f"[Diary] Screen stats saved: {date_str}")
            except Exception as e:
                print(f"[Diary] Screen stats save failed: {e}")

        report_text = report_data
        if isinstance(report_data, dict):
            report_text = report_data.get("summary_text", json.dumps(report_data, ensure_ascii=False))
        elif not report_text and isinstance(raw_stats, dict):
            report_text = raw_stats.get("summary_text", json.dumps(raw_stats, ensure_ascii=False))

        if not report_text and store:
            report_text = await asyncio.to_thread(store.format_screen_stats_for_prompt, date_str)

        chat_history = await asyncio.to_thread(self._fetch_day_chat_history, date_str)
        owner_chat_history = await asyncio.to_thread(self._fetch_day_owner_chat_history, date_str)

        if not report_text and not chat_history and not owner_chat_history:
            print(f"[Diary] Skip {date_str}: no data")
            return

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

        task_desc = f"You are {active_char_name}. Write today's diary entry."
        if is_makeup:
            task_desc = f"You are {active_char_name}. Write a makeup diary entry for {date_str} and mention that it is a makeup entry at the start."

        system_prompt = f"""
{base_prompt}

[Task]
{task_desc}

[Data Source 1: Screen Activity]
{report_text if report_text else "(none)"}

[Data Source 2: Full Conversation History]
{chat_history if chat_history else "(none)"}

[Data Source 3: Owner Cross-Channel History]
{owner_chat_history if owner_chat_history else "(no owner local/QQ shared history today)"}

[Requirements]
1. Use first person and write from the perspective of "{active_char_name}".
2. Include concrete details such as software used and topics discussed.
3. If Data Source 3 is not empty, explicitly include the interactions with the owner across local chat and QQ.
4. Keep it concise, within 500 Chinese characters.
"""

        try:
            diary_content = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "system", "content": system_prompt}],
                task_type="smart",
                caller="daily_summary",
            )
            diary_content = (diary_content or "").strip()

            if not diary_content:
                return

            title = f"{date_str} 日记"
            if is_makeup:
                title += " (补)"

            if store:
                store.upsert_episode({
                    "title": title,
                    "summary": diary_content,
                    "status": "active",
                    "tags": ["daily_log", f"role:{active_char_id}", f"date:{date_str}"],
                    "created_at": datetime.now().isoformat()
                })
            print(f"[Diary] Archived: {title}")

            asyncio.create_task(self._add_memory_safe(
                "assistant",
                f"【日记 {date_str}】{diary_content}",
                meta={"type": "episodic_memory", "date": date_str, "role": active_char_id}
            ))

            if not auto and not is_makeup:
                await self.event_bus.emit("ui.append", role="assistant", text=diary_content)
                await self.presenter.present(diary_content, emotion="neutral", interrupt=False)

        except Exception as e:
            self.logger.error(f"Diary build failed: {e}")

    def _load_day_transcript_rows(self, date_str: str) -> list[Dict[str, Any]]:
        store = getattr(self.brain, "sqlite_store", None)
        if not store:
            return []

        try:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            start_ts = int(dt.timestamp())
            end_ts = start_ts + 86400

            with store._connect() as conn:
                cursor = conn.execute(
                    "SELECT ts, role, content, session_id, meta_json FROM transcript WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                    (start_ts, end_ts)
                )
                rows = cursor.fetchall()

            result: list[Dict[str, Any]] = []
            for row in rows:
                meta: Dict[str, Any] = {}
                raw_meta = row["meta_json"]
                if raw_meta:
                    try:
                        meta = json.loads(raw_meta)
                    except Exception:
                        meta = {}
                result.append({
                    "ts": int(row["ts"]),
                    "role": str(row["role"] or ""),
                    "content": str(row["content"] or ""),
                    "session_id": str(row["session_id"] or ""),
                    "meta": meta,
                })
            return result
        except Exception as e:
            print(f"[ChatService] Load day transcript failed: {e}")
            return []

    def _is_owner_shared_row(self, row: Dict[str, Any]) -> bool:
        if not isinstance(row, dict):
            return False
        session_id = str(row.get("session_id") or "").strip()
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        source = str(meta.get("source") or "").strip().lower()
        if session_id == OWNER_SHARED_SESSION_ID:
            return True
        if session_id and session_id in LEGACY_OWNER_PRIVATE_SESSION_IDS:
            return True
        if source in OWNER_SHARED_LOCAL_SOURCES:
            return True
        if source in QQ_REMOTE_SOURCES and bool(meta.get("is_owner")):
            return True
        return False

    def _format_day_transcript_line(self, row: Dict[str, Any]) -> str:
        if not isinstance(row, dict):
            return ""
        content = str(row.get("content") or "").strip()
        if not content:
            return ""
        ts = int(row.get("ts") or 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M")
        role = str(row.get("role") or "").strip().lower()
        session_id = str(row.get("session_id") or "").strip()
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        source = str(meta.get("source") or "").strip().lower()
        sender_name = str(meta.get("sender_name") or meta.get("user_id") or "").strip()
        message_type = str(meta.get("message_type") or "private").strip().lower() or "private"

        if role == "assistant":
            speaker = "AI"
        elif role == "system":
            speaker = "System"
        elif self._is_owner_shared_row(row):
            if source in QQ_REMOTE_SOURCES or session_id in LEGACY_OWNER_PRIVATE_SESSION_IDS:
                speaker = "Owner(QQ)"
            else:
                speaker = "Owner(Local)"
        elif source in QQ_REMOTE_SOURCES:
            if message_type == "group":
                speaker = f"GroupMember({sender_name or 'Unknown'})"
            else:
                speaker = f"QQContact({sender_name or 'Unknown'})"
        else:
            speaker = "User"

        return f"[{time_str}] {speaker}: {content}"

    def _fetch_day_chat_history(self, date_str: str) -> str:
        rows = self._load_day_transcript_rows(date_str)
        if not rows:
            return "(no chat history)"
        lines = [self._format_day_transcript_line(row) for row in rows]
        lines = [line for line in lines if line]
        return "\n".join(lines) if lines else "(no chat history)"

    def _fetch_day_owner_chat_history(self, date_str: str) -> str:
        rows = self._load_day_transcript_rows(date_str)
        if not rows:
            return ""
        owner_rows = [row for row in rows if self._is_owner_shared_row(row)]
        if not owner_rows:
            return ""
        lines = [self._format_day_transcript_line(row) for row in owner_rows]
        lines = [line for line in lines if line]
        return "\n".join(lines)
