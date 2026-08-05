import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from services.capability_manager import ToolCapabilityManager


@dataclass
class ToolRouteResult:
    need_tools: bool
    tool_triggers: List[str]
    reason: str
    capability_id: str = ""
    capability_args: Optional[Dict[str, Any]] = None
    capability_score: float = 0.0
    capability_match_reason: str = ""
    # Low-confidence capability matches for optional gatekeeper resolution.
    capability_ambiguous: bool = False
    capability_candidates: Optional[List[Dict[str, Any]]] = None


class ToolRouter:
    """轻量级工具路由器：规则优先；模糊 capability 交给上层 gatekeeper 裁决。"""

    _WORKSPACE_READ_HINTS = [
        "读",
        "读一下",
        "读取",
        "看看",
        "查看",
        "打开",
        "搜",
        "搜索",
        "查文件",
        "读代码",
        "看代码",
    ]
    _WEB_READ_HINTS = [
        "解析链接",
        "读网页",
        "网页内容",
        "打开链接",
        "看看链接",
        "总结网页",
        "解析网页",
    ]
    _CODE_AGENT_ACTION_HINTS = [
        "分析",
        "检查",
        "查看",
        "看一下",
        "看看",
        "读一下",
        "审查",
        "排查",
        "修复",
        "修改",
        "重构",
        "改一下",
        "处理",
        "接手",
        "帮我",
        "画",
        "画图",
        "绘图",
        "生图",
        "生成图片",
    ]
    _USER_FILE_PLACES = [
        "下载目录",
        "文档目录",
        "桌面",
        "documents",
        "downloads",
        "desktop",
    ]
    _USER_FILE_ACTIONS = [
        "看看",
        "查看",
        "读取",
        "列出",
        "整理",
        "移动",
        "写入",
        "保存",
    ]
    _MOEGIRL_HINTS = [
        "萌百",
        "萌娘百科",
        "moegirl",
        "角色",
        "作品",
        "设定",
        "什么梗",
        "哪个作品",
        "哪部作品",
        "是谁",
        "是什么",
    ]

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
        delegate_map: Optional[Dict[str, object]] = None,
        *,
        enable_intent_keywords: bool = True,
        capability_manager: Optional[ToolCapabilityManager] = None,
        enable_capability_routes: bool = True,
    ):
        self.react_map = react_map
        self.delegate_map = delegate_map or {}
        self.direct_map = direct_map
        self.enable_intent_keywords = enable_intent_keywords
        self.enable_capability_routes = enable_capability_routes
        self.capability_manager = capability_manager or ToolCapabilityManager.from_plugin_maps(
            react_map=self.react_map,
            direct_map=self.direct_map,
            delegate_map=self.delegate_map,
        )

        self.intent_keywords = self._build_intent_keywords_from_plugins()

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

    def _plugin_has_capabilities(self, trigger: str) -> bool:
        plugin = (
            self.react_map.get(trigger)
            or self.delegate_map.get(trigger)
            or self.direct_map.get(trigger)
        )
        return callable(getattr(plugin, "get_capabilities", None))

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
            rows = [
                item.strip().lower()
                for line in text.splitlines()
                for item in line.split(",")
                if item.strip()
            ]
        else:
            rows = []
        return list(dict.fromkeys(rows))

    def _get_mcp_domain_route_config(self) -> Optional[Dict[str, Any]]:
        plugin = self.react_map.get("mcp_tools") or self.delegate_map.get("mcp_tools")
        if plugin is None:
            return None

        settings = getattr(plugin, "settings", None)
        if not isinstance(settings, dict):
            settings = {}

        enabled_raw = self._read_setting_value(settings, "intent_route_enabled", True)
        enabled = bool(enabled_raw)

        brand_keywords = self._normalize_keywords(
            self._read_setting_value(
                settings, "intent_route_brand_keywords", self._DEFAULT_MCP_DOMAIN_BRANDS
            )
        )
        action_keywords = self._normalize_keywords(
            self._read_setting_value(
                settings,
                "intent_route_action_keywords",
                self._DEFAULT_MCP_DOMAIN_ACTIONS,
            )
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

    @staticmethod
    def _looks_like_workspace_path(text: str) -> bool:
        if not text:
            return False
        markers = [
            "/",
            "\\",
            ".py",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".txt",
            ".toml",
            ".ini",
            ".js",
            ".ts",
        ]
        return any(marker in text for marker in markers)

    def _should_route_to_workspace_read(self, text: str) -> bool:
        if (
            "workspace_ops" not in self.delegate_map
            and "workspace_ops" not in self.react_map
        ):
            return False
        if not self._looks_like_workspace_path(text):
            return False
        return any(hint in text for hint in self._WORKSPACE_READ_HINTS)

    def _should_route_to_code_agent(self, text: str) -> bool:
        if "code_agent" not in self.direct_map:
            return False
        has_provider = bool(
            re.search(r"codex|claude\s*code|(^|[^\w])cc([^\w]|$)", text, re.IGNORECASE)
        )
        return has_provider and any(hint in text for hint in self._CODE_AGENT_ACTION_HINTS)

    def _looks_like_image_generation(self, text: str) -> bool:
        return bool(
            re.search(
                r"(画图|画画|画一张|绘图|生图|生成图|生成图片|图片生成|发图)",
                str(text or ""),
                flags=re.IGNORECASE,
            )
        )

    def _should_route_to_user_files(self, text: str) -> bool:
        if "user_files" not in self.direct_map:
            return False
        has_place = any(hint in text for hint in self._USER_FILE_PLACES)
        has_action = any(hint in text for hint in self._USER_FILE_ACTIONS)
        has_file_name = bool(
            re.search(r"\.(txt|md|json|py|log|csv|zip|png|jpe?g|pdf|docx?)\b", text)
        )
        return has_place and (has_action or has_file_name)

    def _should_route_to_web_reader(self, text: str) -> bool:
        if "web_reader" not in self.delegate_map and "web_reader" not in self.react_map:
            return False
        has_url = bool(re.search(r"https?://[^\s]+", text, flags=re.IGNORECASE))
        if not has_url:
            return False
        return any(hint in text for hint in self._WEB_READ_HINTS)

    def _should_route_to_moegirl(self, text: str) -> bool:
        if (
            "moegirl_wiki" not in self.delegate_map
            and "moegirl_wiki" not in self.react_map
        ):
            return False
        has_explicit_hint = any(
            hint in text for hint in ["萌百", "萌娘百科", "moegirl", "查萌百"]
        )
        if has_explicit_hint:
            return True
        has_question_form = any(
            hint in text
            for hint in ["是谁", "是什么", "什么梗", "哪个作品", "哪部作品"]
        )
        has_acg_context = any(
            hint in text for hint in ["角色", "作品", "设定", "萌娘", "动漫", "二次元"]
        )
        return has_question_form and has_acg_context

    def _build_intent_keywords_from_plugins(self) -> Dict[str, List[str]]:
        keywords: Dict[str, List[str]] = {}

        combined_map: Dict[str, object] = {}
        combined_map.update(self.react_map)
        combined_map.update(self.delegate_map)

        for trigger, plugin in combined_map.items():
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
                kw_list.extend(
                    [
                        "昨天",
                        "前天",
                        "总结",
                        "回顾",
                        "日记",
                        "复盘",
                        "干了什么",
                        "做了什么",
                    ]
                )

            if "task" in trigger or "schedule" in trigger:
                kw_list.extend(["任务", "待办", "日程"])

            keywords[trigger] = list(dict.fromkeys(kw_list))

        return keywords

    @staticmethod
    def _serialize_capability_candidates(candidates: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in candidates or []:
            if not bool(getattr(item, "available", True)):
                continue
            capability_id = str(getattr(item, "capability_id", "") or "").strip()
            plugin = str(getattr(item, "plugin", "") or "").strip()
            if not capability_id or not plugin:
                continue
            try:
                score = float(getattr(item, "score", 0.0) or 0.0)
            except Exception:
                score = 0.0
            args = getattr(item, "args", None)
            rows.append(
                {
                    "capability_id": capability_id,
                    "plugin": plugin,
                    "score": score,
                    "args": dict(args or {}) if isinstance(args, dict) else {},
                    "reason": str(getattr(item, "reason", "") or ""),
                }
            )
        rows.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
        return rows

    def route(
        self, user_text: str, last_tool_triggers: Optional[List[str]] = None
    ) -> ToolRouteResult:
        if not user_text:
            return ToolRouteResult(False, [], "empty_input")

        text = user_text.strip().lower()
        matched: Set[str] = set()
        combined_map: Dict[str, object] = {}
        combined_map.update(self.react_map)
        combined_map.update(self.delegate_map)
        ambiguous_candidates: List[Dict[str, Any]] = []

        if self.enable_capability_routes and self.capability_manager is not None:
            capability_result = self.capability_manager.match(user_text, {})
            if capability_result.selected is not None:
                selected = capability_result.selected
                if (
                    selected.plugin in self.react_map
                    or selected.plugin in self.delegate_map
                    or selected.plugin in self.direct_map
                ):
                    return ToolRouteResult(
                        True,
                        [selected.plugin],
                        f"capability:{selected.capability_id}",
                        capability_id=selected.capability_id,
                        capability_args=dict(selected.args or {}),
                        capability_score=float(selected.score or 0.0),
                        capability_match_reason=str(selected.reason or ""),
                    )
            if capability_result.reason == "unavailable" and capability_result.candidates:
                candidate = capability_result.candidates[0]
                return ToolRouteResult(
                    False,
                    [],
                    f"capability_unavailable:{candidate.capability_id}",
                    capability_id=candidate.capability_id,
                    capability_args=dict(candidate.args or {}),
                    capability_score=float(candidate.score or 0.0),
                    capability_match_reason=str(
                        candidate.unavailable_reason or candidate.reason or ""
                    ),
                )
            if capability_result.ambiguous and capability_result.candidates:
                ambiguous_candidates = self._serialize_capability_candidates(
                    list(capability_result.candidates)
                )

        if not self._plugin_has_capabilities("mcp_tools") and self._should_route_to_mcp_domain(text):
            return ToolRouteResult(True, ["mcp_tools"], "mcp_domain_preferred")

        if not self._plugin_has_capabilities("moegirl_wiki") and self._should_route_to_moegirl(text):
            return ToolRouteResult(True, ["moegirl_wiki"], "moegirl_preferred")

        if not self._plugin_has_capabilities("web_reader") and self._should_route_to_web_reader(text):
            return ToolRouteResult(True, ["web_reader"], "web_reader_preferred")

        if not self._plugin_has_capabilities("user_files") and self._should_route_to_user_files(text):
            return ToolRouteResult(True, ["user_files"], "user_files_preferred")

        if not self._plugin_has_capabilities("workspace_ops") and self._should_route_to_workspace_read(text):
            return ToolRouteResult(True, ["workspace_ops"], "workspace_read_preferred")

        if not self._plugin_has_capabilities("code_agent") and self._should_route_to_code_agent(text):
            return ToolRouteResult(True, ["code_agent"], "code_agent_preferred")

        if last_tool_triggers and any(k in text for k in self.followup_keywords):
            return ToolRouteResult(
                True, list(dict.fromkeys(last_tool_triggers)), "followup_last_tool"
            )

        for trigger in combined_map:
            if trigger.lower() in text:
                matched.add(trigger)
        if matched:
            return ToolRouteResult(True, sorted(matched), "react_trigger_matched")

        if self.enable_intent_keywords:
            for trigger, kws in self.intent_keywords.items():
                for kw in kws:
                    if kw.lower() in text and trigger in combined_map:
                        matched.add(trigger)

        if matched:
            return ToolRouteResult(True, sorted(matched), "intent_keyword_matched")

        if ambiguous_candidates:
            return ToolRouteResult(
                False,
                [],
                "capability_ambiguous",
                capability_ambiguous=True,
                capability_candidates=ambiguous_candidates,
                capability_match_reason="low_confidence_or_multi_candidate",
            )

        return ToolRouteResult(False, [], "no_tool_intent")
