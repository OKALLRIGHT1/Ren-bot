"""Unified natural-chat orchestration for desktop + QQ (group optional)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from services.chat_support.forbidden_phrase_guard import (
    DEFAULT_FORBIDDEN_PHRASES,
    build_retry_constraint,
    find_forbidden_phrases,
    should_retry_after_forbidden,
    strip_forbidden_spans,
)

QQ_REMOTE_SOURCES: Set[str] = {"qq_gateway", "napcat_qq"}
DESKTOP_SOURCES: Set[str] = {"text_input", "desktop", "voice"}


def _int_config(raw: Dict[str, Any], key: str, default: int) -> int:
    if not isinstance(raw, dict) or key not in raw or raw.get(key) in {None, ""}:
        return int(default)
    try:
        return int(raw.get(key))
    except (TypeError, ValueError):
        return int(default)


# 只补系统规则没写的本轮禁令，避免再念一遍「短句/别客服腔」。
SCENE_CORE_LINES = (
    "禁止「切换得好硬/节奏很怪/状态不对/落差好大」等抽象标签。",
)


@dataclass
class NaturalChatConfig:
    character_thought_enabled: bool = True
    character_thought_scope: str = "desktop_and_qq_private"
    group_chat_natural_enabled: bool = False
    character_thought_timeout_ms: int = 2500
    character_thought_max_tokens: int = 220
    expression_inject_in_main_reply: bool = True
    expression_inject_max_items: int = 1
    character_thought_on_error: str = "skip_thought"
    forbidden_phrase_max_retries: int = 1
    detail_intent_bypass_short_shell: bool = True

    @classmethod
    def from_mapping(cls, data: Optional[Dict[str, Any]]) -> "NaturalChatConfig":
        raw = data if isinstance(data, dict) else {}
        return cls(
            character_thought_enabled=bool(
                raw.get("character_thought_enabled", True)
            ),
            character_thought_scope=str(
                raw.get("character_thought_scope") or "desktop_and_qq_private"
            ).strip()
            or "desktop_and_qq_private",
            group_chat_natural_enabled=bool(
                raw.get("group_chat_natural_enabled", False)
            ),
            character_thought_timeout_ms=int(
                raw.get("character_thought_timeout_ms") or 2500
            ),
            character_thought_max_tokens=int(
                raw.get("character_thought_max_tokens") or 220
            ),
            expression_inject_in_main_reply=bool(
                raw.get("expression_inject_in_main_reply", True)
            ),
            expression_inject_max_items=_int_config(
                raw, "expression_inject_max_items", 1
            ),
            character_thought_on_error=str(
                raw.get("character_thought_on_error") or "skip_thought"
            ).strip()
            or "skip_thought",
            forbidden_phrase_max_retries=int(
                raw.get("forbidden_phrase_max_retries") or 1
            ),
            detail_intent_bypass_short_shell=bool(
                raw.get("detail_intent_bypass_short_shell", True)
            ),
        )


@dataclass
class ThoughtGateDecision:
    should_run: bool
    scope_matched: bool
    reason: str = ""
    short_shell: bool = True
    detail_intent: bool = False


def _message_type(ctx: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ctx, dict):
        return "private"
    meta = ctx.get("channel_meta") or {}
    if not isinstance(meta, dict):
        return "private"
    return str(meta.get("message_type") or "private").strip().lower() or "private"


def is_qq_source(source: str) -> bool:
    return str(source or "").strip().lower() in QQ_REMOTE_SOURCES


def is_desktop_source(source: str) -> bool:
    return str(source or "").strip().lower() in DESKTOP_SOURCES


def is_group_message(source: str, message_type: str) -> bool:
    return is_qq_source(source) and str(message_type or "").strip().lower() == "group"


def is_qq_private(source: str, message_type: str) -> bool:
    if not is_qq_source(source):
        return False
    mt = str(message_type or "private").strip().lower() or "private"
    return mt != "group"


def scope_matches(
    source: str,
    message_type: str,
    config: NaturalChatConfig,
) -> bool:
    scope = str(config.character_thought_scope or "desktop_and_qq_private").strip().lower()
    if scope in {"off", "none", "disabled"}:
        return False
    src = str(source or "").strip().lower()
    mt = str(message_type or "private").strip().lower() or "private"
    group = is_group_message(src, mt)

    # 群聊：产品未开放时默认关；打开后与桌面/私聊同源（不要求 scope 再开 qq_all）
    if group:
        return bool(config.group_chat_natural_enabled) and scope not in {
            "desktop_only",
            "qq_private_only",
            "off",
        }

    if scope == "all_chat_sources":
        return is_desktop_source(src) or is_qq_source(src)
    if scope == "desktop_only":
        return is_desktop_source(src)
    if scope == "qq_private_only":
        return is_qq_private(src, mt)
    if scope == "qq_all":
        return is_qq_source(src)
    # default: desktop_and_qq_private
    if is_desktop_source(src):
        return True
    if is_qq_private(src, mt):
        return True
    return False


def decide_thought_gate(
    *,
    user_text: str,
    source: str,
    message_type: str = "private",
    config: NaturalChatConfig,
    need_tools: bool = False,
    codex_mode: bool = False,
    wants_detailed_answer: bool = False,
    is_command: bool = False,
) -> ThoughtGateDecision:
    if not config.character_thought_enabled:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=False,
            reason="disabled",
            short_shell=not wants_detailed_answer,
            detail_intent=wants_detailed_answer,
        )

    matched = scope_matches(source, message_type, config)
    detail = bool(wants_detailed_answer)
    short_shell = True
    if detail and config.detail_intent_bypass_short_shell:
        short_shell = False

    if not matched:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=False,
            reason="scope_miss",
            short_shell=short_shell,
            detail_intent=detail,
        )
    if need_tools or codex_mode:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="tools_or_codex",
            short_shell=short_shell,
            detail_intent=detail,
        )
    if is_command:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="command",
            short_shell=short_shell,
            detail_intent=detail,
        )

    text = str(user_text or "").strip()
    if not text:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="empty",
            short_shell=short_shell,
            detail_intent=detail,
        )
    if len(text) > 200:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="too_long",
            short_shell=short_shell,
            detail_intent=detail,
        )

    # 详细意图仍可跑 Thought（want 倾向 direct_answer），不强制 False
    return ThoughtGateDecision(
        should_run=True,
        scope_matched=True,
        reason="ok",
        short_shell=short_shell,
        detail_intent=detail,
    )


def build_scene_prompt(
    source: str,
    message_type: str = "private",
    *,
    detail_intent: bool = False,
    bypass_short_shell: bool = False,
) -> str:
    """极简本轮约束：系统规则已有短句/聊天默认，这里只补禁令 + 渠道壳。"""
    parts = ["【本轮】"]
    for line in SCENE_CORE_LINES:
        parts.append(f"- {line}")

    src = str(source or "").strip().lower()
    mt = str(message_type or "private").strip().lower() or "private"
    use_short = not (detail_intent and bypass_short_shell)

    if detail_intent:
        parts.append("- 用户要展开：先结论，再必要说明；仍像聊天。")
    elif use_short:
        parts.append("- 默认 1～2 句短接话。")

    if is_qq_source(src):
        if is_group_message(src, mt):
            parts.append("- QQ 群：别刷屏，短回。")
        else:
            parts.append("- QQ 私聊：像真人消息；短句少用句号收尾。")
    elif is_desktop_source(src):
        parts.append("- 本地闲聊：自然短促即可。")

    return "\n".join(parts)


def format_expression_block(hints: list[str], max_items: int = 1) -> str:
    items = [str(h).strip() for h in (hints or []) if str(h).strip()]
    if not items:
        return ""
    cap = max(1, int(max_items or 1))
    lines = ["【说法参考·可忽略】", *[f"- {item}" for item in items[:cap]]]
    return "\n".join(lines)


def evaluate_forbidden_reply(
    text: str,
    *,
    retries_done: int,
    max_retries: int,
) -> Dict[str, Any]:
    hits = find_forbidden_phrases(text)
    retry = should_retry_after_forbidden(
        hits=hits,
        retries_done=retries_done,
        max_retries=max_retries,
    )
    return {
        "hits": hits,
        "should_retry": retry,
        "retry_constraint": build_retry_constraint(hits) if hits else "",
        "stripped": strip_forbidden_spans(text) if hits and not retry else text,
    }
