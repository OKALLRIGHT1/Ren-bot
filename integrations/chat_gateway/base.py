from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .components import component, components_to_dicts
from .tracking import MessageDeduplicator, OutboundTracker, is_send_success


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

    async def send_file(self, session_id: str, file_path: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def send_share(self, session_id: str, url: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def fetch_recent_history(self, session_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError


class ChatGateway:
    def __init__(self):
        self.adapters: Dict[str, BaseChatAdapter] = {}
        self._message_handlers: List[Callable[[ChatMessageEvent], Awaitable[None]]] = []
        self.deduplicator = MessageDeduplicator()
        self.outbound_tracker = OutboundTracker()

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
        if self.deduplicator.is_duplicate(self._dedupe_key(adapter_name, event)):
            return None
        for handler in self._message_handlers:
            await handler(event)
        return event

    def _dedupe_key(self, adapter_name: str, event: ChatMessageEvent) -> str:
        meta = event.metadata if isinstance(event.metadata, dict) else {}
        message_id = str(meta.get("message_id") or "").strip()
        if message_id:
            return f"{adapter_name}:{event.session_id}:message_id:{message_id}"
        text_key = " ".join(str(event.text or "").split())[:240]
        return f"{adapter_name}:{event.session_id}:{event.user_id}:text:{text_key}"

    async def _track_send(
        self,
        *,
        adapter_name: str,
        session_id: str,
        kind: str,
        payload_preview: str,
        send_call: Callable[[], Awaitable[Any]],
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        record = self.outbound_tracker.begin(
            adapter=adapter_name,
            session_id=session_id,
            kind=kind,
            payload_preview=payload_preview,
            components=components,
        )
        try:
            result = await send_call()
        except Exception as exc:
            self.outbound_tracker.finish(record.id, ok=False, error=str(exc))
            raise
        if isinstance(result, dict):
            result.setdefault("outbound_id", record.id)
        self.outbound_tracker.finish(
            record.id,
            ok=is_send_success(result),
            result=result if isinstance(result, dict) else {"raw": result},
        )
        return result

    async def send_text(self, adapter_name: str, session_id: str, text: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await self._track_send(
            adapter_name=adapter_name,
            session_id=session_id,
            kind="text",
            payload_preview=text,
            components=components_to_dicts([component("text", str(text or ""), {})]),
            send_call=lambda: adapter.send_text(session_id, text, **kwargs),
        )

    async def send_voice(self, adapter_name: str, session_id: str, voice_path: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await self._track_send(
            adapter_name=adapter_name,
            session_id=session_id,
            kind="voice",
            payload_preview=voice_path,
            components=components_to_dicts(
                [component("voice", "[语音]", {"path": str(voice_path or "")})]
            ),
            send_call=lambda: adapter.send_voice(session_id, voice_path, **kwargs),
        )

    async def send_image(self, adapter_name: str, session_id: str, image_path: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await self._track_send(
            adapter_name=adapter_name,
            session_id=session_id,
            kind="image",
            payload_preview=image_path,
            components=components_to_dicts(
                [component("image", "[图片]", {"path": str(image_path or "")})]
            ),
            send_call=lambda: adapter.send_image(session_id, image_path, **kwargs),
        )

    async def send_file(self, adapter_name: str, session_id: str, file_path: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await self._track_send(
            adapter_name=adapter_name,
            session_id=session_id,
            kind="file",
            payload_preview=file_path,
            components=components_to_dicts(
                [component("file", "[文件]", {"path": str(file_path or "")})]
            ),
            send_call=lambda: adapter.send_file(session_id, file_path, **kwargs),
        )

    async def send_share(self, adapter_name: str, session_id: str, url: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await self._track_send(
            adapter_name=adapter_name,
            session_id=session_id,
            kind="share",
            payload_preview=url,
            components=components_to_dicts(
                [component("share", str(url or ""), {"url": str(url or "")})]
            ),
            send_call=lambda: adapter.send_share(session_id, url, **kwargs),
        )

    async def fetch_recent_history(self, adapter_name: str, session_id: str, **kwargs: Any) -> Any:
        adapter = self.adapters.get(adapter_name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {adapter_name}")
        return await adapter.fetch_recent_history(session_id, **kwargs)
