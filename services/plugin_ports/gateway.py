from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional


class ChatGatewayPort:
    """Narrow QQ/gateway send facade. Depends on gateway + send callables, not ChatService."""

    def __init__(
        self,
        *,
        gateway_getter: Optional[Callable[[], Any]] = None,
        send_text: Optional[Callable[..., Awaitable[Any]]] = None,
        send_image: Optional[Callable[..., Awaitable[Any]]] = None,
        send_forward: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
        self._gateway_getter = gateway_getter
        self._send_text = send_text
        self._send_image = send_image
        self._send_forward = send_forward

    @classmethod
    def from_chat_service(cls, chat_service: Any) -> "ChatGatewayPort":
        """Test/compat fallback used by resolve_* when ctx has no port."""
        return cls(
            gateway_getter=lambda: getattr(chat_service, "chat_gateway", None),
            send_text=getattr(chat_service, "_send_gateway_reply", None),
            send_image=getattr(chat_service, "_send_gateway_image_reply", None),
            send_forward=getattr(chat_service, "_send_gateway_forward_reply", None),
        )

    def _gateway(self) -> Any:
        getter = self._gateway_getter
        return getter() if callable(getter) else getter

    def available(self) -> bool:
        return bool(self._gateway())

    async def send_text(
        self,
        text: str,
        ctx: Optional[Dict[str, Any]] = None,
        *,
        emotion: str = "neutral",
    ) -> None:
        fn = self._send_text
        if not callable(fn):
            raise RuntimeError("gateway_unavailable")
        await fn(text, ctx or {}, emotion=emotion)

    async def send_image(
        self,
        path: str,
        ctx: Optional[Dict[str, Any]] = None,
        *,
        caption: str = "",
    ) -> Any:
        fn = self._send_image
        if not callable(fn):
            raise RuntimeError("gateway_image_unavailable")
        return await fn(path, ctx or {}, caption=caption)

    async def send_forward(
        self,
        messages: list,
        ctx: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        fn = self._send_forward
        if not callable(fn):
            raise RuntimeError("gateway_forward_unavailable")
        return await fn(messages, ctx or {}, **kwargs)

    async def send_private_image(self, path: str, user_id: str, *, caption: str = "") -> Any:
        gateway = self._gateway()
        if gateway is None:
            raise RuntimeError("gateway_unavailable")
        uid = str(user_id or "").strip()
        if not uid:
            return {"ok": False, "reason": "empty_user_id"}
        return await gateway.send_image(
            "napcat_qq",
            f"private:{uid}",
            path,
            caption=caption,
            source="qq_gateway",
        )

    async def delete_message(self, message_id: str) -> Any:
        gateway = self._gateway()
        adapters = getattr(gateway, "adapters", None) or {}
        adapter = adapters.get("napcat_qq") if isinstance(adapters, dict) else None
        delete = getattr(adapter, "delete_message", None)
        if not callable(delete):
            return {"ok": False, "reason": "delete_unavailable"}
        return await delete(message_id)

    async def fetch_qq_message(
        self,
        session_id: str,
        message_id: str,
        *,
        timeout: float = 10,
    ) -> Dict[str, Any]:
        gateway = self._gateway()
        adapters = getattr(gateway, "adapters", None) or {}
        adapter = adapters.get("napcat_qq") if isinstance(adapters, dict) else None
        fetch = getattr(adapter, "fetch_message_by_id", None)
        if not callable(fetch):
            return {"ok": False, "error": "napcat_unavailable"}
        result = await fetch(session_id, message_id, timeout=timeout)
        return result if isinstance(result, dict) else {"ok": False, "item": result}
