"""Model catalog helpers: purpose tags + filtering for plugins/UI."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


# Stable purpose ids used in custom_models.json and plugin filters.
MODEL_PURPOSE_OPTIONS: List[tuple[str, str]] = [
    ("chat", "聊天"),
    ("tool_reasoning", "推理/工具"),
    ("summary", "总结"),
    ("web_search", "联网搜索"),
    ("vision", "视觉理解"),
    ("image_gen", "画图"),
    ("image_edit", "图生图"),
    ("code", "代码"),
    ("translation", "翻译"),
    ("embedding", "向量"),
]

MODEL_PURPOSE_LABELS: Dict[str, str] = {key: label for key, label in MODEL_PURPOSE_OPTIONS}
MODEL_PURPOSE_IDS: Set[str] = set(MODEL_PURPOSE_LABELS)


def join_endpoint_url(base_url: str, endpoint_path: str) -> str:
    """Join base_url + endpoint_path without duplicating a trailing /v1 segment.

    Examples:
      https://host/v1 + /v1/images/generations -> https://host/v1/images/generations
      https://host     + /v1/images/generations -> https://host/v1/images/generations
      https://host/v1/ + images/generations     -> https://host/v1/images/generations
      https://host/v1/embedding + /embeddings   -> https://host/v1/embeddings
    """
    base = str(base_url or "").strip().rstrip("/")
    path = str(endpoint_path or "").strip()
    if not path:
        return base
    if not path.startswith("/"):
        path = "/" + path
    # Common misconfig for SiliconFlow/OpenAI-compatible embedding providers:
    # base already ends with /embedding(s) and path is also /embeddings.
    if path in {"/embeddings", "/embedding"} and (
        base.endswith("/embeddings") or base.endswith("/embedding")
    ):
        return base[: -len("/embedding")] + "/embeddings" if base.endswith("/embedding") and not base.endswith("/embeddings") else (
            base if base.endswith("/embeddings") else base + path
        )
    # base already ends with /v1 (common OpenAI-compatible root) and path also starts with /v1/
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    # base is exactly .../v1 and path is exactly /v1
    elif base.endswith("/v1") and path == "/v1":
        return base
    return base + path

# Accept Chinese / alias labels so older configs and free-text import still work.
_PURPOSE_ALIASES: Dict[str, str] = {
    "chat": "chat",
    "default": "chat",
    "闲聊": "chat",
    "聊天": "chat",
    "对话": "chat",
    "text": "chat",
    "tool_reasoning": "tool_reasoning",
    "reasoning": "tool_reasoning",
    "tool": "tool_reasoning",
    "推理": "tool_reasoning",
    "工具": "tool_reasoning",
    "summary": "summary",
    "总结": "summary",
    "摘要": "summary",
    "web_search": "web_search",
    "websearch": "web_search",
    "search": "web_search",
    "联网搜索": "web_search",
    "搜索": "web_search",
    "vision": "vision",
    "视觉": "vision",
    "视觉理解": "vision",
    "image": "image_gen",
    "image_gen": "image_gen",
    "image_generation": "image_gen",
    "draw": "image_gen",
    "drawing": "image_gen",
    "画图": "image_gen",
    "生图": "image_gen",
    "绘图": "image_gen",
    "image_edit": "image_edit",
    "image_edits": "image_edit",
    "img2img": "image_edit",
    "图生图": "image_edit",
    "修图": "image_edit",
    "code": "code",
    "codex": "code",
    "代码": "code",
    "translation": "translation",
    "translate": "translation",
    "翻译": "translation",
    "embedding": "embedding",
    "embed": "embedding",
    "向量": "embedding",
}

IMAGE_PURPOSES: Set[str] = {"image_gen", "image_edit"}


def normalize_purpose(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    # Keep original case lookup for Chinese labels.
    raw = str(value or "").strip()
    if raw in _PURPOSE_ALIASES:
        return _PURPOSE_ALIASES[raw]
    if text in _PURPOSE_ALIASES:
        return _PURPOSE_ALIASES[text]
    if raw in MODEL_PURPOSE_IDS:
        return raw
    if text in MODEL_PURPOSE_IDS:
        return text
    # Soft match: strip spaces/underscores/dashes.
    compact = text.replace(" ", "").replace("_", "").replace("-", "")
    for alias, purpose in _PURPOSE_ALIASES.items():
        if alias.replace(" ", "").replace("_", "").replace("-", "") == compact:
            return purpose
    return text


def normalize_purposes(values: Any) -> List[str]:
    if values is None:
        return []
    items: List[Any]
    if isinstance(values, str):
        raw = values.strip()
        if not raw:
            return []
        if "," in raw or "，" in raw or ";" in raw or "；" in raw or "|" in raw:
            parts: List[str] = []
            buf = raw
            for sep in ("，", ",", "；", ";", "|"):
                buf = buf.replace(sep, ",")
            parts = [p.strip() for p in buf.split(",") if p.strip()]
            items = parts
        else:
            items = [raw]
    elif isinstance(values, (list, tuple, set)):
        items = list(values)
    else:
        items = [values]

    result: List[str] = []
    seen: Set[str] = set()
    for item in items:
        purpose = normalize_purpose(item)
        if not purpose or purpose in seen:
            continue
        seen.add(purpose)
        result.append(purpose)
    return result


def normalize_model_selection(values: Any, *, max_items: int = 0) -> List[str]:
    if isinstance(values, str):
        items: Iterable[Any] = values.replace("，", ",").split(",")
    elif isinstance(values, (list, tuple, set)):
        items = values
    else:
        items = []

    result: List[str] = []
    seen: Set[str] = set()
    limit = max(0, int(max_items or 0))
    for item in items:
        model_id = str(item or "").strip()
        if not model_id or model_id in seen:
            continue
        result.append(model_id)
        seen.add(model_id)
        if limit and len(result) >= limit:
            break
    return result


def get_model_purposes(model_cfg: Any) -> List[str]:
    if not isinstance(model_cfg, dict):
        return []
    if "purposes" in model_cfg:
        return normalize_purposes(model_cfg.get("purposes"))
    if "purpose" in model_cfg:
        return normalize_purposes(model_cfg.get("purpose"))
    if "用途" in model_cfg:
        return normalize_purposes(model_cfg.get("用途"))
    tags = model_cfg.get("tags")
    if isinstance(tags, (list, tuple, set, str)):
        # Only treat tags as purposes when they normalize to known ids.
        normalized = normalize_purposes(tags)
        return [p for p in normalized if p in MODEL_PURPOSE_IDS]
    return []


def model_has_purpose(
    model_cfg: Any,
    purpose: str | Sequence[str],
    *,
    allow_untagged: bool = False,
) -> bool:
    purposes = get_model_purposes(model_cfg)
    if not purposes:
        return bool(allow_untagged)
    wanted = normalize_purposes(purpose)
    if not wanted:
        return False
    return any(item in purposes for item in wanted)


def format_purposes_label(purposes: Sequence[str]) -> str:
    labels: List[str] = []
    for purpose in purposes:
        labels.append(MODEL_PURPOSE_LABELS.get(purpose, purpose))
    return "、".join(labels) if labels else "未设置"


def list_models_by_purpose(
    catalog: Optional[Dict[str, Any]] = None,
    purpose: str | Sequence[str] = "",
    *,
    allow_untagged: bool = False,
    preferred_order: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return model ids matching purpose, optionally ordered by preferred_order."""
    if catalog is None:
        try:
            from config import MODELS as catalog  # type: ignore
        except Exception:
            catalog = {}
    if not isinstance(catalog, dict):
        return []

    wanted = normalize_purposes(purpose)
    matched: List[str] = []
    for model_id, cfg in catalog.items():
        if not wanted:
            matched.append(str(model_id))
            continue
        if model_has_purpose(cfg, wanted, allow_untagged=allow_untagged):
            matched.append(str(model_id))

    if not preferred_order:
        return matched

    order = [str(x).strip() for x in preferred_order if str(x).strip()]
    ordered: List[str] = []
    seen: Set[str] = set()
    for model_id in order:
        if model_id in matched and model_id not in seen:
            ordered.append(model_id)
            seen.add(model_id)
    for model_id in matched:
        if model_id not in seen:
            ordered.append(model_id)
            seen.add(model_id)
    return ordered


