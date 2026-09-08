from __future__ import annotations

from typing import Any, Callable, Optional


class TtsPort:
    """Narrow TTS/presenter facade for plugins."""

    def __init__(self, *, presenter_getter: Optional[Callable[[], Any]] = None) -> None:
        self._presenter_getter = presenter_getter

    @classmethod
    def from_chat_service(cls, chat_service: Any) -> "TtsPort":
        return cls(presenter_getter=lambda: getattr(chat_service, "presenter", None))

    def set_enabled(self, enabled: bool) -> bool:
        getter = self._presenter_getter
        presenter = getter() if callable(getter) else getter
        setter = getattr(presenter, "set_tts_enabled", None)
        if not callable(setter):
            return False
        setter(bool(enabled))
        return True
