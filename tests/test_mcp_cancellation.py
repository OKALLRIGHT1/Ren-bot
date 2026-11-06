import asyncio
from types import SimpleNamespace

import pytest

from tests.helpers.optional_plugins import require_optional_plugins
require_optional_plugins('mcp_tools')

from integrations.mcp import bridge


@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
@pytest.mark.parametrize("phase", ["initialize", "call", "list", "enter_session"])
@pytest.mark.asyncio
async def test_cancel_unwinds_acquired_contexts_in_same_task(monkeypatch, transport, phase):
    entered = asyncio.Event()
    exits = []
    owners = {}

    async def block():
        entered.set()
        await asyncio.Event().wait()

    class Transport:
        async def __aenter__(self):
            owners["transport"] = asyncio.current_task()
            return (None, None) if transport == "stdio" else (None, None, None)
        async def __aexit__(self, *args):
            assert asyncio.current_task() is owners["transport"]
            exits.append("transport")

    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self):
            if phase == "enter_session": await block()
            owners["session"] = asyncio.current_task()
            return self
        async def __aexit__(self, *args):
            assert asyncio.current_task() is owners["session"]
            exits.append("session")
        async def initialize(self):
            if phase == "initialize": await block()
        async def call_tool(self, *args, **kwargs): await block()
        async def list_tools(self): await block()

    monkeypatch.setattr(bridge, "ClientSession", Session)
    monkeypatch.setattr(bridge, "StdioServerParameters", lambda **kw: kw)
    monkeypatch.setattr(bridge, "stdio_client", lambda *a, **kw: Transport())
    monkeypatch.setattr(bridge, "streamablehttp_client", lambda *a, **kw: Transport())
    cfg = bridge.MCPServerConfig(name="test", transport=transport, command="unused", url="http://unused")
    client = bridge.MCPToolBridge()
    coro = client._list_remote_tools(cfg) if phase == "list" else client._call_remote_tool(cfg, "tool")
    task = asyncio.create_task(coro)
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exits == (["transport"] if phase == "enter_session" else ["session", "transport"])


@pytest.mark.parametrize("phase", ["initialize", "call", "list"])
@pytest.mark.asyncio
async def test_real_timeout_keeps_timeout_and_closes_resources(monkeypatch, phase):
    exits = []
    class Transport:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *args): exits.append("transport")
    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): exits.append("session")
        async def initialize(self):
            if phase == "initialize": await asyncio.Event().wait()
        async def call_tool(self, *a, **kw): await asyncio.Event().wait()
        async def list_tools(self): await asyncio.Event().wait()
    monkeypatch.setattr(bridge, "ClientSession", Session)
    monkeypatch.setattr(bridge, "StdioServerParameters", lambda **kw: kw)
    monkeypatch.setattr(bridge, "stdio_client", lambda *a, **kw: Transport())
    cfg = bridge.MCPServerConfig(name="test", command="unused", timeout_sec=0.01)
    client = bridge.MCPToolBridge()
    with pytest.raises(asyncio.TimeoutError):
        if phase == "list":
            await client._list_remote_tools(cfg)
        else:
            await client._call_remote_tool(cfg, "tool")
    assert exits == ["session", "transport"]


@pytest.mark.parametrize("failure", ["none", "session_entry", "initialize", "session_exit"])
@pytest.mark.asyncio
async def test_partial_setup_and_normal_exit_always_close_transport(monkeypatch, failure):
    exits = []
    class Transport:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *args): exits.append("transport")
    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self):
            if failure == "session_entry": raise RuntimeError("session_entry")
            return self
        async def __aexit__(self, *args):
            exits.append("session")
            if failure == "session_exit": raise RuntimeError("session_exit")
        async def initialize(self):
            if failure == "initialize": raise RuntimeError("initialize")
        async def call_tool(self, *a, **kw): return {"ok": True}
    monkeypatch.setattr(bridge, "ClientSession", Session)
    monkeypatch.setattr(bridge, "StdioServerParameters", lambda **kw: kw)
    monkeypatch.setattr(bridge, "stdio_client", lambda *a, **kw: Transport())
    cfg = bridge.MCPServerConfig(name="test", command="unused")
    client = bridge.MCPToolBridge()
    if failure == "none":
        assert await client._call_remote_tool(cfg, "tool") == {"ok": True}
    else:
        with pytest.raises(RuntimeError, match=failure):
            await client._call_remote_tool(cfg, "tool")
    assert exits[-1] == "transport"
    assert len(exits) == (1 if failure == "session_entry" else 2)
