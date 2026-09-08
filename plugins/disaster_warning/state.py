from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .event import DisasterEvent
from .watch import COLOR_RANK, haversine_km

SEEN_LIMIT = 500
MAG_REPUSH_DELTA = 0.2
MOVE_REPUSH_KM = 20.0


class WarningState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = Path.cwd() / self.path
        self.seen: Dict[str, Dict[str, Any]] = {}
        self.stats: Dict[str, int] = {
            "pushed": 0,
            "drop_silence": 0,
            "drop_watch": 0,
            "drop_dedup": 0,
            "push_fail": 0,
            "ws_reconnect": 0,
        }
        self.silence_until: float = 0.0
        self.silence_armed: bool = False
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        seen = data.get("seen")
        if isinstance(seen, dict):
            self.seen = {
                str(key): dict(value)
                for key, value in seen.items()
                if isinstance(value, dict)
            }
        stats = data.get("stats")
        if isinstance(stats, dict):
            for key in self.stats:
                try:
                    self.stats[key] = int(stats.get(key, self.stats[key]) or 0)
                except (TypeError, ValueError):
                    continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen": self.seen, "stats": dict(self.stats)}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def bump(self, key: str, amount: int = 1) -> None:
        self.stats[key] = int(self.stats.get(key, 0) or 0) + amount

    def arm_silence(self, seconds: int) -> None:
        if self.silence_armed:
            return
        self.silence_armed = True
        self.silence_until = time.time() + max(0, int(seconds or 0))

    def in_silence(self) -> bool:
        return time.time() < float(self.silence_until or 0)

    def silence_remaining(self) -> int:
        remain = int(self.silence_until - time.time())
        return max(0, remain)

    @staticmethod
    def event_key(event: DisasterEvent) -> str:
        return f"{event.source_id}:{event.event_id}"

    def snapshot_event(self, event: DisasterEvent) -> Dict[str, Any]:
        return {
            "updates": int(event.updates or 0),
            "mag": float(event.magnitude or 0),
            "lat": event.lat,
            "lng": event.lng,
            "intensity": event.intensity,
            "ts": time.time(),
            "place_name": event.place_name,
            "color": str(event.color_level or ""),
            "kind": str(event.kind or ""),
        }

    def mark_seen(self, event: DisasterEvent) -> None:
        self.seen[self.event_key(event)] = self.snapshot_event(event)
        if len(self.seen) > SEEN_LIMIT:
            ordered = sorted(
                self.seen.items(),
                key=lambda item: float(item[1].get("ts") or 0),
            )
            for key, _ in ordered[: len(self.seen) - SEEN_LIMIT]:
                self.seen.pop(key, None)

    def should_skip_duplicate(self, event: DisasterEvent) -> bool:
        prev = self.seen.get(self.event_key(event))
        if not prev:
            return False
        kind = str(event.kind or prev.get("kind") or "").strip()
        if kind in {"weather", "typhoon"}:
            prev_color = str(prev.get("color") or "")
            cur_color = str(event.color_level or "")
            prev_rank = COLOR_RANK.get(prev_color, 0)
            cur_rank = COLOR_RANK.get(cur_color, 0)
            if cur_color and cur_rank > prev_rank:
                return False
            return True
        mag = float(event.magnitude or 0)
        prev_mag = float(prev.get("mag") or 0)
        if abs(mag - prev_mag) >= MAG_REPUSH_DELTA:
            return False
        prev_int = prev.get("intensity")
        cur_int = event.intensity
        if prev_int is not None and cur_int is not None:
            try:
                if abs(float(cur_int) - float(prev_int)) > 1e-6:
                    return False
            except (TypeError, ValueError):
                pass
        elif cur_int is not None and prev_int is None:
            return False
        prev_lat, prev_lng = prev.get("lat"), prev.get("lng")
        if (
            event.lat is not None
            and event.lng is not None
            and prev_lat is not None
            and prev_lng is not None
        ):
            moved = haversine_km(
                float(prev_lat), float(prev_lng), float(event.lat), float(event.lng)
            )
            if moved >= MOVE_REPUSH_KM:
                return False
        return True

    def recent_seen(self, limit: int = 3) -> list:
        items = sorted(
            self.seen.items(),
            key=lambda item: float(item[1].get("ts") or 0),
            reverse=True,
        )
        out = []
        for key, value in items[: max(1, limit)]:
            place = str(value.get("place_name") or "")
            mag = value.get("mag")
            out.append(f"{key} M{mag} {place}".strip())
        return out
