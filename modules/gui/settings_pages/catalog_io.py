"""Shared custom_models.json load/save for provider and model pages."""

from __future__ import annotations

import json
import os

from PySide6 import QtWidgets

from modules.catalog_retarget import (
    provider_label,
    retarget_model_id,
    retarget_provider_name,
)

__all__ = [
    "catalog_path",
    "provider_label",
    "reload_model_catalog",
    "retarget_model_id",
    "retarget_provider_name",
    "save_model_catalog",
]


def _catalog_refs():
    try:
        from config import CUSTOM_MODELS_PATH, LLM_ROUTER, MODELS, PROVIDERS
    except ImportError:
        return {}, {}, {}, "data/custom_models.json"
    return MODELS, LLM_ROUTER, PROVIDERS, CUSTOM_MODELS_PATH


def reload_model_catalog() -> None:
    try:
        import config as runtime_config

        if hasattr(runtime_config, "load_custom_models"):
            runtime_config.load_custom_models(force=True)
    except Exception:
        pass


def save_model_catalog(parent=None) -> bool:
    models, router, providers, path = _catalog_refs()
    data = {"models": models, "router": router, "providers": providers}
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        if parent is not None:
            QtWidgets.QMessageBox.critical(parent, "保存失败", str(exc))
        return False


def catalog_path() -> str:
    return _catalog_refs()[3]
