from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class ActionRisk(str, Enum):
    READ = "read"
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class ActionDecision:
    allowed: bool
    requires_confirmation: bool
    risk: ActionRisk
    reason: str = ""


class ActionGate:
    READ_ACTIONS = {
        "system.health_check",
        "system.list_capabilities",
        "mail.list",
        "mail.read",
        "mcp.list_tools",
    }
    LOW_ACTIONS = {"plugin.reload", "mcp.refresh"}
    HIGH_ACTIONS = {
        "mail.send",
        "mail.reply",
        "mail.forward",
        "mail.trash",
        "system.install_dependency",
        "system.write_file",
        "system.enable_capability",
        "system.edit_config",
    }
    REMOTE_SOURCES = {"qq_gateway", "napcat_qq", "qq_private", "qq_group"}

    def evaluate(self, action: str, ctx: Dict[str, Any]) -> ActionDecision:
        normalized = str(action or "").strip()
        source = str((ctx or {}).get("source") or "text_input").strip().lower()
        is_remote = source in self.REMOTE_SOURCES

        if normalized in self.READ_ACTIONS:
            return ActionDecision(True, False, ActionRisk.READ)
        if normalized in self.LOW_ACTIONS:
            if is_remote:
                return ActionDecision(False, False, ActionRisk.LOW, "远程来源不能执行维护操作")
            return ActionDecision(False, True, ActionRisk.LOW, "需要确认")
        if normalized in self.HIGH_ACTIONS:
            if is_remote:
                return ActionDecision(False, False, ActionRisk.HIGH, "远程来源不能执行高风险操作")
            return ActionDecision(False, True, ActionRisk.HIGH, "需要确认")
        return ActionDecision(False, True, ActionRisk.HIGH, "未知操作默认需要确认")
