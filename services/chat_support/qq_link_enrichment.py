from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class QqLinkEnrichmentService:
    def __init__(
        self,
        *,
        enabled: bool = False,
        max_links: int = 3,
        timeout_sec: float = 8.0,
        parsers: Optional[List[Any]] = None,
    ):
        self.enabled = bool(enabled)
        self.max_links = max(1, int(max_links))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.parsers = list(parsers) if parsers is not None else self._default_parsers()

    def _default_parsers(self) -> List[Any]:
        try:
            from services.chat_support.lite_link_parser.parsers import BilibiliLinkParser
        except Exception:
            return []
        return [BilibiliLinkParser(timeout_sec=self.timeout_sec)]

    async def enrich(
        self, text: str, images: List[Dict[str, Any]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        original_text = str(text or "")
        merged_images = list(images or [])
        if not self.enabled:
            return original_text, merged_images

        sections: List[str] = []
        seen_urls = set()
        seen_images = {
            str(item.get("url") or item.get("file") or "")
            for item in merged_images
            if isinstance(item, dict)
        }
        remaining = self.max_links
        for parser in self.parsers:
            finder = getattr(parser, "find", None)
            parse = getattr(parser, "parse", None)
            if not callable(finder) or not callable(parse):
                continue
            for url in finder(original_text, remaining):
                clean_url = str(url or "").strip()
                if not clean_url or clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)
                remaining -= 1
                try:
                    result = await parse(clean_url)
                except Exception:
                    continue
                section = self._format_result(result)
                if section:
                    sections.append(section)
                for image_url in list(getattr(result, "image_urls", []) or []):
                    clean_image = str(image_url or "").strip()
                    if clean_image and clean_image not in seen_images:
                        seen_images.add(clean_image)
                        merged_images.append({"url": clean_image})
                if remaining <= 0:
                    break
            if remaining <= 0:
                break

        if not sections:
            return original_text, merged_images
        return (
            original_text.rstrip() + "\n\n[链接解析]\n" + "\n\n".join(sections),
            merged_images,
        )

    def _format_result(self, result: Any) -> str:
        platform = str(getattr(result, "platform", "") or "Unknown").strip()
        title = str(getattr(result, "title", "") or "").strip()
        body = str(getattr(result, "text", "") or "").strip()
        url = str(getattr(result, "url", "") or "").strip()
        lines = [f"平台: {platform}"]
        if title:
            lines.append(f"标题: {title[:160]}")
        if body:
            lines.append(f"正文: {body[:600]}")
        if url:
            lines.append(f"链接: {url}")
        return "\n".join(lines)
