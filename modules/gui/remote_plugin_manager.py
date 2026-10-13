"""HTTP-backed stand-in for the brain's PluginManager, for the remote window.

The plugin dialogs were written against the in-process PluginManager. When
the Qt client runs as a remote control (plugin_manager=None), this adapter
implements the same surface over the brain's /gui/plugins HTTP API:
list / toggle / reload / config / config schema / access-control normalize.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import client_config


def _request(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 5.0):
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


class RemotePluginManager:
    """Implements the PluginManager surface used by the plugin dialogs."""

    def __init__(self):
        self._rows: List[Dict[str, Any]] = []

    # ---------- list / enabled state ----------
    def _list(self) -> List[Dict[str, Any]]:
        try:
            data = _request("/plugins")
            if isinstance(data, dict) and data.get("ok"):
                plugins = (data.get("data") or {}).get("plugins") or []
                self._rows = [row for row in plugins if isinstance(row, dict)]
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass
        return list(self._rows)

    def get_all_plugins_info(self) -> List[Dict[str, Any]]:
        return self._list()

    def is_plugin_enabled(self, trigger: str) -> bool:
        key = str(trigger or "").strip()
        for row in self._rows:
            if str(row.get("trigger") or "") == key:
                return bool(row.get("enabled", True))
        return True

    # ---------- toggle / reload ----------
    def _toggle(self, trigger: str, enabled: bool) -> bool:
        try:
            data = _request(
                "/plugins/toggle", {"trigger": str(trigger), "enabled": enabled}
            )
            ok = bool(isinstance(data, dict) and data.get("ok"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return False
        if ok:
            self._list()
        return ok

    def enable_plugin(self, trigger: str) -> bool:
        return self._toggle(trigger, True)

    def disable_plugin(self, trigger: str) -> bool:
        return self._toggle(trigger, False)

    def load_plugins(self) -> bool:
        try:
            data = _request("/plugins/reload", {})
            return bool(isinstance(data, dict) and data.get("ok"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return False

    # ---------- config / schema ----------
    def get_plugin_config(self, trigger: str) -> Dict[str, Any]:
        query = urllib.parse.quote(str(trigger or ""), safe="")
        try:
            data = _request(f"/plugins/config?trigger={query}")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict) and data.get("ok"):
            body = data.get("data") or {}
            # 两种返回形态：直接 config，或 {config, schema}
            config = body.get("config") if isinstance(body.get("config"), dict) else body
            return config if isinstance(config, dict) else {}
        return {}

    def get_plugin_config_schema(self, trigger: str) -> Dict[str, Any]:
        query = urllib.parse.quote(str(trigger or ""), safe="")
        try:
            data = _request(f"/plugins/config/schema?trigger={query}")
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict) and data.get("ok"):
            body = data.get("data") or {}
            schema = body.get("schema") if isinstance(body, dict) else body
            return schema if isinstance(schema, dict) else {}
        return {}

    # ---------- misc used by dialogs ----------
    @staticmethod
    def _normalize_access_control(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def get_plugin_icon_path(self, trigger: str) -> Optional[str]:
        return None

    def get_plugin_readme_path(self, trigger: str) -> Optional[str]:
        return None
