"""HTTP-backed stand-in for the meme_pack plugin's store, for remote mode.

The meme manager dialog talks to the in-process meme_pack plugin (only the
brain process has it). This adapter implements the same store surface over
the brain's /gui/meme-pack HTTP API. Import of local files cannot work
remotely (files live on this machine, store is on the brain) and reports
as skipped/failed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from types import SimpleNamespace

import client_config


def _request(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 6.0):
    cfg = client_config.effective()
    base = (cfg["backend_http_url"] or "").rstrip("/")
    if not base:
        raise RuntimeError("未配置后端 HTTP 地址")
    headers = {}
    if cfg["backend_token"]:
        headers["X-GUI-Token"] = cfg["backend_token"]
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base}{path}", data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class _Asset:
    """Attribute access over the HTTP asset dict (dialog uses asset.id etc.)."""

    def __init__(self, data: Dict[str, Any]):
        self.id = int(data.get("id") or 0)
        self.file_name = str(data.get("file_name") or "")
        self.file_path = str(data.get("file_path") or "")
        self.description = str(data.get("description") or "")
        self.tags = [str(t) for t in (data.get("tags") or [])]
        self.emotion = str(data.get("emotion") or "")
        self.enabled = bool(data.get("enabled", True))
        self.banned = bool(data.get("banned", False))
        self.usage_count = int(data.get("usage_count") or 0)
        self.last_used_at = data.get("last_used_at")
        self.preview_data_url = str(data.get("preview_data_url") or "")


def _ok_data(result: Any) -> Optional[Dict[str, Any]]:
    if isinstance(result, dict) and result.get("ok"):
        body = result.get("data")
        return body if isinstance(body, dict) else {}
    return None


class RemoteMemeStore:
    """Implements the meme store surface used by MemeManagerDialog."""

    def search_assets(self, query: str = "", include_disabled: bool = True, limit: int = 800):
        query_str = urllib.parse.quote(str(query or ""), safe="")
        try:
            result = _request(
                f"/meme-pack?query={query_str}"
                f"&include_disabled={1 if include_disabled else 0}&limit={int(limit)}"
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return []
        body = _ok_data(result) or {}
        return [_Asset(row) for row in body.get("assets") or [] if isinstance(row, dict)]

    def stats(self) -> Dict[str, int]:
        try:
            body = _ok_data(_request("/meme-pack/stats")) or {}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            body = {}
        return {
            "total": int(body.get("total") or 0),
            "enabled": int(body.get("enabled") or 0),
            "banned": int(body.get("banned") or 0),
            "usage_count": int(body.get("usage_count") or 0),
        }

    def get_asset(self, asset_id):
        try:
            result = _request(
                f"/meme-pack/get?id={urllib.parse.quote(str(asset_id), safe='')}"
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None
        body = _ok_data(result) or {}
        asset = body.get("asset") if isinstance(body.get("asset"), dict) else body
        return _Asset(asset) if asset else None

    def update_asset(self, payload: Dict[str, Any]) -> bool:
        try:
            return _ok_data(_request("/meme-pack/update", payload)) is not None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return False

    def set_enabled(self, asset_ids, enabled: bool) -> int:
        try:
            body = _ok_data(
                _request(
                    "/meme-pack/set-enabled",
                    {"ids": list(asset_ids), "enabled": bool(enabled)},
                )
            ) or {}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return 0
        return int(body.get("updated") or 0)

    def delete_assets(self, asset_ids, *, delete_files: bool = False) -> int:
        try:
            body = _ok_data(
                _request(
                    "/meme-pack/delete",
                    {"ids": list(asset_ids), "delete_files": bool(delete_files)},
                )
            ) or {}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return 0
        return int(body.get("deleted") or 0)

    # ---------- import（远程不可行，明确失败而不是静默假成功） ----------
    def import_file(self, path, tags=None):
        return False, "遥控窗连的是远程大脑，本机文件导入不了（把图片放到服务器端再导入）"

    def import_directory(self, directory, tags=None) -> Dict[str, int]:
        return {"imported": 0, "skipped": 0, "failed": 1}
