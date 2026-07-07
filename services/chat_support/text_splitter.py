from __future__ import annotations

import re
from typing import List


def split_chat_text_parts(text: str, *, max_len: int = 55) -> List[str]:
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return []
    if "```" in clean:
        return [clean]
    clean = re.sub(r"\n{3,}", "\n\n", clean)

    blocks = [block.strip() for block in re.split(r"\n\s*\n", clean) if block.strip()]
    parts: List[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) > 1 and any(_is_structured_line(line) for line in lines):
            parts.extend(lines)
        elif 1 < len(lines) <= 5 and all(_is_natural_line(line) for line in lines):
            parts.extend(lines)
        elif len(lines) == 1 and _is_natural_line(lines[0]):
            parts.append(lines[0])
        else:
            parts.append(" ".join(lines))

    parts = [re.sub(r"[ \t]{2,}", " ", part).strip() for part in parts if part.strip()]

    final_parts: List[str] = []
    for part in parts:
        final_parts.extend(split_long_chat_text_part(part, max_len=max_len))
    return [part for part in final_parts if part]


def split_long_chat_text_part(text: str, *, max_len: int = 55) -> List[str]:
    raw = str(text or "").strip()
    if not raw or len(raw) <= max_len:
        return [raw] if raw else []
    if re.search(r"https?://|```", raw):
        return [raw]

    chunks = [item.strip() for item in re.split(r"(?<=[。！？!?])\s*", raw) if item.strip()]
    if len(chunks) <= 1:
        chunks = [item.strip() for item in re.split(r"(?<=[；;、])\s*", raw) if item.strip()]
    if len(chunks) <= 1:
        hard_max = 260
        if len(raw) <= hard_max:
            return [raw]
        return [raw[i : i + hard_max].strip() for i in range(0, len(raw), hard_max)]

    parts: List[str] = []
    current = ""
    for chunk in chunks:
        candidate = f"{current}{chunk}" if current else chunk
        if current and len(candidate) > max_len:
            parts.append(current.strip())
            current = chunk
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())
    return parts or [raw]


def _is_structured_line(line: str) -> bool:
    item = str(line or "").strip()
    if not item:
        return False
    return bool(
        re.match(r"^(?:\d+[.)、]\s+|ID:\s*|摘要:\s*|发件人:|时间:|附件:)", item)
    )


def _is_natural_line(line: str) -> bool:
    item = str(line or "").strip()
    if not item:
        return False
    if len(item) > 90:
        return False
    if re.search(r"https?://|\[[^\]]+\]\([^)]+\)", item):
        return False
    if re.match(r"^\s*(?:[-*•]|#|\d+[.)、])", item):
        return False
    return True
