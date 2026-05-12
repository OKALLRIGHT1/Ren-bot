"""
聊天服务
处理用户输入和AI响应的核心逻辑
"""

import os
import json
import re
import asyncio
import shutil
import time
import tempfile
import uuid
import random  # ✅ 需要导入 random
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
from core.message_source import REMOTE_CHAT_SOURCES, build_output_profile

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
        self._sensor_min_reply_interval_sec = 45

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
        if not self._is_managed_gateway_temp_file(file_path):
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

    def _is_managed_gateway_temp_file(self, path: str) -> bool:
        file_path = str(path or "").strip()
        if not file_path:
            return False
        try:
            resolved = os.path.realpath(file_path)
        except Exception:
            resolved = os.path.abspath(file_path)
        try:
            temp_root = os.path.realpath(tempfile.gettempdir())
        except Exception:
            temp_root = tempfile.gettempdir()
        return resolved.startswith(os.path.join(temp_root, ""))

    def _prepare_gateway_image_transport_path(self, path: str) -> tuple[str, str]:
        file_path = str(path or "").strip()
        if not file_path:
            return "", ""
        if not os.path.isfile(file_path):
            return file_path, ""
        try:
            file_path.encode("ascii")
            return file_path, ""
        except UnicodeEncodeError:
            pass
        temp_dir = os.path.join(tempfile.gettempdir(), "live2d_llm_gateway_media")
        os.makedirs(temp_dir, exist_ok=True)
        suffix = os.path.splitext(file_path)[1] or ".jpg"
        staged_path = os.path.join(temp_dir, f"gateway_img_{uuid.uuid4().hex}{suffix}")
        shutil.copyfile(file_path, staged_path)
        return staged_path, staged_path

    def _should_use_gateway_voice_reply(self, source: str, text: str) -> bool:
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        if not self.gateway_voice_reply_enabled:
            return False
        if not callable(self.gateway_voice_renderer):
            return False
        if self.gateway_voice_reply_probability <= 0:
            return False
        clean = self._clean_text_for_tts(
            self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(text))
        )
        if len(clean) < 2:
            return False
        return random.random() * 100 < self.gateway_voice_reply_probability

    def _split_gateway_text_parts(self, text: str) -> List[str]:
        clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not clean:
            return []
        clean = re.sub(r"\n{3,}", "\n\n", clean)

        def _is_natural_line(line: str) -> bool:
            item = str(line or "").strip()
            if not item:
                return False
            if len(item) > 90:
                return False
            if re.search(r"https?://|\[[^\]]+\]\([^)]+\)", item):
                return False
            if re.match(r"^\s*(?:[-*•>|#]|\d+[.)、])", item):
                return False
            return True

        blocks = [block.strip() for block in re.split(r"\n\s*\n", clean) if block.strip()]
        parts: List[str] = []
        for block in blocks:
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            if 1 < len(lines) <= 5 and all(_is_natural_line(line) for line in lines):
                parts.extend(lines)
            else:
                parts.append(" ".join(lines))

        parts = [re.sub(r"[ \t]{2,}", " ", part).strip() for part in parts if part.strip()]
        if len(parts) > 5:
            return [" ".join(parts)]
        return parts

    async def _send_gateway_text_parts(
        self,
        adapter_name: str,
        session_id: str,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "",
        session_label: str = "",
        log_suffix: str = "text_sent",
    ) -> bool:
        parts = self._split_gateway_text_parts(text)
        if not parts:
            return True
        ok = True
        for idx, part in enumerate(parts):
            try:
                result = await self.chat_gateway.send_text(
                    adapter_name,
                    session_id,
                    part,
                    metadata=metadata,
                    source=source,
                )
                if isinstance(result, dict) and not result.get("ok"):
                    ok = False
                    self.logger.warning(f"Gateway text reply failed: {result}")
                else:
                    suffix = log_suffix
                    if len(parts) > 1:
                        suffix = f"{log_suffix}_{idx + 1}/{len(parts)}"
                    self.logger.info(
                        f"[QQ-OUT-OK][{session_label}][{session_id}] {suffix}"
                    )
            except Exception as e:
                ok = False
                self.logger.error(f"Gateway reply failed: {e}")
            if idx < len(parts) - 1:
                await asyncio.sleep(0.35)
        return ok

    async def _send_gateway_reply(
        self,
        text: str,
        ctx: Optional[Dict[str, Any]] = None,
        emotion: Optional[str] = None,
    ):
        text = str(text or "").strip()
        if not text or not self.chat_gateway or not isinstance(ctx, dict):
            return
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return
        channel_meta = ctx.get("channel_meta") or {}
        adapter_name = (
            str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        )
        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            self.logger.warning("Gateway reply skipped: missing session_id")
            return

        sender_name = str(
            channel_meta.get("sender_name") or channel_meta.get("user_id") or ""
        ).strip()
        session_label = self._qq_session_label(session_id)
        preview = text.replace("\r", " ").replace("\n", " ").strip()
        if len(preview) > 240:
            preview = preview[:240] + "..."
        self.logger.info(
            f"[QQ-OUT][{session_label}][{session_id}][to={sender_name or 'unknown'}] {preview}"
        )

        user_text = str(ctx.get("user_text") or "")
        link_request = self._is_link_request(user_text)
        contains_url = bool(self._extract_first_url(text))
        skip_voice = link_request or contains_url
        sent_share = False

        if link_request:
            url = self._extract_first_url(text) or self._extract_url_from_tool_results(
                ctx
            )
            if url:
                title = self._build_share_title(text, url)
                content = self._build_share_content(text, title)
                try:
                    share_result = await self.chat_gateway.send_share(
                        adapter_name,
                        session_id,
                        url,
                        title=title,
                        content=content,
                        metadata=channel_meta,
                        source=source,
                    )
                    if isinstance(share_result, dict) and share_result.get("ok"):
                        sent_share = True
                except Exception as e:
                    self.logger.warning(f"Gateway share send failed: {e}")

                if sent_share:
                    cleaned = self._strip_urls(text)
                    text = cleaned or "已发送链接卡片。"

        voice_path = ""
        if (not skip_voice) and self._should_use_gateway_voice_reply(source, text):
            clean_voice_text = self._clean_text_for_tts(
                self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(text))
            )
            try:
                voice_path = str(
                    await self.gateway_voice_renderer(
                        clean_voice_text,
                        emotion=emotion,
                        source=source,
                        channel_meta=channel_meta,
                    )
                    or ""
                ).strip()
            except Exception as e:
                self.logger.warning(f"Gateway voice render failed: {e}")
                voice_path = ""
            if voice_path:
                try:
                    result = await self.chat_gateway.send_voice(
                        adapter_name,
                        session_id,
                        voice_path,
                        metadata=channel_meta,
                        source=source,
                    )
                    if self._is_gateway_action_success(result):
                        await self._send_gateway_text_parts(
                            adapter_name,
                            session_id,
                            text,
                            metadata=channel_meta,
                            source=source,
                            session_label=session_label,
                            log_suffix="voice_sent_with_text",
                        )
                        return
                    self.logger.warning(
                        f"Gateway voice send fallback to text: {result}"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Gateway voice send failed, fallback to text: {e}"
                    )
                finally:
                    asyncio.create_task(self._cleanup_gateway_voice_file(voice_path))
        await self._send_gateway_text_parts(
            adapter_name,
            session_id,
            text,
            metadata=channel_meta,
            source=source,
            session_label=session_label,
            log_suffix="text_sent",
        )

    async def _send_gateway_image_reply(
        self, image_path: str, ctx: Optional[Dict[str, Any]] = None, caption: str = ""
    ) -> bool:
        path_text = str(image_path or "").strip()
        if not path_text or not self.chat_gateway or not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        channel_meta = ctx.get("channel_meta") or {}
        adapter_name = (
            str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        )
        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            self.logger.warning("Gateway image reply skipped: missing session_id")
            return False
        sender_name = str(
            channel_meta.get("sender_name") or channel_meta.get("user_id") or ""
        ).strip()
        session_label = self._qq_session_label(session_id)
        caption_preview = (
            str(caption or "").replace("\r", " ").replace("\n", " ").strip()
        )
        if len(caption_preview) > 160:
            caption_preview = caption_preview[:160] + "..."
        self.logger.info(
            f"[QQ-OUT-IMAGE][{session_label}][{session_id}][to={sender_name or 'unknown'}] path={path_text} caption={caption_preview}"
        )
        transport_path = path_text
        staged_path = ""
        try:
            transport_path, staged_path = self._prepare_gateway_image_transport_path(
                path_text
            )
        except Exception as exc:
            self.logger.warning(
                f"Gateway image staging failed, fallback to original path: {exc}"
            )
            transport_path = path_text
            staged_path = ""
        if staged_path:
            self.logger.info(
                f"[QQ-OUT-IMAGE-STAGED][{session_label}][{session_id}] transport_path={transport_path}"
            )
        try:
            result = await self.chat_gateway.send_image(
                adapter_name,
                session_id,
                transport_path,
                caption=caption,
                metadata=channel_meta,
                source=source,
            )
            if self._is_gateway_action_success(result):
                self.logger.info(
                    f"[QQ-OUT-IMAGE-OK][{session_label}][{session_id}] image_sent"
                )
                return True
            self.logger.warning(f"Gateway image reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway image reply failed: {e}")
            return False
        finally:
            if staged_path:
                asyncio.create_task(self._cleanup_gateway_image_file(staged_path))

    async def _send_gateway_file_reply(
        self, file_path: str, ctx: Optional[Dict[str, Any]] = None, file_name: str = ""
    ) -> bool:
        path_text = str(file_path or "").strip()
        if not path_text or not self.chat_gateway or not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        channel_meta = ctx.get("channel_meta") or {}
        adapter_name = (
            str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        )
        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            self.logger.warning("Gateway file reply skipped: missing session_id")
            return False
        sender_name = str(
            channel_meta.get("sender_name") or channel_meta.get("user_id") or ""
        ).strip()
        session_label = self._qq_session_label(session_id)
        self.logger.info(
            f"[QQ-OUT-FILE][{session_label}][{session_id}][to={sender_name or 'unknown'}] file={file_name or path_text}"
        )
        try:
            result = await self.chat_gateway.send_file(
                adapter_name,
                session_id,
                path_text,
                name=file_name,
                metadata=channel_meta,
                source=source,
            )
            if self._is_gateway_action_success(result):
                self.logger.info(
                    f"[QQ-OUT-FILE-OK][{session_label}][{session_id}] file_sent"
                )
                return True
            self.logger.warning(f"Gateway file reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway file reply failed: {e}")
            return False

    async def _send_gateway_voice_reply(
        self, voice_path: str, ctx: Optional[Dict[str, Any]] = None
    ) -> bool:
        path_text = str(voice_path or "").strip()
        if not path_text or not self.chat_gateway or not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return False
        channel_meta = ctx.get("channel_meta") or {}
        adapter_name = (
            str(channel_meta.get("adapter") or "napcat_qq").strip() or "napcat_qq"
        )
        session_id = str(channel_meta.get("session_id") or "").strip()
        if not session_id:
            self.logger.warning("Gateway voice reply skipped: missing session_id")
            return False
        sender_name = str(
            channel_meta.get("sender_name") or channel_meta.get("user_id") or ""
        ).strip()
        session_label = self._qq_session_label(session_id)
        self.logger.info(
            f"[QQ-OUT-VOICE][{session_label}][{session_id}][to={sender_name or 'unknown'}] voice={path_text}"
        )
        try:
            result = await self.chat_gateway.send_voice(
                adapter_name,
                session_id,
                path_text,
                metadata=channel_meta,
                source=source,
            )
            if self._is_gateway_action_success(result):
                self.logger.info(
                    f"[QQ-OUT-VOICE-OK][{session_label}][{session_id}] voice_sent"
                )
                return True
            self.logger.warning(f"Gateway voice reply failed: {result}")
            return False
        except Exception as e:
            self.logger.error(f"Gateway voice reply failed: {e}")
            return False

    def _is_gateway_action_success(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if bool(result.get("ok")):
            return True

        response = result.get("response")
        if isinstance(response, dict):
            status = str(response.get("status") or "").strip().lower()
            if status in {"ok", "success"}:
                return True

            try:
                retcode = int(response.get("retcode", 0) or 0)
            except Exception:
                retcode = None
            if retcode == 0:
                return True

            data = response.get("data")
            if data not in (None, "", [], {}):
                return True

            if response.get("message_id") not in (None, "", 0, "0"):
                return True

        if str(result.get("transport") or "").strip().lower() == "http":
            status_code = result.get("status")
            try:
                status_num = int(status_code)
            except Exception:
                status_num = None
            if status_num is not None and 200 <= status_num < 300:
                return True
        return False

    async def _emit_idle_status(
        self, output_profile: Optional[Dict[str, Any]], reason: str
    ) -> None:
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

    def _is_qq_source(self, ctx: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        return source in QQ_REMOTE_SOURCES

    def _qq_session_label(self, session_id: str) -> str:
        text = str(session_id or "").strip().lower()
        if text.startswith("group:"):
            return "QQ-GROUP"
        if text.startswith("private:"):
            return "QQ-PRIVATE"
        return "QQ"

    def _reply_effect_identity(self, ctx: Optional[Dict[str, Any]]) -> tuple[str, str]:
        if not isinstance(ctx, dict):
            return "", ""
        channel_meta = ctx.get("channel_meta") if isinstance(ctx.get("channel_meta"), dict) else {}
        session_id = str(channel_meta.get("session_id") or "").strip()
        user_id = str(channel_meta.get("user_id") or ctx.get("user_id") or "").strip()
        if not session_id:
            session_id = str(ctx.get("session_id") or "").strip()
        if not session_id:
            source = str(ctx.get("source") or "local").strip() or "local"
            session_id = f"local:{source}"
        return session_id, user_id

    def _observe_reply_effect(self, user_text: str, ctx: Optional[Dict[str, Any]]) -> None:
        try:
            session_id, user_id = self._reply_effect_identity(ctx)
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
            session_id, user_id = self._reply_effect_identity(ctx)
            self.reply_effect_tracker.record_reply(
                session_id=session_id,
                user_id=user_id,
                text=reply_text,
                source=source or str((ctx or {}).get("source") or ""),
            )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"Reply effect record failed: {exc}")

    def _build_transcript_channel_meta(
        self, ctx: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not self._is_qq_source(ctx):
            return {}
        channel_meta = (ctx or {}).get("channel_meta") or {}
        result: Dict[str, Any] = {}
        for key in (
            "adapter",
            "user_id",
            "sender_name",
            "message_type",
            "group_id",
            "is_owner",
            "owner_label",
            "message_id",
        ):
            value = channel_meta.get(key)
            if value in (None, "", [], {}):
                continue
            result[key] = value
        return result

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
            meta = self._row_meta(row)
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
        if not self._is_qq_source(ctx):
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
                    "- 记忆只影响你的态度和语气，不要主动复述用户以前说过的原句。",
                ]
            )
        if source in QQ_REMOTE_SOURCES:
            parts.extend(
                [
                    "- 当前渠道是 QQ，回复要像真人发消息，不像客服。",
                    "- 尽量控制在 8 到 35 字一小句；没有必要不要连续发大段。",
                ]
            )
        elif source in {"text_input", "desktop", "voice"}:
            parts.append("- 当前是日常对话场景，优先自然、短促、有人味。")
        effect_hint = self._build_reply_effect_style_hint(ctx)
        if effect_hint:
            parts.append(effect_hint)
        return "\n".join(parts)

    def _build_reply_effect_style_hint(self, ctx: Optional[Dict[str, Any]]) -> str:
        tracker = getattr(self, "reply_effect_tracker", None)
        if tracker is None or not hasattr(tracker, "stats"):
            return ""
        try:
            session_id, _user_id = self._reply_effect_identity(ctx)
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
        prompt += "\n【自我识别】如果你在图片边缘，尤其是右下角，看到一个二次元/动漫风格的女孩、桌宠、Live2D 角色或悬浮球，请识别为“这是AI助手(你)的桌面形象”。"
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

    def _looks_like_plain_reaction_text(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if len(raw) > 48:
            return False
        lowered = raw.lower()
        slow_complaint = any(hint in raw for hint in ("慢", "卡", "等", "超时"))
        hard_task_hints = (
            "[cmd:",
            "http://",
            "https://",
            "```",
            "/",
            "\\",
            "查",
            "搜",
            "搜索",
            "链接",
            "生成",
            "画图",
            "截图",
            "打开",
            "运行",
            "报错",
            "失败",
            "接口",
            "配置",
            "插件",
            "文件",
            "提交",
            "上传",
            "github",
            "git ",
            "python",
            "rust",
            "cargo",
            "npm",
        )
        if any(hint in lowered for hint in hard_task_hints):
            # "代码跑得慢" 这类抱怨仍然走短反应，不直接当代码任务。
            if not slow_complaint:
                return False
        question_hints = (
            "?",
            "？",
            "为什么",
            "怎么",
            "如何",
            "多少",
            "哪里",
            "啥情况",
            "什么情况",
            "能不能",
            "可不可以",
            "怎么办",
        )
        if any(hint in raw for hint in question_hints):
            if not any(hint in raw for hint in ("在吗", "还在吗", "醒着吗")):
                return False
        if self._wants_detailed_answer(raw) and not slow_complaint:
            return False
        return True

    def _build_short_reaction(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> tuple[str, str]:
        raw = self._strip_wrapping_quotes(user_text)
        if not self._looks_like_plain_reaction_text(raw):
            return "", "neutral"
        compact = re.sub(r"\s+", "", raw.lower())
        if not compact:
            return "", "neutral"

        def pick(options: tuple[str, ...]) -> str:
            return random.choice(options)

        if any(k in compact for k in ("在吗", "还在吗", "醒着吗")):
            return pick(("我在。", "在。", "嗯，我在。")), "neutral"
        if compact in {"嗯", "恩", "哦", "噢", "好", "行", "ok", "okay", "收到"}:
            return pick(("嗯。", "好。", "我知道了。")), "neutral"
        if any(k in compact for k in ("谢谢", "谢啦", "感谢", "thx", "thanks")):
            return pick(("嗯。", "不用谢。", "没事。")), "happy"
        if any(k in compact for k in ("过了", "成功了", "跑通了", "好了", "搞定了", "可以了", "ok了")):
            return pick(("嗯，稳了。", "这样就好。", "先别再动它了。")), "happy"
        if any(k in compact for k in ("好慢", "跑得慢", "跑的慢", "太慢", "卡", "超时", "等好久", "跑不动")):
            return pick(("确实慢，先别急。", "像是卡在重活上了。", "先等它把这轮跑完。")), "think"
        if any(k in compact for k in ("累", "困", "撑不住", "不想动", "没精神")):
            return pick(("先缓一下。", "别硬撑。", "休息几分钟也行。")), "concern"
        if any(k in compact for k in ("烦", "崩溃", "麻了", "服了", "无语", "裂开", "难受")):
            return pick(("先别急。", "嗯，这个确实烦。", "先停一下也可以。")), "concern"
        if len(compact) <= 8 and any(k in compact for k in ("早", "晚安", "睡了", "拜")):
            return pick(("嗯。", "晚安。", "早点休息。")), "neutral"
        return "", "neutral"

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

    def _build_live2d_self_awareness_hint(
        self, ctx: Optional[Dict[str, Any]] = None
    ) -> str:
        source = str((ctx or {}).get("source") or "").strip().lower()
        if source in {"qq_gateway", "napcat_qq", "text_input", "unknown", "desktop"}:
            return (
                "【自我识别】如果你在截图、屏幕描述或桌面画面边缘，尤其是右下角，看到一个二次元/动漫风格的女孩、桌宠、Live2D 角色或悬浮球，"
                "那通常就是你自己的桌面形象（你的实体投影），不是别人。描述屏幕或图片时要知道那是你自己。"
            )
        return ""

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
        parts = [f"【当前时间】{current_time}", str(base_prompt or "").strip()]
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
        if not e:
            return None
        t = str(e).strip().lower()
        t = t.strip("<>").strip()
        if t.startswith("emo="):
            t = t.split("=", 1)[1].strip()
        return t if t in self._emo_set else None

    # 文本净化函数
    def _clean_text_for_tts(self, text: str) -> str:
        if not text:
            return ""
        # 1. 暴力去除所有星号 * 和 #
        text = re.sub(r"[\*#]+", "", text)
        # 2. 去除 markdown 链接
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        # 3. 统一空白，避免回复里出现很多换行和空格
        text = text.replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        return text.strip()

    def _strip_wrapping_quotes(self, text: str) -> str:
        cleaned = str(text or "").strip()
        quote_pairs = {
            '"': '"',
            "'": "'",
            "“": "”",
            "‘": "’",
            "「": "」",
            "『": "』",
            "《": "》",
        }
        changed = True
        while changed and len(cleaned) >= 2:
            changed = False
            first = cleaned[0]
            last = cleaned[-1]
            if quote_pairs.get(first) == last:
                cleaned = cleaned[1:-1].strip()
                changed = True
        return cleaned

    def _get_character_catchphrase_config(self) -> Dict[str, Any]:
        try:
            from modules.character_manager import character_manager

            cfg = character_manager.get_catchphrase_config()
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            return {"enabled": False, "text": "", "probability": 0}
        text = str(cfg.get("text", "") or "").strip()
        try:
            probability = int(cfg.get("probability", 0))
        except Exception:
            probability = 0
        probability = max(0, min(100, probability))
        return {
            "enabled": bool(cfg.get("enabled", False)) and bool(text) and probability > 0,
            "text": text,
            "probability": probability,
        }

    def _catchphrase_variants(self, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
        phrases = {"……はい。", "……はい"}
        if cfg is None:
            cfg = self._get_character_catchphrase_config()
        if isinstance(cfg, dict):
            text = str(cfg.get("text", "") or "").strip()
            if text:
                phrases.add(text)
                phrases.add(re.sub(r"[。.!！?？]+$", "", text).strip())
        return sorted((p for p in phrases if p), key=len, reverse=True)

    def _strip_model_catchphrase(self, text: str, cfg: Optional[Dict[str, Any]] = None) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""
        phrases = self._catchphrase_variants(cfg)
        if not phrases:
            return raw

        def _same_phrase(value: str) -> bool:
            item = str(value or "").strip()
            item_soft = re.sub(r"[。.!！?？]+$", "", item).strip()
            return any(item == phrase or item_soft == phrase for phrase in phrases)

        lines = [line for line in raw.split("\n") if not _same_phrase(line)]
        cleaned = "\n".join(lines).strip()
        if not cleaned:
            return ""

        for phrase in phrases:
            cleaned = re.sub(rf"[ \t]*{re.escape(phrase)}\s*$", "", cleaned).rstrip()
        return cleaned.strip()

    def _apply_character_catchphrase(self, text: str) -> str:
        cfg = self._get_character_catchphrase_config()
        clean = self._strip_model_catchphrase(text, cfg)
        if not clean:
            return ""
        if not cfg.get("enabled"):
            return clean
        phrase = str(cfg.get("text") or "").strip()
        if not phrase:
            return clean
        try:
            probability = int(cfg.get("probability", 0))
        except Exception:
            probability = 0
        if probability <= 0 or random.random() * 100 >= probability:
            return clean
        # Questions sound unnatural with a confirmation catchphrase appended.
        if clean.rstrip().endswith(("?", "？")):
            return clean
        sep = " " if re.match(r"^[A-Za-z0-9]", phrase) else ""
        return clean.rstrip() + sep + phrase

    def _is_link_request(self, text: str) -> bool:
        raw = str(text or "")
        lower = raw.lower()
        if "链接" in raw or "网址" in raw:
            return True
        return ("link" in lower) or ("url" in lower)

    def _extract_first_url(self, text: str) -> str:
        if not text:
            return ""
        m = re.search(r"https?://[^\s)）]+", str(text))
        if not m:
            return ""
        url = m.group(0).rstrip(".,;，。)")
        return url

    def _strip_urls(self, text: str) -> str:
        return re.sub(r"https?://[^\s)）]+", "", str(text or "")).strip()

    def _extract_url_from_tool_results(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not isinstance(ctx, dict):
            return ""
        results = ctx.get("_tool_results") or []
        if not isinstance(results, list):
            results = [results]
        for item in results:
            url = self._extract_first_url(str(item or ""))
            if url:
                return url
        return ""

    def _build_share_title(self, text: str, url: str) -> str:
        cleaned = re.sub(r"\s+", " ", self._strip_urls(text)).strip()
        if cleaned:
            return cleaned[:48]
        return url

    def _build_share_content(self, text: str, title: str) -> str:
        cleaned = re.sub(r"\s+", " ", self._strip_urls(text)).strip()
        if not cleaned:
            return ""
        if cleaned.startswith(title):
            cleaned = cleaned[len(title) :].strip()
        return cleaned[:80]

    def _strip_emo_tags_anywhere(self, text: str) -> str:
        """移除所有情绪标签"""
        return self._emo_tag_re.sub("", text or "")

    def _strip_cmd_anywhere(self, text: str) -> str:
        """移除所有命令标签"""
        return self._cmd_re.sub("", text or "")

    def _strip_internal_tags(self, text: str) -> str:
        raw = str(text or "")
        raw = re.sub(r"\[tool_use\]\s*\[[^\]]*\]\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\[tool_use\]\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\[search_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\[web_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\[moegirl_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
        return raw.strip()

    def _compress_sensor_text(self, text: str, max_len: int = 800) -> str:
        compressed = str(text or "").replace("\r\n", "\n").strip()
        if not compressed:
            return ""

        compressed = re.sub(r"\n{3,}", "\n\n", compressed)
        lines = [line.strip() for line in compressed.split("\n") if line.strip()]
        if len(lines) > 8:
            compressed = "\n".join(lines[:8])
        else:
            compressed = "\n".join(lines)

        if len(compressed) > max_len:
            compressed = compressed[: max_len - 3].rstrip() + "..."

        return compressed

    def _format_sensor_observations(self, entries: list, max_items: int = 3) -> str:
        if not entries:
            return ""
        lines = []
        tail = list(entries)[-max_items:]
        for item in tail:
            time_text = str(item.get("time") or "").strip()
            app = str(item.get("app") or item.get("window_title") or "").strip()
            content = str(item.get("content") or "").strip()
            if content:
                content = self._compress_sensor_text(content, max_len=160)
            prefix = f"{time_text} " if time_text else ""
            if app and content:
                line = f"- {prefix}{app}: {content}"
            elif app:
                line = f"- {prefix}{app}"
            elif content:
                line = f"- {prefix}{content}"
            else:
                continue
            lines.append(line)
        return "\n".join(lines)

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

    def _is_market_price_query(self, text: str) -> bool:
        raw = str(text or "")
        lower = raw.lower()
        hints = [
            "金价",
            "银价",
            "油价",
            "汇率",
            "指数",
            "现价",
            "实时价",
            "实时价格",
            "价格",
            "行情",
            "price",
            "quote",
            "rate",
            "index",
            "gold",
            "usd",
            "cny",
            "rmb",
        ]
        return any(hint in lower or hint in raw for hint in hints)

    def _has_explicit_market_numbers(self, text: str) -> bool:
        raw = str(text or "")
        patterns = [
            r"\d+(?:\.\d+)?\s*(?:美元/盎司|美元/克|元/克|元/盎司)",
            r"\d+(?:\.\d+)?\s*(?:USD|CNY|RMB)\b",
            r"\d+(?:\.\d+)?\s*(?:%|点)\b",
        ]
        return any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in patterns)

    def _is_search_delegate(self, delegate_triggers: list[str], raw_text: str) -> bool:
        trigger_set = {
            str(item or "").strip().lower() for item in (delegate_triggers or [])
        }
        if trigger_set & {"search", "search_web"}:
            return True
        text = str(raw_text or "")
        return ("搜索结果" in text) or ("Exa@" in text) or ("DuckDuckGo" in text)

    def _parse_search_meta(self, text: str) -> Dict[str, str]:
        raw = str(text or "")
        m = re.search(r"\[search_meta\]\s*([^\n]+)", raw, flags=re.IGNORECASE)
        if not m:
            return {}
        line = str(m.group(1) or "").strip()
        data: Dict[str, str] = {}
        for part in line.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = str(key or "").strip().lower()
            value = str(value or "").strip()
            if key:
                data[key] = value
        return data

    def _parse_web_meta(self, text: str) -> Dict[str, str]:
        raw = str(text or "")
        m = re.search(r"\[web_meta\]\s*([^\n]+)", raw, flags=re.IGNORECASE)
        if not m:
            return {}
        line = str(m.group(1) or "").strip()
        data: Dict[str, str] = {}
        for part in line.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = str(key or "").strip().lower()
            value = str(value or "").strip()
            if key:
                data[key] = value
        return data

    def _parse_moegirl_meta(self, text: str) -> Dict[str, str]:
        raw = str(text or "")
        m = re.search(r"\[moegirl_meta\]\s*([^\n]+)", raw, flags=re.IGNORECASE)
        if not m:
            return {}
        line = str(m.group(1) or "").strip()
        data: Dict[str, str] = {}
        for part in line.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = str(key or "").strip().lower()
            value = str(value or "").strip()
            if key:
                data[key] = value
        return data

    def _search_result_lacks_explicit_fact(self, text: str) -> bool:
        raw = str(text or "")
        meta = self._parse_search_meta(raw)
        if not raw:
            return True
        if "未在摘要中发现具体数值" in raw:
            return True
        if meta:
            if (
                str(meta.get("need_numeric") or "0") == "1"
                and str(meta.get("has_numbers") or "0") != "1"
            ):
                return True
            if str(meta.get("has_numbers") or "0") == "1":
                return False
            if (
                str(meta.get("has_links") or "0") == "1"
                or str(meta.get("has_published") or "0") == "1"
            ):
                return False
        if self._has_explicit_market_numbers(raw):
            return False
        explicit_markers = [
            "链接：",
            "关键数值：",
            "发布时间",
            "published",
            "source:",
        ]
        if any(marker.lower() in raw.lower() for marker in explicit_markers):
            return False
        return True

    def _web_result_lacks_body(self, text: str) -> bool:
        meta = self._parse_web_meta(text)
        if not meta:
            return False
        return str(meta.get("has_body") or "0") != "1"

    def _should_fallback_from_moegirl(self, results: list[str]) -> bool:
        if not results:
            return True
        combined = "\n".join(str(item) for item in results if str(item).strip())
        meta = self._parse_moegirl_meta(combined)
        status = str(meta.get("status") or "").strip().lower()
        if status == "not_found":
            return True
        if status == "ambiguous" and str(meta.get("has_page") or "0") != "1":
            return True
        return False

    async def _run_search_fallback_for_moegirl(
        self, *, user_text: str, ctx: Dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        fallback_ctx = dict(ctx or {})
        fallback_ctx["delegate_mode"] = True
        fallback_ctx["allow_read"] = False
        fallback_ctx["allow_write"] = False
        fallback_ctx["allow_exec"] = False
        command = f"[CMD: search | {user_text}]"
        triggered, _clean, results, used = await self.plugin_manager.execute_commands(
            command,
            fallback_ctx,
            allow_tools=True,
            allowed_types={"delegate"},
        )
        if not triggered:
            return [], []
        return results, used

    def _should_use_background_delegate(
        self,
        *,
        route_reason: str,
        delegate_triggers: list[str],
        ctx: Optional[Dict[str, Any]],
    ) -> bool:
        if not delegate_triggers:
            return False
        if str(route_reason or "") == "workspace_read_preferred":
            return False
        # 联网搜索这类需要明确结果的委托，不要先回一条“正在查询”
        if self._is_search_delegate(delegate_triggers, ""):
            return False
        source = str((ctx or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq", "text_input"}

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
    ) -> None:
        final_text = self._clean_text_for_tts(
            self._strip_internal_tags(
                self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(text))
            )
        ).strip()
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
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
        )
        await self._send_gateway_reply(final_text, ctx, emotion=emotion)

    async def _polish_background_delegate_reply(
        self,
        *,
        user_text: str,
        ctx: Dict[str, Any],
        delegate_triggers: list[str],
        delegate_results: list[str],
        delegate_clean: str,
    ) -> tuple[str, str]:
        raw_text = ""
        if delegate_results:
            raw_text = "\n".join(
                str(item) for item in delegate_results if str(item).strip()
            )
        elif delegate_clean:
            raw_text = str(delegate_clean).strip()
        if not raw_text:
            return "后台任务已处理完成。", "neutral"

        prompt = (
            "你现在是在任务完成后回到对话中汇报结果。"
            "保持五十铃怜的语气，但只做轻度人格化整理。"
            "要求：1) 只基于已给出的任务结果；"
            "2) 不扩展诊断，不脑补未明确提供的信息；"
            "3) 不展示工具调用过程；"
            "4) 最多3句话，尽量简短；"
            "5) 如果结果本身已经很清楚，就直接概述。"
        )
        if not self._wants_detailed_answer(user_text):
            prompt += (
                " 13) 默认像即时聊天，不要写成说明文或总结报告；"
                "14) 优先 1 到 2 句短句。"
            )
        is_market_query = self._is_market_price_query(user_text)
        is_search_delegate = self._is_search_delegate(delegate_triggers, raw_text)
        web_meta = self._parse_web_meta(raw_text)
        if is_market_query:
            prompt += (
                "6) 如果这是价格/行情/汇率/指数类请求，只有在任务结果里明确出现具体数值+单位时才可以引用；"
                "7) 如果任务结果里没有明确数值，明确说未拿到可靠现价，不要自行补任何数字。"
            )
        if is_search_delegate:
            prompt += (
                "8) 如果这是联网搜索结果，只能转述结果里已经明确出现的事实；"
                "9) 不要补充结果中未出现的人名、日期、价格、型号、结论；"
                "10) 若搜索结果只有摘要或标题，明确说是基于摘要的概述。"
            )
        if web_meta:
            prompt += (
                "11) 如果这是网页解析结果，优先依据网页标题和正文摘要来概述；"
                "12) 如果没有提取到可靠正文，就明确说明只拿到了标题或少量页面信息，不要把标题扩写成完整正文。"
            )
        trigger_text = (
            ", ".join(delegate_triggers[:4]) if delegate_triggers else "delegate"
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"原始请求：{user_text}\n"
                    f"任务类型：{trigger_text}\n"
                    f"任务结果：\n{raw_text}\n\n"
                    "请把它整理成一条自然、简短的回灌消息。"
                ),
            },
        ]
        try:
            reply = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="default",
                caller="chat_delegate_finalize",
            )
            emo, clean = self._extract_emo_tag(reply or "")
            polished = self._clean_text_for_tts(
                self._strip_internal_tags(
                    self._strip_cmd_anywhere(
                        self._strip_emo_tags_anywhere(clean or reply or "")
                    )
                )
            ).strip()
            if polished:
                if is_market_query and not self._has_explicit_market_numbers(raw_text):
                    polished = re.sub(
                        r"\d+(?:\.\d+)?\s*(?:美元/盎司|美元/克|元/克|元/盎司|USD|CNY|RMB|%|点)",
                        "",
                        polished,
                        flags=re.IGNORECASE,
                    ).strip(" ，,。；;:：")
                    if not polished or self._has_explicit_market_numbers(polished):
                        polished = "查到了相关新闻和摘要，但当前结果里没有可靠的现价数字，我不想乱报。"
                elif is_search_delegate and self._search_result_lacks_explicit_fact(
                    raw_text
                ):
                    polished = "我查到了相关搜索结果，不过当前拿到的主要是标题和摘要，所以我只能先做保守概述，不想把没写明的细节说死。"
                elif web_meta and self._web_result_lacks_body(raw_text):
                    polished = "我打开了这个链接，不过目前只稳定拿到了标题或少量页面信息，正文没有可靠提取出来，所以我先不把内容说得太满。"
                return polished, (emo or "neutral")
        except Exception:
            pass
        if is_market_query and not self._has_explicit_market_numbers(raw_text):
            return (
                "查到了相关新闻和摘要，但当前结果里没有可靠的现价数字，我不想乱报。",
                "neutral",
            )
        if is_search_delegate and self._search_result_lacks_explicit_fact(raw_text):
            return (
                "我查到了相关搜索结果，不过当前拿到的主要是标题和摘要，所以我只能先做保守概述，不想把没写明的细节说死。",
                "neutral",
            )
        if web_meta and self._web_result_lacks_body(raw_text):
            return (
                "我打开了这个链接，不过目前只稳定拿到了标题或少量页面信息，正文没有可靠提取出来，所以我先不把内容说得太满。",
                "neutral",
            )
        return raw_text, "neutral"

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
            (
                delegate_triggered,
                delegate_clean,
                delegate_results,
                delegate_used,
            ) = await self._run_delegate_round(
                user_text=user_text,
                ctx=ctx,
                context_messages=context_messages,
                delegate_triggers=delegate_triggers,
                task_reasoning=task_reasoning,
            )
            if "moegirl_wiki" in set(delegate_used or delegate_triggers):
                if self._should_fallback_from_moegirl(delegate_results):
                    (
                        fallback_results,
                        fallback_used,
                    ) = await self._run_search_fallback_for_moegirl(
                        user_text=user_text,
                        ctx=ctx,
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
            final_text, final_emo = await self._polish_background_delegate_reply(
                user_text=user_text,
                ctx=ctx,
                delegate_triggers=delegate_triggers,
                delegate_results=delegate_results,
                delegate_clean=delegate_clean,
            )
            await self._emit_assistant_text(
                final_text,
                ctx=ctx,
                emotion=final_emo,
                transcript_meta=transcript_meta,
                chat_log_source=chat_log_source,
                output_profile=output_profile,
                tool=True,
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
            await self._emit_assistant_text(
                f"刚才委托的后台任务失败了：{e}",
                ctx=ctx,
                emotion="neutral",
                transcript_meta=transcript_meta,
                chat_log_source=chat_log_source,
                output_profile=output_profile,
                tool=True,
            )

    async def _run_workspace_read_shortcut(
        self, *, user_text: str, ctx: Dict[str, Any]
    ) -> tuple[bool, str, list[str], list[str]]:
        path = self._extract_workspace_read_path(user_text)
        if not path:
            return False, "", [], []
        delegate_ctx = dict(ctx or {})
        delegate_ctx["delegate_mode"] = True
        delegate_ctx["allow_read"] = True
        delegate_ctx["allow_write"] = False
        delegate_ctx["allow_exec"] = False
        command = f"[CMD: workspace_ops | read_file ||| {path}]"
        return await self.plugin_manager.execute_commands(
            command,
            delegate_ctx,
            allow_tools=True,
            allowed_types={"delegate"},
        )

    async def _run_delegate_round(
        self,
        *,
        user_text: str,
        ctx: Dict[str, Any],
        context_messages: list,
        delegate_triggers: list[str],
        task_reasoning: str,
    ) -> tuple[bool, str, list[str], list[str]]:
        if not delegate_triggers:
            return False, "", [], []

        delegate_prompt = self.plugin_manager.get_delegate_prompt_for_triggers(
            list(delegate_triggers), compact=True
        )
        if not delegate_prompt:
            return False, "", [], []

        delegate_ctx = dict(ctx or {})
        delegate_ctx["delegate_mode"] = True
        delegate_ctx["allow_read"] = True
        delegate_ctx["allow_write"] = bool(delegate_ctx.get("allow_write", False))
        delegate_ctx["allow_exec"] = bool(delegate_ctx.get("allow_exec", False))

        delegate_messages = list(context_messages)
        delegate_messages.append(
            {
                "role": "system",
                "content": (
                    "【副脑模式】你当前是任务执行脑，只负责为复杂任务选择并调用委托型工具。"
                    "不要维持人设聊天，不要安抚，不要寒暄，只输出必要的工具调用或极简任务结论。"
                    + delegate_prompt
                ),
            }
        )
        delegate_messages.append(
            {
                "role": "user",
                "content": (
                    "请判断这条请求是否需要委托型工具。"
                    "如果需要，严格输出对应的 [CMD: 命令 | 需求说明]；"
                    "如果不需要，输出一句不超过20字的结论。\n\n"
                    f"原始请求：{user_text}"
                ),
            }
        )

        try:
            delegate_reply = await asyncio.to_thread(
                chat_with_ai,
                delegate_messages,
                task_type=task_reasoning,
                caller="chat_delegate_reasoning",
            )
        except Exception as e:
            err = str(e or "").strip()
            lowered = err.lower()
            if "429" in lowered or "rate limit" in lowered:
                return (
                    True,
                    "",
                    ["副脑当前请求过多，暂时无法读取文件或执行复杂任务，请稍后再试。"],
                    [],
                )
            return (
                True,
                "",
                [f"副脑执行复杂任务时失败：{err or '未知错误'}"],
                [],
            )
        return await self.plugin_manager.execute_commands(
            delegate_reply,
            delegate_ctx,
            allow_tools=True,
            allowed_types={"delegate"},
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

    def _merge_proactive_followup(self, followup_text: str, reply_text: str) -> str:
        followup = (followup_text or "").strip()
        reply = (reply_text or "").strip()
        if followup and reply:
            return f"{followup}\n\n{reply}"
        return followup or reply

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

        input_source = str(ctx.get("source", "unknown") or "unknown").strip()
        channel_meta = ctx.get("channel_meta") or {}
        if input_source in {"qq_gateway", "napcat_qq"}:
            sender_name = str(
                channel_meta.get("sender_name")
                or channel_meta.get("user_id")
                or "unknown"
            ).strip()
            session_preview = str(channel_meta.get("session_id") or "").strip()
            session_label = self._qq_session_label(session_preview)
            incoming_preview = (
                str(user_text or "").replace("\r", " ").replace("\n", " ").strip()
            )
            if len(incoming_preview) > 240:
                incoming_preview = incoming_preview[:240] + "..."
            self.logger.info(
                f"[QQ-IN][{session_label}][{session_preview or 'unknown'}][from={sender_name}] {incoming_preview}"
            )
        self._observe_reply_effect(user_text, ctx)
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
                image_ok = await self._send_gateway_image_reply(
                    image_path,
                    ctx,
                    caption=image_caption if send_caption_with_image else "",
                )
                if image_path:
                    asyncio.create_task(self._cleanup_gateway_image_file(image_path))
                direct_reply_text = "" if image_ok else fallback_text
                direct_memory_reply = (
                    (image_caption or success_text) if image_ok else fallback_text
                )
                handled_gateway_image = image_ok
                if not image_ok and str(ctx.get("source") or "").strip().lower() in {
                    "qq_gateway",
                    "napcat_qq",
                }:
                    await self._send_gateway_reply(
                        direct_reply_text, ctx, emotion="neutral"
                    )
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

            self._dbg("Direct 流程结束，返回 Idle")
            await self._emit_idle_status(output_profile, reason="direct_complete")
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

                diary_text = await self.summarize_day(
                    report,
                    raw_stats=raw_stats,
                    auto=False,
                    output_profile=output_profile,
                )
                if not diary_text:
                    failure_text = self._build_diary_failure_text(
                        datetime.now().strftime("%Y-%m-%d"), False
                    )
                    if output_profile.get("ui_append", True):
                        await self.event_bus.emit(
                            "ui.append", role="assistant", text=failure_text
                        )
                    await self.presenter.present(
                        failure_text,
                        emotion="neutral",
                        speak=output_profile.get("speak", True),
                        show_bubble=output_profile.get("show_bubble", True),
                    )
                    await self._send_gateway_reply(failure_text, ctx, emotion="neutral")
                asyncio.create_task(
                    self._add_memory_safe("user", user_text, meta={"path": "summary"})
                )
                await self._emit_idle_status(output_profile, reason="summary_complete")
                return

        if "总结昨天" in user_text or "补写昨天" in user_text:
            print("📅 [System] 拦截到补写昨天日记请求")
            yesterday = datetime.now().date() - timedelta(days=1)
            diary_text = await self.summarize_day(
                report_data=None,
                auto=False,
                target_date=yesterday,
                output_profile=output_profile,
            )
            if not diary_text:
                failure_text = self._build_diary_failure_text(
                    yesterday.strftime("%Y-%m-%d"), True
                )
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit(
                        "ui.append", role="assistant", text=failure_text
                    )
                await self.presenter.present(
                    failure_text,
                    emotion="neutral",
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
                await self._send_gateway_reply(failure_text, ctx, emotion="neutral")
            asyncio.create_task(
                self._add_memory_safe(
                    "user", user_text, meta={"path": "summary_makeup"}
                )
            )
            await self._emit_idle_status(output_profile, reason="summary_complete")
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
                diary_text = await self.summarize_day(
                    report_data=None,
                    auto=False,
                    target_date=requested_date,
                    output_profile=output_profile,
                )
                if not diary_text:
                    failure_text = self._build_diary_failure_text(
                        requested_date_str, is_makeup
                    )
                    if output_profile.get("ui_append", True):
                        await self.event_bus.emit(
                            "ui.append", role="assistant", text=failure_text
                        )
                    await self.presenter.present(
                        failure_text,
                        emotion="neutral",
                        speak=output_profile.get("speak", True),
                        show_bubble=output_profile.get("show_bubble", True),
                    )
                    await self._send_gateway_reply(
                        failure_text, ctx, emotion="neutral"
                    )
                asyncio.create_task(
                    self._add_memory_safe(
                        "user", user_text, meta={"path": "summary_makeup_date"}
                    )
                )
                await self._emit_idle_status(output_profile, reason="summary_complete")
                return

        # =========================================================================
        # 4. Gatekeeper 拦截层
        # =========================================================================
        remote_sources = {
            str(x).strip().lower()
            for x in set(REMOTE_CHAT_SOURCES or set())
            if str(x).strip()
        }
        direct_chat_sources = {"text_input", "voice", "codex_input", *remote_sources}
        source_key = str(input_source or "").strip().lower()
        should_reply = (
            True
            if source_key in direct_chat_sources or codex_mode or has_external_images
            else await self._should_reply(user_text)
        )
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
        await self.event_bus.emit(
            "chat.log", role="user", content=user_text, meta=user_log_meta
        )
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
                            obs_result["image_base64"], "请客观详细描述这张图片的内容。"
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
        route = self.tool_router.route(user_text)
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
        if codex_mode and need_tools:
            self._set_codex_task_state(
                ctx,
                "execute",
                summary=user_text[:200],
                meta={"triggers": effective_triggers[:8]},
            )
        task_reasoning = "codex" if codex_mode else "tool_reasoning"
        task_default = "codex" if codex_mode else "default"

        short_reply = ""
        short_emo = "neutral"
        if (
            not need_tools
            and not effective_triggers
            and not codex_mode
            and not has_external_images
            and not preface_text
            and source_key in direct_chat_sources
        ):
            short_reply, short_emo = self._build_short_reaction(user_text, ctx)

        if short_reply:
            await self._emit_assistant_text(
                short_reply,
                ctx=ctx,
                emotion=short_emo,
                transcript_meta=transcript_channel_meta,
                chat_log_source=chat_log_source,
                output_profile=output_profile,
                tool=False,
            )
            if self.learning:
                self.learning.record_interaction(
                    user_text,
                    short_reply,
                    short_emo,
                    feedback_type,
                    feedback_reaction,
                )
            short_meta = {"path": "short_reaction"}
            if memory_session_id:
                short_meta["session_id"] = memory_session_id
            await self._add_memory_safe("user", user_text, meta=short_meta)
            await self._add_memory_safe("assistant", short_reply, meta=short_meta)
            await self._emit_idle_status(output_profile, reason="short_reaction")
            return

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
        reply_style_context = self._build_reply_style_context(user_text, ctx)
        system_text = (
            f"【当前时间】{current_time}\n{PERSONA_PROMPT}\n{reply_style_context}\n{special_context}"
        )
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

        # =========================================================================
        # 6. 分支 A: ReAct 工具链
        # =========================================================================
        if need_tools or deferred_tool_flow or chat_with_ai_stream is None:
            self._dbg("进入工具 ReAct 流程")
            await self.event_bus.emit("ui.status", text="Thinking (Tools)...")

            reply1 = await asyncio.to_thread(
                chat_with_ai,
                context_messages,
                task_type=task_reasoning,
                caller="chat_tool_reasoning",
            )

            allow_tools = bool(need_tools) or self._contains_cmd(reply1 or "")
            ret = await self.plugin_manager.execute_commands(
                reply1,
                ctx,
                allow_tools=allow_tools,
                allowed_types={"react"},
            )
            triggered, clean_thought, tool_results, used_triggers = ret
            if (
                triggered
                and used_triggers == ["tool_search"]
                and tool_results
            ):
                context_messages.append(
                    {
                        "role": "assistant",
                        "content": self._strip_cmd_anywhere(reply1 or "").strip() or "我先确认可用工具。",
                    }
                )
                context_messages.append(
                    {
                        "role": "system",
                        "content": "【系统反馈】工具检索结果：\n"
                        + "\n".join(str(r) for r in tool_results)
                        + "\n如果确实需要工具，请只输出一个真实工具命令；如果不需要工具，直接简短回答。",
                    }
                )
                reply1b = await asyncio.to_thread(
                    chat_with_ai,
                    context_messages,
                    task_type=task_reasoning,
                    caller="chat_tool_deferred_reasoning",
                )
                ret = await self.plugin_manager.execute_commands(
                    reply1b,
                    ctx,
                    allow_tools=bool(self._contains_cmd(reply1b or "")),
                    allowed_types={"react"},
                )
                triggered, clean_thought, tool_results, used_triggers = ret
                if not triggered:
                    clean_thought = reply1b or clean_thought
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
                background_delegate = self._should_use_background_delegate(
                    route_reason=str(route.reason or ""),
                    delegate_triggers=delegate_triggers,
                    ctx=ctx,
                )
                if str(route.reason or "") == "workspace_read_preferred":
                    (
                        delegate_triggered,
                        delegate_clean,
                        delegate_results,
                        delegate_used,
                    ) = await self._run_workspace_read_shortcut(
                        user_text=user_text,
                        ctx=ctx,
                    )
                elif background_delegate:
                    delegate_triggered = True
                    delegate_clean = ""
                    delegate_results = ["我先在后台处理，完成后再回来告诉你结果。"]
                    delegate_used = []
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
                else:
                    (
                        delegate_triggered,
                        delegate_clean,
                        delegate_results,
                        delegate_used,
                    ) = await self._run_delegate_round(
                        user_text=user_text,
                        ctx=ctx,
                        context_messages=context_messages,
                        delegate_triggers=delegate_triggers,
                        task_reasoning=task_reasoning,
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

            final_reply = ""
            final_emo = "neutral"

            if triggered and tool_results:
                _, clean1 = self._extract_emo_tag(clean_thought or "")
                if clean1:
                    context_messages.append({"role": "assistant", "content": clean1})

                feedback = "\n".join([str(r) for r in tool_results])
                if used_triggers:
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

                compact_hint = ""
                if used_triggers:
                    used_set = {str(t or "").strip().lower() for t in used_triggers}
                    if used_set & {"search", "search_web"}:
                        compact_hint = "\n请只输出关键信息（<=3条），不要表格，不要展示思考过程，不要输出完整链接。如是行情/价格/汇率/指数问题，尽量给出具体数值+单位+时间；找不到数值就直说未找到。"
                    elif (
                        used_set & {"workspace_ops"}
                        and str(route.reason or "") == "workspace_read_preferred"
                    ):
                        compact_hint = "\n请仅基于文件内容回答：先说明这是什么文件、主要做什么；不要延伸诊断用户未明确提出的问题，不要推测故障原因，不要补充与文件内容无直接依据的建议。"
                context_messages.append(
                    {
                        "role": "system",
                        "content": f"【系统反馈】工具结果：\n{feedback}{compact_hint}\n请据此回答。",
                    }
                )
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

            final_reply = self._clean_text_for_tts(
                self._strip_internal_tags(
                    self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(final_reply))
                )
            )
            if self._should_suppress_followup_preface(user_text or ""):
                final_reply = final_reply or preface_text
            else:
                final_reply = self._merge_preface_texts(preface_text, final_reply)
            final_reply = self._apply_character_catchphrase(final_reply)

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
                assistant_log_meta = {
                    "tool": True,
                    "emotion": final_emo,
                    "source": chat_log_source,
                    **transcript_channel_meta,
                }
                if memory_session_id:
                    assistant_log_meta["session_id"] = memory_session_id
                await self.event_bus.emit(
                    "chat.log",
                    role="assistant",
                    content=final_reply,
                    meta=assistant_log_meta,
                )
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit(
                        "ui.append", role="assistant", text=final_reply
                    )
                await self.presenter.present(
                    final_reply,
                    final_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
                await self._send_gateway_reply(final_reply, ctx, emotion=final_emo)
                self._record_reply_effect(final_reply, ctx, source=chat_log_source)
                await self._record_proactive_followup(proactive_followup)
                await self._record_task_followup(task_followup)
                if codex_mode:
                    self._set_codex_task_state(
                        ctx, "finalize", summary=final_reply[:200]
                    )

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
                    await self.event_bus.emit(
                        "state.changed", state="speaking", reason="proactive_followup"
                    )
                await self.event_bus.emit(
                    "assistant.stream.feed",
                    chunk=proactive_chunk,
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )

            try:
                async for chunk in chat_with_ai_stream(
                    context_messages, task_type=task_default
                ):
                    if not chunk:
                        continue
                    if not first:
                        first = True
                        if output_profile.get("speak", True):
                            await self.event_bus.emit(
                                "state.changed", state="speaking", reason="stream_start"
                            )

                    buffer += chunk

                    if "[CMD:" in buffer and "]" in buffer:
                        buffer = self._cmd_re.sub("", buffer)

                    if "<" in buffer and ">" in buffer:
                        m = self._emo_tag_re.search(buffer)
                        if m:
                            raw = self._normalize_emo(m.group(1)) or "neutral"
                            curr_emo, _ = self.personality.adjust_emotion(raw, 0.8)
                            print(f"🎭 [Stream] 检测到情绪标签: {raw} -> {curr_emo}")
                            if live2d_enabled:
                                asyncio.create_task(
                                    self.event_bus.emit(
                                        "live2d.emotion",
                                        emotion=curr_emo,
                                        prefer_motion=False,
                                    )
                                )
                            buffer = self._emo_tag_re.sub("", buffer, count=1)

                    if len(buffer) > 15 and any(p in buffer for p in "，。！？,.!?\n"):
                        safe = self._clean_text_for_tts(
                            self._strip_cmd_anywhere(
                                self._strip_emo_tags_anywhere(buffer)
                            )
                        )
                        safe = self._strip_model_catchphrase(safe)
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
                    safe = self._clean_text_for_tts(
                        self._strip_cmd_anywhere(self._strip_emo_tags_anywhere(buffer))
                    )
                    safe = self._strip_model_catchphrase(safe)
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

            if full_reply and CHARACTER_SHARING_ENABLED:
                sharing = self.personality.try_share()
                if sharing:
                    sharing_chunk = f"\n\n{sharing}"
                    full_reply += sharing_chunk
                    await self.event_bus.emit(
                        "assistant.stream.feed",
                        chunk=sharing_chunk,
                        emotion=curr_emo,
                        speak=output_profile.get("speak", True),
                        show_bubble=output_profile.get("show_bubble", True),
                    )

            if full_reply:
                with_catchphrase = self._apply_character_catchphrase(full_reply)
                if with_catchphrase != full_reply:
                    extra_chunk = ""
                    if with_catchphrase.startswith(full_reply):
                        extra_chunk = with_catchphrase[len(full_reply) :]
                    full_reply = with_catchphrase
                    if extra_chunk:
                        await self.event_bus.emit(
                            "assistant.stream.feed",
                            chunk=extra_chunk,
                            emotion=curr_emo,
                            speak=output_profile.get("speak", True),
                            show_bubble=output_profile.get("show_bubble", True),
                        )

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
                self._update_active_time()

                # 1. 更新 UI 和 日志
                self._add_codex_session_event(
                    "assistant_reply",
                    text=full_reply,
                    ctx=ctx,
                    meta={"emotion": curr_emo, "stream": True},
                )
                if output_profile.get("ui_append", True):
                    await self.event_bus.emit(
                        "ui.append", role="assistant", text=full_reply
                    )
                stream_log_meta = {
                    "stream": True,
                    "emotion": curr_emo,
                    "source": chat_log_source,
                    **transcript_channel_meta,
                }
                if memory_session_id:
                    stream_log_meta["session_id"] = memory_session_id
                await self.event_bus.emit(
                    "chat.log",
                    role="assistant",
                    content=full_reply,
                    meta=stream_log_meta,
                )
                await self._send_gateway_reply(full_reply, ctx, emotion=curr_emo)
                self._record_reply_effect(full_reply, ctx, source=chat_log_source)
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
                await self._add_memory_safe(
                    "assistant", full_reply, meta=stream_chat_meta
                )
                if codex_mode:
                    self._set_codex_task_state(
                        ctx, "finalize", summary=full_reply[:200]
                    )

    # 🟢 [新增] 主动关怀提醒
    async def send_active_alert(self, app_name: str, minutes: int):
        """处理久坐提醒"""
        print(f"⏰ [Chat] 收到久坐提醒请求: {app_name} ({minutes}m)")

        # 1. 获取人设
        base_prompt = DEFAULT_PERSONA
        try:
            from modules.character_manager import character_manager

            c = character_manager.get_active_character()
            if c:
                base_prompt = c.get("prompt", DEFAULT_PERSONA)
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
                extracted_emo, clean_reply = self._extract_emo_tag(reply)
                clean_reply = self._apply_character_catchphrase(clean_reply)
                if not clean_reply:
                    return
                # 3. 触发弹窗和语音
                # 发送给 UI 显示弹窗 (需 UI 支持 'ui.popup' 事件，或者直接用 append)
                await self.event_bus.emit(
                    "ui.append", role="assistant", text=f"【温馨提醒】{clean_reply}"
                )
                await self.presenter.present(
                    clean_reply, emotion=extracted_emo or "concern", interrupt=True
                )

        except Exception as e:
            self.logger.error(f"Active alert failed: {e}")

    # ==================== 屏幕感知事件处理 (完整版：含自我意识+视觉+文本) ====================

    async def handle_sensor_event(
        self,
        window_title: str,
        category: str,
        count: int = 1,
        use_vision: bool = False,
        app_name: str = "",
        reason: str = "",
    ):
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

        lowered_title = clean_title.lower()
        if any(
            bad in lowered_title
            for bad in ["锁屏", "windows 默认锁屏界面", "live2d agent", "登录"]
        ):
            self.logger.info(f"🛑 [Sensor] 跳过系统/自身界面视觉吐槽 ({clean_title})")
            return

        display_app = app_name or clean_title

        if time.time() - self._last_reply_time < self._sensor_min_reply_interval_sec:
            return

        print(
            f"🤖 [Sensor] 观察: {clean_title} ({category}) | App: {display_app} | Count: {count}"
        )
        # ================= [分支 A] 自我意识 =================
        recent_context = ""
        try:
            if hasattr(self, "screen_sensor_ref") and self.screen_sensor_ref:
                recent_entries = self.screen_sensor_ref.get_recent_observations(3)
                recent_context = self._format_sensor_observations(
                    recent_entries, max_items=3
                )
        except Exception:
            recent_context = ""

        context_block = f"\n【近期观察】\n{recent_context}\n" if recent_context else ""
        sensor_persona_prompt = self._build_sensor_persona_prompt(
            ctx={"source": "desktop"}, extra_context=context_block
        )

        def record_observation(content: str, source: str):
            sensor_ref = getattr(self, "screen_sensor_ref", None)
            if not sensor_ref:
                return
            add_fn = getattr(sensor_ref, "add_observation", None) or getattr(
                sensor_ref, "_append_observation", None
            )
            if not add_fn:
                return
            try:
                add_fn(
                    content,
                    clean_title,
                    category,
                    app_name=display_app,
                    reason=reason,
                    source=source,
                )
            except TypeError:
                try:
                    add_fn(content, clean_title, category, display_app, reason, source)
                except Exception:
                    return
            except Exception:
                return

        if category == "self":
            if count > 1 and random.random() > 0.7:
                return

            sys_prompt = f"""{sensor_persona_prompt}
	     用户正在盯着【你的】程序窗口({clean_title})看。
	     请打破第四面墙，对他简短说一句话。
	     【警告】绝不能超过 15 个字！不要加引号。"""

            try:
                reply = await asyncio.to_thread(
                    chat_with_ai,
                    [{"role": "system", "content": sys_prompt}],
                    task_type="default",
                    caller="sensor_self_talk",
                )
                if reply:
                    await self._send_sensor_reply(
                        reply, "self", count, clean_title, False
                    )
            except:
                pass
            return

        # ================= [分支：看门人 (Gatekeeper) 判断] =================
        if not use_vision:
            if count <= 2 and category not in {"self", "work"}:
                self.logger.info(
                    f"🛑 [Sensor Gatekeeper] 低强度事件跳过 ({clean_title})"
                )
                return

            gk_prompt = f"""
{context_block}【场景】
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
                self.logger.info(
                    f"⚖️ [Sensor Gatekeeper] 判断是否值得吐槽: {gk_decision.strip()}"
                )

                if "YES" not in gk_decision.upper():
                    self.logger.info(
                        f"🛑 [Sensor Gatekeeper] 拦截本次纯文本吐槽 ({clean_title})，保持高冷"
                    )
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
                        v_prompt = f"""{sensor_persona_prompt}
你正看着用户的屏幕(当前活跃窗口: [{clean_title}])。
	【空间自我意识】如果你在画面边缘（如右下角）看到一个动漫女孩、桌宠或悬浮球，那就是你自己（你的实体投影）！切记不要把她当成别人。
	请主要结合用户屏幕上的工作/娱乐内容进行吐槽。
	【字数限制】极度重要！绝不能超过 20 个字！一句话结束！不要加引号，不要动不动关心。"""
                        reply = await analyze_image(img_b64, v_prompt)

                        if reply:
                            record_observation(clean_title, "vision")
                            await self._send_sensor_reply(
                                reply, category, count, window_title, True
                            )
                            return

                    # ===== 模式 2：视觉只描述 → 默认模型吐槽 =====
                    elif VISION_MODE == "separate":
                        # 先让视觉模型客观描述，并强制它识别出助手
                        v_desc_prompt = """请客观详细描述这张图片的内容，重点描述用户正在使用的软件和文字。
【特殊指令】如果你在屏幕右下角或边缘看到一个二次元/动漫风格的女孩、虚拟形象或悬浮球，请在描述中明确标记为“这是AI助手(你)的形象”。"""

                        description = await analyze_image(img_b64, v_desc_prompt)

                        if description:
                            description = self._compress_sensor_text(
                                description, max_len=800
                            )
                            record_observation(description, "vision")
                            sys_prompt = f"""{sensor_persona_prompt}

    【场景】用户当前屏幕内容如下：
    {description}

    【重要设定】描述中提到的“AI助手的形象”、“动漫女孩”就是你自己。你正隔着屏幕陪伴用户。

	    【任务】
	    结合用户屏幕上的主要内容（忽略你自己，关注用户在干嘛）进行一次简短的吐槽。
	    【字数限制】绝对不能超过 20 个字！用最精简的一句话表达，绝对不要像机器人一样罗列画面内容。不要加引号，不要动不动关心。
	    """
                            reply = await asyncio.to_thread(
                                chat_with_ai,
                                [
                                    {
                                        "role": "system",
                                        "content": sensor_persona_prompt,
                                    },
                                    {
                                        "role": "user",
                                        "content": (
                                            f"当前屏幕内容：\n{description}\n\n"
                                            "请直接输出一句不超过20个字的短评或吐槽。只有明显疲劳、焦虑、长时间工作时才关心。不要加引号。"
                                        ),
                                    },
                                ],
                                task_type="sensor_vision_talk",
                                caller="sensor_vision_talk",
                            )

                            if reply:
                                await self._send_sensor_reply(
                                    reply, category, count, window_title, True
                                )
                                return

            except Exception as e:
                self.logger.warning(f"Vision failed: {e}")

        # ================= [分支 C] 文本模式 =================
        sys_prompt = f"""{sensor_persona_prompt}

    用户刚切换到窗口: [{clean_title}] ({category})，这是今天第 {count} 次。

    【任务】直接对他说话进行吐槽。
    【字数限制】极度重要！最多绝对不能超过 20 个字！用符合你高冷/克制人设的一句话表达即可。不要加引号，不要动不动关心。
    """

        try:
            record_observation(clean_title, "text")
            reply = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "system", "content": sys_prompt}],
                task_type="default",
                caller="sensor_text_talk",
            )
            if reply:
                await self._send_sensor_reply(
                    reply, category, count, window_title, False
                )
        except Exception as e:
            self.logger.error(f"Sensor Gen failed: {e}")

    # Helper: reset sensor motion after sensor replies.
    async def _reset_sensor_motion_after(self, delay_s: float) -> None:
        """Reset sensor motion back to idle to avoid think sticking."""
        try:
            await asyncio.sleep(max(0.2, float(delay_s)))
            await self.event_bus.emit("live2d.go_idle")
        except Exception:
            return

    # Helper: send sensor replies (extract emotion only; no forced rewrite).
    async def _send_sensor_reply(
        self, reply: str, category: str, count: int, title: str, is_vision: bool
    ):
        """统一发送传感器回复"""
        extracted_emo, clean_text = self._extract_emo_tag(reply)
        clean_text = self._strip_wrapping_quotes(clean_text)

        lowered = str(clean_text or "").lower()
        bad_patterns = [
            "we need to",
            "your task",
            "up to 20 characters",
            "直接对他说话进行吐槽",
            "结合用户屏幕上的主要内容",
        ]
        if any(p in lowered for p in bad_patterns):
            self.logger.warning("⚠️ [Sensor] 视觉吐槽输出疑似复述提示词，已丢弃")
            return

        if not clean_text or len(clean_text) < 2:
            return
        clean_text = self._apply_character_catchphrase(clean_text)
        if not clean_text:
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

        # Sensor replies often use "think"; return to idle after a short delay.
        if final_emo == "think":
            delay = max(2.0, min(6.0, 1.2 + len(clean_text) * 0.12))
            asyncio.create_task(self._reset_sensor_motion_after(delay))

        # ✅ 改为串行写入
        tag = "[视觉观察]" if is_vision else "[屏幕观察]"
        await self._add_memory_safe(
            "assistant",
            f"{tag} {clean_text}",
            meta={"path": "sensor", "emotion": final_emo},
        )

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

    def _is_invalid_diary_output(self, text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return True
        lowered = content.lower()
        error_markers = (
            "the model does not exist",
            "openai_responses http",
            "error code:",
            "traceback",
            "connection error",
            "invalid api key",
            "not implemented",
            "bad_response_status_code",
            "无法连接 ai",
            "系统繁忙",
        )
        if any(marker in lowered for marker in error_markers):
            return True
        prompt_markers = (
            "[任务]",
            "[输出要求]",
            "[数据源",
            "系统时间:",
            "不要输出标题",
            "必须从“我”的视角",
        )
        return sum(1 for marker in prompt_markers if marker in content) >= 2

    def _build_diary_failure_text(self, date_str: str, is_makeup: bool) -> str:
        if is_makeup:
            return f"{date_str} 的补写日记这次没写成，我已经拦住异常返回，没有把坏内容归档。"
        return "今天的日记这次没写成，我已经拦住异常返回，没有把坏内容归档。"

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

    def _resolve_diary_subject_label(self) -> str:
        store = getattr(self.brain, "sqlite_store", None)
        candidates: list[str] = []

        if store:
            try:
                user_items = store.list_items(
                    status="active", type_="user_profile", limit=200, offset=0
                )
                for item in user_items:
                    if not isinstance(item, dict):
                        continue
                    tags = item.get("tags") or []
                    if "role:user" in tags and "name" in tags:
                        candidates.append(item.get("text"))
            except Exception:
                pass

            try:
                profile = store.get_profile()
                if isinstance(profile, dict):
                    candidates.append(profile.get("name"))
            except Exception:
                pass

            owner_profiles: list[Dict[str, Any]] = []
            owner_ids = [
                str(item).strip() for item in (NAPCAT_OWNER_USER_IDS or []) if str(item).strip()
            ]
            for owner_id in owner_ids:
                try:
                    owner_profile = store.get_qq_user_profile(owner_id)
                except Exception:
                    owner_profile = None
                if owner_profile:
                    owner_profiles.append(owner_profile)

            if not owner_profiles and hasattr(store, "list_qq_user_profiles"):
                try:
                    profiles = store.list_qq_user_profiles(limit=50) or []
                    owner_profiles = [
                        item for item in profiles if isinstance(item, dict) and item.get("is_owner")
                    ]
                except Exception:
                    owner_profiles = []

            for owner_profile in owner_profiles:
                candidates.append(owner_profile.get("remark_name"))
                candidates.append(owner_profile.get("nickname"))

        candidates.append(self._get_runtime_owner_label())

        for value in candidates:
            label = str(value or "").strip()
            if label:
                return label
        return "你"

    def _find_existing_daily_log_id(
        self, store: Any, date_str: str, active_char_id: str
    ) -> str:
        if store is None:
            return ""
        try:
            with store._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id
                    FROM episodes
                    WHERE tags_json LIKE ? AND tags_json LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (f"%date:{date_str}%", f"%role:{active_char_id}%"),
                ).fetchone()
            if row:
                return str(row["id"] or "").strip()
        except Exception:
            return ""
        return ""

    def _normalize_diary_text_block(self, text: Any) -> str:
        normalized = str(text or "").strip()
        if normalized in {"(none)", "(no chat history)", "(no owner chat history)"}:
            return ""
        return normalized

    def _is_diary_heading_line(self, line: str, date_str: str) -> bool:
        text = str(line or "").strip()
        if not text:
            return False
        normalized = text.replace("（", "(").replace("）", ")")
        if normalized.startswith(f"【日记 {date_str}】"):
            return True
        if normalized in {
            f"{date_str} 日记",
            f"{date_str}日记",
            f"{date_str} 日记 (补)",
            f"{date_str}日记(补)",
            f"{date_str} 日记(补)",
        }:
            return True
        if re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", normalized):
            return True
        return False

    def _split_diary_paragraph(self, paragraph: str, max_len: int = 140) -> List[str]:
        text = str(paragraph or "").strip()
        if len(text) <= max_len:
            return [text] if text else []
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[。！？])", text)
            if str(part).strip()
        ]
        if len(sentences) < 2:
            return [text]

        parts: List[str] = []
        current = ""
        remaining = len(sentences)
        for sentence in sentences:
            remaining -= 1
            candidate = f"{current}{sentence}" if current else sentence
            should_flush = (
                current
                and len(candidate) > max_len
                and remaining >= 1
            )
            if should_flush:
                parts.append(current.strip())
                current = sentence
                continue
            current = candidate
        if current.strip():
            parts.append(current.strip())
        return parts or [text]

    def _polish_diary_output(
        self, text: str, date_str: str, is_makeup: bool = False
    ) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""

        lines = [line.rstrip() for line in raw.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and self._is_diary_heading_line(lines[0], date_str):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)

        paragraphs: List[str] = []
        bucket: List[str] = []
        for raw_line in lines:
            line = re.sub(r"[ \t]+", " ", raw_line).strip()
            if not line:
                if bucket:
                    paragraphs.append("".join(bucket).strip())
                    bucket = []
                continue
            bucket.append(line)
        if bucket:
            paragraphs.append("".join(bucket).strip())

        cleaned: List[str] = []
        for paragraph in paragraphs:
            for part in self._split_diary_paragraph(paragraph):
                if part:
                    cleaned.append(part)

        if not cleaned:
            return ""

        if len(cleaned) > 3:
            merged = cleaned[:2]
            merged.append("".join(cleaned[2:]).strip())
            cleaned = merged

        content = "\n\n".join(cleaned).strip()
        if is_makeup and not content.startswith("这是补写"):
            makeup_markers = ("补写", "补记", "补上一笔")
            if not any(marker in cleaned[0] for marker in makeup_markers):
                cleaned[0] = f"这是补写的内容。{cleaned[0]}"
                content = "\n\n".join(cleaned).strip()
        return content

    def _extract_report_hours(self, report_text: str) -> float:
        text = str(report_text or "").strip()
        if not text:
            return 0.0
        match = re.search(r"活跃时长:\s*([\d.]+)\s*小时", text)
        if not match:
            return 0.0
        try:
            return float(match.group(1))
        except Exception:
            return 0.0

    def _is_suspicious_daily_stats(
        self, date_str: str, stats_payload: Optional[Dict[str, Any]], report_text: str
    ) -> bool:
        if isinstance(stats_payload, dict):
            payload_date = str(stats_payload.get("date") or "").strip()
            if payload_date and payload_date != date_str:
                return True
            total_hours = stats_payload.get("total_hours")
            try:
                if total_hours is not None and float(total_hours) > 24.0:
                    return True
            except Exception:
                pass
        return self._extract_report_hours(report_text) > 24.0

    def _build_diary_focus_digest(
        self,
        date_str: str,
        raw_stats: Optional[Dict[str, Any]],
        owner_local_history: str,
        owner_qq_private_history: str,
        owner_qq_group_history: str,
    ) -> str:
        lines = [f"- 日期: {date_str}"]
        stats = raw_stats if isinstance(raw_stats, dict) else {}

        durations = stats.get("durations")
        if isinstance(durations, dict) and durations:
            top_apps = sorted(
                (
                    (str(name or "").strip(), float(seconds or 0.0))
                    for name, seconds in durations.items()
                    if str(name or "").strip()
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
            if top_apps:
                app_text = "、".join(
                    f"{name}({self._format_duration_short(seconds)})"
                    for name, seconds in top_apps
                )
                lines.append(f"- 当天主要应用: {app_text}")

        category_totals = stats.get("category_totals")
        if isinstance(category_totals, dict) and category_totals:
            top_categories = sorted(
                (
                    (str(name or "").strip(), float(seconds or 0.0))
                    for name, seconds in category_totals.items()
                    if str(name or "").strip()
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:4]
            if top_categories:
                category_text = "、".join(
                    f"{name}({self._format_duration_short(seconds)})"
                    for name, seconds in top_categories
                )
                lines.append(f"- 当天主要活动类别: {category_text}")

        observation_compact = stats.get("observation_compact")
        if isinstance(observation_compact, list) and observation_compact:
            highlights = [
                str(item).strip()
                for item in observation_compact[:4]
                if str(item).strip()
            ]
            if highlights:
                lines.append(f"- 屏幕观察摘要: {' | '.join(highlights)}")

        for label, block in (
            ("本地互动", owner_local_history),
            ("QQ私聊互动", owner_qq_private_history),
            ("QQ群互动", owner_qq_group_history),
        ):
            snippet = self._extract_history_focus_lines(block, limit=4)
            if snippet:
                lines.append(f"- {label}: {' | '.join(snippet)}")

        return "\n".join(lines)

    def _extract_history_focus_lines(self, text: str, limit: int = 4) -> List[str]:
        lines: List[str] = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line in {"(none)", "(no chat history)"}:
                continue
            lines.append(line)
            if len(lines) >= limit:
                break
        return lines

    def _format_duration_short(self, seconds: Any) -> str:
        try:
            total_seconds = max(0, int(float(seconds)))
        except Exception:
            return "0分钟"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if hours and minutes:
            return f"{hours}小时{minutes}分钟"
        if hours:
            return f"{hours}小时"
        return f"{max(1, minutes)}分钟"

    async def summarize_day(
        self,
        report_data: str = None,
        raw_stats: Optional[Dict[str, Any]] = None,
        auto: bool = False,
        target_date: date = None,
        output_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not target_date:
            target_date = datetime.now().date()

        date_str = target_date.strftime("%Y-%m-%d")
        is_makeup = target_date < datetime.now().date()

        print(f"[Diary] Build summary ({date_str}) | makeup={is_makeup}")

        store = getattr(self.brain, "sqlite_store", None)

        stats_payload = (
            raw_stats
            if isinstance(raw_stats, dict)
            else (report_data if isinstance(report_data, dict) else None)
        )
        normalized_stats_payload = dict(stats_payload) if isinstance(stats_payload, dict) else None
        if normalized_stats_payload and not normalized_stats_payload.get("date"):
            normalized_stats_payload["date"] = date_str

        report_text = report_data
        if isinstance(report_data, dict):
            report_text = report_data.get(
                "summary_text", json.dumps(report_data, ensure_ascii=False)
            )
        elif not report_text and isinstance(raw_stats, dict):
            report_text = raw_stats.get(
                "summary_text", json.dumps(raw_stats, ensure_ascii=False)
            )

        if self._is_suspicious_daily_stats(
            date_str, normalized_stats_payload, str(report_text or "")
        ):
            self.logger.warning(
                f"Diary build detected suspicious stats for {date_str}; drop malformed screen summary."
            )
            normalized_stats_payload = None
            report_text = ""

        if store and isinstance(normalized_stats_payload, dict):
            try:
                if "summary_text" not in normalized_stats_payload:
                    normalized_stats_payload["summary_text"] = json.dumps(
                        normalized_stats_payload, ensure_ascii=False
                    )
                await asyncio.to_thread(
                    store.save_daily_screen_stats, date_str, normalized_stats_payload
                )
                print(f"[Diary] Screen stats saved: {date_str}")
            except Exception as e:
                print(f"[Diary] Screen stats save failed: {e}")

        if not report_text and store:
            report_text = await asyncio.to_thread(
                store.format_screen_stats_for_prompt, date_str
            )
            if self._is_suspicious_daily_stats(date_str, None, report_text):
                self.logger.warning(
                    f"Diary build skipped suspicious persisted screen summary for {date_str}."
                )
                report_text = ""
            elif not normalized_stats_payload:
                try:
                    persisted_stats = await asyncio.to_thread(
                        store.get_daily_screen_stats, date_str
                    )
                except Exception:
                    persisted_stats = None
                if isinstance(persisted_stats, dict) and not self._is_suspicious_daily_stats(
                    date_str,
                    persisted_stats,
                    str(persisted_stats.get("summary_text") or report_text or ""),
                ):
                    normalized_stats_payload = persisted_stats

        try:
            await self._backfill_napcat_history_for_day(date_str)
        except Exception as exc:
            self.logger.warning(
                f"NapCat history backfill skipped for {date_str}: {exc}"
            )

        chat_history = await asyncio.to_thread(self._fetch_day_chat_history, date_str)
        owner_chat_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str
        )
        owner_local_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str, "local"
        )
        owner_qq_private_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str, "qq_private"
        )
        owner_qq_group_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str, "qq_group"
        )

        chat_history = self._normalize_diary_text_block(chat_history)
        owner_chat_history = self._normalize_diary_text_block(owner_chat_history)
        owner_local_history = self._normalize_diary_text_block(owner_local_history)
        owner_qq_private_history = self._normalize_diary_text_block(
            owner_qq_private_history
        )
        owner_qq_group_history = self._normalize_diary_text_block(
            owner_qq_group_history
        )

        if not report_text and not chat_history and not owner_chat_history:
            print(f"[Diary] Skip {date_str}: no data")
            return ""

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

        subject_label = self._resolve_diary_subject_label()
        daily_focus = self._build_diary_focus_digest(
            date_str,
            normalized_stats_payload,
            owner_local_history,
            owner_qq_private_history,
            owner_qq_group_history,
        )

        task_desc = f"你是 {active_char_name}。请根据记录，用简体中文写一篇你的日记，内容是你看到 {subject_label} 今天做了什么，以及你和 {subject_label} 发生了什么互动。日记主体是你自己，{subject_label} 是你观察和互动的对象。"
        if is_makeup:
            task_desc = f"你是 {active_char_name}。请根据记录，用简体中文补写一篇你的日记，内容是你在 {date_str} 看到 {subject_label} 做了什么，以及你和 {subject_label} 发生了什么互动。开头只需自然带出这是补写，不要单独另起标题。日记主体是你自己，{subject_label} 是你观察和互动的对象。"

        system_prompt = f"""
{base_prompt}

[任务]
{task_desc}

[数据源1：屏幕活动]
{report_text if report_text else "(none)"}

[数据源2：完整对话历史]
{chat_history if chat_history else "(none)"}

[数据源3：{subject_label}跨渠道聊天记录]
{owner_chat_history if owner_chat_history else f"(no {subject_label} local/QQ shared history today)"}

[数据源3a：{subject_label}本地聊天]
{owner_local_history if owner_local_history else "(none)"}

[数据源3b：{subject_label} QQ 私聊]
{owner_qq_private_history if owner_qq_private_history else "(none)"}

[数据源3c：{subject_label} QQ 群聊]
{owner_qq_group_history if owner_qq_group_history else "(none)"}

[当日关键点]
{daily_focus if daily_focus else "(none)"}

[输出要求]
1. 必须只使用简体中文，不要输出英文段落、日文句子或混合语言。
2. 必须使用第一人称，并严格以“{active_char_name}”自己的视角来写；这里的“我”指的是“{active_char_name}”，不是 {subject_label}。
3. 你可以写“我看到 {subject_label} …… / 我陪着 {subject_label} …… / 我跟 {subject_label} 聊了…… / 我们一起……”，但不要把 {subject_label} 的行为直接写成“我今天打开了…… / 我今天去了…… / 我今天做了……”这种像是你亲自完成的表述。
4. 要包含具体细节，例如 {subject_label} 使用过的软件、你们讨论过的话题、你和 {subject_label} 发生过的互动。
5. 如果数据源3或3a/3b/3c不为空，要明确写出你和 {subject_label} 的本地聊天、QQ私聊、QQ群聊互动，并尽量区分这些场景。
6. 保持简洁，控制在 500 字以内。
7. 不要输出标题，不要输出项目符号，直接给出自然的一段或几段日记正文。
8. 数据源1里的屏幕内容只能当作“我看到 {subject_label} 在屏幕上做了什么/处理了什么”的线索，不能直接当作现实世界已经发生的事实。
9. 如果看到天气、锁屏壁纸、宣传文案、网页标题、窗口文字、桌面组件文案，只能写成“屏幕上出现了…… / 我看到 {subject_label} ……”，不要写成“窗外正在…… / 现实里正在……”。
10. 除非聊天记录里明确提到真实天气或真实环境，否则不要把屏幕里的天气文案改写成现实天气。
11. 在聊天记录中，只有 `Owner(Local)`、`Owner(QQ)` 明确代表 {subject_label} 本人；`OtherGroupMember(...)`、`OtherQQContact(...)` 都是别人，绝不能当作 {subject_label} 自己说的话或做的事。
12. `AI(to Owner)` 表示你和 {subject_label} 的直接互动；`AI(to QQ Group)`、`AI(to QQ Contact)` 表示你在和别人交流，不能反推成 {subject_label} 的个人行为。
13. 必须优先围绕“当日关键点”中至少 2 个具体点来写，避免把不同日期写成同一套模板。
14. 如果当天有效数据很少，就明确写“今天信息不多/互动不多”，不要用别的日期常见的活动来补足内容。
15. 默认写成 2 到 3 段短段落，段落之间空一行；不要整篇挤成一大段，也不要拆成很多碎段。
16. 第一段先写你对这一天的整体感受或开场印象，第二段再落到具体观察和互动，最后可以用一句较轻的收束。
17. 不要写成工作汇报、问题清单或分析报告，避免“今天的信息主要集中在……”“比较明确的一次互动是……”这种总结腔。
18. 开头不要单独输出日期、标题或“今日的日记，我……”，直接进入自然叙述。
		 """

        try:
            diary_content = await asyncio.to_thread(
                chat_with_ai,
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"请严格根据上面的数据与要求，以 {active_char_name} 的第一人称记录你观察到的 {subject_label} 的一天，以及你和 {subject_label} 的互动。只有 Owner(Local)/Owner(QQ) 才是 {subject_label} 本人，OtherGroupMember/OtherQQContact 都是别人；不要把别人的发言和行为记到 {subject_label} 身上，也不要把 {subject_label} 的行为直接写成你自己亲自做的事。请优先使用“当日关键点”里的当天独有细节，不要和前一天写成同一篇，直接输出日记正文。",
                    },
                ],
                task_type="summary",
                caller="daily_summary",
            )
            diary_content = (diary_content or "").strip()

            if self._is_invalid_diary_output(diary_content):
                self.logger.warning(
                    f"Diary build skipped invalid output ({date_str}): {diary_content[:180]}"
                )
                return ""

            diary_content = self._polish_diary_output(
                diary_content, date_str, is_makeup=is_makeup
            )
            if not diary_content:
                self.logger.warning(
                    f"Diary build produced empty polished output ({date_str})"
                )
                return ""

            title = f"{date_str} 日记"
            if is_makeup:
                title += " (补)"

            if store:
                episode_payload = {
                    "title": title,
                    "summary": diary_content,
                    "status": "active",
                    "tags": [
                        "daily_log",
                        f"role:{active_char_id}",
                        f"date:{date_str}",
                    ],
                    "created_at": datetime.now().isoformat(),
                }
                existing_id = self._find_existing_daily_log_id(
                    store, date_str, active_char_id
                )
                if existing_id:
                    episode_payload["id"] = existing_id
                store.upsert_episode(episode_payload)
                try:
                    stats = store.get_daily_screen_stats(date_str) or {}
                    stats["diary_done"] = True
                    store.save_daily_screen_stats(date_str, stats)
                except Exception as exc:
                    self.logger.warning(
                        f"Diary status flag update failed ({date_str}): {exc}"
                    )
            print(f"[Diary] Archived: {title}")

            asyncio.create_task(
                self._add_memory_safe(
                    "assistant",
                    f"【日记 {date_str}】{diary_content}",
                    meta={
                        "type": "episodic_memory",
                        "date": date_str,
                        "role": active_char_id,
                    },
                )
            )

            if not auto:
                profile = output_profile or build_output_profile("text_input")
                if profile.get("ui_append", True):
                    await self.event_bus.emit(
                        "ui.append", role="assistant", text=diary_content
                    )
                await self.presenter.present(
                    diary_content,
                    emotion="neutral",
                    interrupt=False,
                    speak=profile.get("speak", True),
                    show_bubble=profile.get("show_bubble", True),
                )
            return diary_content

        except Exception as e:
            self.logger.error(f"Diary build failed: {e}")
            return ""

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
                    (start_ts, end_ts),
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
                result.append(
                    {
                        "ts": int(row["ts"]),
                        "role": str(row["role"] or ""),
                        "content": str(row["content"] or ""),
                        "session_id": str(row["session_id"] or ""),
                        "meta": meta,
                    }
                )
            return result
        except Exception as e:
            print(f"[ChatService] Load day transcript failed: {e}")
            return []

    def _row_meta(self, row: Dict[str, Any]) -> Dict[str, Any]:
        meta = row.get("meta") if isinstance(row, dict) else {}
        return meta if isinstance(meta, dict) else {}

    def _row_source(self, row: Dict[str, Any]) -> str:
        return str(self._row_meta(row).get("source") or "").strip().lower()

    def _row_message_type(self, row: Dict[str, Any]) -> str:
        message_type = str(self._row_meta(row).get("message_type") or "").strip().lower()
        if message_type:
            return message_type
        session_id = str(row.get("session_id") or "").strip().lower()
        if session_id.startswith("group:"):
            return "group"
        if session_id.startswith("private:"):
            return "private"
        return ""

    def _row_sender(self, row: Dict[str, Any]) -> Dict[str, Any]:
        sender = self._row_meta(row).get("sender")
        return sender if isinstance(sender, dict) else {}

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
        message_type = (
            str(meta.get("message_type") or "private").strip().lower() or "private"
        )

        if role == "assistant":
            if self._is_owner_shared_row(row):
                speaker = "AI(to Owner)"
            elif source in QQ_REMOTE_SOURCES:
                speaker = (
                    "AI(to QQ Group)"
                    if message_type == "group"
                    else "AI(to QQ Contact)"
                )
            else:
                speaker = "AI"
        elif role == "system":
            speaker = "System"
        elif self._is_owner_shared_row(row):
            if (
                source in QQ_REMOTE_SOURCES
                or session_id in LEGACY_OWNER_PRIVATE_SESSION_IDS
            ):
                speaker = "Owner(QQ)"
            else:
                speaker = "Owner(Local)"
        elif source in QQ_REMOTE_SOURCES:
            if message_type == "group":
                speaker = f"OtherGroupMember({sender_name or 'Unknown'})"
            else:
                speaker = f"OtherQQContact({sender_name or 'Unknown'})"
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

    def _fetch_day_owner_chat_history(self, date_str: str, mode: str = "all") -> str:
        rows = self._load_day_transcript_rows(date_str)
        if not rows:
            return ""
        owner_rows = []
        for row in rows:
            source = self._row_source(row)
            session_id = str(row.get("session_id") or "").strip().lower()
            message_type = self._row_message_type(row)
            if self._is_owner_shared_row(row):
                if mode == "local" and source in QQ_REMOTE_SOURCES:
                    continue
                if mode == "qq_private" and not (
                    source in QQ_REMOTE_SOURCES and message_type == "private"
                ):
                    continue
                if mode == "qq_group" and not (
                    source in QQ_REMOTE_SOURCES and message_type == "group"
                ):
                    continue
                owner_rows.append(row)
                continue
            if source in QQ_REMOTE_SOURCES:
                sender = self._row_sender(row)
                is_owner = (
                    bool(sender.get("is_owner")) if isinstance(sender, dict) else False
                )
                if not is_owner:
                    meta = self._row_meta(row)
                    is_owner = bool(meta.get("is_owner"))
                if is_owner:
                    if mode == "local":
                        continue
                    if mode == "qq_private" and not (
                        session_id.startswith("private:") or message_type == "private"
                    ):
                        continue
                    if mode == "qq_group" and not (
                        session_id.startswith("group:") or message_type == "group"
                    ):
                        continue
                    owner_rows.append(row)
        if not owner_rows:
            return ""
        lines = [self._format_day_transcript_line(row) for row in owner_rows]
        lines = [line for line in lines if line]
        return "\n".join(lines)
