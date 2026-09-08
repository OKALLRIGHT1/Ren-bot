from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional


class AppPort:
    """Narrow app/runtime facade: character switch, music event, roots."""

    def __init__(
        self,
        *,
        app_getter: Optional[Callable[[], Any]] = None,
        app_root_getter: Optional[Callable[[], str]] = None,
        music_handler: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._app_getter = app_getter
        self._app_root_getter = app_root_getter
        self._music_handler = music_handler

    @classmethod
    def from_chat_service(cls, chat_service: Any) -> "AppPort":
        return cls(
            app_getter=lambda: getattr(chat_service, "app", None),
            app_root_getter=lambda: str(getattr(chat_service, "app_root", "") or ""),
            music_handler=getattr(chat_service, "handle_music_event", None),
        )

    def get_app(self) -> Any:
        getter = self._app_getter
        return getter() if callable(getter) else getter

    def app_root(self) -> str:
        getter = self._app_root_getter
        if callable(getter):
            return str(getter() or "").strip()
        return str(getter or "").strip()

    def switch_character(self, character_id: str) -> bool:
        target_id = str(character_id or "").strip()
        if not target_id:
            return False
        app_ref = self.get_app()
        try:
            if app_ref and hasattr(app_ref, "switch_character_runtime"):
                app_ref.switch_character_runtime(target_id)
                return True
            from modules.character_manager import character_manager

            character_manager.set_active_character(target_id)
            return True
        except Exception:
            return False

    def emit_status(self, text: str) -> None:
        app_ref = self.get_app()
        if not app_ref or not getattr(app_ref, "event_bus", None):
            return
        try:
            asyncio.run_coroutine_threadsafe(
                app_ref.event_bus.emit("ui.status", text=str(text or "")),
                app_ref.loop,
            )
        except Exception:
            pass

    def handle_music_event(self, *, title: str, artist: str = "") -> bool:
        handler = self._music_handler
        if not callable(handler):
            return False
        try:
            asyncio.create_task(handler(title=title, artist=artist))
            return True
        except Exception:
            return False
