import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


RUNTIME_SETTINGS_PATH = Path("./data/runtime_settings.json")
EMBEDDING_MODEL_ID_KEY = "embedding_model_id"
EMBEDDING_MODEL_IDS_KEY = "embedding_model_ids"
OLLAMA_AUTOSTART_KEY = "ollama_autostart_enabled"


class RuntimeSettingsError(RuntimeError):
    pass


def _read_runtime_settings(*, strict: bool) -> Dict[str, Any]:
    path = RUNTIME_SETTINGS_PATH
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        if strict:
            raise RuntimeSettingsError(f"无法读取运行时设置：{path}") from exc
        return {}
    if isinstance(data, dict):
        return data
    if strict:
        raise RuntimeSettingsError(f"运行时设置必须是 JSON 对象：{path}")
    return {}


def load_runtime_settings() -> Dict[str, Any]:
    return _read_runtime_settings(strict=False)


def load_runtime_settings_strict() -> Dict[str, Any]:
    return _read_runtime_settings(strict=True)


def save_runtime_settings(settings: Dict[str, Any]) -> bool:
    path = RUNTIME_SETTINGS_PATH
    temp_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        os.close(fd)
        temp_path = Path(raw_temp_path)
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_path, path)
        return True
    except Exception:
        return False
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def update_runtime_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    settings = load_runtime_settings()
    settings.update(patch or {})
    save_runtime_settings(settings)
    return settings


def _normalize_embedding_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = [part.strip() for part in text.replace(";", ",").split(",")]
        items = [part for part in parts if part]
    elif isinstance(value, (list, tuple)):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        text = str(value or "").strip()
        items = [text] if text else []
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def save_embedding_model_selection(model_id: str = "", model_ids: Any = None) -> Dict[str, Any]:
    """Persist ordered embedding model queue.

    Accepts either a single model_id (legacy) or an ordered model_ids list.
    Empty selection clears both keys so runtime falls back to EMBEDDING_CONFIG.
    """
    settings = load_runtime_settings_strict()
    if model_ids is not None:
        chain = _normalize_embedding_ids(model_ids)
    else:
        chain = _normalize_embedding_ids(model_id)

    if chain:
        settings[EMBEDDING_MODEL_IDS_KEY] = chain
        settings[EMBEDDING_MODEL_ID_KEY] = chain[0]
    else:
        settings.pop(EMBEDDING_MODEL_IDS_KEY, None)
        settings.pop(EMBEDDING_MODEL_ID_KEY, None)
    if not save_runtime_settings(settings):
        raise OSError(f"无法保存运行时设置：{RUNTIME_SETTINGS_PATH}")
    return settings


def save_ollama_autostart(enabled: bool) -> Dict[str, Any]:
    settings = load_runtime_settings_strict()
    settings[OLLAMA_AUTOSTART_KEY] = bool(enabled)
    if not save_runtime_settings(settings):
        raise OSError(f"无法保存运行时设置：{RUNTIME_SETTINGS_PATH}")
    return settings
