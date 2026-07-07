from __future__ import annotations

import json
import re
from typing import Any, List


URL_RE = re.compile(r"https?://[^\s\"'<>\\\]]+", flags=re.IGNORECASE)


def _walk_urls(value: Any, out: List[str]) -> None:
    if isinstance(value, str):
        out.extend(URL_RE.findall(value))
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                _walk_urls(json.loads(stripped), out)
            except Exception:
                pass
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_urls(item, out)
        return
    if isinstance(value, list):
        for item in value:
            _walk_urls(item, out)


def normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("，。,.!！?？)")


def extract_qq_card_links(payload: Any, *, max_links: int = 5) -> List[str]:
    found: List[str] = []
    _walk_urls(payload, found)
    result: List[str] = []
    seen = set()
    for raw in found:
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
        if len(result) >= max_links:
            break
    return result
