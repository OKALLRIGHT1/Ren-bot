from typing import Any, Dict, List, Tuple


TRIGGERS = (
    "/帮助",
    "/功能",
    "/指令",
    "你有什么功能",
    "你会什么",
    "有哪些功能",
    "有什么指令",
)


class Plugin:
    def should_handle_direct(
        self, user_text: str, context: dict, matched_alias: str
    ) -> bool:
        text = str(user_text or "").strip()
        return any(text.startswith(item) for item in TRIGGERS)

    def _read_setting(self, settings: Dict[str, Any], key: str, default: Any) -> Any:
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return default if value is None else value

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(value)

    def _format_direct_plugin(self, trigger: str, config: Dict[str, Any]) -> str:
        name = str(config.get("name") or trigger)
        aliases = config.get("aliases") or [trigger]
        alias_text = " / ".join(
            str(item).strip() for item in aliases[:4] if str(item).strip()
        )
        desc = str(config.get("description") or "").strip()
        line = f"- {name}：{alias_text}"
        if desc:
            line += f" | {desc}"
        return line

    def _parse_query(self, text: str) -> str:
        raw = str(text or "").strip()
        for prefix in TRIGGERS:
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
        return raw

    def _categorize(self, trigger: str, config: Dict[str, Any]) -> Tuple[str, str]:
        name = str(config.get("name") or trigger)
        desc = str(config.get("description") or "")
        text = f"{trigger} {name} {desc}"
        if any(item in text for item in ("画图", "画画", "生图", "image", "draw")):
            return "生图", "生图"
        if any(item in text for item in ("提醒", "日报", "timer", "schedule", "定时")):
            return "提醒", "提醒"
        if any(item in text for item in ("文件", "截图", "screen", "browse", "浏览")):
            return "文件", "文件"
        if any(item in text for item in ("搜索", "联网", "知识", "mcp", "工具")):
            return "工具", "工具"
        return "其他", "其他"

    def _matches_query(self, query: str, trigger: str, config: Dict[str, Any]) -> bool:
        if not query:
            return True
        q = str(query or "").strip().lower()
        category, _ = self._categorize(trigger, config)
        haystack = " ".join(
            [
                str(trigger or ""),
                str(config.get("name") or ""),
                str(config.get("description") or ""),
                " ".join(str(item) for item in (config.get("aliases") or [])),
                category,
            ]
        ).lower()
        return q in haystack

    def _is_plugin_allowed(self, plugin_manager, plugin, context: dict) -> bool:
        checker = getattr(plugin_manager, "_is_plugin_allowed", None)
        if callable(checker):
            try:
                result = checker(plugin, context)
                if isinstance(result, tuple):
                    return bool(result[0])
                return bool(result)
            except Exception:
                return True
        return True

    async def run(self, args, ctx):
        plugin_manager = None
        if isinstance(ctx, dict):
            plugin_manager = ctx.get("plugin_manager")
            if plugin_manager is None:
                chat_service = ctx.get("chat_service")
                plugin_manager = getattr(chat_service, "plugin_manager", None)
        if plugin_manager is None:
            return "帮助系统未就绪。"

        settings = getattr(self, "settings", {}) or {}
        show_disabled = self._to_bool(
            self._read_setting(settings, "show_disabled", False)
        )
        query = self._parse_query(args)

        if query:
            lines: List[str] = [f"📚 与“{query}”相关的 QQ 功能："]
        else:
            lines = [
                "📚 当前 QQ 常用功能：",
                "- /帮助：查看这份功能说明",
                "- /帮助 生图：查看画图相关功能",
                "- /帮助 提醒：查看提醒/日报相关功能",
                "- /帮助 文件：查看文件/截图相关功能",
                "",
                "🧩 已加载的 QQ 指令插件：",
            ]

        grouped: Dict[str, List[str]] = {
            "生图": [],
            "提醒": [],
            "文件": [],
            "工具": [],
            "其他": [],
        }

        for trigger, config in plugin_manager.plugin_configs.items():
            if str(config.get("type") or "") != "direct":
                continue
            if (not show_disabled) and trigger in getattr(
                plugin_manager, "disabled_plugins", set()
            ):
                continue
            access = config.get("access_control") or {}
            if not bool(access.get("allow_remote_qq", True)):
                continue

            plugin = getattr(plugin_manager, "plugins", {}).get(trigger)
            if plugin is not None and not self._is_plugin_allowed(
                plugin_manager, plugin, ctx
            ):
                continue

            if not self._matches_query(query, trigger, config):
                continue

            category, _ = self._categorize(trigger, config)
            grouped.setdefault(category, []).append(
                self._format_direct_plugin(trigger, config)
            )

        has_result = False
        for title in ("生图", "提醒", "文件", "工具", "其他"):
            items = grouped.get(title) or []
            if not items:
                continue
            has_result = True
            lines.append(f"【{title}】")
            lines.extend(items)

        if not has_result:
            return (
                f"没有找到和“{query}”相关、且当前你有权限使用的功能。\n"
                f"你可以试试：/帮助 生图、/帮助 提醒、/帮助 文件、/帮助 qq生图"
            )

        lines.extend(
            [
                "",
                "💡 示例：",
                "- /画图 绘制一个丰川祥子在雨中的街道上漫步的图片",
                "- /提醒 每周1到周5 17:20 提醒我打卡",
                "- /cd /qqsave",
            ]
        )
        return "\n".join(lines)
