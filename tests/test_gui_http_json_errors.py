from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from integrations.gui_http import GuiHttpServer


@pytest.mark.asyncio
async def test_unknown_route_returns_json_404():
    server = GuiHttpServer(host="127.0.0.1", port=0, path_prefix="/gui", access_token="tok")
    app = web.Application(middlewares=[server._cors_middleware, server._auth_middleware])
    app.router.add_get(server._api_path("/health"), server._handle_health)
    not_found = server._api_path("/{tail:.*}")
    app.router.add_get(not_found, server._handle_not_found)
    app.router.add_post(not_found, server._handle_not_found)

    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/gui/this-route-does-not-exist",
            headers={"X-GUI-Token": "tok"},
        )
        assert response.status == 404
        payload = await response.json()
        assert payload["ok"] is False
        assert payload["error"] == "route_not_found"
        assert "hint" in payload


@pytest.mark.asyncio
async def test_health_reports_api_version():
    server = GuiHttpServer(host="127.0.0.1", port=0, path_prefix="/gui", access_token="")
    app = web.Application(middlewares=[server._cors_middleware, server._auth_middleware])
    app.router.add_get(server._api_path("/health"), server._handle_health)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/gui/health")
        assert response.status == 200
        payload = await response.json()
        assert payload["ok"] is True
        assert payload["api_version"] == "enhanced-gui-1"
