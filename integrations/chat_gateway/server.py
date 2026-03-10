from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any, Dict, Optional

from aiohttp import WSMsgType, web

from .base import ChatGateway


class NapCatWebhookServer:
    def __init__(
        self,
        *,
        gateway: ChatGateway,
        loop: asyncio.AbstractEventLoop,
        host: str = "127.0.0.1",
        port: int = 8095,
        path: str = "/chat/napcat",
        adapter_name: str = "napcat_qq",
        access_token: str = "",
        logger: Optional[Any] = None,
    ):
        self.gateway = gateway
        self.loop = loop
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.path = self._normalize_path(path)
        self.adapter_name = str(adapter_name or "napcat_qq").strip() or "napcat_qq"
        self.access_token = str(access_token or "").strip()
        self.logger = logger

        self._thread: Optional[threading.Thread] = None
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None

        self._ws_connections: list[web.WebSocketResponse] = []
        self._pending_actions: dict[str, asyncio.Future] = {}

    @staticmethod
    def _normalize_path(path: str) -> str:
        value = str(path or "/chat/napcat").strip() or "/chat/napcat"
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/"

    def _http_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def _ws_url(self, path: Optional[str] = None) -> str:
        ws_path = self._normalize_path(path or self.path)
        suffix = "" if ws_path == "/" else ws_path
        return f"ws://{self.host}:{self.port}{suffix}"

    def _ws_paths(self) -> list[str]:
        candidates = [self.path]
        if self.path != "/":
            candidates.append("/")
            candidates.append(self.path + "/")
        deduped = []
        for item in candidates:
            normalized = self._normalize_path(item)
            if normalized not in deduped:
                deduped.append(normalized)
        if self.path != "/" and self.path + "/" not in deduped:
            deduped.append(self.path + "/")
        return deduped

    def _check_token(self, token_value: str) -> bool:
        if not self.access_token:
            return True
        token = str(token_value or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token == self.access_token

    def _request_authorized(self, request: web.Request) -> bool:
        if not self.access_token:
            return True
        candidates = [
            request.headers.get("Authorization") or "",
            request.query.get("access_token") or "",
            request.query.get("token") or "",
        ]
        return any(self._check_token(value) for value in candidates)

    async def _dispatch_payload(self, payload: Dict[str, Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(
            self.gateway.dispatch_incoming(self.adapter_name, payload or {}),
            self.loop,
        )
        return await asyncio.wrap_future(future)

    async def _handle_post(self, request: web.Request) -> web.Response:
        if not self._request_authorized(request):
            return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)
        try:
            payload = await request.json(loads=json.loads)
        except Exception as exc:
            return web.json_response({"ok": False, "reason": f"bad_json: {exc}"}, status=400)

        try:
            await self._dispatch_payload(payload if isinstance(payload, dict) else {})
        except Exception as exc:
            if self.logger:
                self.logger.error(f"NapCat HTTP dispatch failed: {exc}")
            return web.json_response({"ok": False, "reason": f"dispatch_failed: {exc}"}, status=500)
        return web.json_response({"ok": True})

    @staticmethod
    def _is_event_payload(payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return bool(payload.get("post_type") or payload.get("message_type"))

    @classmethod
    def _is_action_response(cls, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or "echo" not in payload:
            return False
        if cls._is_event_payload(payload):
            return False
        return any(key in payload for key in ("status", "retcode", "data"))

    def _pick_active_ws(self) -> Optional[web.WebSocketResponse]:
        alive = [ws for ws in self._ws_connections if not ws.closed]
        self._ws_connections = alive
        return alive[-1] if alive else None

    async def _handle_ws_message(self, payload: Dict[str, Any]) -> None:
        if self._is_action_response(payload):
            echo = str(payload.get("echo") or "").strip()
            waiter = self._pending_actions.get(echo)
            if waiter is not None and not waiter.done():
                waiter.set_result(payload)
            return
        if not self._is_event_payload(payload):
            if self.logger:
                self.logger.debug(f"[NapCatWS] ignored non-event payload keys={list(payload.keys())}")
            return
        await self._dispatch_payload(payload)

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        if not self._request_authorized(request):
            return web.json_response({"ok": False, "reason": "unauthorized"}, status=401)

        ws = web.WebSocketResponse(heartbeat=25.0, autoping=True, max_msg_size=2 * 1024 * 1024)
        await ws.prepare(request)
        self._ws_connections.append(ws)
        if self.logger:
            self.logger.info(f"NapCat reverse WS connected: {request.remote} path={request.path}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    raw = str(msg.data or "").strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception as exc:
                        if self.logger:
                            self.logger.warning(f"NapCat WS bad json: {exc}")
                        continue
                    if not isinstance(payload, dict):
                        continue
                    try:
                        await self._handle_ws_message(payload)
                    except Exception as exc:
                        if self.logger:
                            self.logger.error(f"NapCat WS dispatch failed: {exc}")
                elif msg.type == WSMsgType.ERROR:
                    if self.logger:
                        self.logger.warning(f"NapCat reverse WS error: {ws.exception()}")
        finally:
            self._ws_connections = [item for item in self._ws_connections if item is not ws and not item.closed]
            if self.logger:
                self.logger.info("NapCat reverse WS disconnected")
        return ws

    async def _call_action_in_server_loop(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Dict[str, Any]:
        ws = self._pick_active_ws()
        if ws is None:
            return {"ok": False, "transport": "websocket", "reason": "ws_not_connected", "action": action}

        echo = uuid.uuid4().hex
        waiter = self._server_loop.create_future()  # type: ignore[union-attr]
        self._pending_actions[echo] = waiter
        payload = {
            "action": str(action or "").strip(),
            "params": params or {},
            "echo": echo,
        }
        try:
            await ws.send_json(payload)
            response = await asyncio.wait_for(waiter, timeout=max(1.0, float(timeout or 8.0)))
            status = str(response.get("status") or "").strip().lower()
            retcode = int(response.get("retcode", 0) or 0)
            ok = status in {"ok", "success"} or retcode == 0
            return {
                "ok": ok,
                "transport": "websocket",
                "action": action,
                "response": response,
                "reason": "" if ok else f"retcode:{retcode}",
            }
        except Exception as exc:
            return {"ok": False, "transport": "websocket", "action": action, "reason": str(exc)}
        finally:
            self._pending_actions.pop(echo, None)

    async def call_action(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Dict[str, Any]:
        if self._server_loop is None:
            return {"ok": False, "transport": "websocket", "action": action, "reason": "ws_server_not_started"}

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is self._server_loop:
            return await self._call_action_in_server_loop(action, params=params, timeout=timeout)

        future = asyncio.run_coroutine_threadsafe(
            self._call_action_in_server_loop(action, params=params, timeout=timeout),
            self._server_loop,
        )
        try:
            return await asyncio.to_thread(future.result, max(1.0, float(timeout or 8.0)) + 1.0)
        except Exception as exc:
            return {"ok": False, "transport": "websocket", "action": action, "reason": str(exc)}

    async def _async_start(self) -> None:
        app = web.Application()
        app.router.add_post(self.path, self._handle_post)
        for ws_path in self._ws_paths():
            try:
                app.router.add_get(ws_path, self._handle_ws)
            except RuntimeError:
                pass

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        if self.logger:
            self.logger.info(
                "NapCat gateway listening on "
                f"HTTP {self._http_url()} | WS {self._ws_url(self.path)} | WS(root) {self._ws_url('/')}"
            )

    async def _async_shutdown(self) -> None:
        for waiter in list(self._pending_actions.values()):
            if not waiter.done():
                waiter.cancel()
        self._pending_actions.clear()

        for ws in list(self._ws_connections):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_connections.clear()

        if self._runner is not None:
            try:
                await self._runner.cleanup()
            finally:
                self._runner = None
                self._site = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def _worker():
            self._server_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._server_loop)
            try:
                self._server_loop.run_until_complete(self._async_start())
            except BaseException as exc:
                self._start_error = exc
                self._ready.set()
                return

            self._ready.set()
            try:
                self._server_loop.run_forever()
            finally:
                try:
                    self._server_loop.run_until_complete(self._async_shutdown())
                finally:
                    self._server_loop.close()

        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._start_error is not None:
            thread = self._thread
            self._thread = None
            if thread and thread.is_alive():
                thread.join(timeout=0.2)
            raise RuntimeError(str(self._start_error))

    def stop(self) -> None:
        if self._thread is None or self._server_loop is None:
            self._thread = None
            self._server_loop = None
            return

        loop = self._server_loop
        thread = self._thread

        try:
            stopper = asyncio.run_coroutine_threadsafe(self._async_shutdown(), loop)
            stopper.result(timeout=5.0)
        except Exception:
            pass
        finally:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
            if thread.is_alive():
                thread.join(timeout=5.0)
            self._thread = None
            self._server_loop = None
