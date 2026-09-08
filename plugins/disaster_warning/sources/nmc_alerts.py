from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ..event import DisasterEvent
from ..watch import detect_color, fold_zh

DEFAULT_ALARM_URL = "http://www.nmc.cn/rest/findAlarm"
DEFAULT_TYPHOON_URL = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_CACHE_TTL_SEC = 60.0
USER_AGENT = "Mozilla/5.0 (compatible; Live2D-Suzu-disaster-warning/1.0)"

FetchJson = Callable[[str], Awaitable[Any]]
FetchText = Callable[[str], Awaitable[str]]


@dataclass
class AlertItem:
    title: str
    url: str
    issued_at: str
    kind: str


@dataclass
class TyphoonItem:
    storm_id: str
    name_cn: str
    name_en: str
    number: str
    active: bool


def parse_jsonp(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    if text[0] in "{[":
        return json.loads(text)
    start = text.find("(")
    end = text.rfind(")")
    if start >= 0 and end > start:
        return json.loads(text[start + 1 : end])
    return json.loads(text)


def _alert_kind(title: str) -> str:
    return "typhoon" if "台风" in (title or "") else "weather"


_TIME_NOISE_RE = re.compile(
    r"\d{4}年\d{1,2}月\d{1,2}日|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}月\d{1,2}日|"
    r"\d{1,2}时(?:\d{1,2}分)?|"
    r"\d{10,}"
)


def alert_event_id(title: str, url: str = "") -> str:
    """Stable id so '继续发布' / 换了钟点 / URL 时间戳不会每轮都当新报。"""
    color = detect_color(title)
    kind = _alert_kind(title)
    body = fold_zh(str(title or ""))
    body = _TIME_NOISE_RE.sub("", body)
    body = body.replace("继续发布", "发布").replace("更新发布", "发布")
    body = re.sub(r"[\s:：._/\-]+", "", body)
    if len(body) >= 6:
        return f"{kind}:{color}:{body}"
    cleaned_url = _TIME_NOISE_RE.sub("", str(url or ""))
    return cleaned_url or str(url or "").strip() or str(title or "").strip()


def parse_find_alarm(payload: Any) -> List[AlertItem]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    page = data.get("page") if isinstance(data, dict) else None
    rows = None
    if isinstance(page, dict):
        rows = page.get("list")
    if rows is None and isinstance(data, dict):
        rows = data.get("list")
    if rows is None:
        rows = payload.get("list")
    if not isinstance(rows, list):
        return []
    items: List[AlertItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("headline") or "").strip()
        if not title:
            continue
        url = str(row.get("url") or row.get("id") or title).strip()
        issued = str(row.get("issuetime") or row.get("issueTime") or row.get("time") or "").strip()
        items.append(
            AlertItem(
                title=title,
                url=url,
                issued_at=issued,
                kind=_alert_kind(title),
            )
        )
    return items


def parse_typhoon_list(payload: Any) -> List[TyphoonItem]:
    data = parse_jsonp(payload) if not isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        return []
    rows = data.get("typhoonList") or []
    items: List[TyphoonItem] = []
    if not isinstance(rows, list):
        return items
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        name_cn = row[2]
        if name_cn in {None, "null"}:
            name_cn = ""
        status = str(row[-1] or "").strip().lower() if len(row) >= 4 else ""
        number = ""
        if len(row) > 3 and row[3] not in {None, "null"}:
            number = str(row[3]).strip()
        items.append(
            TyphoonItem(
                storm_id=str(row[0] or "").strip(),
                name_en=str(row[1] or "").strip(),
                name_cn=str(name_cn or "").strip(),
                number=number,
                active=status == "start",
            )
        )
    return items


def alert_to_event(item: AlertItem) -> DisasterEvent:
    title = str(item.title or "").strip()
    event_id = alert_event_id(title, item.url)
    return DisasterEvent(
        source_id="nmc",
        event_id=event_id,
        updates=1,
        kind=item.kind or _alert_kind(title),
        place_name=title[:80],
        magnitude=0.0,
        occurred_at=item.issued_at or None,
        location_desc=title,
        raw_title=title,
        color_level=detect_color(title),
        display_name="中央气象台",
    )


def filter_alerts(items: Sequence[AlertItem], place: str) -> List[AlertItem]:
    needle = fold_zh(str(place or "").strip())
    if not needle:
        return list(items)
    out: List[AlertItem] = []
    for item in items:
        hay = fold_zh(item.title)
        if needle in hay:
            out.append(item)
    return out


def format_alerts(items: Sequence[AlertItem], *, place: str = "") -> str:
    header = "中央气象台气象预警"
    if place:
        header = f"与「{place}」相关的气象预警"
    if not items:
        if place:
            return f"没有找到与「{place}」相关的气象预警。"
        return "当前没有气象预警。"
    lines = [header, "（官方预警信号，不是日常天气预报）"]
    for index, item in enumerate(items[:10], start=1):
        when = item.issued_at or "时间未知"
        lines.append(f"{index}. {when} {item.title}")
    return "\n".join(lines)


def format_typhoons(items: Sequence[TyphoonItem]) -> str:
    active = [item for item in items if item.active]
    if not active:
        return "当前没有在编台风。"
    lines = ["中央气象台在编台风", "（台风网路径，不是日常天气预报）"]
    for index, item in enumerate(active, start=1):
        name = item.name_cn or item.name_en or "未命名"
        extra = []
        if item.name_en and item.name_cn:
            extra.append(item.name_en)
        if item.number:
            extra.append(item.number)
        suffix = f"（{' / '.join(extra)}）" if extra else ""
        lines.append(f"{index}. {name}{suffix}")
    return "\n".join(lines)


async def _aiohttp_get_text(url: str, timeout_sec: float) -> str:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_sec)))
    headers = {"User-Agent": USER_AGENT, "Referer": "http://www.nmc.cn/"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()


async def _aiohttp_get_json(url: str, timeout_sec: float) -> Any:
    text = await _aiohttp_get_text(url, timeout_sec)
    try:
        return json.loads(text)
    except Exception:
        return parse_jsonp(text)


class NmcClient:
    def __init__(
        self,
        *,
        alarm_url: str = DEFAULT_ALARM_URL,
        typhoon_url: str = DEFAULT_TYPHOON_URL,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
        fetch_json: Optional[FetchJson] = None,
        fetch_text: Optional[FetchText] = None,
    ):
        self.alarm_url = str(alarm_url or DEFAULT_ALARM_URL).strip() or DEFAULT_ALARM_URL
        self.typhoon_url = str(typhoon_url or DEFAULT_TYPHOON_URL).strip() or DEFAULT_TYPHOON_URL
        self.timeout_sec = float(timeout_sec or DEFAULT_TIMEOUT_SEC)
        self.cache_ttl_sec = float(cache_ttl_sec or DEFAULT_CACHE_TTL_SEC)
        self.fetch_json = fetch_json
        self.fetch_text = fetch_text
        self._cache: Dict[str, Any] = {}
        self._cache_at: Dict[str, float] = {}

    def clear_cache(self) -> None:
        self._cache.clear()
        self._cache_at.clear()

    async def list_alerts(self, *, limit: int = 20) -> List[AlertItem]:
        payload = await self._cached_json(self.alarm_url)
        return parse_find_alarm(payload)[: max(1, min(50, int(limit or 20)))]

    async def list_typhoons(self) -> List[TyphoonItem]:
        raw = await self._cached_text(self.typhoon_url)
        return parse_typhoon_list(raw)

    async def _cached_json(self, url: str) -> Any:
        now = time.monotonic()
        if url in self._cache and (now - self._cache_at.get(url, 0)) < self.cache_ttl_sec:
            return self._cache[url]
        if self.fetch_json is not None:
            data = await self.fetch_json(url)
        else:
            data = await _aiohttp_get_json(url, self.timeout_sec)
        self._cache[url] = data
        self._cache_at[url] = now
        return data

    async def _cached_text(self, url: str) -> Any:
        now = time.monotonic()
        if url in self._cache and (now - self._cache_at.get(url, 0)) < self.cache_ttl_sec:
            return self._cache[url]
        if self.fetch_text is not None:
            data = await self.fetch_text(url)
        elif self.fetch_json is not None:
            data = await self.fetch_json(url)
        else:
            data = await _aiohttp_get_text(url, self.timeout_sec)
        self._cache[url] = data
        self._cache_at[url] = now
        return data
