from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ..watch import fold_zh

DEFAULT_EQLIST_URL = "https://api.wolfx.jp/cenc_eqlist.json"
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_CACHE_TTL_SEC = 60.0
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

FetchJson = Callable[[str], Awaitable[Dict[str, Any]]]


@dataclass
class EqListItem:
    time: str
    location: str
    place_name: str
    magnitude: str
    depth: str
    intensity: str
    latitude: str
    longitude: str


def _no_sort_key(key: str) -> int:
    suffix = str(key or "")[2:]
    try:
        return int(suffix)
    except (TypeError, ValueError):
        return 10**9


def parse_eqlist(payload: Any) -> List[EqListItem]:
    if not isinstance(payload, dict):
        return []
    raw_type = str(payload.get("type") or "").strip()
    if raw_type and raw_type != "cenc_eqlist":
        return []
    items: List[EqListItem] = []
    keys = [key for key, value in payload.items() if str(key).startswith("No") and isinstance(value, dict)]
    keys.sort(key=_no_sort_key)
    for key in keys:
        blob = payload.get(key) or {}
        location = str(blob.get("location") or blob.get("placeName") or "").strip()
        place_name = str(blob.get("placeName") or location).strip()
        mag = str(blob.get("magnitude") or "").strip()
        if not location and not mag:
            continue
        items.append(
            EqListItem(
                time=str(blob.get("time") or "").strip(),
                location=location,
                place_name=place_name,
                magnitude=mag,
                depth=str(blob.get("depth") or "").strip(),
                intensity=str(blob.get("intensity") or "").strip(),
                latitude=str(blob.get("latitude") or "").strip(),
                longitude=str(blob.get("longitude") or "").strip(),
            )
        )
    return items


def filter_eqlist(items: Sequence[EqListItem], place: str) -> List[EqListItem]:
    needle = fold_zh(str(place or "").strip())
    if not needle:
        return list(items)
    out: List[EqListItem] = []
    for item in items:
        hay = fold_zh(" ".join([item.location, item.place_name]))
        if needle and needle in hay:
            out.append(item)
    return out


def format_eqlist(
    items: Sequence[EqListItem],
    *,
    place: str = "",
    source_note: bool = True,
) -> str:
    header = "中国地震台网最近记录"
    if place:
        header = f"与「{place}」相关的最近记录"
    lines = [header]
    if source_note:
        lines.append("（Wolfx 转发的测定目录，不是秒级预警）")
    if not items:
        if place:
            return f"没有找到与「{place}」相关的最近地震记录。"
        return "查不到最近地震。"
    for index, item in enumerate(items, start=1):
        loc = item.location or item.place_name or "未知地点"
        mag = f" M{item.magnitude}" if item.magnitude else ""
        depth = f" 深{item.depth}km" if item.depth else ""
        when = item.time or "时间未知"
        lines.append(f"{index}. {when} {loc}{mag}{depth}")
    return "\n".join(lines)


async def _aiohttp_get_json(url: str, timeout_sec: float) -> Dict[str, Any]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_sec)))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
    if not isinstance(data, dict):
        raise ValueError("eqlist_not_object")
    return data


class CencEqListClient:
    def __init__(
        self,
        *,
        url: str = DEFAULT_EQLIST_URL,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
        fetch_json: Optional[FetchJson] = None,
    ):
        self.url = str(url or DEFAULT_EQLIST_URL).strip() or DEFAULT_EQLIST_URL
        self.timeout_sec = float(timeout_sec or DEFAULT_TIMEOUT_SEC)
        self.cache_ttl_sec = float(cache_ttl_sec or DEFAULT_CACHE_TTL_SEC)
        self.fetch_json = fetch_json
        self._cache_payload: Optional[Dict[str, Any]] = None
        self._cache_at: float = 0.0

    def clear_cache(self) -> None:
        self._cache_payload = None
        self._cache_at = 0.0

    async def list_events(self, *, limit: int = DEFAULT_LIMIT) -> List[EqListItem]:
        payload = await self._payload()
        cap = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
        return parse_eqlist(payload)[:cap]

    async def _payload(self) -> Dict[str, Any]:
        now = time.monotonic()
        if (
            self._cache_payload is not None
            and (now - self._cache_at) < self.cache_ttl_sec
        ):
            return self._cache_payload
        if self.fetch_json is not None:
            data = await self.fetch_json(self.url)
        else:
            data = await _aiohttp_get_json(self.url, self.timeout_sec)
        if not isinstance(data, dict):
            raise ValueError("eqlist_not_object")
        self._cache_payload = data
        self._cache_at = now
        return data
