from __future__ import annotations

import pytest

from modules.embeddings.catalog import (
    EmbeddingConfigurationError,
    resolve_embedding_config,
)


def _catalog_row(**overrides):
    row = {
        "model": "bge-m3",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "ollama",
        "purposes": ["embedding"],
        "embedding_endpoint_path": "/embeddings",
        "embedding_dimension": 1024,
        "embedding_provider": "ollama",
    }
    row.update(overrides)
    return row


def test_selected_embedding_model_must_exist():
    with pytest.raises(EmbeddingConfigurationError, match="不存在"):
        resolve_embedding_config(
            model_id="missing",
            models={},
            legacy_config={"model_name": "bge-m3"},
        )


def test_selected_embedding_model_requires_embedding_purpose():
    models = {"chat": {"model": "chat", "purposes": ["chat"]}}
    with pytest.raises(EmbeddingConfigurationError, match="向量"):
        resolve_embedding_config(
            model_id="chat",
            models=models,
            legacy_config={},
        )


def test_catalog_embedding_resolves_ollama_endpoint():
    resolved = resolve_embedding_config(
        model_id="local-bge-m3",
        models={"local-bge-m3": _catalog_row()},
        legacy_config={},
    )

    assert resolved.api_url == "http://127.0.0.1:11434/v1/embeddings"
    assert resolved.expected_dimension == 1024
    assert resolved.source == "catalog"
    assert resolved.enabled is True


def test_catalog_embedding_reads_declared_key_environment():
    row = _catalog_row(api_key="", api_key_env="LOCAL_EMBEDDING_KEY")
    resolved = resolve_embedding_config(
        model_id="local-bge-m3",
        models={"local-bge-m3": row},
        legacy_config={},
        environ={"LOCAL_EMBEDDING_KEY": "secret-from-env"},
    )

    assert resolved.api_key == "secret-from-env"


def test_catalog_selection_does_not_inherit_legacy_disabled_switch():
    resolved = resolve_embedding_config(
        model_id="local-bge-m3",
        models={"local-bge-m3": _catalog_row()},
        legacy_config={"enabled": False},
    )

    assert resolved.enabled is True


def test_catalog_embedding_does_not_fill_invalid_selection_from_legacy():
    row = _catalog_row(model="")
    with pytest.raises(EmbeddingConfigurationError, match="缺少"):
        resolve_embedding_config(
            model_id="local-bge-m3",
            models={"local-bge-m3": row},
            legacy_config={
                "model_name": "legacy-model",
                "api_url": "http://legacy/v1/embeddings",
                "expected_dimension": 1024,
            },
        )


def test_empty_selection_preserves_legacy_embedding_config():
    resolved = resolve_embedding_config(
        model_id="",
        models={},
        legacy_config={
            "enabled": True,
            "provider": "ollama",
            "api_url": "http://127.0.0.1:11434/v1/embeddings",
            "api_key": "ollama",
            "model_name": "bge-m3",
            "timeout": 9,
            "expected_dimension": 1024,
        },
    )

    assert resolved.source == "legacy"
    assert resolved.model_name == "bge-m3"
    assert resolved.timeout == 9
    assert resolved.enabled is True


def test_embedding_queue_keeps_same_dimension_models_only():
    models = {
        "local": _catalog_row(),
        "remote": _catalog_row(
            model="BAAI/bge-m3",
            base_url="https://api.siliconflow.cn/v1",
            embedding_provider="openai_compatible",
            embedding_dimension=1024,
        ),
        "bad-dim": _catalog_row(
            model="other",
            base_url="https://example.com/v1",
            embedding_dimension=768,
        ),
    }
    resolved = resolve_embedding_config(
        model_ids=["local", "remote", "bad-dim"],
        models=models,
        legacy_config={},
    )
    assert resolved.model_id == "local"
    assert resolved.chain_model_ids == ("local", "remote")


def test_embedding_queue_rejects_all_invalid():
    with pytest.raises(EmbeddingConfigurationError, match="队列无效"):
        resolve_embedding_config(
            model_ids=["missing"],
            models={},
            legacy_config={},
        )
