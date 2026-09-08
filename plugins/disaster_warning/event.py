from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DisasterEvent:
    source_id: str
    event_id: str
    updates: int
    kind: str
    place_name: str
    magnitude: float
    lat: Optional[float] = None
    lng: Optional[float] = None
    depth_km: Optional[float] = None
    intensity: Optional[float] = None
    occurred_at: Optional[str] = None
    location_desc: str = ""
    raw_title: str = ""
    color_level: str = ""
    display_name: str = "中国地震预警网"
