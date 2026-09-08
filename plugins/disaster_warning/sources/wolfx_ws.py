from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional, Set

from .parse_wolfx import WOLFX_EEW_TYPES

DEFAULT_WOLFX_WS_URL = "wss://ws-api.wolfx.jp/all_eew"
WOLFX_DOCS_URL = "https://wolfx.jp/zh/docs/open-api"
SNAPSHOT_GRACE_SEC = 5.0
QUERY_COMMANDS = (
    "query_cenceew",
    "query_sceew",
    "query_fjeew",
    "query_cqeew",
)
EEW_TYPES = frozenset(WOLFX_EEW_TYPES)
SKIP_TYPES = {"heartbeat", "pong"}

OnMessage = Callable[..., Awaitable[Optional[str]]]
OnReady = Callable[[], Awaitable[None]]


def take_snapshot_flag(
    msg_type: str,
    seeded: Set[str],
    grace_until: float,
    now: float,
) -> bool:
    """First packet of each EEW type is a snapshot; extra query echoes in grace also are."""
    kind = str(msg_type or "").strip()
    if kind not in EEW_TYPES:
        return False
    if kind not in seeded:
        seeded.add(kind)
        return True
    return now < grace_until


class WolfxClient:
    def __init__(
        self,
        *,
        ws_url: str,
        ws_backup_url: str = "",
        on_message: OnMessage,
        on_ready: Optional[OnReady] = None,
        snapshot_grace_sec: float = SNAPSHOT_GRACE_SEC,
    ):
        self.ws_url = str(ws_url or DEFAULT_WOLFX_WS_URL).strip() or DEFAULT_WOLFX_WS_URL
        self.ws_backup_url = str(ws_backup_url or "").strip()
        self.on_message = on_message
        self.on_ready = on_ready
        self.snapshot_grace_sec = max(0.0, float(snapshot_grace_sec or 0))
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
            url = self.ws_backup_url if use_backup and self.ws_backup_url else self.ws_url
            self.connect_attempts += 1
            try:
                await self._run_once(url)
                delay = 1.0
                fail_streak = 0
                use_backup = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DisasterWarning] wolfx ws error: {exc!r}")
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
                seeded: Set[str] = set()
                grace_until = time.monotonic() + self.snapshot_grace_sec
                try:
                    await ws.send_str("ping")
                    for command in QUERY_COMMANDS:
                        await ws.send_str(command)
                except Exception:
                    raise
                if self.on_ready is not None:
                    await self.on_ready()
                async for msg in ws:
                    if self._stop.is_set():
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._dispatch_text(msg.data, seeded, grace_until)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await self._dispatch_text(
                            msg.data.decode("utf-8", errors="replace"),
                            seeded,
                            grace_until,
                        )
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break

    async def _dispatch_text(
        self, raw: str, seeded: Set[str], grace_until: float
    ) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            print("[DisasterWarning] wolfx skip bad json")
            return
        if not isinstance(data, dict):
            return
        msg_type = str(data.get("type") or "").strip()
        if msg_type in SKIP_TYPES:
            ws = self._ws
            if ws is not None and msg_type == "heartbeat":
                try:
                    await ws.send_str("ping")
                except Exception:
                    pass
            return
        snapshot = take_snapshot_flag(
            msg_type, seeded, grace_until, time.monotonic()
        )
        await self.on_message(data, snapshot=snapshot)
