from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from services.agent_tool_providers import McpToolProvider, PluginToolProvider
from services.security.pending_confirm import (
    PendingConfirmAction,
    get_pending_confirm_store,
)

# Back-compat alias for older imports/tests
PendingAgentAction = PendingConfirmAction


@dataclass(frozen=True)
class AgentDirectResult:
    handled: bool
    reply: Any = None
    meta: Optional[Dict[str, Any]] = None


class AgentRuntime:
    def __init__(self, plugin_manager: Any, mcp_bridge_getter=None, chat_service: Any = None):
        self.plugin_manager = plugin_manager
        self.chat_service = chat_service
        self._pending_store = get_pending_confirm_store()
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
        if self._is_cancel(text) and self._pending_store.peek():
            self._pending_store.clear()
            return AgentDirectResult(
                handled=True,
                reply="已取消这次操作。",
                meta={"path": "agent_confirm"},
            )
        if self._is_confirm(text) and self._pending_store.peek():
            pending = self._pending_store.take()
            if pending is None or pending.expired:
                return AgentDirectResult(
                    handled=True,
                    reply="这次操作的确认已经过期，请重新发起。",
                    meta={"path": "agent_confirm", "trigger": getattr(pending, "trigger", "")},
                )
            reply = await self._execute_confirmed(pending, ctx)
            return AgentDirectResult(
                handled=True,
                reply=reply,
                meta={"path": "agent_confirm", "trigger": pending.trigger},
            )

        handled, reply = await self.plugin_manager.execute_direct_commands(text, ctx)
        normalized = self._normalize_confirmation_reply(reply)
        if normalized is not None:
            return await self._handle_confirmation_required(normalized, ctx)
        return AgentDirectResult(
            handled=bool(handled),
            reply=reply,
            meta={"path": "direct"},
        )

    async def _handle_confirmation_required(
        self, normalized: Dict[str, Any], ctx: Dict[str, Any]
    ) -> AgentDirectResult:
        trigger = str(normalized.get("trigger") or "").strip()
        summary = str(normalized.get("summary") or "需要确认这个操作。").strip()
        payload = (
            normalized.get("payload")
            if isinstance(normalized.get("payload"), dict)
            else {}
        )
        try:
            expires_in = max(30, int(normalized.get("expires_in") or 300))
        except (TypeError, ValueError):
            expires_in = 300

        # Local UI popup: confirm in-place without requiring chat “确认”
        # Run off the event loop so a modal dialog does not freeze asyncio.
        import asyncio

        popup_result = await asyncio.to_thread(
            self._try_local_popup_confirm, trigger, summary, ctx
        )
        if popup_result is True:
            pending = PendingConfirmAction(
                trigger=trigger,
                summary=summary,
                payload=payload,
                expires_at=__import__("time").monotonic() + expires_in,
            )
            reply = await self._execute_confirmed(pending, ctx)
            return AgentDirectResult(
                handled=True,
                reply=reply,
                meta={"path": "agent_confirm", "trigger": trigger, "via": "local_popup"},
            )
        if popup_result is False:
            self._pending_store.clear()
            return AgentDirectResult(
                handled=True,
                reply="已取消这次操作。",
                meta={"path": "agent_confirm", "trigger": trigger, "via": "local_popup"},
            )

        self._pending_store.set(
            trigger=trigger,
            summary=summary,
            payload=payload,
            expires_in=expires_in,
        )
        return AgentDirectResult(
            handled=True,
            reply=summary + "\n确认请回复“确认”，取消请回复“取消”。",
            meta={"path": "agent_confirm", "trigger": trigger},
        )

    def _try_local_popup_confirm(
        self, trigger: str, summary: str, ctx: Dict[str, Any]
    ) -> Optional[bool]:
        """True=confirmed, False=cancelled, None=no popup / remote."""
        try:
            from services.security.actor import ActorKind, resolve_actor_context

            if resolve_actor_context(ctx).kind != ActorKind.LOCAL:
                return None
        except Exception:
            source = str((ctx or {}).get("source") or "").strip().lower()
            if source not in {"", "text_input", "tauri_gui", "local", "gui", "qt_gui"}:
                return None

        handler = getattr(self.plugin_manager, "local_confirm_handler", None)
        if not callable(handler):
            # Also allow chat_service / app bridge
            chat = self.chat_service
            handler = getattr(chat, "local_confirm_handler", None) if chat else None
        if not callable(handler):
            return None
        try:
            result = handler(f"确认：{trigger or '操作'}", summary)
        except Exception:
            return None
        if result is True or str(result or "").strip().lower() in {
            "confirm",
            "yes",
            "ok",
            "true",
            "1",
            "确认",
        }:
            return True
        if result is False or str(result or "").strip().lower() in {
            "cancel",
            "no",
            "false",
            "0",
            "取消",
        }:
            return False
        return None

    async def _execute_confirmed(
        self, pending: PendingConfirmAction, ctx: Dict[str, Any]
    ) -> Any:
        payload = dict(pending.payload or {})
        runtime_ctx = dict(ctx or {})
        runtime_ctx["action_confirmed"] = True
        runtime_ctx["gate_confirmed"] = True

        # Gate re-run path: PluginManager blocked HIGH and stored args for replay
        if str(payload.get("mode") or "") == "gate_rerun":
            return await self._rerun_gated_plugin(pending.trigger, payload, runtime_ctx)

        plugin = getattr(self.plugin_manager, "plugins", {}).get(pending.trigger)
        if plugin is None:
            return "找不到待确认的工具，操作已取消。"

        confirm = getattr(plugin, "confirm_agent_action", None)
        if callable(confirm):
            return await confirm(payload, runtime_ctx)

        # Fallback: re-run plugin with confirmed flag when payload carries args
        if payload.get("args") is not None:
            return await self._rerun_gated_plugin(pending.trigger, payload, runtime_ctx)

        return "这个工具没有提供确认执行入口，操作已取消。"

    async def _rerun_gated_plugin(
        self,
        trigger: str,
        payload: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> Any:
        plugin = getattr(self.plugin_manager, "plugins", {}).get(trigger)
        if plugin is None:
            return "找不到待确认的工具，操作已取消。"
        args = str(payload.get("args") if payload.get("args") is not None else "")
        gated_action = str(payload.get("gated_action") or "").strip()
        if gated_action:
            ctx["gated_action"] = gated_action
        runner = getattr(self.plugin_manager, "_run_with_timeout", None)
        if callable(runner):
            return await runner(plugin, args, ctx)
        return await plugin.run(args, ctx)

    @staticmethod
    def _normalize_confirmation_reply(reply: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(reply, dict):
            return None
        if str(reply.get("__agent_result__") or "") != "confirmation_required":
            return None
        return reply

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
