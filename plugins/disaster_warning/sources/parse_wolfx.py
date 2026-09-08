from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..event import DisasterEvent

# Wolfx all_eew mixes many feeds. Only mainland EEW types are accepted here.
WOLFX_EEW_TYPES: Dict[str, Tuple[str, str]] = {
    "cenc_eew": ("cea_wolfx", "中国地震台网"),
    "sc_eew": ("sc_wolfx", "四川地震局"),
    "fj_eew": ("fj_wolfx", "福建地震局"),
    "cq_eew": ("cq_wolfx", "重庆地震局"),
}


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return False


def _first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def parse_wolfx_eew(message: Dict[str, Any] | None) -> Optional[DisasterEvent]:
    if not isinstance(message, dict):
        return None
    msg_type = str(message.get("type") or "").strip()
    meta = WOLFX_EEW_TYPES.get(msg_type)
    if meta is None:
        return None
    if _truthy(message.get("isCancel")) or _truthy(message.get("isTraining")):
        return None
    event_id = str(
        _first(
            message.get("EventID"),
            message.get("eventId"),
            message.get("ID"),
            message.get("id"),
        )
        or ""
    ).strip()
    if not event_id:
        return None
    mag = _as_float(
        _first(
            message.get("Magnitude"),
            message.get("magnitude"),
            message.get("Magunitude"),
        )
    )
    if mag is None:
        return None
    place = str(
        _first(
            message.get("HypoCenter"),
            message.get("Hypocenter"),
            message.get("placeName"),
            message.get("PlaceName"),
        )
        or ""
    ).strip()
    occurred = _first(
        message.get("OriginTime"),
        message.get("originTime"),
        message.get("ReportTime"),
    )
    source_id, display_name = meta
    return DisasterEvent(
        source_id=source_id,
        event_id=event_id,
        updates=_as_int(
            _first(message.get("ReportNum"), message.get("Serial"), message.get("updates"), 1),
            1,
        ),
        kind="eew",
        place_name=place,
        magnitude=float(mag),
        lat=_as_float(_first(message.get("Latitude"), message.get("latitude"))),
        lng=_as_float(_first(message.get("Longitude"), message.get("longitude"))),
        depth_km=_as_float(_first(message.get("Depth"), message.get("depth"))),
        intensity=_as_float(
            _first(
                message.get("MaxIntensity"),
                message.get("epiIntensity"),
                message.get("Intensity"),
            )
        ),
        occurred_at=str(occurred).strip() if occurred else None,
        display_name=display_name,
    )
