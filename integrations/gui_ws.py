from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import parse_qs

import websockets


class GuiWebSocketServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8096,
        path: str = "/gui",
        logger: Optional[Any] = None,
        access_token: str = "",
    ):
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.path = self._normalize_path(path)
        self.logger = logger
        self.access_token = str(access_token or "").strip()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._clients: set[websockets.WebSocketServerProtocol] = set()
        self._message_handler: Optional[
            Callable[[Dict[str, Any], websockets.WebSocketServerProtocol], Awaitable[None]]
        ] = None

    @staticmethod
    def _normalize_path(path: str) -> str:
        value = str(path or "/gui").strip() or "/gui"
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/"

    def _path_allowed(self, raw_path: str) -> bool:
        if not raw_path:
            return self.path == "/"
        path = str(raw_path).split("?", 1)[0]
        normalized = self._normalize_path(path)
        if normalized == self.path:
            return True
        if normalized == (self.path + "/"):
            return True
        return False

    def _extract_token(self, ws: websockets.WebSocketServerProtocol, raw_path: str) -> str:
        try:
            headers = getattr(ws, "request_headers", None)
            if not headers:
                request = getattr(ws, "request", None)
                headers = getattr(request, "headers", None)
            headers = headers or {}
            header_token = str(headers.get("X-GUI-Token") or "").strip()
            if header_token:
                return header_token
            auth = str(headers.get("Authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
        except Exception:
            pass
        query = ""
        if "?" in str(raw_path or ""):
            query = str(raw_path).split("?", 1)[1]
        values = parse_qs(query)
        token_values = values.get("token") or []
        return str(token_values[0] if token_values else "").strip()

    def _connection_path(
        self, ws: websockets.WebSocketServerProtocol, raw_path: str | None
    ) -> str:
        if raw_path:
            return str(raw_path)
        legacy_path = str(getattr(ws, "path", "") or "")
        if legacy_path:
            return legacy_path
        request = getattr(ws, "request", None)
        return str(getattr(request, "path", "") or self.path)

    def _authorized(self, ws: websockets.WebSocketServerProtocol, raw_path: str) -> bool:
        if not self.access_token:
            return False
        provided = self._extract_token(ws, raw_path)
        return bool(provided) and secrets.compare_digest(provided, self.access_token)

    def set_message_handler(
        self,
        handler: Callable[[Dict[str, Any], websockets.WebSocketServerProtocol], Awaitable[None]],
    ) -> None:
        self._message_handler = handler

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._server is not None:
            return
        self._loop = loop
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=20.0,
            ping_timeout=20.0,
            max_size=2 * 1024 * 1024,
        )
        if self.logger:
            self.logger.info(f"GUI WS listening on ws://{self.host}:{self.port}{self.path}")

    async def shutdown(self) -> None:
        if self._server is None:
            return
        for ws in list(self._clients):
            try:
                await ws.close(code=1001, reason="server_shutdown")
            except Exception:
                pass
        self._clients.clear()

        try:
            self._server.close()
            await self._server.wait_closed()
        except Exception:
            pass
        self._server = None

    def stop(self) -> None:
        if self._loop is None or self._server is None:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            self._loop.create_task(self.shutdown())
        else:
            asyncio.run_coroutine_threadsafe(self.shutdown(), self._loop)

    async def send(self, ws: websockets.WebSocketServerProtocol, payload: Dict[str, Any]) -> None:
        if ws is None:
            return
        try:
            await ws.send(json.dumps(payload or {}, ensure_ascii=False))
        except Exception:
            try:
                self._clients.discard(ws)
            except Exception:
                pass

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload or {}, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send(message)
            except Exception:
                try:
                    self._clients.discard(ws)
                except Exception:
                    pass

    def emit(self, payload: Dict[str, Any]) -> None:
        if self._loop is None or self._server is None:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            self._loop.create_task(self.broadcast(payload))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), self._loop)

    async def _handle_client(self, ws: websockets.WebSocketServerProtocol, path: str | None = None) -> None:
        path = self._connection_path(ws, path)
        if not self._path_allowed(path):
            try:
                await ws.close(code=1008, reason="invalid_path")
            except Exception:
                pass
            return
        if not self._authorized(ws, path):
            try:
                await ws.close(code=1008, reason="unauthorized")
            except Exception:
                pass
            return

        self._clients.add(ws)
        if self.logger:
            self.logger.info(f"GUI WS connected: {getattr(ws, 'remote_address', None)} path={path}")

        try:
            async for raw in ws:
                if not raw:
                    continue
                if not isinstance(raw, str):
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                if self._message_handler is None:
                    continue
                try:
                    result = self._message_handler(payload, ws)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    if self.logger:
                        self.logger.warning(f"GUI WS message handler error: {exc}")
        finally:
            self._clients.discard(ws)
            if self.logger:
                self.logger.info("GUI WS disconnected")
