from __future__ import annotations

import json

import pytest

from tests.helpers.enhanced_backend_contract import (
    ContractApp,
    run_contract_scenario,
)


@pytest.mark.asyncio
async def test_enhanced_backend_contract_sequence_uses_header_token_only():
    token = "must-not-leak-in-logs"
    result = await run_contract_scenario(ContractApp, token=token)

    assert result.live2d_seen is True
    assert {"status", "config", "character", "costumes"}.issubset(set(result.received_types))
    assert result.activity_config["sedentary_reminder_minutes"] == 45
    assert result.activity_config["revision"] == 1
    assert "gui_access_token" not in result.activity_config

    serialized = json.dumps(
        {
            "types": result.received_types,
            "http": result.http_bodies,
            "events": result.activity_events,
            "commands": result.commands,
            "config": result.activity_config,
        },
        ensure_ascii=False,
    )
    assert token not in serialized
    assert "must-not-leak" not in serialized
    assert any(event.get("source") == "live2d-tauri" for event in result.activity_events)
    assert any(
        command.get("name") == "send_text" or command.get("text") == "contract-ping"
        for command in result.commands
    )


@pytest.mark.asyncio
async def test_contract_rejects_missing_token_on_http():
    # Ensure the activity-config route enforces auth while the server is alive.
    import asyncio
    import socket

    from aiohttp import ClientSession

    from integrations.gui_http import GuiHttpServer
    from tests.helpers.enhanced_backend_contract import ContractApp

    token = "auth-required"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    app = ContractApp(token)
    server = GuiHttpServer(
        host="127.0.0.1",
        port=port,
        path_prefix="/gui",
        app_ref=app,
        access_token=token,
    )
    server.start()
    try:
        await asyncio.sleep(0.05)
        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{server.port}/gui/activity-config") as response:
                assert response.status == 401
    finally:
        server.stop()
