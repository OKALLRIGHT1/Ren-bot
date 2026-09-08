"""Structured prompt contract between ChatService and AdvancedMemory.

Persona and system rules stay assembled in AdvancedMemory. Callers pass
time, runtime context, and tool text as explicit fields instead of a
single concatenated system_persona string.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


TIME_CALENDAR_RULE = (
    "「今天」「现在」「目前」一律以【当前时间】的日历日为准，不要把去年或过期网页当成今天。"
)


def join_prompt_sections(*sections: Optional[str]) -> str:
    chunks = [str(section or "").strip() for section in sections]
    return "\n\n".join(chunk for chunk in chunks if chunk)


def format_time_header(now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return f"【当前时间】{stamp}"


@dataclass(frozen=True)
class PromptParts:
    time_header: str = ""
    time_rule: str = ""
    runtime_context: str = ""
    tool_prompt: str = ""
    system_rules: str = ""
    invocation_mode: str = ""

    def to_legacy_system_text(self) -> str:
        """Join caller-owned sections for test doubles that still read system_persona."""
        return join_prompt_sections(
            self.time_header,
            self.time_rule,
            self.runtime_context,
            self.tool_prompt,
        )


def join_section_list(sections: Iterable[Optional[str]]) -> str:
    return join_prompt_sections(*list(sections))
