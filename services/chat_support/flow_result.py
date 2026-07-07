"""Shared result contract for future ChatService flow extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


OUTPUT_TARGET_LOCAL = "local"
OUTPUT_TARGET_QQ = "qq"
OUTPUT_TARGET_MQTT = "mqtt"
VALID_OUTPUT_TARGETS = {OUTPUT_TARGET_LOCAL, OUTPUT_TARGET_QQ, OUTPUT_TARGET_MQTT}

ALLOWED_METADATA_KEYS = {"trace_id", "route_reason", "raw_tool_result", "timing_ms"}


@dataclass
class ChatFlowResult:
    """Unified return object for extracted chat flows.

    Keep high-frequency fields explicit. Use metadata only for low-frequency
    debug/trace fields listed in ALLOWED_METADATA_KEYS.
    """

    reply_text: str = ""
    emotion: Optional[str] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    memory_writes: list[dict[str, Any]] = field(default_factory=list)
    output_targets: set[str] = field(default_factory=set)
    source: str = ""
    is_search_result: bool = False
    delegate_task_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        invalid_targets = set(self.output_targets) - VALID_OUTPUT_TARGETS
        if invalid_targets:
            raise ValueError(f"invalid output targets: {sorted(invalid_targets)}")
        invalid_metadata = set(self.metadata) - ALLOWED_METADATA_KEYS
        if invalid_metadata:
            raise ValueError(f"invalid metadata keys: {sorted(invalid_metadata)}")

    def add_output_target(self, target: str) -> None:
        if target not in VALID_OUTPUT_TARGETS:
            raise ValueError(f"invalid output target: {target}")
        self.output_targets.add(target)

    def with_metadata(self, key: str, value: Any) -> "ChatFlowResult":
        if key not in ALLOWED_METADATA_KEYS:
            raise ValueError(f"invalid metadata key: {key}")
        self.metadata[key] = value
        return self
