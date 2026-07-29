from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryProfile:
    person_id: str
    text: str = ""
    records: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReplyMemoryContext:
    intent: str = "none"
    impression: str = ""
    profile_text: str = ""
    memory_text: str = ""
    selected_ids: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
