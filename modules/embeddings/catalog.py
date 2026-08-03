from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from modules.model_catalog import join_endpoint_url, model_has_purpose


class EmbeddingConfigurationError(ValueError):
    """Raised when a selected catalog model cannot provide embeddings."""


@dataclass(frozen=True)
class ResolvedEmbeddingConfig:
    source: str
    model_id: str
    enabled: bool
    provider: str
    api_url: str
    api_key: str
    model_name: str
    timeout: float
    expected_dimension: int | None
    # Ordered catalog ids that were validated for the current selection.
    # Empty means legacy-only selection.
    chain_model_ids: tuple[str, ...] = ()


def _optional_positive_int(value: object) -> int | None:
    if value in {None, "", 0, "0"}:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise EmbeddingConfigurationError("向量维度必须是正整数") from exc
    if number <= 0:
        raise EmbeddingConfigurationError("向量维度必须是正整数")
    return number


def _positive_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _resolve_environment_key(
    value: object,
    environ: Mapping[str, str],
) -> str:
    if isinstance(value, str):
        names = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        names = [str(item or "").strip() for item in value]
    else:
        names = []
    for name in names:
        if not name:
            continue
        secret = str(environ.get(name, "") or "").strip()
        if secret:
            return secret
    return ""


def normalize_embedding_model_ids(value: object) -> list[str]:
    """Normalize a primary id / chain list into a de-duplicated ordered list."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # allow "a,b,c" as a compact form
        parts = [part.strip() for part in text.replace(";", ",").split(",")]
        ordered = [part for part in parts if part]
    elif isinstance(value, (list, tuple)):
        ordered = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        ordered = [str(value).strip()] if str(value).strip() else []
    seen: set[str] = set()
    result: list[str] = []
    for item in ordered:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def embedding_model_ids_from_runtime(runtime_settings: Mapping[str, Any] | None) -> list[str]:
    """Read ordered embedding model ids from runtime settings.

    Prefer embedding_model_ids (queue). Fall back to legacy single
    embedding_model_id for older installs.
    """
    data = runtime_settings if isinstance(runtime_settings, Mapping) else {}
    chain = normalize_embedding_model_ids(data.get("embedding_model_ids"))
    if chain:
        return chain
    primary = str(data.get("embedding_model_id") or "").strip()
    return [primary] if primary else []


def resolve_embedding_config(
    *,
    model_id: str = "",
    models: dict[str, object],
    legacy_config: dict[str, object],
    environ: Mapping[str, str] | None = None,
    model_ids: Sequence[str] | None = None,
) -> ResolvedEmbeddingConfig:
    """Resolve the primary embedding endpoint.

    model_ids: optional ordered queue. The first valid catalog entry becomes
    the active primary config; remaining ids are validated and retained so a
    failover wrapper can try them later. Empty selection falls back to legacy
    EMBEDDING_* config.
    """
    chain = normalize_embedding_model_ids(
        model_ids if model_ids is not None else model_id
    )
    if not chain:
        return ResolvedEmbeddingConfig(
            source="legacy",
            model_id="",
            enabled=bool(legacy_config.get("enabled", False)),
            provider=str(
                legacy_config.get("provider") or "openai_compatible"
            ).strip(),
            api_url=str(legacy_config.get("api_url") or "").strip(),
            api_key=str(legacy_config.get("api_key") or "").strip(),
            model_name=str(
                legacy_config.get("model_name")
                or legacy_config.get("model")
                or ""
            ).strip(),
            timeout=_positive_float(legacy_config.get("timeout"), 12.0),
            expected_dimension=_optional_positive_int(
                legacy_config.get("expected_dimension")
            ),
            chain_model_ids=(),
        )

    validated: list[ResolvedEmbeddingConfig] = []
    errors: list[str] = []
    for selected in chain:
        try:
            resolved = _resolve_catalog_model(
                selected,
                models=models,
                environ=environ,
            )
        except EmbeddingConfigurationError as exc:
            errors.append(f"{selected}: {exc}")
            continue
        validated.append(resolved)

    if not validated:
        detail = "；".join(errors) if errors else "无可用向量模型"
        raise EmbeddingConfigurationError(f"向量模型队列无效：{detail}")

    primary = validated[0]
    # All members of the active failover queue must share the same dimension so
    # the current Chroma index remains usable across fallbacks.
    expected = primary.expected_dimension
    compatible_ids: list[str] = [primary.model_id]
    for item in validated[1:]:
        if expected is not None and item.expected_dimension != expected:
            errors.append(
                f"{item.model_id}: 维度 {item.expected_dimension} 与主模型 {expected} 不一致"
            )
            continue
        compatible_ids.append(item.model_id)

    return ResolvedEmbeddingConfig(
        source=primary.source,
        model_id=primary.model_id,
        enabled=primary.enabled,
        provider=primary.provider,
        api_url=primary.api_url,
        api_key=primary.api_key,
        model_name=primary.model_name,
        timeout=primary.timeout,
        expected_dimension=primary.expected_dimension,
        chain_model_ids=tuple(compatible_ids),
    )


def _resolve_catalog_model(
    selected: str,
    *,
    models: dict[str, object],
    environ: Mapping[str, str] | None = None,
) -> ResolvedEmbeddingConfig:
    row = models.get(selected)
    if not isinstance(row, dict):
        raise EmbeddingConfigurationError(f"所选向量模型 {selected} 不存在")
    if not model_has_purpose(row, "embedding", allow_untagged=False):
        raise EmbeddingConfigurationError(f"模型 {selected} 未声明向量用途")

    model_name = str(row.get("model") or "").strip()
    full_url = str(row.get("embedding_api_url") or "").strip()
    base_url = str(row.get("base_url") or "").strip()
    endpoint_path = str(
        row.get("embedding_endpoint_path") or "/embeddings"
    ).strip()
    dimension = _optional_positive_int(row.get("embedding_dimension"))
    if not model_name or not (full_url or base_url) or dimension is None:
        raise EmbeddingConfigurationError(
            f"模型 {selected} 缺少上游模型名、嵌入接口或有效向量维度"
        )

    env = environ if environ is not None else os.environ
    api_key = str(row.get("api_key") or "").strip()
    if not api_key:
        api_key = _resolve_environment_key(row.get("api_key_env"), env)

    return ResolvedEmbeddingConfig(
        source="catalog",
        model_id=selected,
        enabled=True,
        provider=str(
            row.get("embedding_provider") or "openai_compatible"
        ).strip(),
        api_url=full_url or join_endpoint_url(base_url, endpoint_path),
        api_key=api_key,
        model_name=model_name,
        timeout=_positive_float(row.get("embedding_timeout"), 12.0),
        expected_dimension=dimension,
        chain_model_ids=(selected,),
    )
