from __future__ import annotations

import pytest

from modules.embeddings import EmbeddingService, EmbeddingUnavailableError
from modules.memory_core import MemoryCoreService
from modules.memory_core.vector_index import MemoryVectorIndex
from modules.memory_sqlite import MemorySQLite


class _Embedding:
    enabled = True
    model = "bge-m3"

    def embed(self, documents):
        return [[float(len(text)), 1.0] for text in documents]

    def status(self):
        return {
            "enabled": True,
            "available": True,
            "model": self.model,
            "dimension": 2,
            "calls": 0,
            "failures": 0,
            "last_error": "",
        }


class _ChangedEmbedding:
    enabled = True
    model = "new-embedding"

    def embed(self, documents):
        return [[float(len(text)), 1.0, 2.0] for text in documents]

    def status(self):
        return {
            "enabled": True,
            "available": True,
            "model": self.model,
            "dimension": 3,
            "calls": 0,
            "failures": 0,
            "last_error": "",
        }


class _Collection:
    def __init__(self, metadata=None):
        self.metadata = dict(metadata or {})
        self.rows = {}
        self.query_result = None

    def count(self):
        return len(self.rows)

    def upsert(self, *, ids, documents, embeddings, metadatas):
        for index, record_id in enumerate(ids):
            self.rows[record_id] = {
                "document": documents[index],
                "embedding": embeddings[index],
                "metadata": metadatas[index],
            }

    def delete(self, *, ids):
        for record_id in ids:
            self.rows.pop(record_id, None)

    def query(self, **_kwargs):
        if self.query_result is not None:
            return self.query_result
        ids = list(self.rows)
        return {
            "ids": [ids],
            "documents": [[self.rows[item]["document"] for item in ids]],
            "metadatas": [[self.rows[item]["metadata"] for item in ids]],
            "distances": [[0.1 for _item in ids]],
        }


def _core(tmp_path):
    core = MemoryCoreService(MemorySQLite(str(tmp_path / "memory.sqlite")))
    core.initialize()
    return core


def test_same_embedding_model_and_dimension_reuses_index(tmp_path) -> None:
    core = _core(tmp_path)
    core.upsert_memory_record(
        kind="preference",
        key="likes.music.0",
        content="喜欢 MyGO",
        subject_id="owner",
        session_id="owner_shared",
        source_type="test",
        source_id="compat-same",
    )
    index = MemoryVectorIndex(
        repository=core.repository,
        collection=_Collection(
            {"embedding_model": "bge-m3", "embedding_dimension": 2}
        ),
        embedding_service=_Embedding(),
    )
    assert index.process_pending()["indexed"] == 1

    compatibility = core.repository.vector_index_compatibility(
        model="bge-m3",
        dimension=2,
    )

    assert compatibility["rebuild_required"] is False
    assert compatibility["incompatible_count"] == 0


def test_changed_embedding_model_requires_explicit_rebuild(tmp_path) -> None:
    core = _core(tmp_path)
    core.upsert_memory_record(
        kind="preference",
        key="likes.music.0",
        content="喜欢 MyGO",
        subject_id="owner",
        session_id="owner_shared",
        source_type="test",
        source_id="compat-changed",
    )
    old_index = MemoryVectorIndex(
        repository=core.repository,
        collection=_Collection(
            {"embedding_model": "bge-m3", "embedding_dimension": 2}
        ),
        embedding_service=_Embedding(),
    )
    assert old_index.process_pending()["indexed"] == 1

    new_index = MemoryVectorIndex(
        repository=core.repository,
        collection=_Collection(
            {"embedding_model": "new-embedding", "embedding_dimension": 3}
        ),
        embedding_service=_ChangedEmbedding(),
    )

    assert new_index.status()["rebuild_required"] is True
    assert new_index.status()["incompatible_count"] == 1
    with pytest.raises(EmbeddingUnavailableError, match="重建"):
        new_index.query("喜欢什么", person_id="owner")


