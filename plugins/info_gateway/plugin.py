from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from modules.plugin_secret_store import PluginSecretStore
from services.capability_manager import ToolCapability, ToolCapabilityMatch
from services.info_sources import InfoSourceService

ALAPI_SECRET_PLUGIN_TRIGGER = "magic_daily"
ALAPI_SECRET_KEY = "api_token"


class Plugin:
    name = "Info Gateway"
    type = "react"
    aliases = ["/api", "/info", "/信息源"]
    direct_command_aliases = ["/api", "/info", "/信息源"]
    allow_natural_language_direct = False
    description = "Expose shared information sources."
    example_arg = "weather_7d city=上海"

    def __init__(
        self,
        service_getter: Optional[Callable[[Dict[str, Any]], InfoSourceService]] = None,
        alapi_secret_store: Optional[Any] = None,
    ):
        self._service_getter = service_getter
        self._alapi_secret_store = alapi_secret_store

    def get_capabilities(self):
        return [
            ToolCapability(
                id="info.weather_7d",
                plugin="info_gateway",
                trigger_mode="natural",
                match=self._match_weather_7d,
                check_available=self._check_weather_available,
                description="查询未来几天天气",
                examples=["上海这周天气怎么样", "上海未来7天天气"],
            ),
            ToolCapability(
                id="info.weather_now",
                plugin="info_gateway",
                trigger_mode="natural",
                match=self._match_weather_now,
                check_available=self._check_weather_available,
                description="查询实时天气：气温、温度、几度、会不会下雨、要不要带伞",
                examples=["上海今天的天气怎么样", "现在多少度", "外面温度怎么样"],
            ),
        ]

    def _check_weather_available(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        root = Path(__file__).resolve().parents[2]
        token = self._read_alapi_token(root, ctx or {})
        if token:
            return {"available": True}
        return {
            "available": False,
            "reason": "missing_secret: alapi.api_token",
        }

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        service = self._get_service(ctx or {})
        text = self._strip_command_alias(str(args or "").strip())
        if not text or text.lower() in {"help", "status", "list"}:
            return self._format_capabilities(service)

        weather_request = self._parse_weather_request(text, service)
        if weather_request:
            capability, params = weather_request
            result = await service.fetch(capability, **params)
            return self._format_result(result)

        parts = text.split(None, 1)
        capability = parts[0].strip()
        params = self._parse_params(parts[1] if len(parts) > 1 else "")
        result = await service.fetch(capability, **params)
        return self._format_result(result)

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        return any(raw.startswith(prefix) for prefix in self.direct_command_aliases)

    def should_handle_natural(self, text: str, context: Dict[str, Any]) -> bool:
        raw = self._strip_command_alias(str(text or "").strip())
        if not raw:
            return False
        try:
            service = self._get_service(context or {})
            return self._parse_weather_request(raw, service) is not None
        except Exception:
            return self._looks_like_weather_query(raw)

    def _format_result(self, result) -> str:
        if not result.ok:
            return f"info_gateway failed: {result.error}"
        if result.summary:
            return result.summary
        if result.data is None:
            return f"{result.capability or '信息源'} 没有返回可用数据"
        return json.dumps(result.data, ensure_ascii=False, indent=2)

    def _get_service(self, ctx: Dict[str, Any]) -> InfoSourceService:
        if self._service_getter is not None:
            return self._service_getter(ctx)
        root = Path(__file__).resolve().parents[2]
        return InfoSourceService(
            token_getter=lambda: self._read_alapi_token(root, ctx),
            source_root=root / "data" / "info_sources",
        )

    def _read_alapi_token(self, root: Path, ctx: Optional[Dict[str, Any]] = None) -> str:
        runtime_token = self._read_setting_value(
            getattr(self, "settings", None),
            "api_token",
        )
        if runtime_token:
            return runtime_token
        context_token = self._read_setting_value(
            (ctx or {}).get("settings"),
            "api_token",
        )
        if context_token:
            return context_token
        shared_token = self._read_shared_alapi_token()
        if shared_token:
            return shared_token
        config_path = root / "plugins" / "Isuzu_news" / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return ""
        return self._read_setting_value(config.get("settings"), "api_token")

    def _read_shared_alapi_token(self) -> str:
        store = self._alapi_secret_store
        if store is None:
            try:
                store = PluginSecretStore()
            except Exception:
                return ""
            self._alapi_secret_store = store
        try:
            return str(
                store.get_secret(ALAPI_SECRET_PLUGIN_TRIGGER, ALAPI_SECRET_KEY) or ""
            ).strip()
        except Exception:
            return ""

    def _read_setting_value(self, settings: Any, key: str) -> str:
        if not isinstance(settings, dict):
            return ""
        raw = settings.get(key, "")
        if isinstance(raw, dict):
            raw = raw.get("value") or raw.get("default", "")
        return str(raw or "").strip()

    def _parse_params(self, text: str) -> Dict[str, str]:
        params: Dict[str, str] = {}
        for key, double_quoted, single_quoted, bare in re.findall(
            r"([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|(\S+))",
            str(text or ""),
        ):
            params[key] = next(
                value for value in (double_quoted, single_quoted, bare) if value
            )
        return params

    def _strip_command_alias(self, text: str) -> str:
        raw = str(text or "").strip()
        for alias in ("/api", "/info", "/信息源"):
            if raw.startswith(alias):
                return raw[len(alias) :].strip()
        return raw

    def get_tool_schema(self):
        return {
            "name": "info_gateway",
            "description": "查询实时或未来几天天气。气温、温度、几度、带伞、会不会下雨都用本工具，不要改用搜索。城市未知时不要编造。",
            "parameters": {
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "enum": ["weather_now", "weather_7d"],
                        "description": "weather_now=当前实况（气温/温度/几度/会不会下雨）；weather_7d=未来几天",
                    },
                    "city": {
                        "type": "string",
                        "description": "城市名，例如上海、北京",
                    },
                },
                "required": ["capability", "city"],
            },
        }

    def format_invocation(self, arguments):
        args = dict(arguments or {})
        capability = str(args.get("capability") or "weather_now").strip() or "weather_now"
        city = str(args.get("city") or "").strip()
        if city:
            return f"{capability} city={city}"
        return capability

    def collect_invocation_args(
        self,
        user_text: str,
        capability_id: str = "",
        initial_args: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        args = dict(initial_args or {})
        if not str(args.get("capability") or "").strip():
            if str(capability_id or "").endswith("7d") or self._looks_like_forecast_weather(
                user_text
            ):
                args["capability"] = "weather_7d"
            else:
                args["capability"] = "weather_now"
        if not str(args.get("city") or "").strip():
            city = self._extract_city(user_text)
            if city:
                args["city"] = city
        return args

    def _looks_like_weather_query(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if any(word in raw for word in ("邮件", "日报", "接口", "配置", "图片")):
            return False
        weather_cues = (
            "带伞",
            "雨伞",
            "下雨",
            "降雨",
            "气温",
            "温度",
            "几度",
            "多少度",
            "体感",
            "高温",
            "低温",
            "会不会下雨",
        )
        if any(cue in raw for cue in weather_cues):
            return True
        if "伞" in raw and any(cue in raw for cue in ("出门", "带", "明天", "今天", "要不要")):
            return True
        if "天气" not in raw:
            return False
        mood_hints = (
            "真好",
            "好天气",
            "不错",
            "舒服",
            "宜人",
            "好差",
            "真差",
            "好热",
            "好冷",
            "太热",
            "太冷",
            "好想出去",
        )
        query_hints = (
            "怎么样",
            "如何",
            "怎样",
            "几度",
            "多少度",
            "查询",
            "查一下",
            "预报",
            "实况",
            "吗",
            "呢",
            "？",
            "?",
        )
        if any(hint in raw for hint in mood_hints) and not any(
            hint in raw for hint in query_hints
        ):
            return False
        return True

    def _parse_weather_request(
        self,
        text: str,
        service: InfoSourceService,
    ) -> Optional[tuple[str, Dict[str, str]]]:
        raw = str(text or "").strip()
        if not self._looks_like_weather_query(raw):
            return None
        capabilities = set(service.list_capabilities())
        forecast = self._looks_like_forecast_weather(raw)
        capability = "weather_7d" if forecast else "weather_now"
        if capability not in capabilities:
            fallback = "weather_now" if capability == "weather_7d" else "weather_7d"
            if fallback in capabilities:
                capability = fallback
            else:
                return None
        city = self._extract_city(raw)
        params = {"city": city} if city else {}
        return capability, params

    def _match_weather_7d(
        self, text: str, ctx: Dict[str, Any]
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if not self._looks_like_weather_query(raw):
            return None
        if not self._looks_like_forecast_weather(raw):
            return None
        city = self._extract_city(raw)
        return ToolCapabilityMatch(
            capability_id="info.weather_7d",
            plugin="info_gateway",
            score=0.92,
            args={"city": city} if city else None,
            raw_text=raw,
            reason="forecast_weather_query",
        )

    def _match_weather_now(
        self, text: str, ctx: Dict[str, Any]
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if not self._looks_like_weather_query(raw):
            return None
        if self._looks_like_forecast_weather(raw):
            return None
        city = self._extract_city(raw)
        return ToolCapabilityMatch(
            capability_id="info.weather_now",
            plugin="info_gateway",
            score=0.9,
            args={"city": city} if city else None,
            raw_text=raw,
            reason="current_weather_query",
        )

    def _looks_like_forecast_weather(self, text: str) -> bool:
        return bool(
            re.search(
                r"(?:未来|预报|这周|本周|下周|一周|七天|7\s*天|[2-9]\s*天)",
                str(text or ""),
            )
        )

    def _extract_city(self, text: str) -> str:
        raw = str(text or "").strip()
        cleaned = re.sub(
            r"^(?:请|麻烦)?(?:帮我|给我|替我|请问|我想知道)?(?:查一下|查询一下|看一下|看看|查|查询|看)?",
            "",
            raw,
        )
        cleaned = re.sub(
            r"(?:未来|预报|天气|怎么样|如何|情况|实况|今天|今日|明天|后天|现在|目前|实时|这周|本周|下周|[0-9一二三四五六七八九十]+天|一周|七天|出门|带伞|要不要|下雨|降雨|气温|温度|几度|多少度|体感|高温|低温|雨伞|伞)",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"^(?:一下|下|的)\s*", "", cleaned)
        cleaned = re.sub(r"[，,。？！?！\s]+", " ", cleaned).strip()
        if not cleaned:
            return ""
        return cleaned.split(" ")[0].strip()

    def _format_capabilities(self, service: InfoSourceService) -> str:
        capabilities = service.list_capabilities()
        if not capabilities:
            return "info_gateway: no capabilities configured"
        return "info_gateway capabilities: " + ", ".join(capabilities)
