from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from services.gui_api.info_sources_service import InfoSourcesGuiService


class FakeSecretStore:
    def __init__(self) -> None:
        self.values: Dict[tuple[str, str], str] = {}

    def get_secret(self, plugin_trigger: str, secret_key: str) -> str:
        return self.values.get((plugin_trigger, secret_key), "")

    def set_secret(self, plugin_trigger: str, secret_key: str, secret_value: str) -> None:
        self.values[(plugin_trigger, secret_key)] = str(secret_value or "")


def _write_provider(root: Path, provider_id: str = "alapi") -> None:
    provider_dir = root / provider_id
    provider_dir.mkdir(parents=True, exist_ok=True)
    (provider_dir / "provider.json").write_text(
        '{"id":"%s","name":"%s","base_url":"https://v3.alapi.cn","token_param":"token"}\n'
        % (provider_id, provider_id.upper()),
        encoding="utf-8",
    )
    (provider_dir / "hitokoto.json").write_text(
        '{"id":"hitokoto","name":"Hitokoto","method":"GET","path":"/api/hitokoto","params":{},"cache_ttl_sec":300}\n',
        encoding="utf-8",
    )


def test_list_providers_and_endpoints(tmp_path: Path):
    _write_provider(tmp_path)
    service = InfoSourcesGuiService(source_root=tmp_path, secret_store=FakeSecretStore())
    listed = service.list_overview()
    assert listed["ok"] is True
    assert listed["data"]["providers"]
    assert listed["data"]["endpoints"][0]["id"] == "hitokoto"
    assert listed["data"]["has_alapi_token"] is False


def test_token_update_masks_secret(tmp_path: Path):
    _write_provider(tmp_path)
    secrets = FakeSecretStore()
    service = InfoSourcesGuiService(source_root=tmp_path, secret_store=secrets)
    saved = service.update_token({"api_token": {"action": "replace", "value": "secret-token"}})
    assert saved["ok"] is True
    assert saved["data"]["has_alapi_token"] is True
    assert secrets.values[("magic_daily", "api_token")] == "secret-token"
    overview = service.list_overview()
    assert "secret-token" not in str(overview)


def test_endpoint_crud_and_draft(tmp_path: Path):
    _write_provider(tmp_path)
    service = InfoSourcesGuiService(source_root=tmp_path, secret_store=FakeSecretStore())
    draft = service.build_draft(
        "GET https://v3.alapi.cn/api/weather\nparams: city required, format"
    )
    assert draft["ok"] is True
    assert draft["data"]["path"].endswith("/api/weather") or "weather" in draft["data"]["id"]

    saved = service.save_endpoint(
        {
            "id": "weather",
            "name": "Weather",
            "method": "GET",
            "path": "/api/weather",
            "params": {"city": {"type": "string", "required": True}},
            "cache_ttl_sec": 120,
        }
    )
    assert saved["ok"] is True
    loaded = service.get_endpoint("weather")
    assert loaded["ok"] is True
    assert loaded["data"]["path"] == "/api/weather"
