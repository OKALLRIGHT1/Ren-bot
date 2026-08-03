from __future__ import annotations

import hashlib
from typing import Any, Optional

from modules.embeddings import EmbeddingUnavailableError


class MemoryVectorIndex:
    def __init__(self, *, repository: Any, collection: Any, embedding_service: Any) -> None:
        self.repository = repository
        self.collection = collection
        self.embedding_service = embedding_service
        self.model = str(getattr(embedding_service, "model", "") or "").strip()
        embedding_status = dict(embedding_service.status() or {})
        self.dimension = self._optional_int(embedding_status.get("dimension"))
        metadata = dict(getattr(collection, "metadata", None) or {})
        collection_model = str(metadata.get("embedding_model") or "").strip()
        collection_dimension = self._optional_int(metadata.get("embedding_dimension"))
        if collection_model and self.model and collection_model != self.model:
            raise ValueError(
                f"embedding model mismatch: collection={collection_model}, runtime={self.model}"
            )
        if collection_dimension and self.dimension and collection_dimension != self.dimension:
            raise ValueError(
                "embedding dimension mismatch: "
                f"collection={collection_dimension}, runtime={self.dimension}"
            )

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def canonical_document(record: dict[str, Any]) -> str:
        parts = [
            f"kind: {str(record.get('kind') or 'other').strip()}",
            f"key: {str(record.get('key') or '').strip()}",
            f"content: {str(record.get('content') or '').strip()}",
        ]
        return "\n".join(parts)

    @staticmethod
    def content_hash(document: str) -> str:
        return hashlib.sha256(str(document).encode("utf-8")).hexdigest()

    def _delete_record(self, record_id: str) -> None:
        self.collection.delete(ids=[record_id])
        status = dict(self.embedding_service.status() or {})
        dimension = self._optional_int(status.get("dimension")) or self.dimension or 0
        self.repository.mark_vector_job_indexed(
            record_id,
            model=self.model,
            dimension=dimension,
            content_hash="",
        )

    def _compatibility(self) -> dict[str, Any]:
        status = dict(self.embedding_service.status() or {})
        dimension = self.dimension or self._optional_int(status.get("dimension"))
        checker = getattr(self.repository, "vector_index_compatibility", None)
        if not callable(checker):
            return {
                "rebuild_required": False,
                "incompatible_count": 0,
                "indexed_count": 0,
            }
        return dict(checker(model=self.model, dimension=dimension) or {})

    def process_pending(self, *, limit: int = 100) -> dict[str, int]:
        result = {"indexed": 0, "deleted": 0, "failed": 0}
        if self._compatibility().get("rebuild_required"):
            return result
        jobs = self.repository.list_vector_jobs(status="pending", limit=limit)
        for job in jobs:
            record_id = str(job.get("record_id") or "").strip()
            try:
                record = self.repository.get_record(record_id)
                operation = str(job.get("operation") or "upsert")
                if operation == "delete" or not record or record.get("status") != "active":
                    self._delete_record(record_id)
                    result["deleted"] += 1
                    continue

                document = self.canonical_document(record)
                vectors = self.embedding_service.embed([document])
                vector = vectors[0]
                dimension = len(vector)
                if self.dimension and dimension != self.dimension:
                    raise ValueError(
                        f"embedding dimension mismatch: got {dimension}, expected {self.dimension}"
                    )
                self.dimension = dimension
                metadata = {
                    "record_id": record_id,
                    "subject_id": str(record.get("subject_id") or ""),
                    "session_id": str(record.get("session_id") or ""),
                    "kind": str(record.get("kind") or "other"),
                    "key": str(record.get("key") or ""),
                    "embedding_model": self.model,
                    "embedding_dimension": dimension,
                    "content_hash": self.content_hash(document),
                }
                self.collection.upsert(
                    ids=[record_id],
                    documents=[document],
                    embeddings=[vector],
                    metadatas=[metadata],
                )
                self.repository.mark_vector_job_indexed(
                    record_id,
                    model=self.model,
                    dimension=dimension,
                    content_hash=metadata["content_hash"],
                )
                result["indexed"] += 1
            except EmbeddingUnavailableError as exc:
                self.repository.mark_vector_job_failed(record_id, str(exc))
                result["failed"] += 1
                break
            except Exception as exc:
                self.repository.mark_vector_job_failed(record_id, str(exc))
                result["failed"] += 1
        return result

    @staticmethod
    def _scope_matches(
        metadata: dict[str, Any],
        *,
        person_id: str,
        session_id: str,
    ) -> bool:
        subject = str(metadata.get("subject_id") or "").strip()
        person = str(person_id or "owner").strip() or "owner"
        if person == "owner":
            if subject not in {"", "owner"}:
                return False
        elif subject != person:
            return False
        item_session = str(metadata.get("session_id") or "").strip()
        requested_session = str(session_id or "").strip()
        return not requested_session or item_session in {"", requested_session}

    def query(
        self,
        text: str,
        *,
        person_id: str,
        session_id: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        clean_text = str(text or "").strip()
        if not clean_text:
            return []
        compatibility = self._compatibility()
        if compatibility.get("rebuild_required"):
            raise EmbeddingUnavailableError(
                "嵌入模型已变化，请重建当前索引后再检索"
            )
        vector = self.embedding_service.embed([clean_text])[0]
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=max(1, int(limit)) * 3,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        rows: list[dict[str, Any]] = []
        for index, raw_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            if not self._scope_matches(
                metadata,
                person_id=person_id,
                session_id=session_id,
            ):
                continue
            distance = float(distances[index]) if index < len(distances) else 1.0
            rows.append(
                {
                    "id": str(metadata.get("record_id") or raw_id),
                    "document": documents[index] if index < len(documents) else "",
                    "metadata": metadata,
                    "vector_score": max(0.0, min(1.0, 1.0 - distance)),
                }
            )
            if len(rows) >= max(1, int(limit)):
                break
        return rows

    def status(self) -> dict[str, Any]:
        embedding = dict(self.embedding_service.status() or {})
        compatibility = self._compatibility()
        return {
            "collection_count": int(self.collection.count()),
            "jobs": self.repository.vector_job_stats(),
            "embedding": embedding,
            "model": self.model,
            "dimension": self.dimension or embedding.get("dimension"),
            "rebuild_required": bool(compatibility.get("rebuild_required")),
            "incompatible_count": int(
                compatibility.get("incompatible_count") or 0
            ),
        }
