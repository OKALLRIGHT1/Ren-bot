"""Search flow decision helpers for ChatService.process()."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence


@dataclass(frozen=True)
class SearchFlowDecision:
    followup_query: str = ""
    should_force_route: bool = False
    route_text: str = ""


def build_search_acknowledgement(user_text: str) -> str:
    raw = re.sub(r"\s+", " ", str(user_text or "")).strip()
    topic = re.sub(
        r"^(?:请|麻烦)?(?:你)?(?:帮我|帮忙)?"
        r"(?:查一下|查一查|查查|搜索一下|搜一下|搜一搜|搜索|查询|查|搜)\s*",
        "",
        raw,
        count=1,
    )
    topic = re.sub(
        r"(?:的)?(?:最新|最近|实时)(?:消息|信息|新闻|进展|情况)?[。！？?!]*$",
        "",
        topic,
    ).strip(" ，,。！？?!：:")
    topic = topic[:20].strip()

    if not topic:
        fallbacks = (
            "嗯，我去看看最近有什么新消息",
            "等我一下，我查查最新情况",
            "我去找找最近有没有新进展",
            "好，我先看看现在是什么情况",
        )
        return fallbacks[sum(ord(char) for char in raw) % len(fallbacks)]

    templates = (
        "我去看看{topic}，确认一下最新情况",
        "嗯，我查查{topic}现在怎么样了",
        "等我一下，我找找关于{topic}的新消息",
        "我先看看{topic}现在是什么情况",
    )
    template = templates[sum(ord(char) for char in raw) % len(templates)]
    return template.format(topic=topic)


def build_initial_search_decision(
    user_text: str,
    ctx: Optional[Dict[str, Any]],
    *,
    resolve_followup_search_query: Callable[[str, Optional[Dict[str, Any]]], str],
) -> SearchFlowDecision:
    followup_query = str(resolve_followup_search_query(user_text, ctx) or "").strip()
    if not followup_query:
        return SearchFlowDecision()
    return SearchFlowDecision(
        followup_query=followup_query,
        should_force_route=True,
        route_text="查一下",
    )


def choose_forced_search_query(
    *,
    user_text: str,
    first_reply: str,
    followup_query: str,
    triggered: bool,
    tool_results: Sequence[Any],
    delegate_triggers: Sequence[str],
    looks_like_uncertain_answer: Callable[[str], bool],
    is_searchworthy_question: Callable[[str], bool],
) -> str:
    if triggered or tool_results or delegate_triggers:
        return ""
    followup_clean = str(followup_query or "").strip()
    if followup_clean:
        return followup_clean
    if looks_like_uncertain_answer(first_reply) and is_searchworthy_question(user_text):
        return str(user_text or "").strip()
    return ""


async def run_search_delegate_query(
    *,
    query: str,
    ctx: Dict[str, Any],
    plugin_manager: Any,
) -> tuple[bool, str, list[str], list[str]]:
    clean_query = str(query or "").strip()
    if not clean_query:
        return False, "", [], []
    delegate_ctx = dict(ctx or {})
    delegate_ctx["delegate_mode"] = True
    delegate_ctx["allow_read"] = False
    delegate_ctx["allow_write"] = False
    delegate_ctx["allow_exec"] = False
    command = f"[CMD: search | {clean_query}]"
    triggered, clean, results, used = await plugin_manager.execute_commands(
        command,
        delegate_ctx,
        allow_tools=True,
        allowed_types={"delegate"},
    )
    return bool(triggered), str(clean or ""), list(results or []), list(used or [])


async def run_search_fallback_for_moegirl(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    plugin_manager: Any,
) -> tuple[list[str], list[str]]:
    triggered, _clean, results, used = await run_search_delegate_query(
        query=user_text,
        ctx=ctx,
        plugin_manager=plugin_manager,
    )
    if not triggered:
        return [], []
    return results, used
