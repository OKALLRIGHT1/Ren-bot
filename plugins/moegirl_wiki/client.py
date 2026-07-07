from __future__ import annotations

import html
import re
from typing import Any

import aiohttp

from .models import MoegirlCandidate, MoegirlPageSummary


class MoegirlApiError(RuntimeError):
    pass


def _build_page_url(title: str) -> str:
    return f"https://zh.moegirl.org.cn/{title}"


def _clean_search_snippet(snippet: Any) -> str:
    text = str(snippet or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def build_generator_search_params(query: str, limit: int = 5) -> dict[str, str]:
    return {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": str(limit),
        "gsrnamespace": "0",
        "prop": "info|extracts",
        "inprop": "url",
        "exintro": "1",
        "explaintext": "1",
        "format": "json",
    }


def parse_opensearch_payload(payload: list[Any]) -> list[MoegirlCandidate]:
    if len(payload) < 4:
        return []
    titles = payload[1] if isinstance(payload[1], list) else []
    descriptions = payload[2] if isinstance(payload[2], list) else []
    urls = payload[3] if isinstance(payload[3], list) else []
    candidates: list[MoegirlCandidate] = []
    for index, title in enumerate(titles):
        url = urls[index] if index < len(urls) else ""
        description = descriptions[index] if index < len(descriptions) else ""
        if not isinstance(title, str):
            continue
        candidates.append(
            MoegirlCandidate(title=title, url=str(url), description=str(description))
        )
    return candidates


def parse_generator_search_payload(payload: dict[str, Any]) -> list[MoegirlCandidate]:
    query = payload.get("query", {})
    if not isinstance(query, dict):
        return []
    pages = query.get("pages", {})
    if not isinstance(pages, dict):
        return []

    ordered_pages: list[dict[str, Any]] = []
    page_ids = query.get("pageids", [])
    if isinstance(page_ids, list) and page_ids:
        for page_id in page_ids:
            page = pages.get(str(page_id))
            if isinstance(page, dict):
                ordered_pages.append(page)
    else:
        ordered_pages = [page for page in pages.values() if isinstance(page, dict)]

    candidates: list[MoegirlCandidate] = []
    for page in ordered_pages:
        title = str(page.get("title", "")).strip()
        if not title:
            continue
        candidates.append(
            MoegirlCandidate(
                title=title,
                url=str(page.get("fullurl", "") or _build_page_url(title)),
                description=_clean_search_snippet(page.get("extract", "")),
            )
        )
    return candidates


def parse_page_summary_payload(payload: dict[str, Any]) -> MoegirlPageSummary:
    pages = payload.get("query", {}).get("pages", {})
    if not isinstance(pages, dict) or not pages:
        raise MoegirlApiError("未找到页面数据")
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        raise MoegirlApiError("页面数据格式错误")

    categories_raw = page.get("categories", [])
    categories: list[str] = []
    if isinstance(categories_raw, list):
        for item in categories_raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", ""))
            categories.append(title.removeprefix("Category:"))

    thumbnail = page.get("thumbnail", {})
    thumbnail_url = thumbnail.get("source") if isinstance(thumbnail, dict) else None
    return MoegirlPageSummary(
        title=str(page.get("title", "")),
        summary=str(page.get("extract", "")),
        url=str(page.get("fullurl", "")),
        categories=categories,
        thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
        page_id=page.get("pageid"),
    )


class MoegirlApiClient:
    def __init__(
        self,
        base_url: str = "https://zh.moegirl.org.cn/api.php",
        timeout_seconds: int = 10,
        cookie_string: str = "",
    ):
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.cookie_string = cookie_string.strip()

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "live2d-llm-moegirl/1.0"}
        if self.cookie_string:
            headers["Cookie"] = self.cookie_string
        return headers

    async def _request_json(self, params: dict[str, Any]) -> Any:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                self.base_url, params=params, headers=self._build_headers()
            ) as response:
                if response.status != 200:
                    raise MoegirlApiError(f"HTTP {response.status}")
                payload = await response.json()
                if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                    err = payload["error"]
                    raise MoegirlApiError(
                        f"{str(err.get('code', 'unknown'))}: {str(err.get('info', '未知错误'))}"
                    )
                return payload

    async def opensearch(self, query: str, limit: int = 5) -> list[MoegirlCandidate]:
        payload = await self._request_json(
            {
                "action": "opensearch",
                "search": query,
                "limit": str(limit),
                "namespace": "0",
                "format": "json",
            }
        )
        if not isinstance(payload, list):
            raise MoegirlApiError("opensearch 返回格式错误")
        return parse_opensearch_payload(payload)

    async def search(self, query: str, limit: int = 5) -> list[MoegirlCandidate]:
        payload = await self._request_json(build_generator_search_params(query, limit))
        if not isinstance(payload, dict):
            raise MoegirlApiError("search 返回格式错误")
        return parse_generator_search_payload(payload)

    async def fetch_page_summary(self, title: str) -> MoegirlPageSummary:
        payload = await self._request_json(
            {
                "action": "query",
                "prop": "extracts|pageprops|pageimages|info|categories",
                "inprop": "url",
                "redirects": "1",
                "exintro": "1",
                "explaintext": "1",
                "pithumbsize": "400",
                "cllimit": "10",
                "titles": title,
                "format": "json",
            }
        )
        if not isinstance(payload, dict):
            raise MoegirlApiError("query 返回格式错误")
        return parse_page_summary_payload(payload)
