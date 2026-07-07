from __future__ import annotations

import re
from typing import Any, Dict, Optional

from plugins.plugin_utils import handle_plugin_errors
from services.capability_manager import ToolCapability, ToolCapabilityMatch

from .client import MoegirlApiClient
from .query_service import MoegirlQueryService


class Plugin:
    type = "delegate"

    def get_capabilities(self):
        return [
            ToolCapability(
                id="moegirl.lookup",
                plugin="moegirl_wiki",
                trigger_mode="natural",
                match=self._match_lookup,
                description="Look up an explicit Moegirl/Moegirl Wiki query.",
                examples=["查萌百 高松灯", "萌娘百科 初音未来"],
            )
        ]

    def _match_lookup(
        self, text: str, ctx: Dict[str, Any]
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if not raw:
            return None
        lowered = raw.lower()
        explicit_hints = ("萌百", "萌娘百科", "moegirl", "查萌百")
        if not any(hint in lowered for hint in explicit_hints):
            return None
        query = re.sub(
            r"^(?:帮我|请|麻烦)?(?:查一下|查询|查|看看|搜索)?(?:萌百|萌娘百科|moegirl)\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        return ToolCapabilityMatch(
            capability_id="moegirl.lookup",
            plugin="moegirl_wiki",
            score=0.9,
            args={"query": query} if query else None,
            raw_text=raw,
            reason="moegirl_explicit_lookup",
        )

    @handle_plugin_errors("萌娘百科查询")
    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        if not bool((ctx or {}).get("delegate_mode", False)):
            return "moegirl_wiki 现在仅允许通过副脑委托执行。"
        query = str(args or "").strip()
        if not query:
            return "请提供要查询的萌娘百科词条或问题。"

        settings = getattr(self, "settings", {}) or {}
        timeout_seconds = self._to_int(settings.get("timeout_seconds", 10), 10, 3, 60)
        cache_ttl_seconds = self._to_int(
            settings.get("cache_ttl_seconds", 300), 300, 0, 3600
        )
        max_candidates = self._to_int(settings.get("max_candidates", 5), 5, 1, 10)
        prefer_exact_title = self._to_bool(settings.get("prefer_exact_title", True))
        prefer_generator_search = self._to_bool(
            settings.get("prefer_generator_search", True)
        )
        cookie_string = self._to_text(settings.get("cookie_string", ""))

        client = MoegirlApiClient(
            timeout_seconds=timeout_seconds,
            cookie_string=cookie_string,
        )
        service = MoegirlQueryService(
            client=client,
            prefer_exact_title=prefer_exact_title,
            cache_ttl_seconds=cache_ttl_seconds,
            enable_generator_search=prefer_generator_search,
        )
        result = await service.lookup(
            query, mode="summary", max_candidates=max_candidates
        )
        return self._format_result(result)

    def _format_result(self, result: Any) -> str:
        meta = f"[moegirl_meta] status={getattr(result, 'status', 'error')}; candidates={len(getattr(result, 'candidates', []) or [])}; has_page={1 if getattr(result, 'page', None) is not None else 0}"
        if result.status == "ok" and result.page is not None:
            lines = [
                meta,
                f"词条：{result.page.title}",
                f"简介：{result.page.summary}",
                f"链接：{result.page.url}",
            ]
            if result.page.categories:
                lines.append("分类：" + "、".join(result.page.categories[:3]))
            if result.page.thumbnail_url:
                lines.append(f"缩略图：{result.page.thumbnail_url}")
            if result.candidates:
                lines.append("")
                lines.append("相关语境：")
                for index, item in enumerate(result.candidates, start=1):
                    lines.append(f"{index}. {item.title}")
                    if item.description:
                        lines.append(f"简介：{item.description}")
                    lines.append(f"链接：{item.url}")
            return "\n".join(lines)

        if result.status == "ambiguous":
            lines = [meta, result.message or "以下词条可能相关："]
            for index, item in enumerate(result.candidates, start=1):
                lines.append(f"{index}. {item.title}")
                if item.description:
                    lines.append(f"简介：{item.description}")
                lines.append(f"链接：{item.url}")
            return "\n".join(lines)

        if result.status == "not_found":
            return meta + "\n" + (result.message or "未找到明显匹配词条。")
        return meta + "\n" + (result.message or "萌娘百科查询暂时不可用。")

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, dict):
            value = value.get("default", False)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _to_int(self, value: Any, default: int, min_val: int, max_val: int) -> int:
        if isinstance(value, dict):
            value = value.get("default", default)
        try:
            num = int(value)
        except Exception:
            num = default
        return max(min_val, min(max_val, num))

    def _to_text(self, value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("default", "")
        return str(value or "").strip()