def provider_fields_from_model(
    model_id: str,
    model_cfg: Optional[Dict[str, Any]] = None,
    *,
    request_defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map a catalog model row into image-provider fields for plugins."""
    cfg = model_cfg if isinstance(model_cfg, dict) else {}
    if not cfg:
        try:
            from config import MODELS

            row = MODELS.get(model_id)
            if isinstance(row, dict):
                cfg = row
        except Exception:
            cfg = {}

    defaults = request_defaults if isinstance(request_defaults, dict) else {}
    out: Dict[str, Any] = {
        "name": str(model_id),
        "model_ref": str(model_id),
    }
    if cfg.get("base_url"):
        out["base_url"] = cfg.get("base_url")
    if cfg.get("api_key"):
        out["api_key"] = cfg.get("api_key")
    if cfg.get("model"):
        out["model_name"] = cfg.get("model")
    if cfg.get("endpoint_path"):
        out["endpoint_path"] = cfg.get("endpoint_path")
    if cfg.get("edit_endpoint_path"):
        out["edit_endpoint_path"] = cfg.get("edit_endpoint_path")
    if cfg.get("api_mode"):
        out["api_mode"] = cfg.get("api_mode")
    if cfg.get("api_key_env"):
        out["api_key_env"] = cfg.get("api_key_env")
    for key in (
        "size_value",
        "quality",
        "style",
        "negative_prompt",
        "extra_body_json",
        "request_timeout_sec",
        "input_image_field",
        "input_image_format",
        "include_chat_image_part",
    ):
        if cfg.get(key) not in (None, ""):
            out[key] = cfg.get(key)
        elif defaults.get(key) not in (None, ""):
            out[key] = defaults.get(key)

    purposes = get_model_purposes(cfg)
    if purposes:
        out["purposes"] = purposes
    # Sensible defaults for image models when user only filled base_url/model.
    if not out.get("endpoint_path") and any(p in IMAGE_PURPOSES for p in purposes):
        out["endpoint_path"] = "/v1/images/generations"
    if not out.get("edit_endpoint_path") and (
        "image_edit" in purposes or "image_gen" in purposes
    ):
        out["edit_endpoint_path"] = "/v1/images/edits"
    if not out.get("api_mode") and any(p in IMAGE_PURPOSES for p in purposes):
        out["api_mode"] = "images"
    return out


def embedding_fields_from_model(
    model_id: str,
    model_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map one catalog row into normalized embedding endpoint fields."""
    cfg = model_cfg if isinstance(model_cfg, dict) else {}
    base_url = str(cfg.get("base_url") or "").strip()
    full_url = str(cfg.get("embedding_api_url") or "").strip()
    endpoint_path = str(
        cfg.get("embedding_endpoint_path") or "/embeddings"
    ).strip()
    raw_dimension = cfg.get("embedding_dimension")
    try:
        dimension = int(raw_dimension or 0)
    except (TypeError, ValueError):
        dimension = 0
    return {
        "model_id": str(model_id or "").strip(),
        "model_name": str(cfg.get("model") or "").strip(),
        "provider": str(
            cfg.get("embedding_provider") or "openai_compatible"
        ).strip(),
        "api_url": full_url or (
            join_endpoint_url(base_url, endpoint_path) if base_url else ""
        ),
        "api_key": str(cfg.get("api_key") or "").strip(),
        "api_key_env": cfg.get("api_key_env") or "",
        "timeout": float(cfg.get("embedding_timeout") or 12),
        "expected_dimension": dimension if dimension > 0 else None,
    }


def _router_chain(router: Any, task_name: str) -> List[str]:
    if not isinstance(router, dict):
        return []
    raw = router.get(task_name, [])
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def list_model_options(
    catalog: Optional[Dict[str, Any]] = None,
    *,
    purposes: str | Sequence[str] = "",
) -> List[Dict[str, str]]:
    """Return purpose-filtered models for plugin UI: [{id, label}, ...]."""
    if catalog is None:
        try:
            from config import MODELS as catalog  # type: ignore
        except Exception:
            catalog = {}
    if not isinstance(catalog, dict):
        return []
    options: List[Dict[str, str]] = []
    for model_id in list_models_by_purpose(
        catalog, purposes, allow_untagged=False
    ):
        row = catalog.get(model_id) or {}
        upstream = str((row or {}).get("model") or "").strip()
        purpose_text = format_purposes_label(get_model_purposes(row))
        if upstream and purpose_text and purpose_text != "未设置":
            label = f"{model_id} ({upstream}) · {purpose_text}"
        elif upstream:
            label = f"{model_id} ({upstream})"
        else:
            label = str(model_id)
        options.append({"id": str(model_id), "label": label})
    return options


def list_image_model_options(
    catalog: Optional[Dict[str, Any]] = None,
    *,
    purposes: str | Sequence[str] = ("image_gen", "image_edit"),
) -> List[Dict[str, str]]:
    """Backward-compatible image-model option helper."""
    return list_model_options(catalog, purposes=purposes)


def list_image_providers(
    catalog: Optional[Dict[str, Any]] = None,
    *,
    image_base64: str = "",
    request_defaults: Optional[Dict[str, Any]] = None,
    router: Optional[Dict[str, Any]] = None,
    selected_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Build providers from explicit model ids.

    Priority:
    1) selected_ids (plugin 里手动选的)
    2) 任务路由 image_gen / image_edit
    Never auto-includes every image-tagged model.
    """
    if catalog is None:
        try:
            from config import MODELS as catalog  # type: ignore
        except Exception:
            catalog = {}
    if not isinstance(catalog, dict):
        return []

    if router is None:
        try:
            from config import LLM_ROUTER as router  # type: ignore
        except Exception:
            router = {}

    allowed_purposes = (
        ("image_edit", "image_gen") if image_base64 else ("image_gen", "image_edit")
    )

    selected: List[str] = []
    if selected_ids is not None:
        selected = [str(x).strip() for x in selected_ids if str(x).strip()]
    else:
        # Explicit selection only. Fallback chain for edit when image_edit empty.
        if image_base64:
            selected = _router_chain(router, "image_edit") or _router_chain(
                router, "image_gen"
            )
        else:
            selected = _router_chain(router, "image_gen")

    providers: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for model_id in selected:
        if model_id in seen:
            continue
        seen.add(model_id)
        row = catalog.get(model_id)
        if not isinstance(row, dict):
            continue
        # Must be tagged as image model so chat models can't sneak into the chain.
        if not model_has_purpose(row, allowed_purposes, allow_untagged=False):
            continue
        providers.append(
            provider_fields_from_model(
                model_id, row, request_defaults=request_defaults
            )
        )
    return providers


def ensure_default_purposes(model_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy with purposes normalized if present."""
    if not isinstance(model_cfg, dict):
        return {}
    merged = dict(model_cfg)
    purposes = get_model_purposes(merged)
    if purposes:
        merged["purposes"] = purposes
        merged.pop("purpose", None)
    return merged
