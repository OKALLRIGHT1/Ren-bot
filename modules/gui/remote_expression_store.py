"""HTTP adapter for the expression-library dialog (client has no sqlite_store)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import client_config


def _request(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 8.0):
    cfg = client_config.effective()
    base = (cfg["backend_http_url"] or "").rstrip("/")
    if not base:
        raise RuntimeError("未配置后端 HTTP 地址")
    headers: Dict[str, str] = {}
    if cfg["backend_token"]:
        headers["X-GUI-Token"] = cfg["backend_token"]
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base}{path}", data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _ok_data(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict) and result.get("ok"):
        body = result.get("data")
        return body if isinstance(body, dict) else {}
    return {}


class RemoteExpressionStore:
    """Same method names the dialog uses on MemorySQLite."""

    def list_expression_patterns(self, **kwargs) -> List[Dict[str, Any]]:
        params = {
            "character_name": str(kwargs.get("character_name") or ""),
            "scene": str(kwargs.get("scene") or ""),
            "query": str(kwargs.get("query") or ""),
            "enabled_only": "1" if kwargs.get("enabled_only") else "0",
            "limit": str(int(kwargs.get("limit") or 500)),
        }
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        try:
            result = _request(f"/expression-library?{query}")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return []
        return list((_ok_data(result).get("patterns") or []))

    def get_expression_pattern(self, pattern_id: str) -> Optional[Dict[str, Any]]:
        query = urllib.parse.quote(str(pattern_id or ""), safe="")
        try:
            result = _request(f"/expression-library/get?id={query}")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        body = _ok_data(result)
        return body or None

    def upsert_expression_pattern(self, pattern: Dict[str, Any]) -> str:
        result = _request("/expression-library/upsert", dict(pattern or {}))
        body = _ok_data(result)
        return str(body.get("id") or (pattern or {}).get("id") or "")

    def delete_expression_pattern(self, pattern_id: str) -> bool:
        result = _request("/expression-library/delete", {"id": str(pattern_id or "")})
        return bool(isinstance(result, dict) and result.get("ok"))

    def import_expression_patterns(self, rows: List[Dict[str, Any]], replace: bool = False) -> Dict[str, int]:
        added = 0
        skipped = 0
        for row in rows or []:
            if not isinstance(row, dict):
                skipped += 1
                continue
            try:
                if self.upsert_expression_pattern(row):
                    added += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
        return {"added": added, "skipped": skipped}

    def get_runtime(self) -> Dict[str, Any]:
        try:
            return _ok_data(_request("/expression-library/runtime"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return {}

    def save_runtime(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return _ok_data(_request("/expression-library/runtime", dict(patch or {})))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return {}
