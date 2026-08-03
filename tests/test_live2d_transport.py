import asyncio

import pytest

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
