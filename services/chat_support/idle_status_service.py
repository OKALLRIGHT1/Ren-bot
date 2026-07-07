from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional


class IdleStatusService:
    def __init__(
        self,
        *,
        event_emit: Callable[..., Awaitable[Any]],
        debug: Callable[[str], None],
    ) -> None:
        self.event_emit = event_emit
        self.debug = debug

    async def emit_idle_status(
        self, output_profile: Optional[Dict[str, Any]], reason: str
    ) -> None:
        live2d_enabled = True
        if isinstance(output_profile, dict):
            live2d_enabled = bool(output_profile.get("live2d_enabled", True))
        if live2d_enabled:
            await self.event_emit("state.changed", state="idle", reason=reason)
        else:
            await self.event_emit("ui.status", text="Idle")

    def presenter_output_controls_idle(
        self,
        output_profile: Optional[Dict[str, Any]],
        *,
        had_presenter_output: bool,
    ) -> bool:
        if not had_presenter_output:
            return False
        profile = output_profile if isinstance(output_profile, dict) else {}
        return bool(profile.get("speak", True)) or bool(
            profile.get("show_bubble", True)
        )

    async def emit_idle_status_when_safe(
        self,
        output_profile: Optional[Dict[str, Any]],
        *,
        reason: str,
        had_presenter_output: bool,
    ) -> None:
        if self.presenter_output_controls_idle(
            output_profile, had_presenter_output=had_presenter_output
        ):
            self.debug(
                f"跳过即时 idle ({reason})，等待 presenter/TTS/气泡 生命周期收尾"
            )
            return
        await self.emit_idle_status(output_profile, reason=reason)
