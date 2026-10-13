"""HTTP adapter for the knowledge-manager dialog (client has no brain/plugin)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import client_config


def _request(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0):
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


def _call(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    try:
        result = _request(path, payload, timeout=timeout)
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_response"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


class RemoteKnowledgeGui:
    """Drop-in for KnowledgeGuiService over /knowledge*."""

    # Dialogs gate mutate/search on `brain is not None`.
    brain = True
    plugin_manager = None

    def list_dirs(self) -> Dict[str, Any]:
        return _call("/knowledge")

    def save_dirs(self, dirs: Any) -> Dict[str, Any]:
        return _call("/knowledge/dirs", {"dirs": list(dirs or [])})

    def stats(self) -> Dict[str, Any]:
        return _call("/knowledge/stats")

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return _call("/knowledge/search", {"query": str(query or ""), "limit": int(limit or 5)})

    def import_file(self, path: str, progress_callback=None) -> Dict[str, Any]:
        return _call("/knowledge/import", {"path": str(path or "")}, timeout=120)

    def rebuild(self) -> Dict[str, Any]:
        return _call("/knowledge/rebuild", {}, timeout=120)

    def delete_by_dirs(self, dirs: Any) -> Dict[str, Any]:
        return _call("/knowledge/delete-dirs", {"dirs": list(dirs or [])})

    def learn_configured_dirs(self) -> Dict[str, Any]:
        return _call("/knowledge/learn", {}, timeout=300)

    def create_doc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return _call("/knowledge/create-doc", dict(payload or {}))
