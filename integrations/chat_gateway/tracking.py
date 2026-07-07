from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(slots=True)
class OutboundRecord:
    id: str
    adapter: str
    session_id: str
    kind: str
    payload_preview: str = ""
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    external_message_id: str = ""
    components: list[Dict[str, Any]] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MessageDeduplicator:
    def __init__(self, ttl_sec: float = 300.0, max_items: int = 4096):
        self.ttl_sec = max(1.0, float(ttl_sec or 300.0))
        self.max_items = max(128, int(max_items or 4096))
        self._seen: OrderedDict[str, float] = OrderedDict()

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        self._cleanup(now)
        value = str(key or "").strip()
        if not value:
            return False
        if value in self._seen:
            self._seen.move_to_end(value)
            return True
        self._seen[value] = now
        while len(self._seen) > self.max_items:
            self._seen.popitem(last=False)
        return False

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.ttl_sec
        for key, ts in list(self._seen.items()):
            if ts >= cutoff:
                break
            self._seen.pop(key, None)


class OutboundTracker:
    def __init__(
        self,
        max_items: int = 512,
        path: str = "data/outbound/outbound_records.jsonl",
    ):
        self.max_items = max(64, int(max_items or 512))
        self.path = Path(path)
        self._records: OrderedDict[str, OutboundRecord] = OrderedDict()

    def begin(
        self,
        *,
        adapter: str,
        session_id: str,
        kind: str,
        payload_preview: str = "",
        components: Optional[list[Dict[str, Any]]] = None,
    ) -> OutboundRecord:
        record = OutboundRecord(
            id=uuid.uuid4().hex[:12],
            adapter=str(adapter or ""),
            session_id=str(session_id or ""),
            kind=str(kind or "message"),
            payload_preview=str(payload_preview or "")[:240],
            components=list(components or []),
        )
        self._records[record.id] = record
        while len(self._records) > self.max_items:
            self._records.popitem(last=False)
        return record

    def finish(
        self,
        record_id: str,
        *,
        ok: bool,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> Optional[OutboundRecord]:
        record = self._records.get(str(record_id or ""))
        if record is None:
            return None
        record.status = "sent" if ok else "failed"
        record.finished_at = time.time()
        record.result = _compact_result(result)
        record.error = str(error or "")
        record.external_message_id = extract_message_id(result)
        self._records.move_to_end(record.id)
        self._append(record)
        return record

    def recent(self, limit: int = 50) -> list[Dict[str, Any]]:
        count = max(1, min(500, int(limit or 50)))
        rows = [record.to_dict() for record in list(self._records.values())[-count:]]
        if len(rows) >= count or not self.path.exists():
            return rows[-count:]
        persisted = self._read_recent(count)
        seen = {str(item.get("id") or "") for item in rows}
        merged = [item for item in persisted if str(item.get("id") or "") not in seen]
        merged.extend(rows)
        return merged[-count:]

    def _append(self, record: OutboundRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _read_recent(self, limit: int) -> list[Dict[str, Any]]:
        rows: list[Dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return rows
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
            if len(rows) >= limit:
                break
        rows.reverse()
        return rows


def extract_message_id(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    candidates = [result]
    response = result.get("response")
    if isinstance(response, dict):
        candidates.append(response)
        data = response.get("data")
        if isinstance(data, dict):
            candidates.append(data)
    for item in candidates:
        for key in ("message_id", "msg_id", "id"):
            value = item.get(key)
            if value not in (None, "", 0, "0"):
                return str(value)
    return ""


def _compact_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"raw": str(result)[:1000]}
    compact: Dict[str, Any] = {}
    for key, value in result.items():
        if key in {"response", "reason", "status", "ok", "transport", "session_id", "outbound_id", "body"}:
            compact[key] = _json_safe(value)
    return compact


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:500]
    if isinstance(value, dict):
        return {
            str(k): _json_safe(v, depth + 1)
            for k, v in list(value.items())[:60]
            if str(k).lower() not in {"b64_json", "image", "audio", "file"}
        }
    if isinstance(value, list):
        return [_json_safe(item, depth + 1) for item in value[:30]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 1200:
            return value[:1200] + "..."
        return value
    return str(value)[:500]


def is_send_success(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if bool(result.get("ok")):
        return True
    if extract_message_id(result):
        return True
    response = result.get("response")
    if isinstance(response, dict):
        status = str(response.get("status") or "").strip().lower()
        if status in {"ok", "success"}:
            return True
        try:
            if int(response.get("retcode", 1) or 1) == 0:
                return True
        except Exception:
            pass
        data = response.get("data")
        if data not in (None, "", [], {}):
            return True
    return False
