"""Pure text helpers used by ChatService.

Keep this module free of ChatService state so helpers can be moved safely.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def clean_text_for_tts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\*#]+", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def strip_wrapping_quotes(text: str) -> str:
    cleaned = str(text or "").strip()
    quote_pairs = {
        '"': '"',
        "'": "'",
        "“": "”",
        "‘": "’",
        "「": "」",
        "『": "』",
        "《": "》",
    }
    changed = True
    while changed and len(cleaned) >= 2:
        changed = False
        first = cleaned[0]
        last = cleaned[-1]
        if quote_pairs.get(first) == last:
            cleaned = cleaned[1:-1].strip()
            changed = True
    return cleaned


def is_link_request(text: str) -> bool:
    raw = str(text or "")
    lower = raw.lower()
    if "链接" in raw or "网址" in raw:
        return True
    return ("link" in lower) or ("url" in lower)


def extract_first_url(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"https?://[^\s)）]+", str(text))
    if not match:
        return ""
    return match.group(0).rstrip(".,;，。)")


def strip_urls(text: str) -> str:
    return re.sub(r"https?://[^\s)）]+", "", str(text or "")).strip()


def extract_url_from_tool_results(ctx: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ctx, dict):
        return ""
    results = ctx.get("_tool_results") or []
    if not isinstance(results, list):
        results = [results]
    for item in results:
        url = extract_first_url(str(item or ""))
        if url:
            return url
    return ""


def build_share_title(text: str, url: str) -> str:
    cleaned = re.sub(r"\s+", " ", strip_urls(text)).strip()
    if cleaned:
        return cleaned[:48]
    return url


def build_share_content(text: str, title: str) -> str:
    cleaned = re.sub(r"\s+", " ", strip_urls(text)).strip()
    if not cleaned:
        return ""
    if cleaned.startswith(title):
        cleaned = cleaned[len(title) :].strip()
    return cleaned[:80]


def strip_emo_tags_anywhere(text: str, emo_tag_re: re.Pattern[str]) -> str:
    return emo_tag_re.sub("", text or "")


def strip_cmd_anywhere(text: str, cmd_re: re.Pattern[str]) -> str:
    return cmd_re.sub("", text or "")


def strip_internal_tags(text: str) -> str:
    raw = str(text or "")
    raw = re.sub(r"\[tool_use\]\s*\[[^\]]*\]\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[tool_use\]\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[search_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[web_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[moegirl_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
    return raw.strip()


def compress_sensor_text(text: str, max_len: int = 800) -> str:
    compressed = str(text or "").replace("\r\n", "\n").strip()
    if not compressed:
        return ""

    compressed = re.sub(r"\n{3,}", "\n\n", compressed)
    lines = [line.strip() for line in compressed.split("\n") if line.strip()]
    if len(lines) > 8:
        compressed = "\n".join(lines[:8])
    else:
        compressed = "\n".join(lines)

    if len(compressed) > max_len:
        compressed = compressed[: max_len - 3].rstrip() + "..."

    return compressed
