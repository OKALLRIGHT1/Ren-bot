import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


STATE_PATH = Path("./data/model_transport_state.json")
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"models": {}}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"models": {}}
        models = data.get("models", {})
        if not isinstance(models, dict):
            models = {}
        return {"models": models}
    except Exception:
        return {"models": {}}


def _save(data: Dict[str, Any]) -> bool:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _normalize_key(v: str) -> str:
    return str(v or "").strip()


def _ensure_model(data: Dict[str, Any], model_key: str) -> Dict[str, Any]:
    models = data.setdefault("models", {})
    item = models.get(model_key)
    if not isinstance(item, dict):
        item = {}
    item.setdefault("preferred_transport", "")
    item.setdefault("updated_at", "")
    item.setdefault("success", {})
    item.setdefault("failure", {})
    item.setdefault("consecutive_failures", {})
    item.setdefault("last_error", {})
    models[model_key] = item
    return item


def get_preferred_transport(model_key: str) -> Optional[str]:
    model_key = _normalize_key(model_key)
    if not model_key:
        return None
    with _LOCK:
        data = _load()
        item = data.get("models", {}).get(model_key, {})
        if not isinstance(item, dict):
            return None
        preferred = _normalize_key(item.get("preferred_transport", ""))
        return preferred or None


def get_state(model_key: str) -> Dict[str, Any]:
    model_key = _normalize_key(model_key)
    if not model_key:
        return {}
    with _LOCK:
        data = _load()
        item = data.get("models", {}).get(model_key, {})
        return dict(item) if isinstance(item, dict) else {}


def get_all_states() -> Dict[str, Any]:
    with _LOCK:
        data = _load()
        models = data.get("models", {})
        return dict(models) if isinstance(models, dict) else {}


def record_success(model_key: str, transport: str) -> bool:
    model_key = _normalize_key(model_key)
    transport = _normalize_key(transport)
    if not model_key or not transport:
        return False
    with _LOCK:
        data = _load()
        item = _ensure_model(data, model_key)
        success = item.get("success", {})
        if not isinstance(success, dict):
            success = {}
        success[transport] = int(success.get(transport, 0)) + 1
        item["success"] = success

        cons = item.get("consecutive_failures", {})
        if not isinstance(cons, dict):
            cons = {}
        cons[transport] = 0
        item["consecutive_failures"] = cons

        item["preferred_transport"] = transport
        item["updated_at"] = _now()
        return _save(data)


def record_failure(model_key: str, transport: str, error: str = "") -> bool:
    model_key = _normalize_key(model_key)
    transport = _normalize_key(transport)
    if not model_key or not transport:
        return False
    err_text = str(error or "").strip()[:300]
    with _LOCK:
        data = _load()
        item = _ensure_model(data, model_key)

        failure = item.get("failure", {})
        if not isinstance(failure, dict):
            failure = {}
        failure[transport] = int(failure.get(transport, 0)) + 1
        item["failure"] = failure

        cons = item.get("consecutive_failures", {})
        if not isinstance(cons, dict):
            cons = {}
        cons[transport] = int(cons.get(transport, 0)) + 1
        item["consecutive_failures"] = cons

        last_error = item.get("last_error", {})
        if not isinstance(last_error, dict):
            last_error = {}
        last_error[transport] = {"time": _now(), "error": err_text}
        item["last_error"] = last_error

        preferred = _normalize_key(item.get("preferred_transport", ""))
        if preferred == transport and int(cons.get(transport, 0)) >= 2:
            item["preferred_transport"] = ""

        item["updated_at"] = _now()
        return _save(data)
