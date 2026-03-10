from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set


@dataclass
class ToolRouteResult:
    need_tools: bool
    tool_triggers: List[str]
    reason: str


class ToolRouter:
    """轻量级工具路由器（不依赖 LLM）。"""

    _DEFAULT_MCP_DOMAIN_BRANDS = [
        "麦当劳",
        "mcd",
        "mcdonald",
        "麦乐送",
    ]
    _DEFAULT_MCP_DOMAIN_ACTIONS = [
        "查",
        "查一下",
        "查询",
        "看",
        "看一下",
        "领",
        "领取",
        "获取",
        "优惠",
        "优惠券",
        "会员券",
        "券",
        "折扣",
        "活动",
    ]
    _DEFAULT_MCP_EXPLICIT_WEB_SEARCH = [
        "联网",
        "上网",
        "网页",
        "百度",
        "google",
        "bing",
        "搜索",
    ]

    def __init__(
        self,
        react_map: Dict[str, object],
        direct_map: Dict[str, object],
        *,
        enable_intent_keywords: bool = True,
    ):
        self.react_map = react_map
        self.direct_map = direct_map
        self.enable_intent_keywords = enable_intent_keywords

        self.intent_keywords = self._build_intent_keywords_from_plugins()
        print(f"[Router] intent keywords loaded: {list(self.intent_keywords.keys())}")

        self.followup_keywords = [
            "继续",
            "再来一次",
            "同上",
            "还是那个",
            "重复",
            "再查",
            "再搜",
            "再来一个",
            "照刚才的",
        ]

    @staticmethod
    def _read_setting_value(settings: Dict[str, Any], key: str, default: Any) -> Any:
        raw = settings.get(key, default)
        if isinstance(raw, dict):
            if "default" in raw:
                return raw.get("default")
            if "value" in raw:
                return raw.get("value")
        return raw

    @staticmethod
    def _normalize_keywords(value: Any) -> List[str]:
        if isinstance(value, (list, tuple, set)):
            rows = [str(item).strip().lower() for item in value if str(item).strip()]
        elif isinstance(value, str):
            text = value.replace("，", ",").replace("、", ",").replace("|", ",")
            rows = [item.strip().lower() for line in text.splitlines() for item in line.split(",") if item.strip()]
        else:
            rows = []
        return list(dict.fromkeys(rows))

    def _get_mcp_domain_route_config(self) -> Optional[Dict[str, Any]]:
        plugin = self.react_map.get("mcp_tools")
        if plugin is None:
            return None

        settings = getattr(plugin, "settings", None)
        if not isinstance(settings, dict):
            settings = {}

        enabled_raw = self._read_setting_value(settings, "intent_route_enabled", True)
        enabled = bool(enabled_raw)

        brand_keywords = self._normalize_keywords(
            self._read_setting_value(settings, "intent_route_brand_keywords", self._DEFAULT_MCP_DOMAIN_BRANDS)
        )
        action_keywords = self._normalize_keywords(
            self._read_setting_value(settings, "intent_route_action_keywords", self._DEFAULT_MCP_DOMAIN_ACTIONS)
        )
        web_search_keywords = self._normalize_keywords(
            self._read_setting_value(
                settings,
                "intent_route_web_search_override_keywords",
                self._DEFAULT_MCP_EXPLICIT_WEB_SEARCH,
            )
        )

        return {
            "enabled": enabled,
            "brand_keywords": brand_keywords,
            "action_keywords": action_keywords,
            "web_search_keywords": web_search_keywords,
        }

    def _should_route_to_mcp_domain(self, text: str) -> bool:
        route_cfg = self._get_mcp_domain_route_config()
        if not route_cfg or not route_cfg["enabled"]:
            return False

        brand_keywords = route_cfg["brand_keywords"]
        action_keywords = route_cfg["action_keywords"]
        web_search_keywords = route_cfg["web_search_keywords"]

        if not brand_keywords or not action_keywords:
            return False

        has_brand = any(k in text for k in brand_keywords)
        has_action = any(k in text for k in action_keywords)
        wants_web_search = any(k in text for k in web_search_keywords)
        return has_brand and has_action and not wants_web_search

    def _build_intent_keywords_from_plugins(self) -> Dict[str, List[str]]:
        keywords: Dict[str, List[str]] = {}

        for trigger, plugin in self.react_map.items():
            aliases = getattr(plugin, "aliases", [])

            if not aliases:
                kw_list = [trigger]
            else:
                kw_list = list(aliases)
                if trigger not in kw_list:
                    kw_list.append(trigger)

            name = getattr(plugin, "name", "")
            if name and name not in kw_list:
                kw_list.append(name)

            is_diary = ("diary" in trigger) or ("history" in trigger)
            if not is_diary:
                is_diary = any(("diary" in a or "history" in a) for a in kw_list)
            if is_diary:
                kw_list.extend(["昨天", "前天", "总结", "回顾", "日记", "复盘", "干了什么", "做了什么"])

            if "task" in trigger or "schedule" in trigger:
                kw_list.extend(["任务", "待办", "日程"])

            keywords[trigger] = list(dict.fromkeys(kw_list))

        return keywords

    def route(self, user_text: str, last_tool_triggers: Optional[List[str]] = None) -> ToolRouteResult:
        if not user_text:
            return ToolRouteResult(False, [], "empty_input")

        text = user_text.strip().lower()
        matched: Set[str] = set()

        if self._should_route_to_mcp_domain(text):
            return ToolRouteResult(True, ["mcp_tools"], "mcp_domain_preferred")

        if last_tool_triggers and any(k in text for k in self.followup_keywords):
            return ToolRouteResult(True, list(dict.fromkeys(last_tool_triggers)), "followup_last_tool")

        for trigger in self.direct_map:
            if trigger.lower() in text:
                matched.add(trigger)
        if matched:
            return ToolRouteResult(True, sorted(matched), "direct_plugin_matched")

        for trigger in self.react_map:
            if trigger.lower() in text:
                matched.add(trigger)
        if matched:
            return ToolRouteResult(True, sorted(matched), "react_trigger_matched")

        if self.enable_intent_keywords:
            for trigger, kws in self.intent_keywords.items():
                for kw in kws:
                    if kw.lower() in text and trigger in self.react_map:
                        matched.add(trigger)

        if matched:
            return ToolRouteResult(True, sorted(matched), "intent_keyword_matched")

        return ToolRouteResult(False, [], "no_tool_intent")
