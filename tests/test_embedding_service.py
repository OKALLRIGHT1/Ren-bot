from __future__ import annotations

import pytest

from modules.embeddings import (
    ChromaEmbeddingFunction,
    EmbeddingService,
    EmbeddingUnavailableError,
    build_configured_embedding_service,
    build_embedding_service,
)


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_embedding_service_returns_openai_compatible_vectors() -> None:
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(
            200,
            {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )

    service = EmbeddingService(
        enabled=True,
        api_url="http://127.0.0.1:11434/v1/embeddings",
        api_key="local",
        model="bge-m3",
        expected_dimension=2,
        post=post,
    )

    assert service.embed(["第一条", "第二条"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert calls[0][1]["json"] == {
        "input": ["第一条", "第二条"],
        "model": "bge-m3",
    }
    assert service.status()["dimension"] == 2
    assert service.status()["state"] == "ready"
    assert service.status()["calls"] == 1
    assert service.status()["failures"] == 0


def test_embedding_service_disabled_fails_explicitly() -> None:
    service = EmbeddingService(enabled=False, api_url="", model="")

    assert service.status()["state"] == "disabled"

    with pytest.raises(EmbeddingUnavailableError, match="disabled"):
        service.embed(["不会生成零向量"])

    status = service.status()
    assert status["available"] is False
    assert status["failures"] == 1


def test_embedding_service_configured_but_not_called_is_unverified() -> None:
    service = EmbeddingService(
        enabled=True,
        api_url="http://127.0.0.1:11434/v1/embeddings",
        model="bge-m3",
    )

    assert service.status()["available"] is False
    assert service.status()["state"] == "unverified"


def test_embedding_service_http_failure_does_not_return_fallback_vectors() -> None:
    service = EmbeddingService(
        enabled=True,
        api_url="http://embedding.invalid/v1/embeddings",
        model="bge-m3",
        post=lambda *_args, **_kwargs: _Response(503, text="offline"),
    )

    with pytest.raises(EmbeddingUnavailableError, match="HTTP 503"):
        service.embed(["测试"])

    assert service.status()["last_error"] == "HTTP 503: offline"


def test_embedding_service_rejects_dimension_mismatch() -> None:
    service = EmbeddingService(
        enabled=True,
        api_url="http://embedding.invalid/v1/embeddings",
        model="bge-m3",
        expected_dimension=3,
        post=lambda *_args, **_kwargs: _Response(
            200,
            {"data": [{"embedding": [0.1, 0.2]}]},
        ),
    )

    with pytest.raises(EmbeddingUnavailableError, match="dimension mismatch"):
        service.embed(["测试"])

    assert service.status()["failures"] == 1


def test_chroma_embedding_adapter_delegates_to_shared_service() -> None:
    class Service:
        model = "bge-m3"

        def embed(self, documents):
            assert documents == ["知识片段"]
            return [[0.1, 0.2]]

    adapter = ChromaEmbeddingFunction(Service())

    assert adapter(["知识片段"]) == [[0.1, 0.2]]
    assert "bge-m3" in adapter.name()


def test_chroma_embedding_adapter_supports_document_and_query_interfaces() -> None:
    class Service:
        model = "bge-m3"

        def embed(self, documents):
            return [[float(len(text)), 1.0] for text in documents]

    adapter = ChromaEmbeddingFunction(Service())

    assert adapter.embed_documents(["知识片段"]) == [[4.0, 1.0]]
    assert adapter.embed_query(["检索问题"]) == [[4.0, 1.0]]


def test_chroma_adapter_supports_real_query_texts(tmp_path) -> None:
    import chromadb

    class Service:
        model = "bge-m3"

        def embed(self, documents):
            vectors = []
            for text in documents:
                vectors.append([1.0, 0.0] if "身高" in text else [0.0, 1.0])
            return vectors

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        name="knowledge_adapter_test",
        embedding_function=ChromaEmbeddingFunction(Service()),
    )
    collection.add(
        ids=["height", "voice"],
        documents=["身高 160cm", "声优 尾崎由香"],
    )

    result = collection.query(query_texts=["身高是多少"], n_results=1)

    assert result["ids"] == [["height"]]


def test_build_embedding_service_uses_runtime_config() -> None:
    service = build_embedding_service(
        {
            "enabled": True,
            "provider": "ollama",
            "api_url": "http://127.0.0.1:11434/v1/embeddings",
            "api_key": "local",
            "model_name": "bge-m3",
            "timeout": 9,
            "expected_dimension": 1024,
        }
    )

    assert service.enabled is True
    assert service.provider == "ollama"
    assert service.model == "bge-m3"
    assert service.timeout == 9
    assert service.expected_dimension == 1024


def test_build_configured_service_reports_catalog_identity() -> None:
    service = build_configured_embedding_service(
        models={
            "local-bge": {
                "model": "bge-m3",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "ollama",
                "purposes": ["embedding"],
                "embedding_endpoint_path": "/embeddings",
                "embedding_dimension": 1024,
                "embedding_provider": "ollama",
            }
        },
        runtime_settings={"embedding_model_id": "local-bge"},
        legacy_config={},
    )

    status = service.status()
    assert status["model_id"] == "local-bge"
    assert status["configuration_source"] == "catalog"
    assert status["model"] == "bge-m3"
    assert status["dimension"] == 1024


def test_build_configured_service_preserves_disabled_legacy_state() -> None:
    service = build_configured_embedding_service(
        models={},
        runtime_settings={},
        legacy_config={
            "enabled": False,
            "api_url": "http://127.0.0.1:11434/v1/embeddings",
            "model_name": "bge-m3",
            "expected_dimension": 1024,
        },
    )

    assert service.enabled is False
    assert service.status()["configuration_source"] == "legacy"


def test_failover_embedding_service_tries_next_model() -> None:
    calls: list[str] = []

    def post_factory(tag: str, fail: bool = False):
        def _post(url, json=None, headers=None, timeout=None):
            calls.append(tag)
            if fail:
                class _Resp:
                    status_code = 500
                    text = "down"

                    def json(self):
                        return {}

                return _Resp()

            class _Resp:
                status_code = 200

                def json(self):
                    return {
                        "data": [
                            {"embedding": [0.1] * 4}
                            for _ in (json or {}).get("input", [])
                        ]
                    }

            return _Resp()

        return _post

    from modules.embeddings.service import EmbeddingService, FailoverEmbeddingService

    primary = EmbeddingService(
        enabled=True,
        api_url="http://local/embeddings",
        model="bge",
        expected_dimension=4,
        model_id="local",
        post=post_factory("local", fail=True),
    )
    backup = EmbeddingService(
        enabled=True,
        api_url="http://remote/embeddings",
        model="bge",
        expected_dimension=4,
        model_id="remote",
        post=post_factory("remote", fail=False),
    )
    service = FailoverEmbeddingService(
        [primary, backup],
        chain_model_ids=["local", "remote"],
        expected_dimension=4,
    )
    vectors = service.embed(["hello"])
    assert len(vectors) == 1
    assert calls == ["local", "remote"]
    status = service.status()
    assert status["active_model_id"] == "remote"
    assert status["chain_model_ids"] == ["local", "remote"]
