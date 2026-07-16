from __future__ import annotations

import asyncio
import json
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import websockets
from aiohttp import ClientSession

from integrations.gui_http import GuiHttpServer
from integrations.gui_protocol import build_live2d_envelope
from integrations.gui_ws import GuiWebSocketServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ContractMemoryStore:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def ingest_activity_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.events.append(dict(payload))
        return {"latest": True, "historized": payload.get("kind") != "activity_sample"}


class ContractApp:
    """Minimal app surface for GuiHttp/GuiWS contract scenarios."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.logger = None
        self.gui_ws_server: Optional[GuiWebSocketServer] = None
        self.gui_http_server: Optional[GuiHttpServer] = None
        self.memory_store = ContractMemoryStore()
        self.tts_enabled = True
        self.qt_ui = None
        self.plugin_manager = None
        self.skill_manager = None
        self.mcp_bridge = None
        self.chat_gateway = None
        self.chat_service = None
        self.screen_sensor = None
        self.state_machine = type("SM", (), {"state": None})()
        self._settings: Dict[str, Any] = {
            "gui_access_token": token,
            "activity_monitor_enabled": True,
            "sedentary_reminder_minutes": 45,
            "sedentary_break_minutes": 8,
            "sedentary_cooldown_minutes": 30,
            "activity_include_process_path": False,
            "activity_include_window_title": False,
            "activity_include_browser_context": False,
            "activity_config_revision": 1,
        }
        self.commands: List[Dict[str, Any]] = []

    def _load_runtime_settings(self) -> Dict[str, Any]:
        return dict(self._settings)

    def get_activity_client_config(self) -> Dict[str, Any]:
        settings = self._load_runtime_settings()
        return {
            "revision": int(settings.get("activity_config_revision") or 0),
            "monitor_enabled": bool(settings.get("activity_monitor_enabled", True)),
            "sedentary_reminder_minutes": int(
                settings.get("sedentary_reminder_minutes", 60) or 60
            ),
            "sedentary_break_minutes": int(settings.get("sedentary_break_minutes", 5) or 5),
            "sedentary_cooldown_minutes": int(
                settings.get("sedentary_cooldown_minutes", 60) or 60
            ),
            "include_process_path": bool(
                settings.get("activity_include_process_path", False)
            ),
            "include_window_title": bool(
                settings.get("activity_include_window_title", False)
            ),
            "include_browser_context": bool(
                settings.get("activity_include_browser_context", False)
            ),
        }

    def _build_gui_config(self) -> Dict[str, Any]:
        return {"tts": True, "voice": False, "dnd": False}

    def _build_gui_character(self) -> Dict[str, Any]:
        return {"name": "Suzu", "costume": "default"}

    def _build_gui_costumes(self) -> Dict[str, Any]:
        return {"items": [{"name": "default"}], "current": "default"}

    def _current_gui_status_text(self) -> str:
        return "Idle"

    async def _send_gui_snapshot(self, ws=None) -> None:
        if not self.gui_ws_server:
            return
        payloads = [
            {
                "type": "status",
                "text": self._current_gui_status_text(),
                "level": "info",
            },
            {"type": "config", **self._build_gui_config()},
            {"type": "character", **self._build_gui_character()},
            {"type": "costumes", **self._build_gui_costumes()},
        ]
        for payload in payloads:
            if ws is None:
                await self.gui_ws_server.broadcast(payload)
            else:
                await self.gui_ws_server.send(ws, payload)

    async def _on_gui_ws_message(self, payload: Dict[str, Any], ws) -> None:
        msg_type = str(payload.get("type") or "").strip().lower()
        if msg_type == "hello":
            await self._send_gui_snapshot(ws)
            return
        if msg_type != "command":
            return
        self.commands.append(dict(payload))
        name = str(payload.get("name") or "").strip().lower()
        if name == "send_text":
            return
        if name in {"toggle_tts", "toggle_voice", "toggle_dnd", "mode_status"}:
            await self._send_gui_snapshot(ws)

    def on_gui_send(self, text: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.commands.append({"name": "send_text", "text": text, "meta": meta or {}})


@dataclass
class ContractResult:
    token: str
    http_url: str
    ws_url: str
    received_types: List[str] = field(default_factory=list)
    http_bodies: List[str] = field(default_factory=list)
    activity_events: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[Dict[str, Any]] = field(default_factory=list)
    live2d_seen: bool = False
    activity_config: Dict[str, Any] = field(default_factory=dict)


async def _recv_json(ws, timeout: float = 3.0) -> Dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise AssertionError(f"expected object payload, got {type(data)}")
    return data


async def run_contract_scenario(
    app_factory: Optional[Callable[[str], Any]] = None,
    token: str = "contract-secret-token",
) -> ContractResult:
    """
    Authenticated WS + HTTP contract sequence used by enhanced frontend.

    Sequence:
      auth WS connect -> enhanced hello -> status/config/character/costumes
      -> GUI command -> live2d_protocol push
      -> GET /activity-config -> POST /activity-ingest
    """
    token = str(token or "contract-secret-token")
    app = app_factory(token) if app_factory else ContractApp(token)
    http_port = _free_port()
    ws_port = _free_port()

    http = GuiHttpServer(
        host="127.0.0.1",
        port=http_port,
        path_prefix="/gui",
        app_ref=app,
        access_token=token,
    )
    ws = GuiWebSocketServer(
        host="127.0.0.1",
        port=ws_port,
        path="/gui",
        access_token=token,
    )
    app.gui_http_server = http
    app.gui_ws_server = ws
    if hasattr(app, "_on_gui_ws_message"):
        ws.set_message_handler(app._on_gui_ws_message)

    # activity-ingest uses memory store from app_ref when available
    http._get_memory_store = lambda: getattr(app, "memory_store", None)  # type: ignore[method-assign]

    result = ContractResult(
        token=token,
        http_url=f"http://127.0.0.1:{http_port}/gui",
        ws_url=f"ws://127.0.0.1:{ws_port}/gui",
    )

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    start_error: list[BaseException] = []

    def _run_ws() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ws.start(loop))
        except BaseException as exc:  # pragma: no cover - startup failure path
            start_error.append(exc)
            ready.set()
            return
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run_ws, daemon=True)
    thread.start()
    assert ready.wait(5.0), "websocket server failed to become ready"
    if start_error:
        raise RuntimeError(str(start_error[0]))

    http.start()
    try:
        async with websockets.connect(
            result.ws_url,
            additional_headers={"X-GUI-Token": token},
            open_timeout=5,
            close_timeout=2,
            max_size=2 * 1024 * 1024,
        ) as socket:
            await socket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "client": "live2d-enhanced",
                        "protocol_version": 1,
                        "capabilities": [
                            "gui.v1",
                            "live2d.protocol.v1",
                            "activity.config.v1",
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            seen = set()
            while len(seen) < 4:
                message = await _recv_json(socket)
                msg_type = str(message.get("type") or "")
                result.received_types.append(msg_type)
                seen.add(msg_type)
                assert token not in json.dumps(message, ensure_ascii=False)

            assert {"status", "config", "character", "costumes"}.issubset(seen)

            await socket.send(
                json.dumps(
                    {
                        "type": "command",
                        "name": "send_text",
                        "text": "contract-ping",
                    },
                    ensure_ascii=False,
                )
            )
            # allow command dispatch
            await asyncio.sleep(0.1)

            envelope = build_live2d_envelope(
                "contract-cmd-1",
                {"type": "motion", "name": "idle"},
            )
            ws.emit_capability("live2d.protocol.v1", envelope)
            live2d = await _recv_json(socket)
            assert live2d.get("type") == "live2d_protocol"
            assert live2d.get("command_id") == "contract-cmd-1"
            result.live2d_seen = True
            assert token not in json.dumps(live2d, ensure_ascii=False)

        headers = {"X-GUI-Token": token}
        async with ClientSession() as session:
            async with session.get(
                f"{result.http_url}/activity-config", headers=headers
            ) as response:
                body = await response.text()
                result.http_bodies.append(body)
                assert response.status == 200, body
                assert token not in body
                payload = json.loads(body)
                assert payload.get("ok") is True
                data = payload.get("data") or {}
                assert "gui_access_token" not in data
                assert data.get("sedentary_reminder_minutes") == 45
                result.activity_config = dict(data)

            event = {
                "event_id": "contract-evt-1",
                "ts": "2026-07-16T00:00:00+00:00",
                "kind": "activity_sample",
                "presence": "active",
                "source": "live2d-tauri",
                "app": {"name": "Code.exe"},
                "sedentary": {"active_minutes": 1, "rest_streak": 0},
            }
            async with session.post(
                f"{result.http_url}/activity-ingest",
                headers=headers,
                json=event,
            ) as response:
                body = await response.text()
                result.http_bodies.append(body)
                assert response.status == 200, body
                assert token not in body
                payload = json.loads(body)
                assert payload.get("ok") is True

        result.activity_events = list(getattr(app, "memory_store").events)
        result.commands = list(getattr(app, "commands", []))
        assert any(item.get("source") == "live2d-tauri" for item in result.activity_events)
        assert any(
            item.get("name") == "send_text" or item.get("text") == "contract-ping"
            for item in result.commands
        )
        return result
    finally:
        try:
            http.stop()
        except Exception:
            pass
        try:
            fut = asyncio.run_coroutine_threadsafe(ws.shutdown(), loop)
            fut.result(timeout=3)
        except Exception:
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        if thread.is_alive():
            thread.join(timeout=3)
        try:
            loop.close()
        except Exception:
            pass
