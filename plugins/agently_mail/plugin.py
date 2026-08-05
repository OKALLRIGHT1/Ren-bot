from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from core.logger import get_logger
from modules.model_catalog import normalize_model_selection
from modules.plugin_model_gateway import get_plugin_model_gateway


CliRunner = Callable[[List[str], float], Awaitable[Dict[str, Any]]]
IntentResolver = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]
logger = get_logger("AgentlyMailPlugin")


@dataclass(frozen=True)
class MailIntent:
    action: str
    params: Dict[str, Any]


class Plugin:
    name = "Agent Mail"
    type = "direct"
    allow_natural_language_direct = True
    aliases = [
        "agently_mail",
        "邮件",
        "邮箱",
        "收件箱",
        "最近邮件",
        "查邮件",
        "读邮件",
        "发邮件",
        "回邮件",
        "转发邮件",
        "测试邮件通知",
        "邮件通知测试",
    ]
    description = "通过本机 agently-cli 查询、读取、搜索和按确认流程发送邮件；支持新邮件 QQ 私聊通知。"
    example_arg = "最近邮件"
    _FONT_CANDIDATES = (
        "msyh.ttc",
        "msyhbd.ttc",
        "simhei.ttf",
        "simsun.ttc",
        "NotoSansCJK-Regular.ttc",
    )
    _CARD_WIDTH = 920
    _CARD_PADDING = 48
    _CARD_CONTENT_WIDTH = 824

    def __init__(
        self,
        cli_runner: Optional[CliRunner] = None,
        intent_resolver: Optional[IntentResolver] = None,
        persona_resolver: Any = None,
    ):
        self._cli_runner = cli_runner or self._run_cli
        self._intent_resolver = intent_resolver or self._resolve_intent_with_llm
        self._persona_resolver = persona_resolver
        self._chat_service = None
        self._notify_task: Optional[asyncio.Task] = None
        self._seen_ids: set[str] = set()
        self._seen_loaded = False
        self._notify_fail_count = 0

    async def start(self, ctx: Optional[Dict[str, Any]] = None):
        self._capture_context(ctx)
        await self._sync_notifier()

    async def stop(self):
        if self._notify_task and not self._notify_task.done():
            self._notify_task.cancel()
            try:
                await self._notify_task
            except (asyncio.CancelledError, Exception):
                pass
        self._notify_task = None

    def reload_config(self):
        """GUI 保存插件配置后热更新通知开关。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._sync_notifier())

    async def _sync_notifier(self):
        if self._bool_setting("enable_notifications", True):
            await self._ensure_notifier()
            return
        await self.stop()

    def _capture_context(self, ctx: Optional[Dict[str, Any]]):
        if not isinstance(ctx, dict):
            return
        chat_service = ctx.get("chat_service")
        if chat_service is not None:
            self._chat_service = chat_service

    async def _ensure_notifier(self):
        if self._notify_task and not self._notify_task.done():
            return
        self._notify_task = asyncio.create_task(self._notification_loop())
        logger.info("Agent Mail 新邮件通知已启动")

    async def _notification_loop(self):
        while True:
            try:
                if self._bool_setting("enable_notifications", True):
                    await self._check_new_mails_once()
                interval = max(
                    30, int(self._setting("notification_check_interval_sec", 120) or 120)
                )
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Agent Mail 通知轮询异常: %s", exc)
                await asyncio.sleep(30)

    def _seen_state_path(self) -> Path:
        path = Path(str(self._setting("seen_state_path", "data/agently_mail_seen.json") or "data/agently_mail_seen.json"))
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_seen_ids(self):
        if self._seen_loaded:
            return
        self._seen_loaded = True
        path = self._seen_state_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                ids = data.get("seen_ids") if isinstance(data, dict) else data
                if isinstance(ids, list):
                    self._seen_ids = {str(item).strip() for item in ids if str(item).strip()}
        except Exception:
            self._seen_ids = set()

    def _save_seen_ids(self):
        path = self._seen_state_path()
        ids = list(self._seen_ids)[-200:]
        self._seen_ids = set(ids)
        path.write_text(
            json.dumps({"seen_ids": ids}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _check_new_mails_once(self):
        self._load_seen_ids()
        cli_path = str(self._setting("cli_path", "agently-cli") or "agently-cli").strip() or "agently-cli"
        timeout_sec = float(self._setting("request_timeout_sec", 20))
        limit = max(1, min(20, int(self._setting("notification_poll_limit", 10) or 10)))
        payload = await self._call(
            [cli_path, "message", "+list", "--dir", "inbox", "--limit", str(limit)],
            timeout_sec,
        )
        if not payload.get("ok", True):
            self._notify_fail_count += 1
            if self._notify_fail_count >= 3:
                logger.warning(
                    "Agent Mail 轮询连续失败 %s 次: %s",
                    self._notify_fail_count,
                    payload.get("error"),
                )
            return
        self._notify_fail_count = 0
        items, _ = self._extract_list_items(payload)
        if not items:
            return

        current_ids: List[str] = []
        for item in items:
            message_id = str(item.get("message_id") or "").strip()
            if message_id:
                current_ids.append(message_id)

        if not self._seen_ids:
            # 首次启动：已读邮件记入基线，避免历史轰炸；
            # 未读邮件仍视为待通知，避免“启动前收到的未读”被静默吞掉。
            baseline_ids = {
                str(item.get("message_id") or "").strip()
                for item in items
                if str(item.get("message_id") or "").strip()
                and item.get("is_read") is not False
            }
            self._seen_ids.update(baseline_ids)
            self._save_seen_ids()
            unread_count = sum(
                1
                for item in items
                if str(item.get("message_id") or "").strip()
                and item.get("is_read") is False
            )
            logger.info(
                "Agent Mail 通知基线已建立：已读 %s 封，待通知未读 %s 封",
                len(baseline_ids),
                unread_count,
            )

        new_items = [
            item
            for item in reversed(items)
            if str(item.get("message_id") or "").strip()
            and str(item.get("message_id") or "").strip() not in self._seen_ids
        ]
        if not new_items:
            # 同步最新窗口里的 ID，避免已读集合过旧。
            self._seen_ids.update(current_ids)
            if len(self._seen_ids) > 200:
                self._save_seen_ids()
            return

        for item in new_items:
            message_id = str(item.get("message_id") or "").strip()
            if not message_id:
                continue
            try:
                await self._notify_new_mail(item, cli_path, timeout_sec)
            except Exception as exc:
                logger.warning("推送新邮件通知失败 %s: %s", message_id, exc)
            finally:
                self._seen_ids.add(message_id)
        self._save_seen_ids()

    async def _run_test_notification(
        self,
        cli_path: str,
        timeout_sec: float,
        ctx: Dict[str, Any],
    ) -> str:
        """手动触发：推送最新一封邮件通知，便于验证 QQ 推送链路。"""
        if not self._bool_setting("enable_notifications", True):
            return "新邮件通知当前是关闭的。请先在插件设置里启用“启用新邮件通知”。"
        targets = self._notification_targets()
        if not targets:
            return "未配置通知目标。请在插件设置里填写 notification_target_sessions，例如 private:1132824061。"

        limit = max(1, min(5, int(self._setting("notification_poll_limit", 10) or 10)))
        payload = await self._call(
            [cli_path, "message", "+list", "--dir", "inbox", "--limit", str(limit)],
            timeout_sec,
        )
        if not payload.get("ok", True):
            return f"测试通知失败：无法读取收件箱。{payload.get('error') or ''}".strip()
        items, _ = self._extract_list_items(payload)
        if not items:
            return "收件箱是空的，没法测试推送。先发一封邮件到 agent 邮箱再试。"

        # 优先推最新未读；没有未读就推最新一封。
        candidate = next((item for item in items if item.get("is_read") is False), items[0])
        message_id = str(candidate.get("message_id") or "").strip()
        try:
            await self._notify_new_mail(candidate, cli_path, timeout_sec)
        except Exception as exc:
            logger.warning("测试邮件通知失败: %s", exc)
            return f"测试通知推送失败：{exc}"

        # 测试后也记入 seen，避免下一轮轮询重复推同一封。
        if message_id:
            self._load_seen_ids()
            self._seen_ids.add(message_id)
            self._save_seen_ids()

        character_name = str(
            self._setting("notification_character_name", "丰川祥子") or "丰川祥子"
        ).strip() or "丰川祥子"
        subject = str(candidate.get("subject") or "(无主题)")
        target_text = "、".join(targets)
        source_hint = "QQ 私聊" if self._is_qq_context(ctx) else "配置的目标会话"
        return (
            f"已按通知流程推送一封测试邮件。\n"
            f"角色：{character_name}\n"
            f"主题：{subject}\n"
            f"目标：{target_text}\n"
            f"通道：{source_hint}\n"
            f"如果 QQ 没收到，请确认 NapCat 网关已连接，且目标会话正确。"
        )

    async def _notify_new_mail(
        self,
        list_item: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ):
        targets = self._notification_targets()
        if not targets:
            logger.warning("Agent Mail 新邮件通知已启用，但未配置 notification_target_sessions")
            return
        if self._chat_service is None or not getattr(self._chat_service, "chat_gateway", None):
            logger.warning("Agent Mail 通知缺少 chat_service/chat_gateway，跳过本次推送")
            return

        message_id = str(list_item.get("message_id") or "").strip()
        mail = dict(list_item)
        if message_id:
            detail = await self._call(
                [cli_path, "message", "+read", "--id", message_id],
                timeout_sec,
            )
            if detail.get("ok", True) and isinstance(detail.get("data"), dict):
                mail.update(detail.get("data") or {})

        character_name = str(
            self._setting("notification_character_name", "丰川祥子") or "丰川祥子"
        ).strip() or "丰川祥子"
        summary = await self._generate_notification_summary(mail, character_name)
        body_limit = int(self._setting("notification_body_chars", 1600) or 1600)
        image_path = self._render_mail_detail_card(mail, body_limit=body_limit)

        for session_id in targets:
            ctx = {
                "source": "qq_gateway",
                "channel_meta": {
                    "session_id": session_id,
                    "adapter": "napcat_qq",
                },
            }
            try:
                await self._chat_service._send_gateway_reply(
                    summary, ctx, emotion="happy"
                )
            except Exception as exc:
                logger.warning("推送邮件摘要到 %s 失败: %s", session_id, exc)
                continue
            try:
                send_image = getattr(self._chat_service, "_send_gateway_image_reply", None)
                if callable(send_image):
                    await send_image(image_path, ctx, caption="")
                else:
                    await self._chat_service._send_gateway_reply(
                        self._format_read({"ok": True, "data": mail}),
                        ctx,
                        emotion="neutral",
                    )
            except Exception as exc:
                logger.warning("推送邮件正文图片到 %s 失败: %s", session_id, exc)

        try:
            if image_path and Path(image_path).exists():
                Path(image_path).unlink(missing_ok=True)
        except Exception:
            pass

    def _notification_targets(self) -> List[str]:
        raw = self._setting("notification_target_sessions", ["private:1132824061"])
        if isinstance(raw, str):
            items = [part.strip() for part in re.split(r"[\n,;]+", raw) if part.strip()]
        elif isinstance(raw, list):
            items = [str(item).strip() for item in raw if str(item).strip()]
        else:
            items = ["private:1132824061"]
        normalized: List[str] = []
        for item in items:
            if item.isdigit():
                normalized.append(f"private:{item}")
            elif ":" in item:
                normalized.append(item)
            else:
                normalized.append(item)
        return normalized

    async def _generate_notification_summary(
        self,
        mail: Dict[str, Any],
        character_name: str,
    ) -> str:
        fallback = self._fallback_notification_summary(mail, character_name)
        persona = self._resolve_character_persona(character_name)
        sender = mail.get("from") if isinstance(mail.get("from"), dict) else {}
        sender_text = str(sender.get("name") or sender.get("email") or "未知发件人")
        subject = str(mail.get("subject") or "(无主题)")
        body = self._mail_plain_text(mail, limit=1200)
        messages = [
            {
                "role": "system",
                "content": (
                    f"你正在扮演“{persona['name']}”。\n"
                    f"人设：\n{persona['prompt']}\n\n"
                    "任务：用这个角色的口吻，告诉用户刚收到一封新邮件。\n"
                    "要求：\n"
                    "1. 简短自然，1-3 句连成一段，不要列表。\n"
                    "2. 说明发件人、主题，以及你对这封邮件的简要理解。\n"
                    "3. 如果要用角色口癖（如 desuwa / 呢 / 哦），必须接在句子末尾，"
                    "绝对不要单独成行，也不要单独作为一条消息。\n"
                    "4. 不要输出 JSON，不要加系统说明，不要暴露你是 AI。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"发件人: {sender_text}\n"
                    f"主题: {subject}\n"
                    f"时间: {mail.get('created_at') or ''}\n"
                    f"邮件摘要/正文:\n{body or '（无正文）'}"
                ),
            },
        ]
        try:
            gateway = get_plugin_model_gateway()
            result = await gateway.invoke_text(
                messages,
                selected_ids=normalize_model_selection(self._setting("model_queue", [])),
                required_purpose="tool_reasoning",
                task_type="gatekeeper",
                caller="agently_mail_notify",
                timeout_sec=12,
            )
            text = str(result.text or "").strip() if result.ok else ""
            if text:
                return self._normalize_notification_summary(text)
        except Exception as exc:
            logger.warning("生成新邮件人设摘要失败，使用兜底文案: %s", exc)
        return fallback

    def _fallback_notification_summary(
        self,
        mail: Dict[str, Any],
        character_name: str,
    ) -> str:
        sender = mail.get("from") if isinstance(mail.get("from"), dict) else {}
        sender_text = str(sender.get("name") or sender.get("email") or "未知发件人")
        subject = str(mail.get("subject") or "(无主题)")
        name = character_name or "助手"
        return self._normalize_notification_summary(
            f"{name}这边刚收到一封邮件，发件人是{sender_text}，标题是“{subject}”，"
            f"我先把正文整理成图片给你，desuwa。"
        )

    def _normalize_notification_summary(self, text: str) -> str:
        """合并单独成行的口癖，避免 QQ 里出现只有 desuwa 的第二条消息。"""
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""
        # 去掉常见包裹
        raw = re.sub(r'^["“]|["”]$', "", raw).strip()
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        if not lines:
            return ""

        particle_only = re.compile(
            r"^(?P<p>desuwa|desu|ね|呢|哦|呀|啦|哟|嘛|呐|哈|哼|哼哼|……|…|\.{2,}|~+|～+)$",
            flags=re.IGNORECASE,
        )
        merged: List[str] = []
        for line in lines:
            match = particle_only.match(line)
            if match and merged:
                particle = match.group("p")
                prev = merged[-1].rstrip(" \t")
                # 仅当句末已经是同一个口癖时跳过；desuwa 不要因为已有“呢/哦”被丢掉
                same_tail = re.compile(
                    rf"(?:{re.escape(particle)})[。！？!?.…~～]*$",
                    flags=re.IGNORECASE,
                )
                if same_tail.search(prev):
                    continue
                if particle.lower() in {"desuwa", "desu"}:
                    # 大小姐口癖接到句末：去掉尾部句号后再补 “，desuwa”
                    if prev.endswith(("。", "！", "？", "!", "?", "…", ".")):
                        prev = prev[:-1].rstrip()
                    if prev.endswith(("，", ",", "、")):
                        merged[-1] = f"{prev}{particle}"
                    else:
                        merged[-1] = f"{prev}，{particle}"
                elif prev.endswith(("。", "！", "？", "!", "?", "…", ".", "~", "～")):
                    merged[-1] = prev + particle
                else:
                    merged[-1] = f"{prev}，{particle}"
            else:
                merged.append(line)

        # QQ gateway 会按换行拆气泡，通知摘要强制合成一段。
        return " ".join(merged[:3]).strip()

    def _html_to_plain_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("text", "content", "raw", "html", "body"):
                if value.get(key):
                    return self._html_to_plain_text(value.get(key))
            return ""
        text = str(value)
        if not text.strip():
            return ""
        # 常见块级标签先转行
        text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
        text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|section|article)\s*>", "\n", text)
        text = re.sub(r"(?i)<\s*(p|div|li|tr|h[1-6]|section|article)[^>]*>", "\n", text)
        text = re.sub(r"(?i)<\s*style[^>]*>[\s\S]*?<\s*/\s*style\s*>", "", text)
        text = re.sub(r"(?i)<\s*script[^>]*>[\s\S]*?<\s*/\s*script\s*>", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = text.replace("\xa0", " ").replace("\u200b", "")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        # 压缩连续空行
        cleaned: List[str] = []
        blank = False
        for line in lines:
            if not line:
                if cleaned and not blank:
                    cleaned.append("")
                    blank = True
                continue
            cleaned.append(line)
            blank = False
        return "\n".join(cleaned).strip()

    def _mail_plain_text(self, mail: Dict[str, Any], *, limit: Optional[int] = None) -> str:
        raw_body = mail.get("body")
        plain = self._html_to_plain_text(raw_body)
        if not plain:
            plain = self._html_to_plain_text(mail.get("snippet"))
        if not plain:
            plain = self._html_to_plain_text(mail.get("text"))
        if not plain:
            plain = self._html_to_plain_text(mail.get("html"))
        plain = plain or ""
        if limit is not None and limit > 0 and len(plain) > limit:
            plain = plain[:limit].rstrip() + "\n\n（正文已截断）"
        return plain

    def _resolve_character_persona(self, character_name: str) -> Dict[str, str]:
        name = str(character_name or "").strip() or "丰川祥子"
        try:
            from modules.character_manager import character_manager

            characters = character_manager.get_all_characters() or {}
            for char in characters.values():
                if not isinstance(char, dict):
                    continue
                if str(char.get("name") or "").strip() == name:
                    prompt = str(char.get("prompt") or "").strip()
                    return {"name": name, "prompt": prompt or f"你是{name}。"}
            for char in characters.values():
                if not isinstance(char, dict):
                    continue
                if name in str(char.get("name") or ""):
                    prompt = str(char.get("prompt") or "").strip()
                    return {
                        "name": str(char.get("name") or name),
                        "prompt": prompt or f"你是{name}。",
                    }
        except Exception:
            pass
        return {"name": name, "prompt": f"你是{name}。"}

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        lowered = raw.lower()
        if not raw:
            return False
        if any(word in lowered for word in ("agently_mail", "agent mail")):
            return True
        if any(word in raw for word in ("测试邮件通知", "邮件通知测试", "测试新邮件通知")):
            return True
        if re.search(r"\b(?:mail|email)\b", lowered) and any(
            word in lowered
            for word in (
                "list",
                "inbox",
                "search",
                "read",
                "send",
                "reply",
                "forward",
                "trash",
            )
        ):
            return True
        mail_intent_patterns = (
            r"最近.*(?:邮件|邮箱|收件箱)",
            r"(?:收到了哪些|有哪些).*邮件",
            r"(?:查|看|打开|读|搜索|搜|查找).*(?:邮件|邮箱|收件箱)",
            r"(?:邮件|邮箱|收件箱).*(?:列表|账号|授权|状态)",
            r"(?:发|发送|回|回复|转发|删除)邮件",
            r"发一封.*邮件.*(?:到|给)[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
            r"(?:给|到)?[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*(?:邮箱|邮件).*(?:发|发送)",
            r"\bmsg_[A-Za-z0-9_\-]+\b",
        )
        return any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in mail_intent_patterns)

    def resolve_gated_action(self, args: str, ctx: Optional[Dict[str, Any]] = None) -> str:
        intent = self._parse_intent(args)
        action = str(getattr(intent, "action", "") or "").strip().lower()
        write_map = {
            "send": "mail.send",
            "reply": "mail.reply",
            "forward": "mail.forward",
            "trash": "mail.trash",
        }
        if action in write_map:
            return write_map[action]
        if action in {"list", "search", "read", "me"}:
            return f"mail.{action}" if action != "me" else "mail.me"
        return "mail.list"

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        ctx = dict(ctx or {})
        self._capture_context(ctx)
        args, actor_or_error = self._extract_tool_actor(args, ctx)
        if isinstance(actor_or_error, str):
            return actor_or_error
        intent = self._parse_intent(args)
        timeout_sec = float(self._setting("request_timeout_sec", 20))
        cli_path = str(self._setting("cli_path", "agently-cli") or "agently-cli").strip()
        if not cli_path:
            cli_path = "agently-cli"

        if intent.action == "test_notify":
            return await self._run_test_notification(cli_path, timeout_sec, ctx)
        if intent.action == "me":
            payload = await self._call([cli_path, "+me"], timeout_sec)
            return self._format_me(payload)
        if intent.action == "list":
            limit = self._limit(intent.params.get("limit"))
            payload = await self._call(
                [
                    cli_path,
                    "message",
                    "+list",
                    "--dir",
                    "inbox",
                    "--limit",
                    str(limit),
                ],
                timeout_sec,
            )
            if self._is_qq_context(ctx):
                return self._format_list_card_result(payload, title="最近邮件")
            return self._format_list(payload, title="最近邮件")
        if intent.action == "search":
            query = str(intent.params.get("q") or "").strip()
            if not query:
                return "要搜索邮件的话，请告诉我要搜什么关键词。"
            limit = self._limit(intent.params.get("limit"))
            payload = await self._call(
                [
                    cli_path,
                    "message",
                    "+search",
                    "--q",
                    query,
                    "--limit",
                    str(limit),
                ],
                timeout_sec,
            )
            if self._is_qq_context(ctx):
                return self._format_list_card_result(payload, title=f"搜索结果：{query}")
            return self._format_list(payload, title=f"搜索结果：{query}")
        if intent.action == "read":
            message_id = str(intent.params.get("id") or "").strip()
            if not message_id:
                return "要读哪封邮件？请给我 msg_xxx 这样的邮件 ID。"
            payload = await self._call(
                [cli_path, "message", "+read", "--id", message_id],
                timeout_sec,
            )
            return self._format_read(payload)
        if intent.action in {"send", "reply", "forward", "trash"}:
            params = await self._prepare_write_params(
                args,
                ctx,
                intent.action,
                intent.params,
                cli_path,
                timeout_sec,
            )
            return await self._start_write(intent.action, params, cli_path, timeout_sec)
        return self._help_text()

    def _extract_tool_actor(self, args: str, ctx: Dict[str, Any]) -> tuple[str, Optional[str]]:
        resolver = self._persona_resolver
        if resolver is None:
            try:
                from modules.persona_resolver import PersonaResolver

                resolver = PersonaResolver()
                self._persona_resolver = resolver
            except Exception:
                return str(args or ""), None
        try:
            actor, remaining = resolver.extract_leading_actor(str(args or ""))
        except Exception:
            return str(args or ""), None
        if not actor:
            return str(args or ""), None
        if isinstance(actor, dict):
            actor_ctx = dict(actor)
        elif getattr(actor, "ambiguous", False):
            names = "、".join(
                str(item.get("name") or item.get("character_id") or "")
                for item in getattr(actor, "candidates", [])
                if isinstance(item, dict)
            )
            return str(args or ""), f"角色称呼不明确：{names}。请使用完整角色名。"
        else:
            actor_ctx = actor.to_context()
        if actor_ctx.get("ambiguous"):
            candidates = actor_ctx.get("candidates") if isinstance(actor_ctx.get("candidates"), list) else []
            names = "、".join(str(item.get("name") or "") for item in candidates if isinstance(item, dict))
            return str(args or ""), f"角色称呼不明确：{names}。请使用完整角色名。"
        ctx["tool_actor"] = actor_ctx
        return str(remaining or "").strip(), None

    async def _prepare_write_params(
        self,
        raw_text: str,
        ctx: Dict[str, Any],
        action: str,
        params: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        params = dict(params or {})
        actor = ctx.get("tool_actor") if isinstance(ctx, dict) else None
        if isinstance(actor, dict):
            params["_actor"] = actor
        if action == "reply":
            params = await self._prepare_reply_source(raw_text, ctx, params, cli_path, timeout_sec)
        return await self._maybe_resolve_intent(raw_text, ctx, action, params)

    async def _prepare_reply_source(
        self,
        raw_text: str,
        ctx: Dict[str, Any],
        params: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        message_id = str(params.get("id") or self._extract_message_id(raw_text) or "").strip()
        if message_id and not str(params.get("id") or "").strip():
            params["id"] = message_id
        if not message_id or str(params.get("body") or "").strip():
            return params
        payload = await self._call([cli_path, "message", "+read", "--id", message_id], timeout_sec)
        if not payload.get("ok", True):
            params["_source_error"] = str(payload.get("error") or "读取原邮件失败")
            return params
        source_mail = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if source_mail:
            ctx["source_mail"] = self._source_mail_context(source_mail)
        return params

    async def _maybe_resolve_intent(
        self,
        raw_text: str,
        ctx: Dict[str, Any],
        action: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not bool(self._setting("llm_intent_enabled", True)):
            return params
        if action not in {"send", "reply", "forward", "trash"}:
            return params
        if params.get("_source_error"):
            return params
        should_resolve = self._looks_natural_mail_request(raw_text) or any(
            not str(params.get(key) or "").strip()
            for key in self._required_fields_for_action(action)
        )
        if not should_resolve:
            return params
        try:
            resolved = await asyncio.wait_for(
                self._intent_resolver(raw_text, ctx or {}),
                timeout=float(self._setting("intent_timeout_sec", 18)),
            )
        except asyncio.TimeoutError:
            merged = dict(params)
            merged["_intent_error"] = "草稿生成超时，请稍后重试，或直接提供 subject= 和 body=。"
            return merged
        except Exception:
            merged = dict(params)
            merged["_intent_error"] = "草稿生成失败，请稍后重试，或直接提供 subject= 和 body=。"
            return merged
        normalized = self._normalize_llm_intent(resolved)
        if normalized.get("action") and normalized["action"] != action:
            return params
        merged = dict(params)
        allow_llm_override = (
            self._looks_natural_mail_request(raw_text)
            or bool((ctx or {}).get("tool_actor"))
            or bool((ctx or {}).get("source_mail"))
        )
        for key in ("to", "subject", "body", "id"):
            value = str(normalized.get(key) or "").strip()
            if value and (allow_llm_override or not str(merged.get(key) or "").strip()):
                merged[key] = value
        return merged

    def _required_fields_for_action(self, action: str) -> tuple[str, ...]:
        if action == "send":
            return ("to", "subject", "body")
        if action == "reply":
            return ("id", "body")
        if action == "forward":
            return ("id", "to")
        if action == "trash":
            return ("id",)
        return ()

    def _looks_natural_mail_request(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if re.search(r"\b(?:to|subject|body|id)=", raw, flags=re.IGNORECASE):
            return False
        if re.search(r"(?:发|发送|回|回复|转发|删除).*邮件", raw):
            return True
        return bool(
            re.search(
                r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*(?:邮箱|邮件).*(?:发|发送)",
                raw,
                flags=re.IGNORECASE,
            )
        )

    def _extract_message_id(self, text: str) -> str:
        match = re.search(r"\bmsg_[A-Za-z0-9_\-]+\b", str(text or ""))
        return match.group(0) if match else ""

    def _source_mail_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sender = data.get("from") if isinstance(data.get("from"), dict) else {}
        body = str(data.get("body") or "")
        return {
            "id": str(data.get("message_id") or ""),
            "subject": str(data.get("subject") or ""),
            "from_name": str(sender.get("name") or ""),
            "from_email": str(sender.get("email") or ""),
            "body": body[:4000],
        }

    def _normalize_llm_intent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        try:
            confidence = float(payload.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence and confidence < 0.55:
            return {}
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"send", "reply", "forward", "trash"}:
            action = ""
        result = {"action": action}
        for key in ("to", "subject", "body", "id"):
            value = payload.get(key)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value if str(item).strip())
            result[key] = str(value or "").strip()
        return result

    async def _resolve_intent_with_llm(self, text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        persona_prompt = self._current_persona_prompt(ctx)
        user_content = str(text or "")
        source_mail = ctx.get("source_mail") if isinstance(ctx, dict) else None
        if isinstance(source_mail, dict) and source_mail:
            user_content += (
                "\n\n[原邮件信息]\n"
                + json.dumps(source_mail, ensure_ascii=False, indent=2)
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是邮件意图解析器。只输出 JSON，不要解释。\n"
                    "字段: action(send/reply/forward/trash/list/search/read/me/unknown), "
                    "to, subject, body, id, confidence。\n"
                    "如果用户要发邮件但正文需要你根据请求生成，可以按照当前角色人设生成简短正文草稿；"
                    "邮件正文只写邮件内容，不要添加 Agent/AI 署名或系统说明。\n"
                    f"当前角色人设:\n{persona_prompt}\n"
                    "不要执行发送，不要编造收件人。"
                ),
            },
            {"role": "user", "content": user_content},
        ]
        gateway = (ctx or {}).get("model_gateway") or get_plugin_model_gateway()
        result = await gateway.invoke_text(
            messages,
            selected_ids=normalize_model_selection(
                self._setting("model_queue", [])
            ),
            required_purpose="tool_reasoning",
            task_type="gatekeeper",
            caller="agently_mail_intent",
            timeout_sec=12,
        )
        return self._parse_llm_json(result.text) if result.ok else {}

    def _current_persona_prompt(self, ctx: Optional[Dict[str, Any]] = None) -> str:
        actor = (ctx or {}).get("tool_actor") if isinstance(ctx, dict) else None
        if isinstance(actor, dict):
            name = str(actor.get("name") or "").strip()
            prompt = str(actor.get("prompt") or "").strip()
            parts = []
            if name:
                parts.append(f"角色名: {name}")
            if prompt:
                parts.append(prompt)
            if parts:
                return "\n".join(parts)
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if isinstance(active_char, dict):
                name = str(active_char.get("name") or "").strip()
                prompt = str(active_char.get("prompt") or "").strip()
                parts = []
                if name:
                    parts.append(f"角色名: {name}")
                if prompt:
                    parts.append(prompt)
                if parts:
                    return "\n".join(parts)
        except Exception:
            pass
        try:
            from config import DEFAULT_PERSONA

            return str(DEFAULT_PERSONA or "").strip()
        except Exception:
            return ""

    def _parse_llm_json(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        if not raw.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", raw)
            raw = match.group(0) if match else raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    async def confirm_agent_action(self, payload: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        argv = payload.get("argv") if isinstance(payload, dict) else None
        if not isinstance(argv, list) or not argv:
            return "邮件确认失败：缺少原始命令。"
        token = str(payload.get("confirmation_token") or "").strip()
        if not token:
            return "邮件确认失败：缺少 confirmation token。"
        action = str(payload.get("action") or "").strip() or "send"
        timeout_sec = float(self._setting("request_timeout_sec", 20))
        result = await self._call(
            [str(item) for item in argv] + ["--confirmation-token", token],
            timeout_sec,
        )
        return self._format_write_success(result, action)

    async def _call(self, argv: List[str], timeout_sec: float) -> Dict[str, Any]:
        result = await self._cli_runner(argv, timeout_sec)
        code = int(result.get("returncode", 1))
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if code != 0:
            message = self._extract_error_message(stdout) or stderr.strip()
            return {
                "ok": False,
                "error": message or f"agently-cli 退出码 {code}",
                "returncode": code,
            }
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {"ok": True, "data": stdout.strip()}
        if isinstance(data, dict):
            return data
        return {"ok": True, "data": data}

    async def _run_cli(self, argv: List[str], timeout_sec: float) -> Dict[str, Any]:
        def run_blocking() -> Dict[str, Any]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                shell=False,
            )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }

        try:
            return await asyncio.to_thread(run_blocking)
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "", "stderr": "agently-cli 调用超时"}
        except FileNotFoundError:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": "找不到 agently-cli，请检查插件里的 cli_path 设置。",
            }

    def _parse_intent(self, text: str) -> MailIntent:
        raw = str(text or "").strip()
        lowered = raw.lower()
        if any(word in raw for word in ("测试邮件通知", "邮件通知测试", "测试新邮件通知")):
            return MailIntent("test_notify", {})
        if any(word in raw for word in ("当前邮箱", "邮箱账号", "邮箱授权")) or "+me" in lowered:
            return MailIntent("me", {})
        if any(word in raw for word in ("发邮件", "发送邮件", "发送一封邮件", "发一封邮件")):
            params = self._parse_key_values(raw)
            params.update({k: v for k, v in self._parse_natural_send(raw).items() if k not in params or not params[k]})
            return MailIntent("send", params)
        if self._looks_like_send(raw, lowered):
            params = self._parse_key_values(raw)
            params.update({k: v for k, v in self._parse_natural_send(raw).items() if k not in params or not params[k]})
            return MailIntent("send", params)
        if any(word in raw for word in ("回复邮件", "回复")) or lowered.startswith("reply "):
            params = self._parse_key_values(raw)
            message_id = self._extract_message_id(raw)
            if message_id and not params.get("id"):
                params["id"] = message_id
            return MailIntent("reply", params)
        if any(word in raw for word in ("回邮件", "回复邮件")) or lowered.startswith("reply "):
            return MailIntent("reply", self._parse_key_values(raw))
        if any(word in raw for word in ("转发邮件", "转邮件")) or lowered.startswith("forward "):
            return MailIntent("forward", self._parse_key_values(raw))
        if any(word in raw for word in ("删除邮件", "邮件移到回收站", "移到回收站")) or lowered.startswith("trash "):
            return MailIntent("trash", self._parse_key_values(raw))
        msg_match = re.search(r"\bmsg_[A-Za-z0-9_\-]+\b", raw)
        if msg_match and any(word in raw for word in ("读", "看", "打开", "详情")):
            return MailIntent("read", {"id": msg_match.group(0)})
        if any(word in raw for word in ("搜索邮件", "搜邮件", "查找邮件")):
            query = re.sub(r"^(搜索邮件|搜邮件|查找邮件)[:：\s]*", "", raw).strip()
            return MailIntent("search", {"q": query, "limit": self._extract_limit(raw)})
        if "search" in lowered:
            return MailIntent(
                "search",
                {
                    "q": raw.split("search", 1)[-1].strip(),
                    "limit": self._extract_limit(raw),
                },
            )
        if any(word in raw for word in ("最近", "收到了哪些", "收件箱", "查邮件", "邮件列表")):
            return MailIntent("list", {"limit": self._extract_limit(raw)})
        if any(word in lowered for word in ("list", "inbox")):
            return MailIntent("list", {"limit": self._extract_limit(raw)})
        return MailIntent("list", {"limit": self._extract_limit(raw)})

    def _looks_like_send(self, raw: str, lowered: str) -> bool:
        if any(word in raw for word in ("发邮件", "发送邮件")) or lowered.startswith("send "):
            return True
        return bool(
            re.search(
                r"(?:发|发送).*邮件.*(?:到|给)\s*[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
                raw,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"(?:给|到)?\s*[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*(?:邮箱|邮件).*(?:发|发送)",
                raw,
                flags=re.IGNORECASE,
            )
        )

    def _parse_natural_send(self, text: str) -> Dict[str, str]:
        raw = str(text or "").strip()
        result: Dict[str, str] = {}
        email_match = re.search(
            r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
            raw,
        )
        if email_match:
            result["to"] = email_match.group(0)

        subject_match = re.search(
            r"(?:主题|标题)\s*(?:是|为|:|：)?\s*([\s\S]*?)(?=\s*(?:正文|内容)\s*(?:是|为|:|：)|$)",
            raw,
        )
        if subject_match:
            subject = subject_match.group(1).strip()
        else:
            subject = re.sub(r"^(?:帮我)?(?:发|发送)一?封?", "", raw).strip()
            subject = re.sub(
                r"(?:邮件)?(?:到|给)\s*[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*$",
                "",
                subject,
                flags=re.IGNORECASE,
            ).strip()
            subject = re.sub(r"邮件$", "", subject).strip()
        if subject:
            result["subject"] = subject

        if email_match and "subject" not in result:
            after_email = raw[email_match.end() :].strip()
            after_email = re.sub(r"^(?:邮箱|邮件)?\s*(?:发送|发)(?:一封)?", "", after_email).strip()
            if after_email:
                result["subject"] = after_email

        body_match = re.search(
            r"(?:正文|内容)\s*(?:是|为|:|：)?\s*([\s\S]+)$",
            raw,
        )
        if body_match:
            result["body"] = body_match.group(1).strip()
        return result

    async def _start_write(
        self,
        action: str,
        params: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ) -> str | Dict[str, Any]:
        argv_or_error = self._build_write_argv(action, params, cli_path)
        if isinstance(argv_or_error, str):
            return argv_or_error
        argv = argv_or_error
        payload = await self._call(argv, timeout_sec)
        token = self._confirmation_token(payload)
        if token:
            summary = self._confirmation_summary(payload)
            actor = params.get("_actor") if isinstance(params.get("_actor"), dict) else None
            return {
                "__agent_result__": "confirmation_required",
                "trigger": "agently_mail",
                "summary": self._format_confirmation_summary(summary, action, actor),
                "payload": {
                    "action": action,
                    "argv": argv,
                    "confirmation_token": token,
                    "actor": actor,
                },
                "expires_in": self._confirmation_expires_in(payload),
            }
        return self._format_write_success(payload, action)

    def _build_write_argv(
        self,
        action: str,
        params: Dict[str, Any],
        cli_path: str,
    ) -> List[str] | str:
        if params.get("_intent_error"):
            return str(params.get("_intent_error"))
        if action == "send":
            to = str(params.get("to") or "").strip()
            subject = str(params.get("subject") or "").strip()
            body = str(params.get("body") or "").strip()
            missing = [
                label
                for label, value in (
                    ("收件人(to=)", to),
                    ("主题(subject=)", subject),
                    ("正文(body=)", body),
                )
                if not value
            ]
            if missing:
                hint_parts = []
                if to:
                    hint_parts.append(f"to={to}")
                if subject:
                    hint_parts.append(f"subject={subject}")
                hint = " ".join(hint_parts + ["body=正文"])
                return (
                    "发邮件还缺少："
                    + "、".join(missing)
                    + "。格式：发邮件 to=a@example.com subject=主题 body=正文"
                    + (f"\n你这次可以补成：发邮件 {hint}" if hint_parts else "")
                )
            return [
                cli_path,
                "message",
                "+send",
                "--to",
                to,
                "--subject",
                subject,
                "--body",
                body,
            ]
        if action == "reply":
            if params.get("_source_error"):
                return f"回信前读取原邮件失败：{params.get('_source_error')}"
            message_id = str(params.get("id") or "").strip()
            body = str(params.get("body") or "").strip()
            if not message_id or not body:
                return "回邮件需要 id=msg_xxx 和 body=回复正文。"
            return [cli_path, "message", "+reply", "--id", message_id, "--body", body]
        if action == "forward":
            message_id = str(params.get("id") or "").strip()
            to = str(params.get("to") or "").strip()
            body = str(params.get("body") or "").strip()
            if not message_id or not to:
                return "转发邮件需要 id=msg_xxx 和 to=收件人。可选 body=说明。"
            argv = [cli_path, "message", "+forward", "--id", message_id, "--to", to]
            if body:
                argv.extend(["--body", body])
            return argv
        if action == "trash":
            message_id = str(params.get("id") or "").strip()
            if not message_id:
                return "删除邮件需要 id=msg_xxx。"
            return [cli_path, "message", "+trash", "--id", message_id]
        return f"不支持的邮件写操作：{action}"

    def _parse_key_values(self, text: str) -> Dict[str, str]:
        result: Dict[str, str] = {}
        pattern = re.compile(
            r"\b(to|subject|body|id)=([\s\S]*?)(?=\s+\b(?:to|subject|body|id)=|$)",
            re.IGNORECASE,
        )
        for key, value in pattern.findall(str(text or "")):
            result[key.lower()] = value.strip().strip('"').strip("'")
        return result

    def _format_me(self, payload: Dict[str, Any]) -> str:
        if not payload.get("ok", True):
            return f"邮箱状态获取失败：{payload.get('error')}"
        return "当前 Agent Mail 账号：\n" + json.dumps(
            payload.get("data", payload),
            ensure_ascii=False,
            indent=2,
        )

    def _format_list(self, payload: Dict[str, Any], title: str) -> str:
        if not payload.get("ok", True):
            return f"{title}获取失败：{payload.get('error')}"
        envelope = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = envelope.get("data") if isinstance(envelope, dict) else []
        if not items:
            return f"{title}：没有找到邮件。"
        lines = [f"{title}："]
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            sender = item.get("from") if isinstance(item.get("from"), dict) else {}
            sender_text = sender.get("name") or sender.get("email") or "未知发件人"
            unread = "未读" if item.get("is_read") is False else "已读"
            lines.append(
                f"{index}. {item.get('subject') or '(无主题)'} | {sender_text} | "
                f"{item.get('created_at') or ''} | {unread}\n"
                f"   ID: {item.get('message_id') or ''}\n"
                f"   摘要: {item.get('snippet') or ''}"
            )
        pagination = envelope.get("pagination") if isinstance(envelope, dict) else {}
        if isinstance(pagination, dict) and pagination.get("has_more"):
            lines.append(f"还有更多邮件，next_cursor={pagination.get('next_cursor')}")
        return "\n".join(lines)

    def _format_list_card_result(self, payload: Dict[str, Any], title: str) -> Any:
        if not payload.get("ok", True):
            return f"{title}获取失败：{payload.get('error')}"
        items, has_more = self._extract_list_items(payload)
        if not items:
            return f"{title}：没有找到邮件。"
        image_path = self._render_mail_list_card(title, items, has_more=has_more)
        unread_count = sum(
            1 for item in items if isinstance(item, dict) and item.get("is_read") is False
        )
        more_text = "，还有更多" if has_more else ""
        summary = f"{title}：共 {len(items)} 封，未读 {unread_count} 封{more_text}。详情见图片。"
        return {
            "__type__": "gateway_image",
            "image_path": image_path,
            "caption": summary,
            "post_send_text": summary,
            "success_text": summary,
            "fallback_text": self._format_list(payload, title=title),
            "cleanup": True,
            "suppress_fallback_reply": False,
        }

    def _extract_list_items(self, payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
        envelope = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        raw_items = envelope.get("data") if isinstance(envelope, dict) else []
        items = [item for item in raw_items if isinstance(item, dict)]
        pagination = envelope.get("pagination") if isinstance(envelope, dict) else {}
        has_more = bool(isinstance(pagination, dict) and pagination.get("has_more"))
        return items, has_more

    def _is_qq_context(self, ctx: Dict[str, Any]) -> bool:
        source = str((ctx or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq"}

    def _card_output_path(self, prefix: str) -> Path:
        output_dir = Path(tempfile.gettempdir()) / "live2d_llm_agently_mail"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{prefix}_{int(time.time() * 1000)}.png"

    def _text_size(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
        text = str(text or "")
        if hasattr(draw, "textbbox"):
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
            return max(0, right - left), max(0, bottom - top)
        if hasattr(font, "getbbox"):
            left, top, right, bottom = font.getbbox(text)
            return max(0, right - left), max(0, bottom - top)
        return font.getsize(text)

    def _measure_text_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        return self._text_size(draw, text, font)[0]

    def _wrap_text_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        max_lines: Optional[int] = None,
    ) -> List[str]:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = raw.split("\n") if raw else [""]
        lines: List[str] = []
        for paragraph in paragraphs:
            content = paragraph.strip() if paragraph.strip() else paragraph
            if not content:
                lines.append("")
                if max_lines and len(lines) >= max_lines:
                    break
                continue
            current = ""
            for char in content:
                candidate = current + char
                if self._measure_text_width(draw, candidate, font) <= max_width or not current:
                    current = candidate
                    continue
                lines.append(current)
                current = char
                if max_lines and len(lines) >= max_lines:
                    break
            if max_lines and len(lines) >= max_lines:
                if current:
                    # 最后一行尽量补上省略号
                    overflow = current
                    base = lines[-1]
                    ellipsis = "..."
                    while base and self._measure_text_width(draw, base + ellipsis, font) > max_width:
                        base = base[:-1]
                    lines[-1] = (base + ellipsis) if base else ellipsis
                break
            if current:
                lines.append(current)
            if max_lines and len(lines) >= max_lines:
                break
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
        return lines or [""]

    def _fit_text_by_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        clean = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
        if not clean:
            return ""
        if self._measure_text_width(draw, clean, font) <= max_width:
            return clean
        ellipsis = "..."
        kept = clean
        while kept and self._measure_text_width(draw, kept + ellipsis, font) > max_width:
            kept = kept[:-1]
        return (kept + ellipsis) if kept else ellipsis

    def _draw_text_lines(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        lines: List[str],
        font: ImageFont.ImageFont,
        fill: Tuple[int, int, int],
        line_gap: int = 6,
    ) -> int:
        cursor = y
        for line in lines:
            draw.text((x, cursor), line, fill=fill, font=font)
            _, h = self._text_size(draw, line or " ", font)
            cursor += h + line_gap
        return cursor

    def _render_mail_list_card(
        self, title: str, items: List[Dict[str, Any]], *, has_more: bool = False
    ) -> str:
        output_path = self._card_output_path("mail_list")
        width = self._CARD_WIDTH
        content_width = self._CARD_CONTENT_WIDTH
        left = self._CARD_PADDING
        right = width - self._CARD_PADDING
        title_font = self._load_font(32, bold=True)
        meta_font = self._load_font(18)
        subject_font = self._load_font(22, bold=True)
        body_font = self._load_font(18)
        small_font = self._load_font(16)

        # 先用临时画布测量每行真实高度，避免固定行高导致中文重叠/溢出。
        measure = ImageDraw.Draw(Image.new("RGB", (width, 10), (255, 255, 255)))
        visible_items = items[:10]
        row_layouts: List[Dict[str, Any]] = []
        for index, item in enumerate(visible_items, 1):
            sender = item.get("from") if isinstance(item.get("from"), dict) else {}
            sender_text = str(sender.get("name") or sender.get("email") or "未知发件人")
            subject = str(item.get("subject") or "(无主题)")
            created_at = str(item.get("created_at") or "")
            snippet = self._html_to_plain_text(item.get("snippet") or item.get("body") or "")
            message_id = str(item.get("message_id") or "")
            is_unread = item.get("is_read") is False
            state = "未读" if is_unread else "已读"
            inner_width = content_width - 40
            subject_line = self._fit_text_by_width(
                measure, f"{index}. {subject}", subject_font, inner_width
            )
            meta_line = self._fit_text_by_width(
                measure,
                f"{sender_text} · {created_at} · {state}",
                small_font,
                inner_width,
            )
            snippet_lines = self._wrap_text_to_width(
                measure, snippet, body_font, inner_width, max_lines=2
            )
            id_line = self._fit_text_by_width(
                measure, f"ID: {message_id}", small_font, inner_width
            )
            subject_h = self._text_size(measure, subject_line, subject_font)[1]
            meta_h = self._text_size(measure, meta_line, small_font)[1]
            snippet_h = 0
            for line in snippet_lines:
                snippet_h += self._text_size(measure, line or " ", body_font)[1] + 4
            if snippet_lines:
                snippet_h = max(0, snippet_h - 4)
            id_h = self._text_size(measure, id_line, small_font)[1]
            row_height = 18 + subject_h + 8 + meta_h + 8 + snippet_h + 8 + id_h + 16
            row_layouts.append(
                {
                    "is_unread": is_unread,
                    "subject_line": subject_line,
                    "meta_line": meta_line,
                    "snippet_lines": snippet_lines,
                    "id_line": id_line,
                    "height": max(118, row_height),
                }
            )

        header_h = 110
        footer_h = 46
        gap = 12
        rows_h = sum(row["height"] for row in row_layouts) + gap * max(0, len(row_layouts) - 1)
        height = 24 + header_h + rows_h + footer_h + 24
        image = Image.new("RGB", (width, height), (248, 250, 252))
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle(
            (20, 20, width - 20, height - 20),
            radius=18,
            fill=(255, 255, 255),
            outline=(226, 232, 240),
            width=2,
        )
        unread_count = sum(1 for item in visible_items if item.get("is_read") is False)
        draw.text((left, 40), title, fill=(15, 23, 42), font=title_font)
        summary = f"{len(items)} 封邮件 · {unread_count} 封未读"
        if has_more:
            summary += " · 还有更多"
        draw.text((left, 84), summary, fill=(71, 85, 105), font=meta_font)

        y = 24 + header_h
        for row in row_layouts:
            top = y
            bottom = y + row["height"]
            accent = (37, 99, 235) if row["is_unread"] else (148, 163, 184)
            draw.rounded_rectangle(
                (left, top, right, bottom),
                radius=12,
                fill=(248, 250, 252),
                outline=(226, 232, 240),
                width=1,
            )
            draw.rounded_rectangle((left, top, left + 6, bottom), radius=4, fill=accent)
            text_x = left + 20
            cursor = top + 14
            draw.text((text_x, cursor), row["subject_line"], fill=(15, 23, 42), font=subject_font)
            cursor += self._text_size(draw, row["subject_line"], subject_font)[1] + 8
            draw.text((text_x, cursor), row["meta_line"], fill=(71, 85, 105), font=small_font)
            cursor += self._text_size(draw, row["meta_line"], small_font)[1] + 8
            cursor = self._draw_text_lines(
                draw,
                text_x,
                cursor,
                row["snippet_lines"],
                body_font,
                (51, 65, 85),
                line_gap=4,
            )
            draw.text((text_x, cursor), row["id_line"], fill=(100, 116, 139), font=small_font)
            y = bottom + gap

        footer = "回复“读邮件 msg_xxx”可查看正文"
        draw.text((left, height - 52), footer, fill=(100, 116, 139), font=small_font)
        image.save(output_path, "PNG", optimize=True)
        return str(output_path)

    def _render_mail_detail_card(
        self,
        mail: Dict[str, Any],
        *,
        body_limit: int = 1600,
    ) -> str:
        output_path = self._card_output_path("mail_detail")
        width = self._CARD_WIDTH
        content_width = self._CARD_CONTENT_WIDTH
        left = self._CARD_PADDING
        right = width - self._CARD_PADDING
        title_font = self._load_font(30, bold=True)
        subject_font = self._load_font(24, bold=True)
        meta_font = self._load_font(18)
        body_font = self._load_font(18)
        small_font = self._load_font(16)

        sender = mail.get("from") if isinstance(mail.get("from"), dict) else {}
        sender_text = str(sender.get("name") or sender.get("email") or "未知发件人")
        if sender.get("email") and sender.get("name"):
            sender_text = f"{sender.get('name')} <{sender.get('email')}>"
        subject = str(mail.get("subject") or "(无主题)")
        created_at = str(mail.get("created_at") or "")
        message_id = str(mail.get("message_id") or "")
        body = self._mail_plain_text(mail, limit=body_limit) or "（无正文内容）"
        attachments = mail.get("attachments") if isinstance(mail.get("attachments"), list) else []

        measure = ImageDraw.Draw(Image.new("RGB", (width, 10), (255, 255, 255)))
        subject_lines = self._wrap_text_to_width(
            measure, subject, subject_font, content_width, max_lines=3
        )
        meta_lines = [
            self._fit_text_by_width(measure, f"发件人：{sender_text}", meta_font, content_width),
            self._fit_text_by_width(measure, f"时间：{created_at}", meta_font, content_width),
            self._fit_text_by_width(measure, f"ID：{message_id}", small_font, content_width),
        ]
        if attachments:
            names = []
            for attachment in attachments[:6]:
                if not isinstance(attachment, dict):
                    continue
                names.append(str(attachment.get("filename") or attachment.get("attachment_id") or "附件"))
            if names:
                meta_lines.append(
                    self._fit_text_by_width(
                        measure,
                        f"附件：{len(attachments)} 个 · " + "、".join(names),
                        small_font,
                        content_width,
                    )
                )
        body_lines = self._wrap_text_to_width(
            measure, body, body_font, content_width - 24, max_lines=40
        )

        def _block_height(lines: List[str], font: ImageFont.ImageFont, gap: int) -> int:
            total = 0
            for line in lines:
                total += self._text_size(measure, line or " ", font)[1] + gap
            return max(0, total - gap) if lines else 0

        header_h = 56
        subject_h = _block_height(subject_lines, subject_font, 6)
        meta_h = _block_height(meta_lines, meta_font, 6)
        body_h = _block_height(body_lines, body_font, 6)
        height = 28 + header_h + 18 + subject_h + 16 + meta_h + 20 + body_h + 36
        height = max(420, min(2200, height))

        image = Image.new("RGB", (width, height), (248, 250, 252))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (20, 20, width - 20, height - 20),
            radius=18,
            fill=(255, 255, 255),
            outline=(226, 232, 240),
            width=2,
        )
        draw.rounded_rectangle((left, 36, left + 10, 72), radius=4, fill=(37, 99, 235))
        draw.text((left + 22, 40), "新邮件正文", fill=(15, 23, 42), font=title_font)

        y = 28 + header_h
        y = self._draw_text_lines(draw, left, y, subject_lines, subject_font, (15, 23, 42), line_gap=6)
        y += 10
        y = self._draw_text_lines(draw, left, y, meta_lines, meta_font, (71, 85, 105), line_gap=6)
        y += 12
        draw.line((left, y, right, y), fill=(226, 232, 240), width=1)
        y += 14
        body_box_top = y - 8
        body_box_bottom = min(height - 28, y + body_h + 16)
        draw.rounded_rectangle(
            (left - 4, body_box_top, right + 4, body_box_bottom),
            radius=12,
            fill=(248, 250, 252),
            outline=(226, 232, 240),
            width=1,
        )
        self._draw_text_lines(
            draw,
            left + 12,
            y,
            body_lines,
            body_font,
            (30, 41, 59),
            line_gap=6,
        )
        image.save(output_path, "PNG", optimize=True)
        return str(output_path)

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates = (("msyhbd.ttc",) if bold else ()) + self._FONT_CANDIDATES
        for font_name in candidates:
            font_path = Path(windir) / "Fonts" / font_name
            if not font_path.exists():
                continue
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _fit_text(self, text: str, width: int) -> str:
        # 兼容旧调用：按字符数粗略截断；新渲染统一走 _fit_text_by_width。
        clean = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
        if len(clean) <= width:
            return clean
        return textwrap.shorten(clean, width=width, placeholder="...")

    def _bool_setting(self, key: str, default: bool = False) -> bool:
        raw = self._setting(key, default)
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)

    def _format_read(self, payload: Dict[str, Any]) -> str:
        if not payload.get("ok", True):
            return f"读取邮件失败：{payload.get('error')}"
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        sender = data.get("from") if isinstance(data.get("from"), dict) else {}
        body = self._mail_plain_text(data)
        limit = int(self._setting("max_body_chars", 2400))
        truncated = ""
        if len(body) > limit:
            body = body[:limit].rstrip()
            truncated = "\n\n（正文已截断）"
        attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
        attachment_lines = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            label = str(attachment.get("filename") or attachment.get("attachment_id") or "附件")
            if attachment.get("attachment_id"):
                label += f" ({attachment.get('attachment_id')})"
            elif attachment.get("download_url"):
                label += f" ({attachment.get('download_url')})"
            attachment_lines.append(f"- {label}")
        return (
            f"邮件详情：{data.get('subject') or '(无主题)'}\n"
            f"ID: {data.get('message_id') or ''}\n"
            f"发件人: {sender.get('name') or ''} <{sender.get('email') or ''}>\n"
            f"时间: {data.get('created_at') or ''}\n"
            f"附件: {len(attachments)} 个"
            + (("\n" + "\n".join(attachment_lines)) if attachment_lines else "")
            + f"\n\n正文:\n{body}{truncated}"
        )

    def _extract_error_message(self, stdout: str) -> str:
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        return str(error or "")

    def _confirmation_token(self, payload: Dict[str, Any]) -> str:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if data.get("confirmation_required"):
            return str(data.get("confirmation_token") or "").strip()
        return ""

    def _confirmation_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        summary = data.get("summary")
        return summary if isinstance(summary, dict) else {}

    def _confirmation_expires_in(self, payload: Dict[str, Any]) -> int:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        try:
            return max(30, int(data.get("expires_in") or 300))
        except (TypeError, ValueError):
            return 300

    def _format_confirmation_summary(
        self,
        summary: Dict[str, Any],
        action: str,
        actor: Optional[Dict[str, Any]] = None,
    ) -> str:
        to_value = summary.get("to")
        to = ", ".join(str(item) for item in to_value if str(item).strip()) if isinstance(to_value, list) else ""
        subject = str(summary.get("subject") or "")
        action_name = str(summary.get("action") or action)
        lines = [f"这次邮件操作需要确认。", f"操作: {action_name}"]
        if isinstance(actor, dict) and str(actor.get("name") or "").strip():
            lines.append(f"执行角色: {str(actor.get('name')).strip()}")
        if to:
            lines.append(f"收件人: {to}")
        if subject:
            lines.append(f"主题: {subject}")
        return "\n".join(lines)

    def _format_write_success(self, payload: Dict[str, Any], action: str) -> str:
        if not payload.get("ok", True):
            return f"邮件操作失败：{payload.get('error')}"
        labels = {
            "send": "邮件已提交发送。",
            "reply": "回复已提交发送。",
            "forward": "转发已提交发送。",
            "trash": "邮件已移到回收站。",
        }
        return labels.get(action, "邮件操作已完成。")

    def _extract_limit(self, text: str) -> Optional[int]:
        match = re.search(r"(?:limit=|前|最近)?\s*(\d{1,2})\s*(?:封|条|个)?", str(text or ""))
        if not match:
            return None
        return int(match.group(1))

    def _limit(self, value: Any) -> int:
        try:
            number = int(value or self._setting("default_limit", 10))
        except (TypeError, ValueError):
            number = int(self._setting("default_limit", 10))
        return max(1, min(20, number))

    def _setting(self, key: str, default: Any) -> Any:
        settings = getattr(self, "settings", {}) or {}
        raw = settings.get(key, default) if isinstance(settings, dict) else default
        if isinstance(raw, dict):
            return raw.get("value", raw.get("default", default))
        return raw

    def _help_text(self) -> str:
        return (
            "Agent Mail 用法：\n"
            "- 最近邮件\n"
            "- 搜索邮件 关键词\n"
            "- 读邮件 msg_xxx\n"
            "- 当前邮箱\n"
            "- 测试邮件通知"
        )
