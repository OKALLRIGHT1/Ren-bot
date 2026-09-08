"""Shared result contract for extracted ChatService flows.

P5 (2026-09-07): ChatService.process uses this for the plain-chat path.
Do not invent a second parallel result type. Do not treat memory_writes as
a write command — recording stays with the existing output helpers.
See docs/architecture/project_slimdown.md Flow gate and
docs/superpowers/plans/2026-09-07-chat-service-p5-natural-chat.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


OUTPUT_TARGET_LOCAL = "local"
OUTPUT_TARGET_QQ = "qq"
OUTPUT_TARGET_MQTT = "mqtt"
VALID_OUTPUT_TARGETS = {OUTPUT_TARGET_LOCAL, OUTPUT_TARGET_QQ, OUTPUT_TARGET_MQTT}

STATUS_CONTINUE = "continue"
STATUS_REPLIED = "replied"
STATUS_HANDLED_SILENT = "handled_silent"
STATUS_QUEUED = "queued"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
VALID_STATUSES = {
    STATUS_CONTINUE,
    STATUS_REPLIED,
    STATUS_HANDLED_SILENT,
    STATUS_QUEUED,
    STATUS_FAILED,
    STATUS_CANCELLED,
}

ALLOWED_METADATA_KEYS = {"trace_id", "route_reason", "raw_tool_result", "timing_ms"}


@dataclass
class ChatFlowResult:
    """Unified return object for extracted chat flows.

    Keep high-frequency fields explicit. Use metadata only for low-frequency
    debug/trace fields listed in ALLOWED_METADATA_KEYS.
    Empty memory_text means "same as reply_text".
    """

    reply_text: str = ""
    memory_text: str = ""
    status: str = STATUS_CONTINUE
    emotion: Optional[str] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    memory_writes: list[dict[str, Any]] = field(default_factory=list)
    output_targets: set[str] = field(default_factory=set)
    source: str = ""
    is_search_result: bool = False
    delegate_task_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {self.status}")
        invalid_targets = set(self.output_targets) - VALID_OUTPUT_TARGETS
        if invalid_targets:
            raise ValueError(f"invalid output targets: {sorted(invalid_targets)}")
        invalid_metadata = set(self.metadata) - ALLOWED_METADATA_KEYS
        if invalid_metadata:
            raise ValueError(f"invalid metadata keys: {sorted(invalid_metadata)}")

    @property
    def handled(self) -> bool:
        return self.status != STATUS_CONTINUE

    def text_for_memory(self) -> str:
        return str(self.memory_text or self.reply_text or "")

    def add_output_target(self, target: str) -> None:
        if target not in VALID_OUTPUT_TARGETS:
            raise ValueError(f"invalid output target: {target}")
        self.output_targets.add(target)

    def with_metadata(self, key: str, value: Any) -> "ChatFlowResult":
        if key not in ALLOWED_METADATA_KEYS:
            raise ValueError(f"invalid metadata key: {key}")
        self.metadata[key] = value
        return self