def test_vector_index_processes_upsert_and_delete_jobs(tmp_path) -> None:
    core = _core(tmp_path)
    record_id = core.upsert_memory_record(
        kind="preference",
        key="likes.music.0",
        content="喜欢 MyGO",
        subject_id="owner",
        session_id="owner_shared",
        source_type="test",
        source_id="vector-index",
    )
    collection = _Collection(
        {"embedding_model": "bge-m3", "embedding_dimension": 2}
    )
    index = MemoryVectorIndex(
        repository=core.repository,
        collection=collection,
        embedding_service=_Embedding(),
    )

    result = index.process_pending()

    assert result == {"indexed": 1, "deleted": 0, "failed": 0}
    assert record_id in collection.rows
    row = collection.rows[record_id]
    assert "likes.music.0" in row["document"]
    assert row["metadata"]["record_id"] == record_id
    assert row["metadata"]["subject_id"] == "owner"
    assert row["metadata"]["embedding_model"] == "bge-m3"
    assert core.vector_job_stats()["indexed"] == 1

    core.update_memory_record(record_id, status="archived")
    result = index.process_pending()
    assert result == {"indexed": 0, "deleted": 1, "failed": 0}
    assert record_id not in collection.rows


def test_vector_index_filters_query_results_by_person_and_session(tmp_path) -> None:
    core = _core(tmp_path)
    collection = _Collection(
        {"embedding_model": "bge-m3", "embedding_dimension": 2}
    )
    collection.query_result = {
        "ids": [["owner-id", "qq-id", "other-session"]],
        "documents": [["owner memory", "qq memory", "old session"]],
        "metadatas": [[
            {"record_id": "owner-id", "subject_id": "owner", "session_id": ""},
            {"record_id": "qq-id", "subject_id": "qq:42", "session_id": "private:42"},
            {"record_id": "other-session", "subject_id": "qq:42", "session_id": "private:99"},
        ]],
        "distances": [[0.1, 0.2, 0.05]],
    }
    index = MemoryVectorIndex(
        repository=core.repository,
        collection=collection,
        embedding_service=_Embedding(),
    )

    results = index.query(
        "上次说过什么",
        person_id="qq:42",
        session_id="private:42",
        limit=5,
    )

    assert [item["id"] for item in results] == ["qq-id"]
    assert results[0]["vector_score"] == pytest.approx(0.8)


def test_vector_index_rejects_collection_model_mismatch(tmp_path) -> None:
    core = _core(tmp_path)
    collection = _Collection(
        {"embedding_model": "old-model", "embedding_dimension": 384}
    )

    with pytest.raises(ValueError, match="model mismatch"):
        MemoryVectorIndex(
            repository=core.repository,
            collection=collection,
            embedding_service=_Embedding(),
        )


def test_vector_index_stops_batch_after_embedding_becomes_unavailable(tmp_path) -> None:
    class OfflineEmbedding(_Embedding):
        def embed(self, _documents):
            raise EmbeddingUnavailableError("embedding offline")

    core = _core(tmp_path)
    for index in range(2):
        core.upsert_memory_record(
            kind="fact",
            key=f"fact.{index}",
            content=f"记忆 {index}",
            subject_id="owner",
            source_type="test",
            source_id=f"offline-{index}",
        )
    collection = _Collection(
        {"embedding_model": "bge-m3", "embedding_dimension": 2}
    )
    index = MemoryVectorIndex(
        repository=core.repository,
        collection=collection,
        embedding_service=OfflineEmbedding(),
    )

    result = index.process_pending()

    assert result == {"indexed": 0, "deleted": 0, "failed": 1}
    assert core.vector_job_stats()["failed"] == 1
    assert core.vector_job_stats()["pending"] == 1


def test_vector_index_works_with_persistent_chroma(tmp_path) -> None:
    import chromadb

    class Response:
        status_code = 200
        text = ""

        def __init__(self, count):
            self.count = count

        def json(self):
            return {"data": [{"embedding": [1.0, 0.0]} for _ in range(self.count)]}

    service = EmbeddingService(
        enabled=True,
        api_url="http://embedding.test/v1/embeddings",
        model="bge-m3",
        expected_dimension=2,
        post=lambda _url, **kwargs: Response(len(kwargs["json"]["input"])),
    )
    core = _core(tmp_path)
    record_id = core.upsert_memory_record(
        kind="episode",
        content="发布会议持续四十分钟",
        subject_id="owner",
        source_type="test",
        source_id="real-chroma",
    )
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        name="memory_core_test",
        metadata={"embedding_model": "bge-m3", "embedding_dimension": 2},
    )
    index = MemoryVectorIndex(
        repository=core.repository,
        collection=collection,
        embedding_service=service,
    )

    assert index.process_pending()["indexed"] == 1
    assert collection.count() == 1
    results = index.query("会议时长", person_id="owner", limit=3)
    assert [item["id"] for item in results] == [record_id]
