from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from services.agent_tool_providers import McpToolProvider, PluginToolProvider


@dataclass(frozen=True)
class AgentDirectResult:
    handled: bool
    reply: Any = None
    meta: Optional[Dict[str, Any]] = None


@dataclass
class PendingAgentAction:
    trigger: str
    summary: str
    payload: Dict[str, Any]
    expires_at: float


class AgentRuntime:
    def __init__(self, plugin_manager: Any, mcp_bridge_getter=None, chat_service: Any = None):
        self.plugin_manager = plugin_manager
        self.chat_service = chat_service
        self._pending_action: Optional[PendingAgentAction] = None
        self._tool_providers = [PluginToolProvider(plugin_manager)]
        if mcp_bridge_getter is not None:
            self._tool_providers.append(McpToolProvider(mcp_bridge_getter))

    def list_tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for provider in self._tool_providers:
            rows.extend(provider.list_tools())
        return rows

    async def run_steps(
        self,
        text: str,
        ctx: Dict[str, Any],
        *,
        planner: Callable[[Dict[str, Any]], Any],
        max_steps: int = 3,
    ) -> AgentDirectResult:
        observations: list[dict[str, Any]] = []
        steps = max(1, min(5, int(max_steps or 3)))

        for _ in range(steps):
            state = {
                "input": text,
                "context": dict(ctx or {}),
                "observations": list(observations),
            }
            decision = planner(state)
            if hasattr(decision, "__await__"):
                decision = await decision
            if not isinstance(decision, dict):
                return AgentDirectResult(
                    handled=False,
                    reply=None,
                    meta={"path": "agent_steps", "reason": "invalid_planner_decision"},
                )

            final = str(decision.get("final") or "").strip()
            if final:
                return AgentDirectResult(
                    handled=True,
                    reply=final,
                    meta={"path": "agent_steps", "steps": len(observations)},
                )

            tool_text = str(decision.get("tool_text") or "").strip()
            if not tool_text:
                return AgentDirectResult(
                    handled=False,
                    reply=None,
                    meta={"path": "agent_steps", "reason": "empty_tool_text"},
                )

            result = await self.handle_direct_text(tool_text, ctx)
            observations.append(
                {
                    "tool_text": tool_text,
                    "handled": bool(result.handled),
                    "reply": result.reply,
                    "meta": result.meta or {},
                }
            )
            if not result.handled:
                return AgentDirectResult(
                    handled=False,
                    reply=None,
                    meta={
                        "path": "agent_steps",
                        "reason": "tool_unhandled",
                        "steps": len(observations),
                    },
                )
            if (result.meta or {}).get("path") == "agent_confirm":
                return result

        return AgentDirectResult(
            handled=True,
            reply="Agent 已达到最大步骤数，已停止。请缩小任务或继续补充指令。",
            meta={"path": "agent_steps", "reason": "max_steps", "steps": len(observations)},
        )

    async def handle_direct_text(self, text: str, ctx: Dict[str, Any]) -> AgentDirectResult:
        if self._is_cancel(text) and self._pending_action:
            self._pending_action = None
            return AgentDirectResult(
                handled=True,
                reply="已取消这次操作。",
                meta={"path": "agent_confirm"},
            )
        if self._is_confirm(text) and self._pending_action:
            pending = self._pending_action
            self._pending_action = None
            if pending.expires_at < time.monotonic():
                return AgentDirectResult(
                    handled=True,
                    reply="这次操作的确认已经过期，请重新发起。",
                    meta={"path": "agent_confirm", "trigger": pending.trigger},
                )
            plugin = getattr(self.plugin_manager, "plugins", {}).get(pending.trigger)
            confirm = getattr(plugin, "confirm_agent_action", None)
            if not callable(confirm):
                return AgentDirectResult(
                    handled=True,
                    reply="这个工具没有提供确认执行入口，操作已取消。",
                    meta={"path": "agent_confirm", "trigger": pending.trigger},
                )
            reply = await confirm(pending.payload, ctx)
            return AgentDirectResult(
                handled=True,
                reply=reply,
                meta={"path": "agent_confirm", "trigger": pending.trigger},
            )

        handled, reply = await self.plugin_manager.execute_direct_commands(text, ctx)
        if (
            isinstance(reply, dict)
            and str(reply.get("__agent_result__") or "") == "confirmation_required"
        ):
            trigger = str(reply.get("trigger") or "").strip()
            summary = str(reply.get("summary") or "需要确认这个操作。").strip()
            payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
            try:
                expires_in = max(30, int(reply.get("expires_in") or 300))
            except (TypeError, ValueError):
                expires_in = 300
            self._pending_action = PendingAgentAction(
                trigger=trigger,
                summary=summary,
                payload=payload,
                expires_at=time.monotonic() + expires_in,
            )
            return AgentDirectResult(
                handled=True,
                reply=summary + "\n确认请回复“确认”，取消请回复“取消”。",
                meta={"path": "agent_confirm", "trigger": trigger},
            )
        return AgentDirectResult(
            handled=bool(handled),
            reply=reply,
            meta={"path": "direct"},
        )

    def _is_confirm(self, text: str) -> bool:
        return str(text or "").strip().lower() in {
            "确认",
            "同意",
            "执行",
            "发送",
            "ok",
            "yes",
            "confirm",
        }

    def _is_cancel(self, text: str) -> bool:
        return str(text or "").strip().lower() in {
            "取消",
            "算了",
            "不要",
            "不要发",
            "cancel",
            "no",
        }
