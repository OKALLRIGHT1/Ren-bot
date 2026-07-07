import json
import os
import random
from typing import Any, Dict, List


class Plugin:
    type = "direct"
    aliases = ["新三国", "三国语音", "三国玩梗"]

    def __init__(self):
        self._trigger_count = 0
        self._rules_cache: List[Dict[str, str]] | None = None
        self._session_cooldown_until: Dict[str, float] = {}

    def should_handle_direct(self, text: str, context: Dict, key: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw.startswith("/sanguo") or raw.startswith("/三国语音"):
            return True
        if self._is_in_session_cooldown(context):
            return False
        matched = self._match_rules(raw)
        if not matched:
            return False
        if self._enable_group_only() and self._is_private_chat(context):
            return False
        return True

    async def run(self, args: str, ctx: Dict[str, Any]):
        text = str(args or "").strip()
        if text.startswith("/sanguo") or text.startswith("/三国语音"):
            return self._handle_command(text)

        matched = self._match_rules(text)
        if not matched:
            return ""

        selected = [random.choice(matched)] if self._random_select() else matched[:3]
        first = selected[0]
        self._trigger_count += 1
        self._mark_session_cooldown(ctx)

        source = str((ctx or {}).get("source") or "").strip().lower()
        if source in {"qq_gateway", "napcat_qq"}:
            return {
                "__type__": "gateway_voice",
                "voice_path": first["path"],
                "success_text": f"发你一段新三国：{first['keyword']}",
                "fallback_text": f"匹配到了“{first['keyword']}”，但音频回发失败了。",
            }
        return f"匹配到了“{first['keyword']}”，音频文件已准备好：{os.path.basename(first['path'])}"

    def _handle_command(self, text: str) -> str:
        cmd = str(text or "").strip()
        if cmd in {"/sanguo", "/sanguo help", "/三国语音", "/三国语音 help"}:
            return self._help_text()
        if cmd in {"/sanguo reload", "/三国语音 reload"}:
            self._rules_cache = None
            rules = self._load_rules()
            return f"已重新加载新三国语音规则，共 {len(rules)} 条。"
        if cmd in {"/sanguo stats", "/三国语音 stats"}:
            return self._stats_text()
        if cmd in {"/sanguo list", "/三国语音 list"}:
            return self._list_text()
        return self._help_text()

    def _help_text(self) -> str:
        return (
            "新三国语音\n"
            "- 自动匹配经典台词关键词并回发音频\n"
            "- /sanguo help 查看帮助\n"
            "- /sanguo list 查看关键词\n"
            "- /sanguo stats 查看统计\n"
            "- /sanguo reload 重新加载规则"
        )

    def _stats_text(self) -> str:
        rules = self._load_rules()
        audio_dir = self._audio_dir()
        audio_count = 0
        if os.path.isdir(audio_dir):
            audio_count = len(
                [
                    name
                    for name in os.listdir(audio_dir)
                    if name.lower().endswith(".mp3")
                ]
            )
        return (
            f"新三国语音统计\n- 规则数量: {len(rules)}\n- 音频文件: {audio_count}\n"
            f"- 本次运行触发次数: {self._trigger_count}\n- 音频目录: {audio_dir}"
        )

    def _list_text(self) -> str:
        rules = self._load_rules()
        keywords = sorted(
            {
                str(item.get("keyword") or "").strip()
                for item in rules
                if str(item.get("keyword") or "").strip()
            }
        )
        if not keywords:
            return "当前没有可用的新三国语音关键词。"
        return f"新三国语音关键词（共 {len(keywords)} 个）\n" + "、".join(keywords)

    def _match_rules(self, message: str) -> List[Dict[str, str]]:
        text = str(message or "")
        out = []
        for rule in self._load_rules():
            keyword = str(rule.get("keyword") or "").strip()
            audio = str(rule.get("audio") or "").strip()
            if not keyword or not audio:
                continue
            if keyword not in text:
                continue
            path = os.path.join(
                self._audio_dir(), audio.replace("/", os.sep).replace("\\", os.sep)
            )
            if not os.path.exists(path):
                flat_path = os.path.join(self._audio_dir(), os.path.basename(audio))
                if os.path.exists(flat_path):
                    path = flat_path
                else:
                    continue
            out.append({"keyword": keyword, "path": path})
        return out

    def _load_rules(self) -> List[Dict[str, str]]:
        if self._rules_cache is not None:
            return self._rules_cache
        path = self._rules_path()
        if not os.path.exists(path):
            self._rules_cache = []
            return self._rules_cache
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._rules_cache = data if isinstance(data, list) else []
        except Exception:
            self._rules_cache = []
        return self._rules_cache

    def _audio_dir(self) -> str:
        settings = getattr(self, "settings", {}) or {}
        subdir = self._setting_text(
            settings.get("audio_subdir", "data/sound"), "data/sound"
        )
        return os.path.join(
            os.path.dirname(__file__), subdir.replace("/", os.sep).replace("\\", os.sep)
        )

    def _rules_path(self) -> str:
        settings = getattr(self, "settings", {}) or {}
        rel = self._setting_text(settings.get("rules_file", "rules.json"), "rules.json")
        return os.path.join(
            os.path.dirname(__file__), rel.replace("/", os.sep).replace("\\", os.sep)
        )

    def _enable_group_only(self) -> bool:
        settings = getattr(self, "settings", {}) or {}
        return self._setting_bool(settings.get("enable_group_only", True), True)

    def _session_cooldown_enabled(self) -> bool:
        settings = getattr(self, "settings", {}) or {}
        return self._setting_bool(settings.get("session_cooldown_enabled", True), True)

    def _session_cooldown_seconds(self) -> int:
        settings = getattr(self, "settings", {}) or {}
        value = settings.get("session_cooldown_seconds", 600)
        if isinstance(value, dict):
            value = value.get("default", 600)
        try:
            num = int(value)
        except Exception:
            num = 600
        return max(10, min(7200, num))

    def _random_select(self) -> bool:
        settings = getattr(self, "settings", {}) or {}
        return self._setting_bool(settings.get("random_select", True), True)

    def _is_private_chat(self, ctx: Dict[str, Any]) -> bool:
        channel_meta = (ctx or {}).get("channel_meta") or {}
        if channel_meta.get("group_id"):
            return False
        message_type = str(channel_meta.get("message_type") or "").strip().lower()
        return message_type == "private" or not channel_meta.get("group_id")

    def _build_scope_key(self, ctx: Dict[str, Any]) -> str:
        channel_meta = (ctx or {}).get("channel_meta") or {}
        source = str((ctx or {}).get("source") or "local").strip().lower()
        session_id = str(
            channel_meta.get("group_id")
            or channel_meta.get("session_id")
            or source
            or "default"
        ).strip()
        return session_id or "default"

    def _is_in_session_cooldown(self, ctx: Dict[str, Any]) -> bool:
        if not self._session_cooldown_enabled():
            return False
        import time

        scope_key = self._build_scope_key(ctx)
        return time.time() < float(self._session_cooldown_until.get(scope_key, 0.0))

    def _mark_session_cooldown(self, ctx: Dict[str, Any]) -> None:
        if not self._session_cooldown_enabled():
            return
        import time

        scope_key = self._build_scope_key(ctx)
        self._session_cooldown_until[scope_key] = (
            time.time() + self._session_cooldown_seconds()
        )

    def _setting_text(self, value: Any, default: str) -> str:
        if isinstance(value, dict):
            value = value.get("default", default)
        text = str(value or "").strip()
        return text or default

    def _setting_bool(self, value: Any, default: bool) -> bool:
        if isinstance(value, dict):
            value = value.get("default", default)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
