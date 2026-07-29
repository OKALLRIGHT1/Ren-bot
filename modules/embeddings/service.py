from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

import requests

from .catalog import (
    EmbeddingConfigurationError,
    ResolvedEmbeddingConfig,
    embedding_model_ids_from_runtime,
    normalize_embedding_model_ids,
    resolve_embedding_config,
)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when configured embeddings cannot produce valid vectors."""


class EmbeddingService:
    def __init__(
        self,
        *,
        enabled: bool,
        api_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 12.0,
        expected_dimension: Optional[int] = None,
        provider: str = "openai_compatible",
        model_id: str = "",
        configuration_source: str = "legacy",
        post: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.api_url = str(api_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout = max(0.1, float(timeout))
        self.expected_dimension = (
            max(1, int(expected_dimension)) if expected_dimension else None
        )
        self.provider = str(provider or "openai_compatible").strip()
        self.model_id = str(model_id or "").strip()
        self.configuration_source = str(
            configuration_source or "legacy"
        ).strip()
        self._post = post or requests.post
        self._lock = threading.Lock()
        self._calls = 0
        self._failures = 0
        self._dimension = self.expected_dimension
        self._last_success_at = ""
        self._last_error = ""

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _fail(self, message: str) -> EmbeddingUnavailableError:
        self._failures += 1
        self._last_error = str(message or "embedding unavailable")
        return EmbeddingUnavailableError(self._last_error)

    def embed(self, documents: Iterable[str]) -> list[list[float]]:
        texts = [str(item or "").strip() for item in documents]
        if not texts:
            return []
        with self._lock:
            self._calls += 1
            if not self.enabled:
                raise self._fail("embedding service is disabled")
            if not self.api_url or not self.model:
                raise self._fail("embedding service is not configured")

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            try:
                response = self._post(
                    self.api_url,
                    json={"input": texts, "model": self.model},
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise self._fail(str(exc)) from exc

            if int(getattr(response, "status_code", 0)) != 200:
                detail = str(getattr(response, "text", "") or "")[:200]
                raise self._fail(
                    f"HTTP {getattr(response, 'status_code', 0)}: {detail}".rstrip()
                )

            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(texts):
                raise self._fail(
                    f"invalid embedding count: got {len(data) if isinstance(data, list) else 0}, "
                    f"expected {len(texts)}"
                )

            vectors: list[list[float]] = []
            for item in data:
                raw_vector = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(raw_vector, list) or not raw_vector:
                    raise self._fail("invalid embedding vector")
                try:
                    vector = [float(value) for value in raw_vector]
                except (TypeError, ValueError) as exc:
                    raise self._fail("embedding vector contains non-numeric values") from exc
                vectors.append(vector)

            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1:
                raise self._fail("embedding vectors have inconsistent dimensions")
            dimension = dimensions.pop()
            expected = self.expected_dimension or self._dimension
            if expected and dimension != expected:
                raise self._fail(
                    f"embedding dimension mismatch: got {dimension}, expected {expected}"
                )

            self._dimension = dimension
            self._last_success_at = self._now_iso()
            self._last_error = ""
            return vectors

    def status(self) -> dict[str, Any]:
        configured = bool(self.api_url and self.model)
        if not self.enabled:
            state = "disabled"
        elif not configured:
            state = "unconfigured"
        elif self._last_error:
            state = "error"
        elif self._last_success_at:
            state = "ready"
        else:
            state = "unverified"
        return {
            "enabled": self.enabled,
            "configured": configured,
            "available": state == "ready",
            "state": state,
            "provider": self.provider,
            "model_id": self.model_id,
            "configuration_source": self.configuration_source,
            "api_url": self.api_url,
            "model": self.model,
            "dimension": self._dimension,
            "calls": self._calls,
            "failures": self._failures,
            "last_success_at": self._last_success_at,
            "last_error": self._last_error,
            "chain_model_ids": [self.model_id] if self.model_id else [],
            "active_model_id": self.model_id,
        }


class FailoverEmbeddingService:
    """Try catalog embedding models in order until one succeeds.

    Dimension is locked to the primary resolved model so the current vector
    index stays consistent across fallbacks.
    """

    def __init__(
        self,
        services: Sequence[EmbeddingService],
        *,
        chain_model_ids: Sequence[str] | None = None,
        expected_dimension: Optional[int] = None,
    ) -> None:
        self._services = [service for service in services if service is not None]
        if not self._services:
            raise EmbeddingConfigurationError("向量模型队列为空")
        self.chain_model_ids = [
            str(item).strip()
            for item in (chain_model_ids or [s.model_id for s in self._services])
            if str(item).strip()
        ]
        primary = self._services[0]
        self.enabled = any(service.enabled for service in self._services)
        self.api_url = primary.api_url
        self.api_key = primary.api_key
        self.model = primary.model
        self.timeout = primary.timeout
        self.expected_dimension = (
            expected_dimension
            if expected_dimension is not None
            else primary.expected_dimension
        )
        self.provider = primary.provider
        self.model_id = primary.model_id
        self.configuration_source = primary.configuration_source
        self._lock = threading.Lock()
        self._calls = 0
        self._failures = 0
        self._dimension = self.expected_dimension
        self._last_success_at = ""
        self._last_error = ""
        self._active_model_id = primary.model_id
        self._active_index = 0

    def embed(self, documents: Iterable[str]) -> list[list[float]]:
        texts = list(documents)
        if not texts:
            return []
        errors: list[str] = []
        with self._lock:
            self._calls += 1
            for index, service in enumerate(self._services):
                try:
                    vectors = service.embed(texts)
                except Exception as exc:  # noqa: BLE001 - collect and continue
                    errors.append(f"{service.model_id or service.model or index}: {exc}")
                    continue
                self._active_index = index
                self._active_model_id = service.model_id
                self.api_url = service.api_url
                self.api_key = service.api_key
                self.model = service.model
                self.timeout = service.timeout
                self.provider = service.provider
                self.model_id = service.model_id
                self.configuration_source = service.configuration_source
                self._dimension = service.expected_dimension or getattr(
                    service, "_dimension", None
                )
                self._last_success_at = EmbeddingService._now_iso()
                self._last_error = ""
                return vectors
            detail = "；".join(errors) if errors else "all embedding models failed"
            self._failures += 1
            self._last_error = detail
            raise EmbeddingUnavailableError(detail)

    def status(self) -> dict[str, Any]:
        active = self._services[self._active_index]
        base = active.status()
        configured = any(
            bool(service.api_url and service.model) for service in self._services
        )
        if not self.enabled:
            state = "disabled"
        elif not configured:
            state = "unconfigured"
        elif self._last_error:
            state = "error"
        elif self._last_success_at:
            state = "ready"
        else:
            state = "unverified"
        base.update(
            {
                "enabled": self.enabled,
                "configured": configured,
                "available": state == "ready",
                "state": state,
                "model_id": self.model_id,
                "active_model_id": self._active_model_id,
                "chain_model_ids": list(self.chain_model_ids),
                "calls": self._calls,
                "failures": self._failures,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error,
                "dimension": self._dimension or base.get("dimension"),
                "configuration_source": self.configuration_source,
            }
        )
        return base


class ChromaEmbeddingFunction:
    """Small adapter so Chroma collections share the configured embedding service."""

    def __init__(self, service: EmbeddingService | FailoverEmbeddingService) -> None:
        self.service = service

    def __call__(self, input: Iterable[str]) -> list[list[float]]:
        documents = [input] if isinstance(input, str) else input
        return self.service.embed(documents)

    def embed_documents(self, input: Iterable[str]) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: Iterable[str]) -> list[list[float]]:
        return self(input)

    def name(self) -> str:
        model = str(getattr(self.service, "model", "") or "unconfigured")
        return f"live2d-suzu-{model}"

    def is_legacy(self) -> bool:
        # The runtime service contains credentials and cannot be reconstructed
        # from Chroma collection metadata alone.
        return True


def build_embedding_service(config: dict[str, Any]) -> EmbeddingService:
    expected_dimension = config.get("expected_dimension")
    return EmbeddingService(
        enabled=bool(config.get("enabled", False)),
        provider=str(config.get("provider") or "openai_compatible"),
        api_url=str(config.get("api_url") or ""),
        api_key=str(config.get("api_key") or ""),
        model=str(config.get("model_name") or config.get("model") or ""),
        timeout=float(config.get("timeout") or 12),
        expected_dimension=(
            int(expected_dimension) if expected_dimension not in {None, "", 0, "0"} else None
        ),
    )


def _service_from_resolved(
    resolved: ResolvedEmbeddingConfig,
    *,
    post: Optional[Callable[..., Any]] = None,
) -> EmbeddingService:
    return EmbeddingService(
        enabled=resolved.enabled,
        provider=resolved.provider,
        api_url=resolved.api_url,
        api_key=resolved.api_key,
        model=resolved.model_name,
        timeout=resolved.timeout,
        expected_dimension=resolved.expected_dimension,
        model_id=resolved.model_id,
        configuration_source=resolved.source,
        post=post,
    )


def build_configured_embedding_service(
    *,
    models: dict[str, object],
    runtime_settings: dict[str, object],
    legacy_config: dict[str, object],
    post: Optional[Callable[..., Any]] = None,
):
    chain_ids = embedding_model_ids_from_runtime(runtime_settings)
    # Allow callers that still only pass embedding_model_id.
    if not chain_ids:
        chain_ids = normalize_embedding_model_ids(
            runtime_settings.get("embedding_model_id")
            if isinstance(runtime_settings, dict)
            else ""
        )

    resolved = resolve_embedding_config(
        model_ids=chain_ids,
        models=models,
        legacy_config=legacy_config,
    )

    if not resolved.chain_model_ids:
        return _service_from_resolved(resolved, post=post)

    services: list[EmbeddingService] = []
    for model_id in resolved.chain_model_ids:
        item = resolve_embedding_config(
            model_id=model_id,
            models=models,
            legacy_config=legacy_config,
        )
        services.append(_service_from_resolved(item, post=post))

    if len(services) == 1:
        return services[0]
    return FailoverEmbeddingService(
        services,
        chain_model_ids=resolved.chain_model_ids,
        expected_dimension=resolved.expected_dimension,
    )
