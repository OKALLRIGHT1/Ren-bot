from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass(slots=True)
class ChatMessageEvent:
    source: str
    channel: str
    user_id: str
    session_id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseChatAdapter:
    name = "base"

    def normalize_event(self, payload: Dict[str, Any]) -> Optional[ChatMessageEvent]:
        raise NotImplementedError

    async def send_text(self, session_id: str, text: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def send_voice(self, session_id: str, voice_path: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def send_image(self, session_id: str, image_path: str, **kwargs: Any) -> Any:
        raise NotImplementedError


class ChatGateway:
    def __init__(self):
        self.adapters: Dict[str, BaseChatAdapter] = {}
        self._message_handlers: List[Callable[[ChatMessageEvent], Awaitable[None]]] = []

    def register_adapter(self, adapter: BaseChatAdapter) -> None:
        self.adapters[adapter.name] = adapter

    def on_message(self, handler: Callable[[ChatMessageEvent], Awaitable[None]]) -> None:
        self._message_handlers.append(handler)

    async def dispatch_incoming(self, adapter_name: str, payload: Dict[str, Any]) -> Optional[ChatMessageEvent]:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        event = adapter.normalize_event(payload)
        if not event:
            return None
        for handler in self._message_handlers:
            await handler(event)
        return event

    async def send_text(self, adapter_name: str, session_id: str, text: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await adapter.send_text(session_id, text, **kwargs)

    async def send_voice(self, adapter_name: str, session_id: str, voice_path: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await adapter.send_voice(session_id, voice_path, **kwargs)

    async def send_image(self, adapter_name: str, session_id: str, image_path: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await adapter.send_image(session_id, image_path, **kwargs)
