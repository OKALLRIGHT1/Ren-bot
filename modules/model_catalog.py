"""Model catalog helpers: purpose tags + filtering for plugins/UI."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


# Capability tags: what a model can actually do. Task routing is separate.
MODEL_PURPOSE_OPTIONS: List[tuple[str, str]] = [
    ("chat", "聊天"),
    ("vision", "视觉"),
    ("web_search", "联网搜索"),
    ("image_gen", "画图"),
    ("embedding", "向量"),
]

MODEL_PURPOSE_LABELS: Dict[str, str] = {key: label for key, label in MODEL_PURPOSE_OPTIONS}
MODEL_PURPOSE_IDS: Set[str] = set(MODEL_PURPOSE_LABELS)

# Matches OpenAI-compatible roots such as /v1, /v4, /V2 (Zhipu paas/v4, etc.).
_API_VERSION_SUFFIX_RE = re.compile(r"/v\d+$", re.IGNORECASE)
_API_VERSION_PREFIX_RE = re.compile(r"^/v\d+(?=/|$)", re.IGNORECASE)


def has_api_version_suffix(url: str) -> bool:
    """Return True when URL already ends with an API version segment like /v1 or /v4."""
    base = str(url or "").strip().rstrip("/")
    return bool(_API_VERSION_SUFFIX_RE.search(base))


def strip_leading_api_version(path: str) -> str:
    """Strip a leading /vN from a path. /v1/chat/completions -> /chat/completions."""
    text = str(path or "").strip()
    if not text:
        return ""
    if not text.startswith("/"):
        text = "/" + text
    match = _API_VERSION_PREFIX_RE.match(text)
    if not match:
        return text
    rest = text[match.end() :]
    return rest if rest else ""


def join_openai_compat_url(base_url: str, endpoint: str) -> str:
    """Join base_url with an OpenAI-compatible endpoint.

    If base_url already ends with /vN (e.g. .../paas/v4), do not insert another /v1.
    Otherwise default to the common OpenAI root: base + /v1 + endpoint.
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("openai compatible call missing base_url")
    ep = str(endpoint or "").strip().lstrip("/")
    if not ep:
        raise ValueError("openai compatible call missing endpoint")
    if has_api_version_suffix(base):
        return f"{base}/{ep}"
    return f"{base}/v1/{ep}"


