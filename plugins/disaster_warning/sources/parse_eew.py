from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..event import DisasterEvent

CEA_SOURCES = {"cea"}
CEA_SNAPSHOT_KEYS = ("cea",)
PROVINCIAL_SOURCES = {"cea-pr", "cea_pr"}


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


def extract_data_dict(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("Data")
    if nested is None:
        nested = raw.get("data")
    if isinstance(nested, dict):
        return nested
    return raw


def extract_cea_payload(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = str(message.get("source") or "").strip().lower()
    if source in PROVINCIAL_SOURCES:
        return None
    if source and source not in CEA_SOURCES:
        return None
    payload = extract_data_dict(message)
    if not payload:
        return None
    if "province" in payload and payload.get("province") not in (None, ""):
        return None
    if "epiIntensity" not in payload:
        return None
    return payload


def parse_cea_event(payload: Dict[str, Any], *, source_id: str = "cea") -> Optional[DisasterEvent]:
    event_id = str(payload.get("eventId") or payload.get("id") or "").strip()
    if not event_id:
        return None
    mag = _as_float(payload.get("magnitude"))
    if mag is None:
        return None
    occurred = payload.get("shockTime") or payload.get("createTime") or payload.get("create_time")
    return DisasterEvent(
        source_id=source_id,
        event_id=event_id,
        updates=_as_int(payload.get("updates", payload.get("reportNum", 1)), 1),
        kind="eew",
        place_name=str(payload.get("placeName") or "").strip(),
        magnitude=float(mag),
        lat=_as_float(payload.get("latitude")),
        lng=_as_float(payload.get("longitude")),
        depth_km=_as_float(payload.get("depth")),
        intensity=_as_float(payload.get("epiIntensity")),
        occurred_at=str(occurred).strip() if occurred else None,
        display_name="中国地震预警网",
    )


def snapshot_cea_events(message: Dict[str, Any]) -> List[DisasterEvent]:
    events: List[DisasterEvent] = []
    for key in CEA_SNAPSHOT_KEYS:
        blob = message.get(key)
        payload = extract_data_dict(blob) if blob is not None else {}
        if not payload or "epiIntensity" not in payload:
            continue
        if payload.get("province") not in (None, ""):
            continue
        event = parse_cea_event(payload, source_id="cea")
        if event is not None:
            events.append(event)
    return events


def iter_snapshot_keys(message: Dict[str, Any]) -> Iterable[str]:
    skip = {"type", "message", "md5", "ver", "id", "timestamp"}
    for key in message:
        if str(key) not in skip:
            yield str(key)
