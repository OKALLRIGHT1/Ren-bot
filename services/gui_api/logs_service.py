from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List


SAFE_LOG_NAMES = {
    "console.log",
    "agent.log",
    "activity_sidecar.log",
}


def _safe_name(name: str) -> str:
    text = str(name or "").strip().replace("\\", "/").split("/")[-1]
    return text


class LogsGuiService:
    def __init__(self, *, log_dir: str | Path = "./logs", max_bytes: int = 240_000) -> None:
        self.log_dir = Path(log_dir).resolve()
        self.max_bytes = max(4_096, int(max_bytes or 240_000))

    def list_logs(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        try:
            if self.log_dir.exists():
                for path in sorted(self.log_dir.glob("*.log*")):
                    if not path.is_file():
                        continue
                    name = path.name
                    if name not in SAFE_LOG_NAMES and not name.startswith("agent.log"):
                        # allow rotated agent.log.* but keep other unknown logs out
                        if not name.startswith("agent.log."):
                            continue
                    try:
                        size = path.stat().st_size
                    except Exception:
                        size = 0
                    rows.append(
                        {
                            "name": name,
                            "size": int(size),
                            "path": str(path),
                        }
                    )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "data": {
                "logs": rows,
                "count": len(rows),
                "log_dir": str(self.log_dir),
            },
        }

    def tail(self, name: str, *, max_bytes: int | None = None) -> Dict[str, Any]:
        safe = _safe_name(name)
        if not safe or ".." in safe or "/" in safe or "\\" in safe:
            return {"ok": False, "error": "invalid_name"}
        if safe not in SAFE_LOG_NAMES and not safe.startswith("agent.log"):
            return {"ok": False, "error": "log_not_allowed"}
        path = (self.log_dir / safe).resolve()
        try:
            path.relative_to(self.log_dir)
        except Exception:
            return {"ok": False, "error": "path_escape"}
        if not path.exists() or not path.is_file():
            return {
                "ok": True,
                "data": {
                    "name": safe,
                    "text": f"{safe} 不存在。",
                    "truncated": False,
                    "size": 0,
                },
            }
        limit = max(4_096, int(max_bytes or self.max_bytes))
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                if size > limit:
                    fh.seek(-limit, os.SEEK_END)
                    raw = fh.read()
                    truncated = True
                    prefix = f"... 仅显示最近 {limit // 1024} KB ...\n"
                else:
                    raw = fh.read()
                    truncated = False
                    prefix = ""
            text = prefix + raw.decode("utf-8", errors="replace")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "data": {
                "name": safe,
                "text": text,
                "truncated": truncated,
                "size": int(size),
            },
        }
