"""Build PromptParts for ChatService / natural-chat callers."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from modules.prompt_parts import (
    TIME_CALENDAR_RULE,
    PromptParts,
    format_time_header,
    join_section_list,
)
from services.chat_support.tool_invocation import (
    rewrite_system_tool_rules,
    should_rewrite_tool_rules,
)

try:
    from config import SYSTEM_RULES_PROMPT
except Exception:
    SYSTEM_RULES_PROMPT = ""


def make_chat_prompt_parts(
    *,
    runtime_sections: Iterable[Optional[str]],
    tool_sections: Iterable[Optional[str]] = (),
    invocation_mode: str = "",
    now: Optional[datetime] = None,
    system_rules: Optional[str] = None,
) -> PromptParts:
    rules = SYSTEM_RULES_PROMPT if system_rules is None else str(system_rules or "")
    mode = str(invocation_mode or "").strip()
    if should_rewrite_tool_rules(mode) and rules:
        rules = rewrite_system_tool_rules(rules, mode)
    return PromptParts(
        time_header=format_time_header(now),
        time_rule=TIME_CALENDAR_RULE,
        runtime_context=join_section_list(runtime_sections),
        tool_prompt=join_section_list(tool_sections),
        system_rules=rules,
        invocation_mode=mode,
    )
