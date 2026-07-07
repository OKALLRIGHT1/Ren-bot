from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(slots=True)
class PendingReply:
    id: str
    session_id: str
    user_id: str = ""
    text: str = ""
    source: str = ""
    created_at: float = field(default_factory=time.time)


class ReplyEffectTracker:
    POSITIVE = ("谢谢", "谢啦", "可以", "懂了", "有用", "不错", "好用", "正是", "thanks", "ok")
    NEGATIVE = ("不对", "错了", "没用", "不是", "答非所问", "离谱", "算了", "重来", "wrong")
    REPAIR = ("我是说", "我的意思", "你理解错", "不是这个", "重新", "再说一遍")

    def __init__(self, path: str = "data/reply_effect/reply_effects.jsonl", ttl_sec: float = 1800.0):
        self.path = Path(path)
        self.ttl_sec = max(60.0, float(ttl_sec or 1800.0))
        self._pending: Dict[str, PendingReply] = {}

    def record_reply(self, *, session_id: str, user_id: str = "", text: str = "", source: str = "") -> str:
        sid = str(session_id or "").strip()
        if not sid or not str(text or "").strip():
            return ""
        item = PendingReply(
            id=uuid.uuid4().hex[:12],
            session_id=sid,
            user_id=str(user_id or ""),
            text=str(text or "")[:1200],
            source=str(source or ""),
        )
        self._pending[sid] = item
        self._cleanup()
        return item.id

    def observe_user_message(self, *, session_id: str, user_id: str = "", text: str = "", source: str = "") -> Optional[Dict[str, Any]]:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        pending = self._pending.pop(sid, None)
        if pending is None:
            return None
        now = time.time()
        if now - pending.created_at > self.ttl_sec:
            return None
        incoming = str(text or "").strip()
        if not incoming:
            return None
        lower = incoming.lower()
        labels = []
        score = 0
        if any(word.lower() in lower for word in self.POSITIVE):
            labels.append("positive")
            score += 1
        if any(word.lower() in lower for word in self.NEGATIVE):
            labels.append("negative")
            score -= 1
        if any(word.lower() in lower for word in self.REPAIR):
            labels.append("repair")
            score -= 1
        if not labels:
            if len(incoming) <= 8 and incoming in {"嗯", "哦", "行", "好", "知道了"}:
                labels.append("ack")
            else:
                labels.append("continued")
        record = {
            "ts": now,
            "session_id": sid,
            "user_id": str(user_id or ""),
            "source": str(source or pending.source or ""),
            "reply": asdict(pending),
            "followup_text": incoming[:1200],
            "labels": labels,
            "score": score,
        }
        self._append(record)
        return record

    def recent(self, limit: int = 50, session_id: str = "") -> list[Dict[str, Any]]:
        count = max(1, min(500, int(limit or 50)))
        sid = str(session_id or "").strip()
        rows: list[Dict[str, Any]] = []
        if not self.path.exists():
            return rows
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
            if sid and str(item.get("session_id") or "") != sid:
                continue
            rows.append(item)
            if len(rows) >= count:
                break
        return rows

    def stats(self, limit: int = 200, session_id: str = "") -> Dict[str, Any]:
        rows = self.recent(limit=limit, session_id=session_id)
        label_counts: Dict[str, int] = {}
        total_score = 0
        for row in rows:
            try:
                total_score += int(row.get("score") or 0)
            except Exception:
                pass
            for label in row.get("labels") or []:
                key = str(label or "").strip()
                if key:
                    label_counts[key] = label_counts.get(key, 0) + 1
        return {
            "count": len(rows),
            "score_sum": total_score,
            "score_avg": round(total_score / len(rows), 3) if rows else 0,
            "labels": label_counts,
        }

    def _cleanup(self) -> None:
        cutoff = time.time() - self.ttl_sec
        for key, item in list(self._pending.items()):
            if item.created_at < cutoff:
                self._pending.pop(key, None)

    def _append(self, record: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
