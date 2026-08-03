from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Sequence


@dataclass(frozen=True)
class Live2DDelivery:
    command_id: str
    message: dict[str, Any]


@dataclass
class Live2DDeliveryResult:
    delivered: int
    errors: list[str] = field(default_factory=list)


class Live2DDeliveryError(RuntimeError):
    def __init__(self, message: str, *, delivered: int = 0, errors: Sequence[str] | None = None):
        super().__init__(message)
        self.delivered = int(delivered)
        self.errors = list(errors or [])


class Live2DTransport(Protocol):
    name: str

    async def deliver(self, delivery: Live2DDelivery) -> None: ...


class Live2DTransportBus:
    def __init__(
        self,
        transports: Sequence[Live2DTransport],
        *,
        id_factory: Callable[[], str] | None = None,
        logger: Any | None = None,
    ):
        self._transports = list(transports or [])
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._logger = logger

    async def send(self, message: dict[str, Any]) -> Live2DDeliveryResult:
        if not self._transports:
            raise Live2DDeliveryError("no Live2D transports configured", delivered=0, errors=[])
        payload = dict(message or {})
        delivery = Live2DDelivery(
            command_id=str(self._id_factory() or "").strip() or uuid.uuid4().hex,
            message=payload,
        )

        async def _run(transport: Live2DTransport) -> tuple[str, Exception | None]:
            try:
                await transport.deliver(delivery)
                return transport.name, None
            except Exception as exc:  # noqa: BLE001 - aggregate transport failures
                return transport.name, exc

        results = await asyncio.gather(
            *[_run(transport) for transport in self._transports],
            return_exceptions=False,
        )
        errors: list[str] = []
        delivered = 0
        for name, error in results:
            if error is None:
                delivered += 1
                continue
            detail = f"{name}: {error}"
            errors.append(detail)
            if self._logger is not None:
                try:
                    self._logger.warning(f"Live2D transport failed: {detail}")
                except Exception:
                    pass

        if delivered <= 0:
            raise Live2DDeliveryError(
                "all Live2D transports failed: " + "; ".join(errors),
                delivered=0,
                errors=errors,
            )
        return Live2DDeliveryResult(delivered=delivered, errors=errors)


class LegacyLocalWebSocketTransport:
    name = "legacy_local_ws"

    def __init__(self, *, max_retries: int = 2):
        self.max_retries = int(max_retries)

    async def deliver(self, delivery: Live2DDelivery) -> None:
        # Keep the existing connection-pool semantics inside modules.live2d.
        from modules import live2d as live2d_mod

        payload = dict(delivery.message or {})
        payload["command_id"] = delivery.command_id

        retry_count = 0
        max_retries = self.max_retries
        while retry_count <= max_retries:
            try:
                ws = await live2d_mod._connection_pool.get_connection()
                async with live2d_mod._connection_pool._send_lock:
                    await asyncio.wait_for(
                        ws.send(json.dumps(payload, ensure_ascii=False)),
                        timeout=live2d_mod.SEND_TIMEOUT,
                    )
                return
            except Exception as exc:
                retry_count += 1
                await live2d_mod._connection_pool.mark_broken()
                if retry_count <= max_retries:
                    await asyncio.sleep(0.1)
                    continue
                raise RuntimeError(f"legacy local websocket send failed: {exc}") from exc


class GuiWebSocketTransport:
    name = "gui_ws"

    def __init__(self, gui_ws_server: Any, media_registry: Any | None = None):
        self._gui_ws_server = gui_ws_server
        self._media_registry = media_registry

    def _attach_media_ticket(self, message: dict[str, Any]) -> dict[str, Any]:
        payload = dict(message or {})
        try:
            msg = int(payload.get("msg") or 0)
        except (TypeError, ValueError):
            msg = 0
        if msg not in {13500, 13600}:
            return payload
        data = payload.get("data")
        if not isinstance(data, dict):
            return payload
        sound = str(data.get("sound") or "").strip()
        if not sound or sound.startswith(("http://", "https://", "asset://", "tauri://")):
            return payload
        from pathlib import Path

        from integrations.gui_media import MediaTicketError, guess_audio_media_type

        path = Path(sound).expanduser()
        if not path.is_file():
            return payload
        media_type = guess_audio_media_type(path)
        if not media_type or self._media_registry is None:
            return payload
        try:
            ticket = self._media_registry.register(path, media_type=media_type)
        except MediaTicketError:
            return payload
        except Exception:
            return payload
        next_data = dict(data)
        # Remote clients must fetch via ticket; strip host-local absolute paths.
        next_data.pop("sound", None)
        payload["data"] = next_data
        payload["media"] = {
            "ticket": ticket,
            "content_type": media_type,
        }
        return payload

    async def deliver(self, delivery: Live2DDelivery) -> None:
        if self._gui_ws_server is None:
            raise RuntimeError("gui websocket server is not available")
        from integrations.gui_protocol import build_live2d_envelope

        message = self._attach_media_ticket(delivery.message)
        envelope = build_live2d_envelope(delivery.command_id, message)
        if isinstance(message.get("media"), dict):
            envelope["media"] = dict(message["media"])
        emit_capability = getattr(self._gui_ws_server, "emit_capability", None)
        if callable(emit_capability):
            delivered = emit_capability("live2d.protocol.v1", envelope)
            if delivered is False:
                raise RuntimeError("no capable GUI client is connected")
            return
        # Fallback for unexpected servers without capability routing.
        emit = getattr(self._gui_ws_server, "emit", None)
        if callable(emit):
            emit(envelope)
            return
        raise RuntimeError("gui websocket server cannot emit live2d protocol")


_ACTIVE_BUS: Live2DTransportBus | None = None


def configure_live2d_transport(bus: Live2DTransportBus | None) -> None:
    global _ACTIVE_BUS
    _ACTIVE_BUS = bus


def get_live2d_transport() -> Live2DTransportBus:
    global _ACTIVE_BUS
    if _ACTIVE_BUS is None:
        _ACTIVE_BUS = Live2DTransportBus([LegacyLocalWebSocketTransport()])
    return _ACTIVE_BUS


async def send_live2d_message(message: dict[str, Any]) -> Live2DDeliveryResult:
    return await get_live2d_transport().send(message)