def join_endpoint_url(base_url: str, endpoint_path: str) -> str:
    """Join base_url + endpoint_path without duplicating a trailing /vN segment.

    Examples:
      https://host/v1 + /v1/images/generations -> https://host/v1/images/generations
      https://host/v4 + /v1/chat/completions   -> https://host/v4/chat/completions
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
    # base already ends with /vN (OpenAI /v1, Zhipu /v4, ...) and path also starts with /vN
    if has_api_version_suffix(base):
        stripped = strip_leading_api_version(path)
        if stripped == "":
            return base
        if stripped != path:
            path = stripped
    return base + path

# Accept Chinese / alias labels so older configs and free-text import still work.
_PURPOSE_ALIASES: Dict[str, str] = {
    "chat": "chat",
    "default": "chat",
    "闲聊": "chat",
    "聊天": "chat",
    "对话": "chat",
    "text": "chat",
    "tool_reasoning": "chat",
    "reasoning": "chat",
    "tool": "chat",
    "推理": "chat",
    "工具": "chat",
    "summary": "chat",
    "总结": "chat",
    "摘要": "chat",
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
    "image_edit": "image_gen",
    "image_edits": "image_gen",
    "img2img": "image_gen",
    "图生图": "image_gen",
    "修图": "image_gen",
    "translation": "chat",
    "translate": "chat",
    "翻译": "chat",
    "embedding": "embedding",
    "embed": "embedding",
    "向量": "embedding",
}

IMAGE_PURPOSES: Set[str] = {"image_gen"}

# Tasks shown in GUI / API. Empty chains fall back to 闲聊 except vision/image/search.
# embedding is not a router task — it uses runtime_settings.embedding_model_ids.
ROUTE_TASK_OPTIONS: List[tuple[str, str, str, tuple[str, ...], bool]] = [
    ("default", "闲聊", "主回复；没单独配的任务也走这里", ("chat",), True),
    ("tool_reasoning", "推理", "工具规划和复杂推理；空则用闲聊", ("chat",), True),
    ("summary", "总结", "摘要压缩；空则用闲聊", ("chat",), True),
    ("vision", "识图", "聊天模型不能看图时，用来描述图片和截图", ("vision",), False),
    ("web_search", "联网搜索", "搜索插件；空则用插件自己的接口", ("web_search", "chat"), False),
    ("image_gen", "画图", "文生图和图生图共用；带参考图时走模型的图生图接口", ("image_gen",), False),
]


def route_task_ids() -> List[str]:
    return [item[0] for item in ROUTE_TASK_OPTIONS]


def route_task_purposes(task_name: str) -> tuple[str, ...]:
    wanted = str(task_name or "").strip()
    for task_id, _label, _desc, purposes, _allow in ROUTE_TASK_OPTIONS:
        if task_id == wanted:
            return tuple(purposes)
    return ()


def route_task_allow_untagged(task_name: str) -> bool:
    wanted = str(task_name or "").strip()
    for task_id, _label, _desc, _purposes, allow_untagged in ROUTE_TASK_OPTIONS:
        if task_id == wanted:
            return bool(allow_untagged)
    return True


# Empty chains use 闲聊, except these capability-specific tasks.
SPECIAL_ROUTE_TASKS: Set[str] = {"vision", "web_search", "image_gen"}


def chat_fallback_task(task_name: str) -> str:
    wanted = str(task_name or "").strip() or "default"
    if wanted == "image_edit":
        return "image_gen"
    if wanted in SPECIAL_ROUTE_TASKS or wanted == "default":
        return ""
    return "default"


def image_router_chain(router: Optional[Dict[str, Any]] = None) -> List[str]:
    """画图链路。旧配置只填了图生图时，沿用那条链。"""
    gen = router_chain(router, "image_gen")
    if gen:
        return gen
    return router_chain(router, "image_edit")


def vision_router_chain(
    router: Optional[Dict[str, Any]] = None,
    catalog: Optional[Dict[str, Any]] = None,
    fallback_model: str = "",
) -> List[str]:
    """识图链路。空着时沿用 SENSOR_VISION_MODEL（默认 GLM-4V-Flash）。"""
    chain = router_chain(router, "vision")
    if chain:
        return chain
    fallback = str(fallback_model or "").strip()
    if not fallback:
        try:
            from config import SENSOR_VISION_MODEL as fallback_id

            fallback = str(fallback_id or "").strip()
        except Exception:
            fallback = ""
    if not fallback:
        return []
    if catalog is None:
        try:
            from config import MODELS as catalog  # type: ignore
        except Exception:
            catalog = {}
    if isinstance(catalog, dict) and fallback in catalog:
        return [fallback]
    return []


def vision_capable_chat_model_ids(
    catalog: Optional[Dict[str, Any]] = None,
    router: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """闲聊链里勾了「视觉」的模型。这些模型可以直接看图，不必再跑识图任务。"""
    return resolve_router_model_ids(
        "default",
        catalog=catalog,
        router=router,
        required_purpose="vision",
        allow_untagged=False,
    )


def chat_route_has_vision(
    catalog: Optional[Dict[str, Any]] = None,
    router: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(vision_capable_chat_model_ids(catalog=catalog, router=router))


def image_data_url(image_base64: str) -> str:
    raw = str(image_base64 or "").strip()
    if not raw:
        return ""
    if raw.startswith("data:image/") and "," in raw:
        return raw
    if raw.startswith("base64://"):
        raw = raw.split("://", 1)[1].strip()
    return f"data:image/jpeg;base64,{raw}"


def build_multimodal_user_content(
    text: Any,
    image_base64_list: Sequence[str],
) -> Any:
    """Build OpenAI-style user content with optional image parts."""
    images = [str(item or "").strip() for item in (image_base64_list or []) if str(item or "").strip()]
    if isinstance(text, list):
        parts: List[Dict[str, Any]] = [item for item in text if isinstance(item, dict)]
    else:
        body = str(text or "").strip()
        parts = [{"type": "text", "text": body}] if body else []
    for raw in images:
        url = image_data_url(raw)
        if not url:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if not parts:
        return str(text or "")
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0].get("text") or ""
    return parts


def attach_images_to_last_user_message(
    messages: Sequence[Any],
    image_base64_list: Sequence[str],
) -> List[Any]:
    images = [str(item or "").strip() for item in (image_base64_list or []) if str(item or "").strip()]
    out: List[Any] = [dict(item) if isinstance(item, dict) else item for item in (messages or [])]
    if not images:
        return out
    for index in range(len(out) - 1, -1, -1):
        msg = out[index]
        if not isinstance(msg, dict) or str(msg.get("role") or "") != "user":
            continue
        out[index] = {
            **msg,
            "content": build_multimodal_user_content(msg.get("content"), images),
        }
        break
    return out


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
        return _known_purposes(model_cfg.get("purposes"))
    if "purpose" in model_cfg:
        return _known_purposes(model_cfg.get("purpose"))
    if "用途" in model_cfg:
        return _known_purposes(model_cfg.get("用途"))
    tags = model_cfg.get("tags")
    if isinstance(tags, (list, tuple, set, str)):
        return _known_purposes(tags)
    return []


def _known_purposes(values: Any) -> List[str]:
    """Drop retired tags such as the old 代码 purpose."""
    return [item for item in normalize_purposes(values) if item in MODEL_PURPOSE_IDS]


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
    if not out.get("edit_endpoint_path") and "image_gen" in purposes:
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


def router_chain(router: Any, task_name: str) -> List[str]:
    if not isinstance(router, dict):
        return []
    raw = router.get(task_name, [])
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _router_chain(router: Any, task_name: str) -> List[str]:
    """Backward-compatible alias for router_chain()."""
    return router_chain(router, task_name)


def resolve_router_model_ids(
    task_name: str,
    *,
    catalog: Optional[Dict[str, Any]] = None,
    router: Optional[Dict[str, Any]] = None,
    fallbacks: Optional[Sequence[str]] = None,
    fallback_task: str = "",
    required_purpose: str | Sequence[str] = "",
    allow_untagged: bool = True,
) -> List[str]:
    """Return the explicit router chain, then fallback_task / named fallbacks.

    Does not auto-include every purpose-tagged model. The chain is what runs;
    required_purpose only drops models that cannot serve this task.
    """
    if catalog is None:
        try:
            from config import MODELS as catalog  # type: ignore
        except Exception:
            catalog = {}
    if router is None:
        try:
            from config import LLM_ROUTER as router  # type: ignore
        except Exception:
            router = {}
    if not isinstance(catalog, dict):
        catalog = {}

    task_id = str(task_name or "").strip()
    selected = (
        image_router_chain(router)
        if task_id == "image_gen"
        else router_chain(router, task_name)
    )
    fallback_name = str(fallback_task or "").strip()
    if not selected and fallback_name and fallback_name != str(task_name or "").strip():
        selected = router_chain(router, fallback_name)
    if not selected:
        selected = [
            str(item).strip() for item in (fallbacks or []) if str(item).strip()
        ]

    wanted = normalize_purposes(required_purpose)
    result: List[str] = []
    seen: Set[str] = set()
    for model_id in selected:
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        row = catalog.get(model_id)
        if not isinstance(row, dict):
            continue
        if wanted and not model_has_purpose(
            row, wanted, allow_untagged=allow_untagged
        ):
            continue
        result.append(model_id)
    return result


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
    purposes: str | Sequence[str] = "image_gen",
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
    2) 任务路由「画图」；旧配置只填了图生图时沿用那条链
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

    allowed_purposes = ("image_gen",)

    selected: List[str] = []
    if selected_ids is not None:
        selected = [str(x).strip() for x in selected_ids if str(x).strip()]
    else:
        selected = image_router_chain(router)

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
