from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Awaitable, Callable, List, Optional

from ..data import LiteLinkResult


BV_RE = re.compile(
    r"https?://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]+)",
    flags=re.IGNORECASE,
)


class BilibiliLinkParser:
    platform = "Bilibili"

    def __init__(
        self,
        *,
        fetch_json: Optional[Callable[[str], Awaitable[dict[str, Any]]]] = None,
        timeout_sec: float = 8.0,
    ):
        self.fetch_json = fetch_json
        self.timeout_sec = max(1.0, float(timeout_sec))

    def find(self, text: str, max_links: int) -> List[str]:
        found: List[str] = []
        seen = set()
        for match in BV_RE.finditer(str(text or "")):
            url = match.group(0).rstrip("，。,.!！?？)")
            if url in seen:
                continue
            seen.add(url)
            found.append(url)
            if len(found) >= max(1, int(max_links)):
                break
        return found

    async def parse(self, url: str) -> LiteLinkResult:
        clean_url = str(url or "").strip()
        bvid = self._extract_bvid(clean_url)
        if not bvid:
            return LiteLinkResult(platform=self.platform, url=clean_url)
        api_url = (
            "https://api.bilibili.com/x/web-interface/view?"
            + urllib.parse.urlencode({"bvid": bvid})
        )
        try:
            payload = (
                await self.fetch_json(api_url)
                if self.fetch_json is not None
                else await asyncio.to_thread(self._fetch_json_sync, api_url)
            )
        except Exception:
            return LiteLinkResult(platform=self.platform, url=clean_url)
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return LiteLinkResult(platform=self.platform, url=clean_url)
        image_urls = []
        pic = str(data.get("pic") or "").strip()
        if pic:
            image_urls.append(pic)
        return LiteLinkResult(
            platform=self.platform,
            url=clean_url,
            title=str(data.get("title") or "").strip(),
            text=str(data.get("desc") or "").strip(),
            image_urls=image_urls,
        )

    def _fetch_json_sync(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            raw = response.read(1024 * 1024)
        return json.loads(raw.decode("utf-8", errors="replace"))

    @staticmethod
    def _extract_bvid(url: str) -> str:
        match = BV_RE.search(str(url or ""))
        return match.group(1) if match else ""
