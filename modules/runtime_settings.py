import json
from pathlib import Path
from typing import Any, Dict


RUNTIME_SETTINGS_PATH = Path("./data/runtime_settings.json")


def load_runtime_settings() -> Dict[str, Any]:
    path = RUNTIME_SETTINGS_PATH
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_runtime_settings(settings: Dict[str, Any]) -> bool:
    path = RUNTIME_SETTINGS_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def update_runtime_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    settings = load_runtime_settings()
    settings.update(patch or {})
    save_runtime_settings(settings)
    return settings
