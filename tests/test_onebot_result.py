import asyncio
import json

import pytest

from integrations.chat_gateway import napcat
from integrations.chat_gateway.server import NapCatWebhookServer
from integrations.chat_gateway.tracking import is_send_success


@pytest.mark.parametrize("payload,expected", [
    ({"status": "ok", "retcode": 0}, True),
    ({"status": "ok", "retcode": "0"}, True),
    ({"retcode": 0}, True),
    ({"status": "ok"}, True),
    ({"status": "failed", "retcode": 1400}, False),
    ({"status": "failed", "retcode": 0}, False),
    ({"status": "ok", "retcode": 1400}, False),
    ({"status": "ok", "retcode": None}, False),
    ({"retcode": "invalid"}, False),
    ({"status": "async", "retcode": 1}, False),
    ({"data": {"message_id": 42}}, False),
    ({}, False),
    ([], False),
])
@pytest.mark.asyncio
async def test_http_and_ws_use_same_onebot_result_rules(monkeypatch, payload, expected):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()

    monkeypatch.setattr(napcat.request, "urlopen", lambda *a, **kw: Response())
    adapter = napcat.NapCatOneBotAdapter(api_base="http://example.invalid")
    result = await adapter._send_action("private:1", "send_private_msg", {})
    assert result["ok"] is expected
    assert is_send_success(result) is expected
    if not expected:
        assert result["reason"]
    if not isinstance(payload, dict):
        return
    loop = asyncio.get_running_loop()
    server = NapCatWebhookServer(gateway=None, loop=loop)
    server._server_loop = loop

    class Socket:
        closed = False
        async def send_json(self, frame):
            server._pending_actions[frame["echo"]].set_result(payload)

    server._ws_connections = [Socket()]
    result = await server.call_action("send_private_msg")
    assert result["ok"] is expected
    assert is_send_success(result) is expected
    assert server._pending_actions == {}


def test_explicit_failure_wins_over_message_id_or_outer_ok():
    assert not is_send_success({"ok": False, "response": {"data": {"message_id": 42}}})
    assert not is_send_success({"ok": True, "response": {"status": "failed", "retcode": 1400}})
    assert is_send_success({"response": {"retcode": 0}})
