from __future__ import annotations

import asyncio
import inspect
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from services.info_sources.models import InfoSourceResult
from services.info_sources.providers.alapi import AlapiProvider

Fetcher = Callable[..., Any]

DEFAULT_DAILY_FALLBACKS = {
    "today_anime": [],
    "bili_hot": [],
    "hitokoto": {"hitokoto": "", "from": ""},
    "moyu": [],
    "world_news": [],
    "it_news": [],
}

DAILY_CAPABILITY_ALIASES = {
    "world_news": ("world_news", "zaobao"),
    "moyu": ("moyu", "holiday"),
}

NUMBER_PREFIX_PATTERN = re.compile(r"^\d+[\.\、\s]*")


class InfoSourceService:
    def __init__(
        self,
        token_getter: Callable[[], str],
        alapi_provider: Optional[AlapiProvider] = None,
        providers: Optional[Iterable[AlapiProvider]] = None,
        builtin_fetchers: Optional[Dict[str, Fetcher]] = None,
        endpoint_dir: str | Path | None = None,
        source_root: str | Path | None = None,
        daily_fallbacks: Optional[Dict[str, Any]] = None,
    ):
        self.token_getter = token_getter
        self.alapi_provider = alapi_provider
        if self.alapi_provider is None and endpoint_dir is not None:
            self.alapi_provider = AlapiProvider(endpoint_dir, token_getter)
        self.providers = list(providers or [])
        if self.alapi_provider is not None and self.alapi_provider not in self.providers:
            self.providers.insert(0, self.alapi_provider)
        if source_root is not None:
            self.providers.extend(self._load_providers_from_root(source_root))
        if self.alapi_provider is None:
            for provider in self.providers:
                if getattr(provider, "name", "") == "alapi":
                    self.alapi_provider = provider
                    break
        self.builtin_fetchers = dict(builtin_fetchers or {})
        self.daily_fallbacks = {
            **DEFAULT_DAILY_FALLBACKS,
            **dict(daily_fallbacks or {}),
        }

    def list_capabilities(self) -> list[str]:
        capabilities = set(self.builtin_fetchers)
        for provider in self.providers:
            capabilities.update(provider.list_capabilities())
        return sorted(capabilities)

    async def fetch(self, capability: str, **params: Any) -> InfoSourceResult:
        capability = str(capability or "").strip()
        for provider in self.providers:
            if provider.has_capability(capability):
                return await provider.fetch(capability, **params)
        fetcher = self.builtin_fetchers.get(capability)
        if fetcher is None:
            return InfoSourceResult(
                ok=False,
                capability=capability,
                error=f"unknown info source capability: {capability}",
            )
        try:
            data = await self._call_fetcher(fetcher, **params)
        except Exception as exc:
            return InfoSourceResult(ok=False, capability=capability, error=str(exc))
        return InfoSourceResult(
            ok=data is not None,
            capability=capability,
            provider="builtin",
            data=data,
            summary=self._summarize_data(capability, data),
        )

    def _load_providers_from_root(self, source_root: str | Path) -> list[AlapiProvider]:
        root = Path(source_root)
        providers: list[AlapiProvider] = []
        if not root.exists():
            return providers
        for provider_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            provider_id = provider_dir.name
            base_url = "https://v3.alapi.cn" if provider_id == "alapi" else ""
            token_param = "token"
            provider_config = provider_dir / "provider.json"
            if provider_config.exists():
                try:
                    import json

                    data = json.loads(provider_config.read_text(encoding="utf-8-sig"))
                except Exception:
                    data = {}
                if isinstance(data, dict):
                    base_url = str(data.get("base_url") or base_url).strip()
                    token_param = str(data.get("token_param") or token_param).strip()
            if not base_url:
                continue
            providers.append(
                AlapiProvider(
                    endpoint_dir=provider_dir,
                    token_getter=self.token_getter,
                    base_url=base_url,
                    name=provider_id,
                    token_param=token_param,
                )
            )
        return providers

    async def fetch_daily_bundle(
        self,
        max_anime_count: int,
        max_news_count: int,
        max_hotword_count: int,
        max_holiday_count: int,
    ) -> Dict[str, Any]:
        specs = [
            (
                "today_anime",
                {"limit": max_anime_count, "max_count": max_anime_count},
                self._slice_fallback("today_anime", max_anime_count),
            ),
            (
                "bili_hot",
                {"limit": max_hotword_count, "max_count": max_hotword_count},
                self._slice_fallback("bili_hot", max_hotword_count),
            ),
            ("hitokoto", {}, self.daily_fallbacks["hitokoto"]),
            (
                "moyu",
                {"limit": max_holiday_count, "max_count": max_holiday_count},
                self._slice_fallback("moyu", max_holiday_count),
            ),
            (
                "world_news",
                {"limit": max_news_count, "max_count": max_news_count},
                self._slice_fallback("world_news", max_news_count),
            ),
            (
                "it_news",
                {"limit": max_news_count, "max_count": max_news_count},
                self._slice_fallback("it_news", max_news_count),
            ),
        ]
        results = await self._fetch_with_retry(specs)
        return {
            "anime_list": results[0],
            "bili_hotwords": results[1],
            "hitokoto_data": results[2],
            "moyu_list": results[3],
            "world_news": results[4],
            "it_news": results[5],
        }

    async def _fetch_with_retry(self, specs: Iterable[tuple[str, Dict[str, Any], Any]]) -> list[Any]:
        spec_list = list(specs)
        best_results: list[Any] = [None for _ in spec_list]
        pending = list(range(len(spec_list)))
        for _attempt in range(2):
            round_results = await asyncio.gather(
                *[
                    self._fetch_daily_source(spec_list[idx][0], spec_list[idx][1])
                    for idx in pending
                ]
            )
            next_pending = []
            for pos, result in enumerate(round_results):
                source_idx = pending[pos]
                if self._has_value(result):
                    best_results[source_idx] = result
                else:
                    next_pending.append(source_idx)
            pending = next_pending
            if not pending:
                break
        final = []
        for idx, result in enumerate(best_results):
            fallback = spec_list[idx][2]
            final.append(result if self._has_value(result) else fallback)
        return final

    async def _fetch_daily_source(self, capability: str, params: Dict[str, Any]) -> Any:
        for source_capability in self._daily_capability_candidates(capability):
            if self.alapi_provider is None:
                break
            if source_capability not in self.alapi_provider.list_capabilities():
                continue
            result = await self.alapi_provider.fetch(
                source_capability, **self._external_daily_params(params)
            )
            if result.ok:
                return self._normalize_daily_source(capability, result.data, params)

        fetcher = self.builtin_fetchers.get(capability)
        if fetcher is not None:
            try:
                return await self._call_fetcher(fetcher, **params)
            except Exception:
                return None
        result = await self.fetch(capability, **params)
        return result.data if result.ok else None

    def _daily_capability_candidates(self, capability: str) -> tuple[str, ...]:
        capability = str(capability or "").strip()
        return DAILY_CAPABILITY_ALIASES.get(capability, (capability,))

    def _external_daily_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in params.items()
            if key not in {"limit", "max_count"}
        }

    def _normalize_daily_source(
        self, capability: str, data: Any, params: Dict[str, Any]
    ) -> Any:
        if capability == "world_news":
            return self._normalize_world_news(data, self._param_limit(params, 5))
        if capability == "moyu":
            return self._normalize_holidays(data, self._param_limit(params, 3))
        if capability == "hitokoto":
            return self._normalize_hitokoto(data)
        return data

    def _param_limit(self, params: Dict[str, Any], default: int) -> int:
        for key in ("limit", "max_count"):
            try:
                value = int(params.get(key))
            except Exception:
                continue
            if value > 0:
                return value
        return default

    def _normalize_world_news(self, data: Any, max_count: int) -> list[str]:
        news_data = data.get("news") if isinstance(data, dict) else data
        if not isinstance(news_data, list):
            return []
        result: list[str] = []
        for item in news_data:
            if not isinstance(item, str):
                continue
            cleaned = NUMBER_PREFIX_PATTERN.sub("", item.strip())
            if cleaned:
                result.append(cleaned)
            if len(result) >= max_count:
                break
        return result

    def _normalize_hitokoto(self, data: Any) -> Dict[str, str]:
        if not isinstance(data, dict):
            return {}
        from_value = data.get("from") or data.get("from_who") or ""
        from_text = str(from_value or "").strip()
        if not from_text or from_text == "网络":
            from_text = "佚名"
        return {
            "hitokoto": str(data.get("hitokoto") or "").strip(),
            "from": from_text,
        }

    def _normalize_holidays(self, data: Any, max_count: int) -> list[Dict[str, Any]]:
        if not isinstance(data, list):
            return []
        today = date.today()
        processed: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("is_off_day") != 1:
                continue
            date_str = str(item.get("date") or "").strip()
            if not date_str:
                continue
            try:
                holiday_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if holiday_date < today:
                continue
            name = str(item.get("name") or "未知").strip() or "未知"
            days_left = (holiday_date - today).days
            if name in seen:
                for idx, existing in enumerate(processed):
                    if existing["name"] == name and days_left < existing["days_left"]:
                        processed[idx] = {
                            "name": name,
                            "days_left": days_left,
                            "date": date_str,
                        }
                        break
            else:
                seen.add(name)
                processed.append({"name": name, "days_left": days_left, "date": date_str})
        processed.sort(key=lambda item: item["days_left"])
        return [
            {"name": item["name"], "days_left": item["days_left"]}
            for item in processed[:max_count]
        ]

    async def _call_fetcher(self, fetcher: Fetcher, **params: Any) -> Any:
        kwargs = self._filter_kwargs(fetcher, params)
        value = fetcher(**kwargs)
        if inspect.isawaitable(value):
            return await value
        return value

    def _filter_kwargs(self, fetcher: Fetcher, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            signature = inspect.signature(fetcher)
        except (TypeError, ValueError):
            return params
        if any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        ):
            return params
        return {
            key: value
            for key, value in params.items()
            if key in signature.parameters
        }

    def _has_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, (list, tuple, dict, set)) and not value:
            return False
        return True

    def _slice_fallback(self, key: str, limit: int) -> Any:
        value = self.daily_fallbacks.get(key)
        if isinstance(value, list):
            return value[:limit]
        return value

    def _summarize_data(self, capability: str, data: Any) -> str:
        if isinstance(data, list):
            return f"{capability}: {len(data)} items"
        if isinstance(data, dict):
            return f"{capability}: {len(data)} fields"
        return str(data or "")
