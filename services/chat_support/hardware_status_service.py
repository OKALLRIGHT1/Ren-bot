from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from services.chat_support import output_coordinator


class HardwareStatusService:
    def __init__(
        self,
        *,
        plugin_manager_getter: Callable[[], Any],
        event_bus: Any,
        tool_result_formatter: Any,
        split_gateway_text_parts: Callable[[str], list[str]],
        emit_assistant_text: Callable[..., Awaitable[None]],
        add_memory_safe: Callable[..., Awaitable[Any]],
    ) -> None:
        self.plugin_manager_getter = plugin_manager_getter
        self.event_bus = event_bus
        self.tool_result_formatter = tool_result_formatter
        self.split_gateway_text_parts = split_gateway_text_parts
        self.emit_assistant_text = emit_assistant_text
        self.add_memory_safe = add_memory_safe

    async def try_handle_hardware_status_query(
        self,
        *,
        user_text: str,
        ctx: Dict[str, Any],
        transcript_meta: Optional[Dict[str, Any]],
        chat_log_source: str,
        output_profile: Dict[str, Any],
        memory_session_id: str = "",
    ) -> bool:
        if not self.tool_result_formatter.looks_like_hardware_status_query(user_text):
            return False

        if bool((output_profile or {}).get("live2d_enabled", True)):
            await self.event_bus.emit(
                "state.changed", state="thinking", reason="hardware_status"
            )
        else:
            await self.event_bus.emit("ui.status", text="Thinking (Tools)...")

        action = self.tool_result_formatter.hardware_monitor_action_from_query(user_text)
        command = f"[CMD: check | {action}]"
        plugin_manager = self.plugin_manager_getter()
        triggered, _clean, results, used = await plugin_manager.execute_commands(
            command,
            ctx,
            allow_tools=True,
            allowed_types={"react"},
        )
        if not triggered or not results:
            return False

        raw_status = "\n".join(str(item) for item in results if str(item).strip())
        final_text = await self.tool_result_formatter.polish_hardware_status_reply(
            user_text=user_text,
            raw_status=raw_status,
            ctx=ctx,
        )
        return await output_coordinator.emit_hardware_status_reply(
            final_text=final_text,
            user_text=user_text,
            ctx=ctx,
            transcript_meta=transcript_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            memory_session_id=memory_session_id,
            used_triggers=list(used or ["monitor"]),
            split_gateway_text_parts=self.split_gateway_text_parts,
            event_bus=self.event_bus,
            emit_assistant_text=self.emit_assistant_text,
            add_memory_safe=self.add_memory_safe,
        )
