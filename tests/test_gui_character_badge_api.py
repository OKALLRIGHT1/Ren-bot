from __future__ import annotations

import json
import base64
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from integrations.gui_http import GuiHttpServer


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _catalog(root: Path) -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    path = data / "characters.json"
    path.write_text(
        json.dumps(
            {
                "active_id": "suzu",
                "characters": {
                    "suzu": {
                        "name": "Suzu",
                        "current_costume": "uniform",
                        "costumes": {"uniform": {"path": "model.json"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _app(server: GuiHttpServer) -> web.Application:
    app = web.Application()
    app.router.add_get(
        server._api_path("/characters/badge/current"),
        server._handle_characters_badge_current,
    )
    app.router.add_get(
        server._api_path("/characters/badge"), server._handle_characters_badge_get
    )
    app.router.add_post(
        server._api_path("/characters/badge/import"),
        server._handle_characters_badge_import,
    )
    app.router.add_post(
        server._api_path("/characters/badge/update"),
        server._handle_characters_badge_update,
    )
    app.router.add_post(
        server._api_path("/characters/badge/clear"),
        server._handle_characters_badge_clear,
    )
    return app


@pytest.mark.asyncio
async def test_badge_http_import_current_update_and_clear(tmp_path: Path, monkeypatch):
    _catalog(tmp_path)
    source = tmp_path / "avatar.png"
    source.write_bytes(PNG_BYTES)
    server = GuiHttpServer(host="127.0.0.1", port=0, path_prefix="/gui")
    monkeypatch.setattr(server, "_find_backend_root", lambda _cwd: str(tmp_path))
    monkeypatch.setattr(server, "_reload_characters", lambda: None)

    async with TestClient(TestServer(_app(server))) as client:
        imported = await client.post(
            "/gui/characters/badge/import",
            json={
                "character_id": "suzu",
                "source_path": str(source),
                "scale": 1.4,
            },
        )
        assert imported.status == 200
        assert (await imported.json())["data"]["source"] == "character"

        current = await client.get("/gui/characters/badge/current")
        current_payload = await current.json()
        assert current_payload["data"]["badge"]["scale"] == 1.4
        assert current_payload["data"]["image_data_url"].startswith("data:image/png")

        updated = await client.post(
            "/gui/characters/badge/update",
            json={"character_id": "suzu", "scale": 2, "offset_x": 0.25},
        )
        assert (await updated.json())["data"]["badge"]["offset_x"] == 0.25

        cleared = await client.post(
            "/gui/characters/badge/clear", json={"character_id": "suzu"}
        )
        assert (await cleared.json())["data"]["source"] == "none"


@pytest.mark.asyncio
async def test_badge_http_get_explicit_costume_inheritance(tmp_path: Path, monkeypatch):
    _catalog(tmp_path)
    source = tmp_path / "avatar.png"
    source.write_bytes(PNG_BYTES)
    server = GuiHttpServer(host="127.0.0.1", port=0, path_prefix="/gui")
    monkeypatch.setattr(server, "_find_backend_root", lambda _cwd: str(tmp_path))
    service = server._characters_service(str(tmp_path))
    assert service.import_badge("suzu", str(source))["ok"] is True

    async with TestClient(TestServer(_app(server))) as client:
        response = await client.get(
            "/gui/characters/badge",
            params={"character_id": "suzu", "costume": "uniform"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["data"]["source"] == "character"
