from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass(frozen=True)
class ChatCompletionResult:
    text: str
    tool_calls: Tuple[ToolCallRequest, ...] = ()
    model_key: str = ""
    transport: str = ""
    finish_reason: str = ""
    fc_skipped_reason: str = ""
