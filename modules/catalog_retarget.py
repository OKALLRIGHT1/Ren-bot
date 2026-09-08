"""Rename catalog model/provider IDs and rewrite callers that store them.

Qt-free so both the desktop settings pages and the Live2D HTTP catalog API
can share the same rewrite rules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional


def _catalog_refs():
    try:
        from config import LLM_ROUTER, MODELS, PROVIDERS
    except ImportError:
        return {}, {}, {}
    return MODELS, LLM_ROUTER, PROVIDERS


def provider_label(cfg: Any) -> str:
    if not isinstance(cfg, Mapping):
        return "—"
    name = str(cfg.get("provider") or "").strip()
    return name or "—"


def _rewrite_id_list(values: Any, old_id: str, new_id: str) -> tuple[Any, bool]:
    if not isinstance(values, list):
        return values, False
    changed = False
    rewritten = []
    for item in values:
        if str(item) == old_id:
            rewritten.append(new_id)
            changed = True
        else:
            rewritten.append(item)
    return rewritten, changed


def _rename_mapping_key(mapping: MutableMapping[str, Any], old_id: str, new_id: str) -> None:
    items = list(mapping.items())
    mapping.clear()
    for key, value in items:
        mapping[new_id if key == old_id else key] = value


def _rewrite_router(router: MutableMapping[str, Any], old_id: str, new_id: str) -> int:
    hits = 0
    for task, chain in list(router.items()):
        if isinstance(chain, str):
            if chain == old_id:
                router[task] = new_id
                hits += 1
            continue
        if not isinstance(chain, list):
            continue
        rewritten, changed = _rewrite_id_list(chain, old_id, new_id)
        if changed:
            router[task] = rewritten
            hits += 1
    return hits


def _rewrite_plugin_settings(settings: Any, old_id: str, new_id: str) -> bool:
    if not isinstance(settings, dict):
        return False
    changed = False
    for key, value in list(settings.items()):
        if isinstance(value, dict):
            setting_type = str(value.get("type") or "").strip().lower()
            if setting_type in {"model_queue", "model_list"}:
                default, did = _rewrite_id_list(value.get("default"), old_id, new_id)
                if did:
                    value["default"] = default
                    changed = True
            continue
        if isinstance(value, list) and str(key).strip() in {"model_queue", "model_list"}:
            rewritten, did = _rewrite_id_list(value, old_id, new_id)
            if did:
                settings[key] = rewritten
                changed = True
    return changed


def _rewrite_plugin_config_dict(data: Any, old_id: str, new_id: str) -> bool:
    if not isinstance(data, dict):
        return False
    return _rewrite_plugin_settings(data.get("settings"), old_id, new_id)


def _rewrite_plugin_config_file(path: Path, old_id: str, new_id: str) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not _rewrite_plugin_config_dict(raw, old_id, new_id):
        return False
    try:
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _retarget_plugin_files(plugins_dir: Any, old_id: str, new_id: str) -> int:
    if not plugins_dir:
        return 0
    root = Path(plugins_dir)
    if not root.is_dir():
        return 0
    hits = 0
    for config_path in sorted(root.glob("*/config.json")):
        if _rewrite_plugin_config_file(config_path, old_id, new_id):
            hits += 1
    return hits


def _retarget_plugin_manager(plugin_manager: Any, old_id: str, new_id: str) -> int:
    if plugin_manager is None:
        return 0
    hits = 0
    configs = getattr(plugin_manager, "plugin_configs", None)
    if isinstance(configs, dict):
        for cfg in configs.values():
            if _rewrite_plugin_config_dict(cfg, old_id, new_id):
                hits += 1
    plugins = getattr(plugin_manager, "plugins", None)
    if isinstance(plugins, dict):
        for plugin in plugins.values():
            settings = getattr(plugin, "settings", None)
            if _rewrite_plugin_settings(settings, old_id, new_id):
                hits += 1
    return hits


def _retarget_runtime_embedding_ids(old_id: str, new_id: str) -> bool:
    try:
        from modules.runtime_settings import (
            EMBEDDING_MODEL_ID_KEY,
            EMBEDDING_MODEL_IDS_KEY,
            load_runtime_settings,
            save_runtime_settings,
        )
    except Exception:
        return False
    settings = load_runtime_settings()
    if not isinstance(settings, dict) or not settings:
        return False
    changed = False
    if str(settings.get(EMBEDDING_MODEL_ID_KEY) or "") == old_id:
        settings[EMBEDDING_MODEL_ID_KEY] = new_id
        changed = True
    ids = settings.get(EMBEDDING_MODEL_IDS_KEY)
    rewritten, did = _rewrite_id_list(ids, old_id, new_id)
    if did:
        settings[EMBEDDING_MODEL_IDS_KEY] = rewritten
        changed = True
    if not changed:
        return False
    return bool(save_runtime_settings(settings))


def retarget_model_id(
    old_id: str,
    new_id: str,
    *,
    models: Optional[MutableMapping[str, Any]] = None,
    router: Optional[MutableMapping[str, Any]] = None,
    plugins_dir: Any = "plugins",
    update_runtime: bool = True,
    plugin_manager: Any = None,
) -> dict:
    """Rename a catalog model ID and rewrite every live caller that stores it."""
    old_id = str(old_id or "").strip()
    new_id = str(new_id or "").strip()
    if not old_id or not new_id:
        return {"ok": False, "error": "invalid_id", "changed": False}
    if old_id == new_id:
        return {"ok": True, "changed": False}

    live_models, live_router, _ = _catalog_refs()
    models = live_models if models is None else models
    router = live_router if router is None else router

    if new_id in models:
        return {"ok": False, "error": "id_exists", "changed": False}
    if old_id not in models:
        return {"ok": False, "error": "not_found", "changed": False}

    _rename_mapping_key(models, old_id, new_id)
    router_hits = _rewrite_router(router, old_id, new_id)
    plugin_file_hits = _retarget_plugin_files(plugins_dir, old_id, new_id)
    plugin_mem_hits = _retarget_plugin_manager(plugin_manager, old_id, new_id)
    runtime_hit = _retarget_runtime_embedding_ids(old_id, new_id) if update_runtime else False
    return {
        "ok": True,
        "changed": True,
        "router": router_hits,
        "plugins": plugin_file_hits,
        "plugin_runtime": plugin_mem_hits,
        "runtime": runtime_hit,
    }


def retarget_provider_name(
    old_name: str,
    new_name: str,
    *,
    models: Optional[MutableMapping[str, Any]] = None,
    providers: Optional[MutableMapping[str, Any]] = None,
) -> dict:
    """Rename a provider and rewrite model.provider fields that pointed at it."""
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if not old_name or not new_name:
        return {"ok": False, "error": "invalid_id", "changed": False}
    if old_name == new_name:
        return {"ok": True, "changed": False}

    live_models, _, live_providers = _catalog_refs()
    models = live_models if models is None else models
    providers = live_providers if providers is None else providers

    if new_name in providers:
        return {"ok": False, "error": "id_exists", "changed": False}
    if old_name not in providers:
        return {"ok": False, "error": "not_found", "changed": False}

    _rename_mapping_key(providers, old_name, new_name)
    model_hits = 0
    for cfg in models.values():
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("provider") or "").strip() == old_name:
            cfg["provider"] = new_name
            model_hits += 1
    return {
        "ok": True,
        "changed": True,
        "models": model_hits,
    }
