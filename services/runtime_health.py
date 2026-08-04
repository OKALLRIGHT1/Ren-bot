from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


VALID_STATES = {
    "healthy",
    "degraded",
    "reconnecting",
    "cooldown",
    "offline",
    "disabled",
}
REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "prompt",
    "response",
    "window_title",
)


def _sanitize(value: Any, key: str = "") -> Any:
    lowered = str(key).strip().lower()
    if lowered and any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _iso_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class RuntimeHealthCenter:
    """Thread-safe, observation-only runtime health registry."""

    def __init__(self, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self._components: Dict[str, Dict[str, Any]] = {}

    def report(
        self,
        component: str,
        state: str,
        summary: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        stale_after_seconds: Optional[float] = None,
        updated_at: Optional[float] = None,
    ) -> None:
        component_key = str(component or "").strip()
        state_key = str(state or "").strip().lower()
        if not component_key:
            raise ValueError("component is required")
        if state_key not in VALID_STATES:
            raise ValueError(f"invalid runtime health state: {state_key}")
        timestamp = self._clock() if updated_at is None else float(updated_at)
        stale_after = (
            None
            if stale_after_seconds is None
            else max(0.0, float(stale_after_seconds))
        )
        record = {
            "state": state_key,
            "summary": str(summary or "")[:300],
            "details": _sanitize(details or {}),
            "stale_after_seconds": stale_after,
            "_updated_epoch": timestamp,
        }
        with self._lock:
            self._components[component_key] = record

    def clear(self, component: str) -> None:
        with self._lock:
            self._components.pop(str(component or "").strip(), None)

    def snapshot(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        current = self._clock() if now is None else float(now)
        with self._lock:
            records = {key: dict(value) for key, value in self._components.items()}
        components: Dict[str, Dict[str, Any]] = {}
        effective_states = []
        for key, record in sorted(records.items()):
            updated_epoch = float(record.pop("_updated_epoch"))
            stale_after = record.get("stale_after_seconds")
            stale = stale_after is not None and current - updated_epoch > stale_after
            state = str(record["state"])
            effective = "degraded" if stale and state == "healthy" else state
            record.update(
                {
                    "updated_at": _iso_timestamp(updated_epoch),
                    "stale": stale,
                    "effective_state": effective,
                }
            )
            components[key] = record
            if effective != "disabled":
                effective_states.append(effective)
        if "offline" in effective_states:
            overall = "offline"
        elif any(
            state in {"degraded", "reconnecting", "cooldown"}
            for state in effective_states
        ):
            overall = "degraded"
        else:
            overall = "healthy"
        return {"overall": overall, "components": components}


_RUNTIME_HEALTH = RuntimeHealthCenter()


def get_runtime_health() -> RuntimeHealthCenter:
    return _RUNTIME_HEALTH


def report_runtime_health(*args: Any, **kwargs: Any) -> bool:
    try:
        _RUNTIME_HEALTH.report(*args, **kwargs)
        return True
    except Exception:
        return False
