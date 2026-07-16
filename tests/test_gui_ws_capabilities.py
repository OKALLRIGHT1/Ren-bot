import asyncio

import pytest

from integrations.gui_ws import GuiWebSocketServer


class FakeWs:
    def __init__(self, headers=None, path="/gui"):
        self.request_headers = headers or {}
        self.path = path
        self.remote_address = ("127.0.0.1", 12345)
        self.sent = []
        self.closed = None

    async def send(self, message):
        self.sent.append(message)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_capability_broadcast_only_targets_supported_clients():
    server = GuiWebSocketServer(access_token="secret")
    capable = FakeWs()
    legacy = FakeWs()
    server._clients.update({capable, legacy})
    server._client_capabilities[capable] = {"live2d.protocol.v1"}
    server._client_capabilities[legacy] = {"gui.v1"}
    await server.broadcast_capability(
        "live2d.protocol.v1",
        {"type": "live2d_protocol"},
    )
    assert len(capable.sent) == 1
    assert legacy.sent == []


def test_ws_query_token_is_not_accepted():
    server = GuiWebSocketServer(access_token="secret")
    assert server._extract_token(FakeWs(headers={}), "/gui?token=secret") == ""


def test_ws_header_token_is_accepted():
    server = GuiWebSocketServer(access_token="secret")
    connection = FakeWs(headers={"X-GUI-Token": "secret"})
    assert server._extract_token(connection, "/gui") == "secret"
    assert server._authorized(connection, "/gui") is True


@pytest.mark.asyncio
async def test_hello_updates_client_capabilities():
    server = GuiWebSocketServer(access_token="secret")
    client = FakeWs()
    server._clients.add(client)
    server._client_capabilities[client] = {"gui.v1"}
    handled = []

    async def handler(payload, ws):
        handled.append((payload, ws))

    server.set_message_handler(handler)
    await server._dispatch_message(
        {
            "type": "hello",
            "client": "live2d-enhanced",
            "protocol_version": 1,
            "capabilities": ["gui.v1", "live2d.protocol.v1"],
        },
        client,
    )
    assert "live2d.protocol.v1" in server._client_capabilities[client]
    assert handled and handled[0][1] is client


@pytest.mark.asyncio
async def test_activity_config_capability_is_supported_for_targeted_broadcast():
    server = GuiWebSocketServer(access_token="secret")
    capable = FakeWs()
    legacy = FakeWs()
    server._clients.update({capable, legacy})
    server._client_capabilities[capable] = {"activity.config.v1"}
    server._client_capabilities[legacy] = {"gui.v1"}
    await server.broadcast_capability(
        "activity.config.v1",
        {"type": "activity_config_changed", "revision": 2},
    )
    assert len(capable.sent) == 1
    assert legacy.sent == []


def test_emit_capability_uses_event_loop_scheduler():
    import threading

    server = GuiWebSocketServer(access_token="secret")
    client = FakeWs()
    server._clients.add(client)
    server._client_capabilities[client] = {"live2d.protocol.v1"}
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run_loop, daemon=True)
    try:
        server._loop = loop
        server._server = object()
        thread.start()
        assert ready.wait(2.0)
        # Call from outside the loop thread so emit_capability must use
        # run_coroutine_threadsafe rather than touching websockets directly.
        server.emit_capability("live2d.protocol.v1", {"type": "live2d_protocol"})
        for _ in range(50):
            if client.sent:
                break
            threading.Event().wait(0.02)
        assert len(client.sent) == 1
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        loop.close()
