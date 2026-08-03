from __future__ import annotations

from datetime import datetime, time
from typing import Any, Awaitable, Callable, Dict, Optional
from zoneinfo import ZoneInfo

try:
    import aiohttp
except Exception:
    aiohttp = None

from services.info_sources.models import InfoSourceResult

RequestFunc = Callable[[str, Dict[str, Any], float], Awaitable[Dict[str, Any]]]


class AnilistAnimeProvider:
    name = "anilist"

    def __init__(
        self,
        request_func: Optional[RequestFunc] = None,
        timeout_sec: float = 10.0,
        timezone: str = "Asia/Shanghai",
    ):
        self.request_func = request_func
        self.timeout_sec = float(timeout_sec)
        self.timezone = timezone
        self.url = "https://graphql.anilist.co"

    def list_capabilities(self) -> list[str]:
        return ["today_anime"]

    def has_capability(self, capability: str) -> bool:
        return str(capability or "").strip() == "today_anime"

    async def fetch(self, capability: str, **params: Any) -> InfoSourceResult:
        if not self.has_capability(capability):
            return InfoSourceResult(
                ok=False,
                provider=self.name,
                capability=capability,
                error=f"unknown anilist capability: {capability}",
            )
        try:
            max_count = self._param_limit(params, 4)
            raw = await self._request(self._build_payload(max_count))
            items = self._parse_today_anime(raw, max_count)
        except Exception as exc:
            return InfoSourceResult(
                ok=False,
                provider=self.name,
                capability=capability,
                error=str(exc),
            )
        return InfoSourceResult(
            ok=bool(items),
            provider=self.name,
            capability=capability,
            data=items,
            summary=self._summary(items),
            raw=raw,
        )

    def _build_payload(self, max_count: int) -> Dict[str, Any]:
        start, end = self._today_range()
        query = """
        query TodayAnime($start: Int, $end: Int, $perPage: Int) {
          Page(page: 1, perPage: $perPage) {
            airingSchedules(
              airingAt_greater: $start,
              airingAt_lesser: $end,
              sort: TIME
            ) {
              airingAt
              episode
              media {
                title {
                  romaji
                  english
                  native
                }
                coverImage {
                  medium
                  large
                }
              }
            }
          }
        }
        """
        return {
            "query": query,
            "variables": {"start": start, "end": end, "perPage": max_count},
        }

    def _today_range(self) -> tuple[int, int]:
        tz = ZoneInfo(self.timezone)
        today = datetime.now(tz).date()
        start = datetime.combine(today, time.min, tzinfo=tz)
        end = datetime.combine(today, time.max, tzinfo=tz)
        return int(start.timestamp()), int(end.timestamp())

    async def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.request_func is not None:
            return await self.request_func(self.url, payload, self.timeout_sec)
        if aiohttp is None:
            raise RuntimeError("aiohttp not installed")
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Live2D-Suzu/1.0",
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url, json=payload, headers=headers) as response:
                response.raise_for_status()
                return await response.json()

    def _parse_today_anime(self, raw: Any, max_count: int) -> list[Dict[str, str]]:
        schedules = (
            ((raw or {}).get("data") or {}).get("Page") or {}
        ).get("airingSchedules")
        if not isinstance(schedules, list):
            return []
        items: list[Dict[str, str]] = []
        seen: set[str] = set()
        for schedule in schedules:
            media = schedule.get("media") if isinstance(schedule, dict) else None
            if not isinstance(media, dict):
                continue
            title_info = media.get("title") or {}
            title = (
                str(title_info.get("native") or "").strip()
                or str(title_info.get("romaji") or "").strip()
                or str(title_info.get("english") or "").strip()
            )
            image_info = media.get("coverImage") or {}
            image = (
                str(image_info.get("large") or "").strip()
                or str(image_info.get("medium") or "").strip()
            )
            if not title or not image or title in seen:
                continue
            seen.add(title)
            items.append({"title": title, "image": image})
            if len(items) >= max_count:
                break
        return items

    def _param_limit(self, params: Dict[str, Any], default: int) -> int:
        for key in ("limit", "max_count"):
            try:
                value = int(params.get(key))
            except Exception:
                continue
            if value > 0:
                return value
        return default

    def _summary(self, items: list[Dict[str, str]]) -> str:
        titles = [str(item.get("title") or "").strip() for item in items[:3]]
        titles = [title for title in titles if title]
        return "今日新番：" + "、".join(titles) if titles else ""
