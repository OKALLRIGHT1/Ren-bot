"""Rule-layer guard against abstract label / hard-switch commentary phrases."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

# 用户体感金标：点评腔 / 状态标签
DEFAULT_FORBIDDEN_PHRASES: Tuple[str, ...] = (
    "切换得好硬",
    "切换好硬",
    "落差好大",
    "落差很大",
    "状态不对",
    "状态切换",
    "节奏很怪",
    "节奏怪怪",
    "节奏不对",
    "太突然了吧",
    "落差感",
)


def normalize_for_match(text: str) -> str:
    clean = str(text or "")
    clean = re.sub(r"\s+", "", clean)
    return clean.lower()


def find_forbidden_phrases(
    text: str,
    phrases: Sequence[str] | None = None,
) -> List[str]:
    hay = normalize_for_match(text)
    if not hay:
        return []
    pool = list(phrases) if phrases is not None else list(DEFAULT_FORBIDDEN_PHRASES)
    hits: List[str] = []
    for phrase in pool:
        key = normalize_for_match(phrase)
        if key and key in hay and phrase not in hits:
            hits.append(phrase)
    return hits


def should_retry_after_forbidden(
    *,
    hits: Iterable[str],
    retries_done: int,
    max_retries: int,
) -> bool:
    if not list(hits):
        return False
    try:
        done = int(retries_done)
    except Exception:
        done = 0
    try:
        cap = int(max_retries)
    except Exception:
        cap = 0
    return done < max(0, cap)


def strip_forbidden_spans(
    text: str,
    phrases: Sequence[str] | None = None,
) -> str:
    """Last-resort rule strip when retry still hits; keeps message usable."""
    clean = str(text or "")
    pool = list(phrases) if phrases is not None else list(DEFAULT_FORBIDDEN_PHRASES)
    for phrase in pool:
        if phrase and phrase in clean:
            clean = clean.replace(phrase, "")
    clean = re.sub(r"[，,]{2,}", "，", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean.strip(" ，,。．\n")


def build_retry_constraint(hits: Sequence[str]) -> str:
    listed = "、".join(hits[:6]) if hits else "抽象标签点评"
    return (
        "【输出约束·重试】上一稿出现了禁止的点评腔/抽象标签"
        f"（如：{listed}）。请重写：先接话，不要状态分析，不要复述总结，不要使用这类标签句。"
    )
