import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .claw_mailer import ClawMailer
from .email_formatter import (
    format_auth_result,
    format_error,
    format_mail_detail,
    format_mail_list,
    format_mail_summary,
)
from .notification_watcher import NotificationWatcher

logger = logging.getLogger("ClawEmailPlugin")


class Plugin:
    """claw.163.com 邮件助手插件（direct 类型）。"""

    name = "邮件助手"
    type = "direct"

    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self._settings: Dict[str, Any] = {}
        self._chat_service = None
        self._mailer: Optional[ClawMailer] = None
        self._watcher: Optional[NotificationWatcher] = None
        self.reload_config()

    def reload_config(self):
        try:
            config = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
        runtime_settings = getattr(self, "settings", None)
        settings = (
            runtime_settings
            if isinstance(runtime_settings, dict) and runtime_settings
            else (config.get("settings") or {})
        )
        self._settings = {
            "cli_path": self._read_setting(settings, "cli_path", ""),
            "account": self._read_setting(settings, "account", ""),
            "profile": self._read_setting(settings, "profile", ""),
            "auth_token": self._read_setting(settings, "auth_token", ""),
            "enable_notifications": self._read_bool(
                settings, "enable_notifications", True
            ),
            "notification_check_interval_sec": int(
                self._read_setting(settings, "notification_check_interval_sec", 120)
                or 120
            ),
            "notification_target_sessions": self._read_list(
                settings, "notification_target_sessions"
            ),
            "summary_folders": self._read_list(settings, "summary_folders")
            or ["1"],
            "default_folder": self._read_setting(settings, "default_folder", "1"),
            "max_list_count": int(
                self._read_setting(settings, "max_list_count", 10) or 10
            ),
            "daily_summary_enabled": self._read_bool(
                settings, "daily_summary_enabled", True
            ),
            "daily_summary_time": self._read_setting(
                settings, "daily_summary_time", "09:00"
            ),
        }
        self._mailer = ClawMailer(
            cli_path=self._settings["cli_path"],
            account=self._settings["account"],
            auth_token=self._settings["auth_token"],
            profile=self._settings["profile"],
        )
        # 热更新运行中的 watcher
        if self._watcher and self._watcher.running:
            self._watcher.update_mailer(self._mailer)

    @staticmethod
    def _read_setting(settings: dict, key: str, default):
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    @staticmethod
    def _read_bool(settings: dict, key: str, default: bool) -> bool:
        value = settings.get(key, default)
        if isinstance(value, dict):
            value = value.get("default", default)
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    @staticmethod
    def _read_list(settings: dict, key: str) -> list:
        value = settings.get(key, [])
        if isinstance(value, dict):
            value = value.get("default", [])
        return value if isinstance(value, list) else []

    def _capture_context(self, ctx: Optional[Dict[str, Any]]):
        if not isinstance(ctx, dict):
            return
        cs = ctx.get("chat_service")
        if cs is not None:
            self._chat_service = cs

    async def start(self, ctx: Optional[Dict[str, Any]] = None):
        self._capture_context(ctx)
        if (
            self._settings.get("enable_notifications")
            and self._mailer
            and self._settings.get("auth_token")
        ):
            await self._ensure_watcher()

    async def stop(self):
        if self._watcher:
            await self._watcher.stop()
            self._watcher = None

    async def _ensure_watcher(self):
        if self._watcher and self._watcher.running:
            return
        self._watcher = NotificationWatcher(
            mailer=self._mailer,
            interval_sec=self._settings["notification_check_interval_sec"],
            on_new_mail=self._push_notification,
            target_sessions=self._settings["notification_target_sessions"],
        )
        await self._watcher.start()

    async def _push_notification(self, text: str, target_sessions: List[str]):
        if not self._chat_service or not target_sessions:
            return
        for session_id in target_sessions:
            try:
                ctx = {
                    "source": "qq_gateway",
                    "channel_meta": {
                        "session_id": session_id,
                        "adapter": "napcat_qq",
                    },
                }
                await self._chat_service._send_gateway_reply(
                    text, ctx, emotion="happy"
                )
            except Exception as e:
                logger.warning("推送邮件通知到 %s 失败: %s", session_id, e)

    def _parse_args(self, raw: str) -> Tuple[str, List[str]]:
        parts = [p.strip() for p in (raw or "").split("|||")]
        if not parts:
            return "", []
        return parts[0], parts[1:]

    # /指令 到 动作 的映射
    _CMD_ACTION_MAP = {
        "/邮件": ("help", []),
        "/邮箱": ("help", []),
        "/email": ("help", []),
        "/查邮件": ("list", []),
        "/看邮件": ("list", []),
        "/邮件诊断": ("diagnose", []),
        "/发邮件": ("send", []),
        "/邮件摘要": ("summary", []),
        "/邮件总结": ("summary", []),
    }

    _NATURAL_ACTION_MAP = (
        (("诊断", "排查", "diagnose"), ("diagnose", [])),
        (("认证", "登录", "login", "授权"), ("auth", ["login"])),
        (("认证", "测试", "test", "状态"), ("auth", ["test"])),
        (("收件箱", "邮件列表", "查看邮件", "查邮件", "看邮件", "列出邮件", "未读邮件", "最新邮件", "list"), ("list", [])),
        (("文件夹", "目录", "folders"), ("folders", [])),
        (("摘要", "总结", "summary"), ("summary", [])),
        (("搜索", "查找", "search"), ("search", [])),
        (("发送", "发邮件", "send"), ("send", [])),
        (("回复", "reply"), ("reply", [])),
        (("转发", "forward"), ("forward", [])),
        (("标记", "已读", "未读", "mark"), ("mark", [])),
        (("读取", "打开", "详情", "read"), ("read", [])),
    )

    # 已知的动作关键词（不需要映射）
    _KNOWN_ACTIONS = frozenset({
        "auth", "list", "read", "search", "send", "reply",
        "forward", "mark", "folders", "summary", "diagnose", "help",
    })

    def _normalize_action(self, action: str, parts: List[str]) -> Tuple[str, List[str]]:
        raw_action = str(action or "").strip()
        parts = list(parts or [])

        tokens = raw_action.split()
        if tokens:
            first = tokens[0].lower().strip()
            if first in self._KNOWN_ACTIONS or first.startswith("/"):
                if len(tokens) > 1 and not parts:
                    parts = tokens[1:]
                return first, parts

        lowered = raw_action.lower().strip()
        if lowered.startswith("/"):
            lowered = lowered[1:]
            if lowered in self._KNOWN_ACTIONS:
                return lowered, parts

        text = " ".join([raw_action] + [str(p or "") for p in parts]).lower()
        for keywords, mapped in self._NATURAL_ACTION_MAP:
            if any(keyword.lower() in text for keyword in keywords):
                mapped_action, default_parts = mapped
                if not parts and default_parts:
                    parts = list(default_parts)
                return mapped_action, parts
        return lowered, parts

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        self._capture_context(ctx)

        if not self._mailer:
            return format_error("init", "邮件服务未初始化，请检查插件配置。")

        action, parts = self._parse_args(args)
        action, parts = self._normalize_action(action, parts)

        # /指令 直接映射到动作
        if action in self._CMD_ACTION_MAP:
            mapped, default_parts = self._CMD_ACTION_MAP[action]
            action = mapped
            if not parts:
                parts = default_parts
        # 去掉 / 前缀后尝试映射（兼容 /list 等英文指令）
        elif action.startswith("/"):
            action = action[1:]

        if action in ("", "help"):
            return self._help_text()

        try:
            if action == "auth":
                return await self._handle_auth(parts)
            if action == "list":
                return await self._handle_list(parts)
            if action == "read":
                return await self._handle_read(parts)
            if action == "search":
                return await self._handle_search(parts)
            if action == "send":
                return await self._handle_send(parts)
            if action == "reply":
                return await self._handle_reply(parts)
            if action == "forward":
                return await self._handle_forward(parts)
            if action == "mark":
                return await self._handle_mark(parts)
            if action == "folders":
                return await self._handle_folders()
            if action == "summary":
                return await self._handle_summary(parts)
            if action == "diagnose":
                return await self._handle_diagnose()
            return format_error(action, "未知动作。输入 help 查看可用命令。")
        except Exception as e:
            logger.exception("邮件操作异常: %s", e)
            return format_error(action, str(e))

    def _help_text(self) -> str:
        return (
            "📧 邮件助手命令：\n"
            "  auth ||| test          — 验证认证状态\n"
            "  auth ||| login         — 获取认证链接\n"
            "  list ||| [文件夹] ||| [数量] — 列出邮件\n"
            "  read ||| <邮件ID>      — 读取邮件详情\n"
            "  search ||| <关键词> ||| [文件夹] — 搜索邮件\n"
            "  send ||| <收件人> ||| <主题> ||| <正文> — 发送邮件\n"
            "  reply ||| <邮件ID> ||| <正文> — 回复邮件\n"
            "  forward ||| <邮件ID> ||| <收件人> — 转发邮件\n"
            "  mark ||| <邮件ID> ||| <read|unread> — 标记邮件\n"
            "  folders                — 列出文件夹\n"
            "  diagnose               — 检查认证、文件夹和 INBOX 状态\n"
            "  summary ||| [文件夹] ||| [天数] — 邮件摘要"
        )

    async def _handle_auth(self, parts: List[str]) -> str:
        action = parts[0].lower() if parts else "test"
        if action == "test":
            result = await self._mailer.auth_test()
            return format_auth_result(result)
        if action == "login":
            result = await self._mailer.auth_login()
            if result.get("ok") or result.get("success"):
                return "✅ 认证成功。"
            url = result.get("url", result.get("raw", ""))
            if url:
                return f"请访问以下链接完成验证:\n{url}"
            return f"认证结果: {result}"
        return format_error("auth", f"未知子命令: {action}")

    @staticmethod
    def _safe_int(value: str, default: int, min_val: int = 1, max_val: int = 9999) -> int:
        try:
            return max(min_val, min(max_val, int(value)))
        except (ValueError, TypeError):
            return default

    async def _handle_list(self, parts: List[str]) -> str:
        folder = parts[0] if len(parts) >= 1 else self._settings["default_folder"]
        limit = self._safe_int(parts[1], self._settings["max_list_count"]) if len(parts) >= 2 else self._settings["max_list_count"]
        result = await self._mailer.list_mails(folder=folder, limit=limit)
        if not result.get("ok", True) and "error" in result:
            return format_error("list", result["error"])
        mails = result.get("mails", result.get("data", []))
        if not isinstance(mails, list):
            raw = result.get("raw", "")
            return raw if raw else format_mail_list([], folder)
        text = format_mail_list(mails, folder)
        if not mails and str(folder).upper() in {"INBOX", "1"}:
            text += "\n\n提示：如果你确认已发到这个邮箱，请先用 /邮件诊断 查看认证账号和实际文件夹。ClawEmail 默认只接收已授权邮箱的来信，外部邮箱需要在控制台对应 Agent 邮箱的通讯规则中开启外部通信，或把发件人/域名加入信任联系人白名单。"
        return text

    async def _handle_read(self, parts: List[str]) -> str:
        if not parts:
            return format_error("read", "需要邮件 ID。用法: read ||| <邮件ID>")
        folder = parts[1] if len(parts) >= 2 else self._settings["default_folder"]
        result = await self._mailer.read_mail_body(parts[0], folder=folder)
        if not result.get("ok", True) and "error" in result:
            return format_error("read", result["error"])
        mail = result.get("mail", result.get("data", result))
        if isinstance(mail, dict):
            return format_mail_detail(mail)
        return str(result.get("raw", result))

    async def _handle_search(self, parts: List[str]) -> str:
        if not parts:
            return format_error("search", "需要搜索关键词。用法: search ||| <关键词>")
        query = parts[0]
        folder = parts[1] if len(parts) >= 2 else self._settings["default_folder"]
        limit = self._safe_int(parts[2], self._settings["max_list_count"]) if len(parts) >= 3 else self._settings["max_list_count"]
        result = await self._mailer.search_mails(
            query=query, folder=folder, limit=limit
        )
        if not result.get("ok", True) and "error" in result:
            return format_error("search", result["error"])
        mails = result.get("mails", result.get("data", []))
        if not isinstance(mails, list):
            raw = result.get("raw", "")
            return raw if raw else format_mail_list([], folder)
        return format_mail_list(mails, f"搜索 [{query}]")

    async def _handle_send(self, parts: List[str]) -> str:
        if len(parts) < 3:
            return format_error(
                "send", "需要收件人、主题和正文。用法: send ||| <收件人> ||| <主题> ||| <正文>"
            )
        to, subject, body = parts[0], parts[1], parts[2]
        cc = parts[3] if len(parts) >= 4 else None
        bcc = parts[4] if len(parts) >= 5 else None
        result = await self._mailer.send_mail(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc
        )
        if not result.get("ok", True) and "error" in result:
            return format_error("send", result["error"])
        return f"✅ 邮件已发送给 {to}，主题: {subject}"

    async def _handle_reply(self, parts: List[str]) -> str:
        if len(parts) < 2:
            return format_error(
                "reply", "需要邮件 ID 和回复内容。用法: reply ||| <邮件ID> ||| <正文>"
            )
        message_id, body = parts[0], parts[1]
        result = await self._mailer.reply_mail(message_id, body)
        if not result.get("ok", True) and "error" in result:
            return format_error("reply", result["error"])
        return f"✅ 已回复邮件 {message_id}"

    async def _handle_forward(self, parts: List[str]) -> str:
        if len(parts) < 2:
            return format_error(
                "forward", "需要邮件 ID 和收件人。用法: forward ||| <邮件ID> ||| <收件人>"
            )
        message_id, to = parts[0], parts[1]
        result = await self._mailer.forward_mail(message_id, to)
        if not result.get("ok", True) and "error" in result:
            return format_error("forward", result["error"])
        return f"✅ 已将邮件 {message_id} 转发给 {to}"

    async def _handle_mark(self, parts: List[str]) -> str:
        if len(parts) < 2:
            return format_error(
                "mark", "需要邮件 ID 和标记。用法: mark ||| <邮件ID> ||| <read|unread>"
            )
        message_id = parts[0]
        flag = parts[1].lower().strip()
        is_read = flag in ("read", "已读", "1", "true")
        result = await self._mailer.mark_mail(message_id, read=is_read)
        if not result.get("ok", True) and "error" in result:
            return format_error("mark", result["error"])
        label = "已读" if is_read else "未读"
        return f"✅ 已将邮件 {message_id} 标记为 {label}"

    async def _handle_folders(self) -> str:
        result = await self._mailer.list_folders()
        if not result.get("ok", True) and "error" in result:
            return format_error("folders", result["error"])
        folders = result.get("folders", result.get("data", []))
        if isinstance(folders, list) and folders:
            lines = ["📁 文件夹列表："]
            for f in folders:
                if isinstance(f, dict):
                    folder_id = f.get("id") or f.get("fid") or ""
                    name = f.get("name", f.get("path", str(f)))
                    count = f.get("count", f.get("total", ""))
                    line = f"  - {name}"
                    if folder_id:
                        line += f" [id={folder_id}]"
                    if count:
                        line += f" ({count})"
                    lines.append(line)
                else:
                    lines.append(f"  - {f}")
            return "\n".join(lines)
        raw = result.get("raw", "")
        return raw if raw else "📁 无法获取文件夹列表。"

    async def _handle_diagnose(self) -> str:
        lines = ["🧪 邮件诊断"]
        lines.append(f"配置账号: {self._settings.get('account') or '未配置'}")
        profile = self._settings.get("profile") or "默认 profile"
        lines.append(f"CLI profile: {profile}")
        lines.append("Claw API Key: 已配置" if self._settings.get("auth_token") else "Claw API Key: 未配置")

        auth = await self._mailer.auth_test()
        lines.append("\n认证状态:")
        lines.append(format_auth_result(auth))

        folders_result = await self._mailer.list_folders()
        lines.append("\n文件夹状态:")
        if not folders_result.get("ok", True) and "error" in folders_result:
            lines.append(format_error("folders", folders_result["error"]))
        else:
            folders = folders_result.get("folders", folders_result.get("data", []))
            if isinstance(folders, list) and folders:
                preview = []
                for folder in folders[:12]:
                    if isinstance(folder, dict):
                        folder_id = folder.get("id") or folder.get("fid") or ""
                        name = folder.get("name") or folder.get("path") or folder_id or str(folder)
                        count = folder.get("count", folder.get("total", ""))
                        label = f"{name}[id={folder_id}]" if folder_id else str(name)
                        preview.append(f"{label}({count})" if count != "" else label)
                    else:
                        preview.append(str(folder))
                lines.append("可见文件夹: " + ", ".join(preview))
            else:
                raw = folders_result.get("raw", "")
                lines.append(raw if raw else "未返回文件夹列表。")

        default_folder = self._settings["default_folder"]
        inbox = await self._mailer.list_mails(folder=default_folder, limit=5)
        lines.append(f"\n默认收件箱状态(folder={default_folder}):")
        if not inbox.get("ok", True) and "error" in inbox:
            lines.append(format_error("list", inbox["error"]))
        else:
            mails = inbox.get("mails", inbox.get("data", []))
            if isinstance(mails, list):
                lines.append(f"可见邮件数: {len(mails)}")
                if mails:
                    lines.append(format_mail_list(mails, str(default_folder)))
                else:
                    lines.append("默认收件箱当前为空。请对照文件夹列表检查垃圾箱/分类文件夹；如果这是外部邮箱发来的邮件，请检查 ClawEmail 控制台的通讯规则/信任联系人白名单。")
            else:
                lines.append(str(inbox.get("raw") or inbox))

        return "\n".join(lines)

    async def _handle_summary(self, parts: List[str]) -> str:
        folder = parts[0] if len(parts) >= 1 else self._settings["default_folder"]
        days = self._safe_int(parts[1], 1, min_val=1, max_val=30) if len(parts) >= 2 else 1
        limit = min(days * 20, 100)
        result = await self._mailer.list_mails(folder=folder, limit=limit)
        if not result.get("ok", True) and "error" in result:
            return format_error("summary", result["error"])
        mails = result.get("mails", result.get("data", []))
        if not isinstance(mails, list):
            mails = []
        return format_mail_summary(mails, days)

    # sub_mailbox 已移除：CLI 不支持子邮箱操作
