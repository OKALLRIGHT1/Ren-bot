import asyncio

import pytest

from services.runtime_health import RuntimeHealthCenter
from modules.live2d_transport import (
    Live2DDelivery,
    Live2DDeliveryError,
    Live2DTransportBus,
)


class RecordingTransport:
    def __init__(self, name: str = "recording"):
        self.name = name
        self.calls: list[Live2DDelivery] = []

    async def deliver(self, delivery: Live2DDelivery) -> None:
        self.calls.append(delivery)


class FailingTransport:
    def __init__(self, name: str = "failing", error: str = "boom"):
        self.name = name
        self.error = error

    async def deliver(self, delivery: Live2DDelivery) -> None:
        del delivery
        raise RuntimeError(self.error)


@pytest.mark.asyncio
async def test_bus_delivers_same_command_id_to_every_transport():
    local = RecordingTransport("local")
    gui = RecordingTransport("gui")
    bus = Live2DTransportBus([local, gui], id_factory=lambda: "cmd-1")
    result = await bus.send({"msg": 13200, "msgId": 2, "data": {"mtn": "idle"}})
    assert result.delivered == 2
    assert local.calls[0].command_id == "cmd-1"
    assert gui.calls[0].command_id == "cmd-1"
    assert local.calls[0].message["msg"] == 13200
    assert gui.calls[0].message["data"]["mtn"] == "idle"


@pytest.mark.asyncio
async def test_bus_succeeds_when_one_transport_delivers():
    bus = Live2DTransportBus([FailingTransport(), RecordingTransport()])
    result = await bus.send({"msg": 13302, "msgId": 4, "data": {}})
    assert result.delivered == 1
    assert len(result.errors) == 1


@pytest.mark.asyncio
async def test_bus_raises_when_all_transports_fail():
    bus = Live2DTransportBus(
        [FailingTransport("a", "err-a"), FailingTransport("b", "err-b")]
    )
    try:
        await bus.send({"msg": 1, "msgId": 1, "data": {}})
    except Live2DDeliveryError as exc:
        assert "err-a" in str(exc)
        assert "err-b" in str(exc)
        assert exc.delivered == 0
        assert len(exc.errors) == 2
    else:
        raise AssertionError("expected Live2DDeliveryError")


@pytest.mark.asyncio
async def test_legacy_transport_writes_command_id_on_payload(monkeypatch):
    from modules.live2d_transport import LegacyLocalWebSocketTransport
    import modules.live2d as live2d

    sent = []

    class FakePool:
        def __init__(self):
            self._send_lock = asyncio.Lock()

        async def get_connection(self):
            class Ws:
                async def send(self, text):
                    sent.append(text)

            return Ws()

        async def mark_broken(self):
            return None

    monkeypatch.setattr(live2d, "_connection_pool", FakePool())
    monkeypatch.setattr(live2d, "LIVE2D_MODEL_IDS", [0])
    transport = LegacyLocalWebSocketTransport()
    await transport.deliver(
        Live2DDelivery(
            command_id="cmd-9",
            message={"msg": 13200, "msgId": 2, "data": {"id": 0, "mtn": "idle"}},
        )
    )
    assert len(sent) == 1
    payload = __import__("json").loads(sent[0])
    assert payload["command_id"] == "cmd-9"
    assert payload["msg"] == 13200


@pytest.mark.asyncio
async def test_gui_transport_rejects_silent_no_delivery():
    from modules.live2d_transport import GuiWebSocketTransport

    class OfflineGuiServer:
        def emit_capability(self, capability, payload):
            del capability, payload
            return False

    transport = GuiWebSocketTransport(OfflineGuiServer())
    with pytest.raises(RuntimeError, match="no capable GUI client"):
        await transport.deliver(
            Live2DDelivery(
                command_id="cmd-offline",
                message={"msg": 13200, "msgId": 2, "data": {"mtn": "idle"}},
            )
        )


def test_live2d_connection_timeout_is_five_seconds():
    import modules.live2d as live2d

    assert live2d.CONNECT_TIMEOUT == 5.0
    assert live2d.PING_TIMEOUT == 5.0


@pytest.mark.asyncio
async def test_connection_pool_backs_off_and_resets_after_success(monkeypatch):
    import modules.live2d as live2d

    clock = {"now": 100.0}
    attempts = []
    health = RuntimeHealthCenter(clock=lambda: clock["now"])

    class FakeWs:
        async def close(self):
            return None

        async def ping(self):
            return None

    async def fake_resolve_host():
        return "ws://127.0.0.1:10086/api"

    async def fake_connect(host):
        attempts.append(host)
        if len(attempts) == 1:
            raise ConnectionRefusedError("refused")
        return FakeWs()

    monkeypatch.setattr(live2d, "_resolve_host", fake_resolve_host)
    monkeypatch.setattr(live2d, "_ws_connect", fake_connect)

    pool = live2d.WebSocketConnectionPool(
        health=health,
        clock=lambda: clock["now"],
        jitter=lambda delay: 0.0,
    )

    with pytest.raises(ConnectionRefusedError):
        await pool.get_connection()
    assert pool._failure_count == 1
    assert pool._next_retry_at == 101.0

    with pytest.raises(live2d.Live2DConnectionBackoffError, match="1.0"):
        await pool.get_connection()
    assert len(attempts) == 1
    assert (
        health.snapshot(now=100.0)["components"]["live2d_ws"]["state"]
        == "reconnecting"
    )

    clock["now"] = 101.0
    connection = await pool.get_connection()
    assert isinstance(connection, FakeWs)
    assert pool._failure_count == 0
    assert pool._next_retry_at == 0.0
    assert health.snapshot(now=101.0)["components"]["live2d_ws"]["state"] == "healthy"

    await pool.close()
    assert health.snapshot(now=101.0)["components"]["live2d_ws"]["state"] == "offline"


def test_connection_backoff_is_capped_at_fifteen_seconds():
    import modules.live2d as live2d

    pool = live2d.WebSocketConnectionPool(jitter=lambda delay: 0.0)
    assert [pool._backoff_delay(n) for n in range(1, 8)] == [
        1.0,
        2.0,
        4.0,
        8.0,
        15.0,
        15.0,
        15.0,
    ]
