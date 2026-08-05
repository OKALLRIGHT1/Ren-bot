"""Unified action gate for high-risk side effects.

Layering (long-term):
  Plugin access_control → who may invoke the plugin
  ActionGate            → whether this concrete action may execute / needs confirm

HIGH is allowed only for local operator or qq_owner private chat.
Group chat never allows HIGH, even for owners.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from services.security.actor import (
    ActorChannel,
    ActorContext,
    ActorKind,
    resolve_actor_context,
)

_logger = logging.getLogger(__name__)


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
    action: str = ""
    actor_kind: str = ""
    channel: str = ""

    def deny_message(self) -> str:
        if self.reason:
            return f"⚠️ 操作被拒绝：{self.reason}"
        return "⚠️ 操作被拒绝"


class ActionGate:
    READ_ACTIONS = {
        "system.health_check",
        "system.list_capabilities",
        "mail.list",
        "mail.read",
        "mail.me",
        "mail.search",
        "mcp.list_tools",
        "backup.list",
    }
    # backup.create = LOW (owner private, no heavy confirm); restore = HIGH.
    LOW_ACTIONS = {
        "plugin.reload",
        "mcp.refresh",
        "backup.create",
        "system.spawn_process_trusted",  # open_app trust list
    }
    HIGH_ACTIONS = {
        "mail.send",
        "mail.reply",
        "mail.forward",
        "mail.trash",
        "system.install_dependency",
        "system.write_file",
        "system.enable_capability",
        "system.edit_config",
        "system.exec_code",
        "system.code_agent",  # external Codex/Claude CLI
        "system.spawn_process",  # open_app outside trust list
        "system.backup_restore",
        "workspace.apply_change",
    }

    # Plugin trigger → default action when plugin does not set gated_action
    PLUGIN_DEFAULT_ACTIONS = {
        "打开": "system.spawn_process_trusted",  # open_app may refine
        "open_app": "system.spawn_process_trusted",
        "backup_manager": "backup.create",
        "code": "system.exec_code",
        "execute_code": "system.exec_code",
        "code_executor": "system.exec_code",
        "code_agent": "system.code_agent",
        "workspace_ops": "system.write_file",
    }

    # Actions that skip confirmation when actor already allows HIGH
    AUTO_CONFIRM_WHEN_ALLOWED = {
        "system.spawn_process_trusted",
        "backup.create",
        "backup.list",
    }

    def evaluate(self, action: str, ctx: Optional[Dict[str, Any]] = None) -> ActionDecision:
        normalized = str(action or "").strip()
        actor = resolve_actor_context(ctx)
        confirmed = bool((ctx or {}).get("action_confirmed") or (ctx or {}).get("gate_confirmed"))

        risk = self._risk_for(normalized)
        decision = self._decide(normalized, risk, actor, confirmed)
        self._audit(decision, actor, ctx)
        return decision

    def _risk_for(self, action: str) -> ActionRisk:
        if action in self.READ_ACTIONS:
            return ActionRisk.READ
        if action in self.LOW_ACTIONS:
            return ActionRisk.LOW
        if action in self.HIGH_ACTIONS:
            return ActionRisk.HIGH
        # Unknown → HIGH (fail closed)
        return ActionRisk.HIGH

    def _decide(
        self,
        action: str,
        risk: ActionRisk,
        actor: ActorContext,
        confirmed: bool,
    ) -> ActionDecision:
        base = dict(
            action=action,
            actor_kind=actor.kind.value,
            channel=actor.channel.value,
            risk=risk,
        )

        if risk == ActionRisk.READ:
            return ActionDecision(
                allowed=True,
                requires_confirmation=False,
                reason="",
                **base,
            )

        # Group chat: no LOW/HIGH side effects for remote (including owner)
        if actor.kind != ActorKind.LOCAL and actor.channel == ActorChannel.GROUP:
            return ActionDecision(
                allowed=False,
                requires_confirmation=False,
                reason="群聊中不允许执行该操作，请私聊管理员账号",
                **base,
            )

        if risk == ActionRisk.LOW:
            if actor.kind == ActorKind.QQ_OTHER:
                return ActionDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason="仅管理员 QQ 或本地可执行该操作",
                    **base,
                )
            # local + qq_owner private (group already rejected above)
            if action in self.AUTO_CONFIRM_WHEN_ALLOWED or confirmed:
                return ActionDecision(
                    allowed=True,
                    requires_confirmation=False,
                    reason="",
                    **base,
                )
            return ActionDecision(
                allowed=False,
                requires_confirmation=True,
                reason="需要确认",
                **base,
            )

        # HIGH
        if not actor.allows_high_risk:
            if actor.kind == ActorKind.QQ_OTHER:
                reason = "仅管理员 QQ 私聊或本地可执行高风险操作"
            elif actor.channel == ActorChannel.GROUP:
                reason = "群聊中不允许执行高风险操作，请私聊管理员账号"
            else:
                reason = "当前身份不能执行高风险操作"
            return ActionDecision(
                allowed=False,
                requires_confirmation=False,
                reason=reason,
                **base,
            )

        # open_app trusted already mapped to LOW; HIGH spawn_process needs confirm
        if action in self.AUTO_CONFIRM_WHEN_ALLOWED:
            return ActionDecision(True, False, reason="", **base)

        if confirmed:
            return ActionDecision(True, False, reason="", **base)

        return ActionDecision(
            allowed=False,
            requires_confirmation=True,
            reason="需要确认后才能执行",
            **base,
        )

    def _audit(
        self,
        decision: ActionDecision,
        actor: ActorContext,
        ctx: Optional[Dict[str, Any]],
    ) -> None:
        plugin = ""
        if isinstance(ctx, dict):
            plugin = str(
                ctx.get("plugin_trigger")
                or ctx.get("gated_plugin")
                or ""
            )
        _logger.info(
            "[ActionGate] action=%s risk=%s allowed=%s confirm=%s actor=%s channel=%s plugin=%s reason=%s",
            decision.action,
            decision.risk.value,
            decision.allowed,
            decision.requires_confirmation,
            actor.kind.value,
            actor.channel.value,
            plugin,
            decision.reason or "-",
        )

    def resolve_plugin_action(
        self,
        plugin: Any,
        args: str = "",
        ctx: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Return action id for a plugin invocation, or None if no gate required."""
        if plugin is None:
            return None
        # Explicit per-call override from plugin/context
        if isinstance(ctx, dict):
            forced = str(ctx.get("gated_action") or "").strip()
            if forced:
                return forced
        resolver = getattr(plugin, "resolve_gated_action", None)
        if callable(resolver):
            try:
                value = resolver(args, ctx)
                # Empty string = explicit "no gate for this call"
                if value is not None and not str(value).strip():
                    return None
                if value:
                    return str(value).strip()
            except Exception:
                pass
        explicit = getattr(plugin, "gated_action", None)
        if explicit:
            return str(explicit).strip()
        trigger = str(
            getattr(plugin, "plugin_trigger", None)
            or getattr(plugin, "llm_command", None)
            or getattr(plugin, "name", None)
            or ""
        ).strip()
        if trigger in self.PLUGIN_DEFAULT_ACTIONS:
            return self.PLUGIN_DEFAULT_ACTIONS[trigger]
        # llm_command map
        llm_cmd = str(getattr(plugin, "llm_command", "") or "").strip()
        if llm_cmd in self.PLUGIN_DEFAULT_ACTIONS:
            return self.PLUGIN_DEFAULT_ACTIONS[llm_cmd]
        return None


# Process-wide gate used by PluginManager
_default_gate: Optional[ActionGate] = None


def get_action_gate() -> ActionGate:
    global _default_gate
    if _default_gate is None:
        _default_gate = ActionGate()
    return _default_gate
