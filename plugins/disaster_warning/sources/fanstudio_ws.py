from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

AUTH_TYPES = {"auth_success", "auth_ok", "authenticated"}


def redact_log(text: Any) -> str:
    raw = str(text or "")
    if "sk-" in raw:
        return "[REDACTED]"
    return raw


def build_auth_message(app_id: str, api_key: str) -> dict:
    return {
        "type": "auth",
        "appId": str(app_id or "").strip(),
        "key": str(api_key or "").strip(),
    }


class FanStudioClient:
    def __init__(
        self,
        *,
        ws_url: str,
        ws_backup_url: str,
        app_id: str,
        api_key: str,
        on_message: Callable[[dict], Awaitable[None]],
        on_auth_success: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.ws_url = ws_url
        self.ws_backup_url = ws_backup_url
        self.app_id = app_id
        self.api_key = api_key
        self.on_message = on_message
        self.on_auth_success = on_auth_success
        self._stop = asyncio.Event()
        self._ws = None
        self.connect_attempts = 0

    def request_stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set()

    async def close_socket(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            pass

    async def run_forever(self) -> None:
        delay = 1.0
        fail_streak = 0
        use_backup = False
        while not self._stop.is_set():
            url = self.ws_backup_url if use_backup else self.ws_url
            self.connect_attempts += 1
            try:
                await self._run_once(url)
                delay = 1.0
                fail_streak = 0
                use_backup = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DisasterWarning] ws error: {redact_log(exc)!r}")
                fail_streak += 1
                if fail_streak >= 3 and self.ws_backup_url:
                    use_backup = True
                    fail_streak = 0
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)
            finally:
                await self.close_socket()

    async def _run_once(self, url: str) -> None:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(url, heartbeat=30.0) as ws:
                self._ws = ws
                await ws.send_json(build_auth_message(self.app_id, self.api_key))
                async for msg in ws:
                    if self._stop.is_set():
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._dispatch_text(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await self._dispatch_text(msg.data.decode("utf-8", errors="replace"))
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break

    async def _dispatch_text(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            print("[DisasterWarning] skip bad json")
            return
        if not isinstance(data, dict):
            return
        msg_type = str(data.get("type") or "").strip().lower()
        if msg_type in AUTH_TYPES:
            if self.on_auth_success is not None:
                await self.on_auth_success()
        if msg_type == "heartbeat":
            ws = self._ws
            if ws is not None:
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    pass
            return
        await self.on_message(data)
