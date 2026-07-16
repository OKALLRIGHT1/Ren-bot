from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from services.gui_api.models_service import (
    ModelsCatalogService,
    SecretUpdate,
    mask_model_row,
    normalize_provider_row,
)


def _catalog() -> Dict[str, Any]:
    return {
        "providers": {
            "demo": {
                "base_url": "https://example.com/v1",
                "api_key": "secret-provider-key",
            }
        },
        "models": {
            "chat-a": {
                "model": "gpt-test",
                "base_url": "https://example.com/v1",
                "api_key": "secret-model-key",
                "provider": "demo",
                "purposes": ["chat", "tool_reasoning"],
            },
            "embed-a": {
                "model": "bge-m3",
                "base_url": "https://example.com/v1",
                "api_key": "secret-embed-key",
                "purposes": ["embedding"],
                "embedding_dimension": 1024,
            },
        },
        "router": {
            "default": ["chat-a"],
            "tool_reasoning": ["chat-a"],
            "embedding": ["embed-a"],
        },
    }


def test_mask_model_row_never_returns_api_key():
    masked = mask_model_row("chat-a", _catalog()["models"]["chat-a"])
    blob = json.dumps(masked, ensure_ascii=False)
    assert "secret-model-key" not in blob
    assert masked["id"] == "chat-a"
    assert masked["has_api_key"] is True
    assert masked["model"] == "gpt-test"
    assert "chat" in masked["purposes"]


def test_list_catalog_masks_providers_and_models(tmp_path: Path):
    path = tmp_path / "custom_models.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = ModelsCatalogService(path)
    data = service.list_catalog()
    blob = json.dumps(data, ensure_ascii=False)
    assert "secret-provider-key" not in blob
    assert "secret-model-key" not in blob
    assert data["providers"][0]["id"] == "demo"
    assert data["providers"][0]["has_api_key"] is True
    assert {item["id"] for item in data["models"]} == {"chat-a", "embed-a"}
    assert data["router"]["default"] == ["chat-a"]
    assert data["purpose_options"]


def test_upsert_model_keep_replace_clear_secret(tmp_path: Path):
    path = tmp_path / "custom_models.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = ModelsCatalogService(path)

    keep = service.upsert_model(
        {
            "id": "chat-a",
            "model": "gpt-test-2",
            "base_url": "https://example.com/v1",
            "provider": "demo",
            "purposes": ["chat"],
            "api_key": {"action": "keep"},
        }
    )
    assert keep["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["models"]["chat-a"]["api_key"] == "secret-model-key"
    assert raw["models"]["chat-a"]["model"] == "gpt-test-2"

    replace = service.upsert_model(
        {
            "id": "chat-a",
            "model": "gpt-test-3",
            "base_url": "https://example.com/v1",
            "purposes": ["chat"],
            "api_key": {"action": "replace", "value": "new-secret"},
        }
    )
    assert replace["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["models"]["chat-a"]["api_key"] == "new-secret"

    cleared = service.upsert_model(
        {
            "id": "chat-a",
            "model": "gpt-test-3",
            "base_url": "https://example.com/v1",
            "purposes": ["chat"],
            "api_key": {"action": "clear"},
        }
    )
    assert cleared["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert not raw["models"]["chat-a"].get("api_key")


def test_upsert_model_rejects_empty_id(tmp_path: Path):
    path = tmp_path / "custom_models.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = ModelsCatalogService(path)
    result = service.upsert_model({"id": "", "model": "x"})
    assert result["ok"] is False
    assert result["error"] == "invalid_model_id"


def test_delete_model_and_router_cleanup(tmp_path: Path):
    path = tmp_path / "custom_models.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = ModelsCatalogService(path)
    result = service.delete_model("chat-a")
    assert result["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "chat-a" not in raw["models"]
    assert raw["router"]["default"] == []


def test_upsert_provider_and_router(tmp_path: Path):
    path = tmp_path / "custom_models.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = ModelsCatalogService(path)
    provider = service.upsert_provider(
        {
            "id": "demo",
            "base_url": "https://example.com/v2",
            "api_key": {"action": "keep"},
        }
    )
    assert provider["ok"] is True
    router = service.save_router({"default": ["chat-a", "missing"], "tool_reasoning": ["chat-a"]})
    assert router["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["providers"]["demo"]["base_url"] == "https://example.com/v2"
    # unknown model ids dropped from chains
    assert raw["router"]["default"] == ["chat-a"]


def test_normalize_provider_row_masks_secret():
    row = normalize_provider_row(
        "p1", {"base_url": "https://x", "api_key": "abc"}
    )
    assert row["has_api_key"] is True
    assert "api_key" not in row or row.get("api_key") in ("", None, "********")


def test_secret_update_parse():
    assert SecretUpdate.parse({"action": "keep"}).action == "keep"
    assert SecretUpdate.parse({"action": "replace", "value": "x"}).value == "x"
    assert SecretUpdate.parse("plain").action == "replace"
    assert SecretUpdate.parse(None).action == "keep"
