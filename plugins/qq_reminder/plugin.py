import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


COMMAND_PREFIXES = ("/提醒列表", "/提醒删除", "/提醒测试", "/提醒")
WEEKDAY_MAP = {
    "1": 0,
    "一": 0,
    "2": 1,
    "二": 1,
    "3": 2,
    "三": 2,
    "4": 3,
    "四": 3,
    "5": 4,
    "五": 4,
    "6": 5,
    "六": 5,
    "7": 6,
    "日": 6,
    "天": 6,
}
CN_NUM_MAP = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


class Plugin:
    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self._settings: Dict[str, Any] = {}
        self._chat_service = None
        self._task: Optional[asyncio.Task] = None
        self._reminders: List[Dict[str, Any]] = []
        self.reload_config()

    def reload_config(self):
        try:
            config = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
        settings = config.get("settings") or {}
        self._settings = {
            "storage_path": self._read_setting(
                settings, "storage_path", "data/qq_reminders.json"
            ),
            "scan_interval_sec": int(
                self._read_setting(settings, "scan_interval_sec", 20) or 20
            ),
            "default_reminder_prefix": self._read_setting(
                settings, "default_reminder_prefix", "⏰ 提醒时间到："
            ),
        }
        self._load_reminders()

    async def start(self, ctx: Optional[Dict[str, Any]] = None):
        self._capture_context(ctx)
        await self._ensure_scheduler()

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    def _read_setting(self, settings: dict, key: str, default):
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    def _storage_path(self) -> Path:
        path = Path(str(self._settings.get("storage_path") or "data/qq_reminders.json"))
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_reminders(self):
        path = self._storage_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._reminders = data if isinstance(data, list) else []
            else:
                self._reminders = []
        except Exception:
            self._reminders = []

    def _save_reminders(self):
        path = self._storage_path()
        path.write_text(
            json.dumps(self._reminders, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _capture_context(self, ctx: Optional[Dict[str, Any]]):
        if not isinstance(ctx, dict):
            return
        chat_service = ctx.get("chat_service")
        if chat_service is not None:
            self._chat_service = chat_service

    async def _ensure_scheduler(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self):
        while True:
            try:
                await self._tick_once()
                await asyncio.sleep(
                    max(5, int(self._settings.get("scan_interval_sec") or 20))
                )
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(30)

    async def _tick_once(self):
        if self._chat_service is None or not getattr(
            self._chat_service, "chat_gateway", None
        ):
            return
        now = datetime.now()
        today = now.date().isoformat()
        changed = False
        for item in self._reminders:
            if not item.get("enabled", True):
                continue
            weekdays = item.get("weekdays") or []
            if weekdays and now.weekday() not in weekdays:
                continue
            if (
                int(item.get("hour", -1)) != now.hour
                or int(item.get("minute", -1)) != now.minute
            ):
                continue
            if str(item.get("last_sent_date") or "") == today:
                continue
            print(
                f"[QQReminder] 命中提醒 id={item.get('id')} session={item.get('target_session')} time={item.get('hour'):02d}:{item.get('minute'):02d} content={item.get('content', '')}"
            )
            await self._send_reminder(item)
            item["last_sent_date"] = today
            changed = True
        if changed:
            self._save_reminders()

    async def _send_reminder(self, item: Dict[str, Any]):
        session_id = str(item.get("target_session") or "").strip()
        if not session_id or self._chat_service is None:
            return
        text = f"{self._settings.get('default_reminder_prefix', '⏰ 提醒时间到：')}{item.get('content', '')}".strip()
        ctx = {
            "source": "qq_gateway",
            "channel_meta": {"session_id": session_id, "adapter": "napcat_qq"},
        }
        await self._chat_service._send_gateway_reply(text, ctx, emotion="neutral")

    def should_handle_direct(
        self, user_text: str, context: dict, matched_alias: str
    ) -> bool:
        if not self._is_qq_context(context):
            return False
        text = str(user_text or "").strip()
        if any(text.startswith(prefix) for prefix in COMMAND_PREFIXES):
            return True
        return "提醒我" in text and (
            "每周" in text or "周" in text or "工作日" in text or "每天" in text
        )

    def _is_qq_context(self, context: dict) -> bool:
        source = str((context or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq"}

    def _extract_user_info(self, ctx: dict) -> Tuple[str, str]:
        channel_meta = (ctx or {}).get("channel_meta") or {}
        user_id = str(channel_meta.get("user_id") or "").strip()
        if not user_id:
            session_id = str(channel_meta.get("session_id") or "").strip()
            if session_id.startswith("private:"):
                user_id = session_id.split(":", 1)[1].strip()
        return user_id, f"private:{user_id}" if user_id else ""

    def _cn_to_int(self, text: str) -> int:
        raw = str(text or "").strip()
        if not raw:
            return 0
        if raw.isdigit():
            return int(raw)
        if raw == "十":
            return 10
        if raw.startswith("十"):
            return 10 + self._cn_to_int(raw[1:])
        if raw.endswith("十"):
            return self._cn_to_int(raw[:-1]) * 10
        if "十" in raw:
            left, right = raw.split("十", 1)
            return self._cn_to_int(left) * 10 + self._cn_to_int(right)
        return CN_NUM_MAP.get(raw, 0)

    def _parse_time(self, text: str) -> Tuple[int, int]:
        raw = str(text or "").strip()
        m = re.search(r"(?P<hour>\d{1,2}):(\d{1,2})", raw)
        if m:
            return int(m.group("hour")), int(m.group(2))

        m = re.search(
            r"(?P<prefix>早上|上午|中午|下午|晚上|凌晨)?(?P<hour>[零一二两三四五六七八九十\d]{1,3})点(?:(?P<minute>[零一二两三四五六七八九十\d]{1,3})分?)?",
            raw,
        )
        if not m:
            raise ValueError("未识别到提醒时间，请使用 17:20 或 五点二十 这类格式。")
        hour = self._cn_to_int(m.group("hour"))
        minute = self._cn_to_int(m.group("minute")) if m.group("minute") else 0
        prefix = str(m.group("prefix") or "")
        if prefix in {"下午", "晚上"} and hour < 12:
            hour += 12
        elif prefix == "中午" and hour < 11:
            hour += 12
        elif not prefix and 1 <= hour <= 7:
            hour += 12
        if hour > 23 or minute > 59:
            raise ValueError("提醒时间超出范围，请检查小时和分钟。")
        return hour, minute

    def _parse_weekdays(self, text: str) -> List[int]:
        raw = str(text or "")
        if "每天" in raw:
            return []
        if "工作日" in raw:
            return [0, 1, 2, 3, 4]
        m = re.search(
            r"(?:每周|周)([一二三四五六日天1-7])(?:到|至|-|~)(?:周)?([一二三四五六日天1-7])",
            raw,
        )
        if m:
            start = WEEKDAY_MAP[m.group(1)]
            end = WEEKDAY_MAP[m.group(2)]
            if start <= end:
                return list(range(start, end + 1))
            return list(range(start, 7)) + list(range(0, end + 1))
        hits = re.findall(r"(?:每周|周)([一二三四五六日天1-7])", raw)
        if hits:
            return sorted({WEEKDAY_MAP[item] for item in hits})
        return [0, 1, 2, 3, 4]

    def _parse_add_command(self, text: str, ctx: dict) -> Dict[str, Any]:
        user_id, target_session = self._extract_user_info(ctx)
        if not user_id or not target_session:
            raise ValueError("未识别到当前 QQ 私聊身份，请在 QQ 私聊里创建提醒。")
        weekdays = self._parse_weekdays(text)
        hour, minute = self._parse_time(text)

        time_match = re.search(
            r"\d{1,2}:\d{1,2}|(早上|上午|中午|下午|晚上|凌晨)?[零一二两三四五六七八九十\d]{1,3}点([零一二两三四五六七八九十\d]{1,3}分?)?",
            text,
        )
        content = text[time_match.end() :].strip() if time_match else text
        for prefix in ("提醒我", "提醒", "/提醒"):
            if content.startswith(prefix):
                content = content[len(prefix) :].strip()
        if not content:
            content = "该处理你的待办了。"

        return {
            "id": uuid.uuid4().hex,
            "owner_user_id": user_id,
            "target_session": target_session,
            "weekdays": weekdays,
            "hour": hour,
            "minute": minute,
            "content": content,
            "enabled": True,
            "last_sent_date": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _format_days(self, weekdays: List[int]) -> str:
        if not weekdays:
            return "每天"
        labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        if weekdays == [0, 1, 2, 3, 4]:
            return "工作日"
        return "、".join(labels[idx] for idx in weekdays)

    def _list_user_reminders(self, user_id: str) -> str:
        items = [
            item
            for item in self._reminders
            if str(item.get("owner_user_id")) == str(user_id)
        ]
        if not items:
            return "你现在还没有 QQ 定时提醒。\n示例：/提醒 每周1到周5 17:20 提醒我打卡"
        lines = ["📌 你的 QQ 定时提醒："]
        for idx, item in enumerate(items, 1):
            lines.append(
                f"{idx}. {self._format_days(item.get('weekdays') or [])} {int(item.get('hour', 0)):02d}:{int(item.get('minute', 0)):02d} - {item.get('content', '')}"
            )
        lines.append("可用：/提醒删除 1  或  /提醒测试 1")
        return "\n".join(lines)

    def _delete_user_reminder(self, user_id: str, index_text: str) -> str:
        if not str(index_text or "").strip().isdigit():
            return "请使用 /提醒删除 序号，例如 /提醒删除 1"
        index = int(index_text.strip())
        items = [
            item
            for item in self._reminders
            if str(item.get("owner_user_id")) == str(user_id)
        ]
        if index < 1 or index > len(items):
            return "提醒序号超出范围。"
        target_id = items[index - 1].get("id")
        self._reminders = [
            item for item in self._reminders if item.get("id") != target_id
        ]
        self._save_reminders()
        return f"✅ 已删除第 {index} 条提醒。"

    async def _test_user_reminder(self, user_id: str, index_text: str) -> str:
        if not str(index_text or "").strip().isdigit():
            return "请使用 /提醒测试 序号，例如 /提醒测试 1"
        index = int(index_text.strip())
        items = [
            item
            for item in self._reminders
            if str(item.get("owner_user_id")) == str(user_id)
        ]
        if index < 1 or index > len(items):
            return "提醒序号超出范围。"
        item = dict(items[index - 1])
        await self._send_reminder(item)
        return f"✅ 已立即测试发送第 {index} 条提醒。"

    @staticmethod
    def _strip_command_prefix(text: str) -> str:
        raw = str(text or "").strip()
        for prefix in COMMAND_PREFIXES:
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
        return raw

    async def run(self, args: str, ctx: dict):
        self._capture_context(ctx)
        await self._ensure_scheduler()
        if not self._is_qq_context(ctx):
            return "这个提醒功能目前只支持 QQ。"

        text = str(args or "").strip()
        user_id, _ = self._extract_user_info(ctx)
        if not user_id:
            return "未识别到当前 QQ 用户，建议在 QQ 私聊里使用该提醒功能。"

        if text.startswith("/提醒列表"):
            return self._list_user_reminders(user_id)
        if text.startswith("/提醒删除"):
            return self._delete_user_reminder(user_id, self._strip_command_prefix(text))
        if text.startswith("/提醒测试"):
            return await self._test_user_reminder(
                user_id, self._strip_command_prefix(text)
            )

        try:
            reminder = self._parse_add_command(self._strip_command_prefix(text), ctx)
        except Exception as exc:
            return f"提醒创建失败：{exc}"

        self._reminders.append(reminder)
        self._save_reminders()
        return (
            f"✅ 已创建提醒：{self._format_days(reminder['weekdays'])} "
            f"{reminder['hour']:02d}:{reminder['minute']:02d} - {reminder['content']}\n"
            f"默认会发到你的 QQ 私聊，不会发群里。"
        )
