from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


class DailyInfoCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def read_today(self, capability: str, today: date | None = None) -> Any:
        today = today or date.today()
        self.cleanup(capability, today)
        path = self._path(capability, today)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("date") != today.isoformat():
            return None
        return payload.get("data")

    def write_today(self, capability: str, data: Any, today: date | None = None) -> None:
        today = today or date.today()
        self.cleanup(capability, today)
        path = self._path(capability, today)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"date": today.isoformat(), "data": data}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def cleanup(self, capability: str, today: date | None = None) -> None:
        today = today or date.today()
        keep_name = f"{today.isoformat()}.json"
        directory = self._directory(capability)
        if not directory.exists():
            return
        for path in directory.glob("*.json"):
            if path.name != keep_name:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _path(self, capability: str, day: date) -> Path:
        return self._directory(capability) / f"{day.isoformat()}.json"

    def _directory(self, capability: str) -> Path:
        safe_name = str(capability or "").strip().replace("/", "_").replace("\\", "_")
        return self.root / safe_name
