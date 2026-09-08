from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .event import DisasterEvent


@dataclass
class WatchRegion:
    id: str
    label: str
    keywords: List[str] = field(default_factory=list)
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_km: float = 300.0
    min_intensity: float = 2.0
    kinds: List[str] = field(default_factory=lambda: ["eew", "typhoon", "weather"])
    min_color_level: str = "黄色"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    acc = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(acc)))


def rough_local_intensity(mag: float, dist_km: float) -> float:
    return max(0.0, 1.5 * mag - 3.5 * math.log10(dist_km + 10.0))


COLOR_RANK = {"白色": 0, "蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}


def fold_zh(text: str) -> str:
    table = str.maketrans("台灣臺東縣鄉鎮嶼蓮", "台湾台东县乡镇屿莲")
    return str(text or "").translate(table)


def detect_color(text: str) -> str:
    blob = str(text or "")
    for color in ("红色", "橙色", "黄色", "蓝色", "白色"):
        if color in blob:
            return color
    return "白色"


def region_matches_eew(region: WatchRegion, event: DisasterEvent) -> Tuple[bool, dict]:
    extra: dict = {}
    if "eew" not in (region.kinds or []):
        return False, extra
    place = fold_zh(
        " ".join(
            [str(event.place_name or ""), str(event.location_desc or "")]
        )
    )
    keywords = [fold_zh(item.strip()) for item in (region.keywords or []) if str(item).strip()]
    keyword_hit = any(item and item in place for item in keywords)

    geo_hit = False
    if (
        region.lat is not None
        and region.lng is not None
        and event.lat is not None
        and event.lng is not None
    ):
        dist = haversine_km(
            float(region.lat), float(region.lng), float(event.lat), float(event.lng)
        )
        intensity = rough_local_intensity(float(event.magnitude or 0), dist)
        extra = {
            "dist_km": dist,
            "rough_intensity": intensity,
            "label": region.label,
        }
        geo_hit = dist <= float(region.radius_km) and intensity >= float(
            region.min_intensity
        )

    if not keywords and region.lat is None:
        return False, extra
    if keyword_hit or geo_hit:
        extra.setdefault("label", region.label)
    return (keyword_hit or geo_hit), extra


def should_push_eew(
    event: DisasterEvent,
    regions: Sequence[WatchRegion],
    *,
    min_magnitude: float,
    major_override_mag: float,
    simulation: bool = False,
) -> Tuple[bool, str, dict]:
    if simulation:
        return True, "simulation", {}
    mag = float(event.magnitude or 0)
    if not regions:
        return False, "no_watch_regions", {}
    if mag + 1e-9 < float(min_magnitude):
        return False, "below_min_magnitude", {}
    for region in regions:
        hit, extra = region_matches_eew(region, event)
        if hit:
            return True, f"region:{region.id}", extra
    if float(major_override_mag) > 0 and mag + 1e-9 >= float(major_override_mag):
        return True, "major_override", {}
    return False, "no_region_hit", {}


def should_push_alert(
    event: DisasterEvent,
    regions: Sequence[WatchRegion],
) -> Tuple[bool, str, dict]:
    if not regions:
        return False, "no_watch_regions", {}
    kind = str(event.kind or "weather").strip() or "weather"
    hay = fold_zh(
        " ".join(
            [
                str(event.raw_title or ""),
                str(event.place_name or ""),
                str(event.location_desc or ""),
            ]
        )
    )
    color = str(event.color_level or "").strip() or detect_color(hay)
    color_rank = COLOR_RANK.get(color, 0)
    saw_color_fail = False
    for region in regions:
        if kind not in (region.kinds or []):
            continue
        min_color = str(region.min_color_level or "黄色").strip() or "黄色"
        if color_rank < COLOR_RANK.get(min_color, 2):
            saw_color_fail = True
            continue
        keywords = [
            fold_zh(str(item).strip())
            for item in (list(region.keywords or []) + [region.label])
            if str(item).strip()
        ]
        if not any(item and item in hay for item in keywords):
            continue
        return True, f"region:{region.id}", {"label": region.label, "color": color}
    if saw_color_fail:
        return False, "below_color_level", {"color": color}
    return False, "no_region_hit", {"color": color}
