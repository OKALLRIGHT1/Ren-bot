from __future__ import annotations

from typing import Any, Dict, Optional

from services.plugin_ports.app import AppPort
from services.plugin_ports.gateway import ChatGatewayPort
from services.plugin_ports.tts import TtsPort


def resolve_gateway(ctx: Optional[Dict[str, Any]]) -> Optional[ChatGatewayPort]:
    if not isinstance(ctx, dict):
        return None
    port = ctx.get("gateway")
    if port is not None:
        return port
    chat_service = ctx.get("chat_service")
    if chat_service is None:
        return None
    return ChatGatewayPort.from_chat_service(chat_service)


def resolve_tts(ctx: Optional[Dict[str, Any]]) -> Optional[TtsPort]:
    if not isinstance(ctx, dict):
        return None
    port = ctx.get("tts")
    if port is not None:
        return port
    chat_service = ctx.get("chat_service")
    if chat_service is None:
        return None
    return TtsPort.from_chat_service(chat_service)


def resolve_app(ctx: Optional[Dict[str, Any]]) -> Optional[AppPort]:
    if not isinstance(ctx, dict):
        return None
    port = ctx.get("app")
    if port is not None:
        return port
    chat_service = ctx.get("chat_service")
    if chat_service is None:
        return None
    return AppPort.from_chat_service(chat_service)
