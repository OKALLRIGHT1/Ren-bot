"""HTTP-backed external services store, for the remote control window.

计划 §3/§4（docs/部分重构.md）：客户端只做薄适配 —— 通过 Brain 的
/external-services* API 查看和操作 Ollama / GPT-SoVITS / NapCat。
本模块不扫描目录、不启动进程、不复制 external_services.py 的发现逻辑。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import client_config


def _request(
    path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 6.0
) -> Dict[str, Any]:
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


class RemoteExternalServicesStore:
    """Brain /external-services* API 的薄客户端。

    `last_error` 区分失败原因（HTTP 状态码 / 网络异常 / 响应不合法），
    供页面显示——「接口不存在」「认证失败」「后端未连接」和「列表真的
    为空」必须是不同的用户可见状态。
    """

    def __init__(self) -> None:
        self._rows: List[Dict[str, Any]] = []
        self.last_error: str = ""

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    @staticmethod
    def _services_from(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, list):
            return []
        return [row for row in services if isinstance(row, dict)]

    def refresh(self) -> List[Dict[str, Any]]:
        self.last_error = ""
        try:
            payload = _request("/external-services")
            if not isinstance(payload, dict) or not payload.get("ok"):
                self.last_error = (
                    str(payload.get("error") if isinstance(payload, dict) else "")
                    or "invalid_response"
                )
                return []
            self._rows = self._services_from(payload)
        except urllib.error.HTTPError as exc:
            self.last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        return list(self._rows)

    def _ensure(self, service_id: str, action: str) -> Dict[str, Any]:
        """action: ensure=启动/确保运行, stop=停止."""
        path = "/external-services/ensure" if action == "ensure" else "/external-services/stop"
        try:
            return _request(path, {"id": service_id})
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error") or ""
            except Exception:
                detail = ""
            return {"ok": False, "error": f"HTTP {exc.code}: {detail or exc.reason}"}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def start(self, service_id: str) -> Dict[str, Any]:
        return self._ensure(service_id, "ensure")

    def stop(self, service_id: str) -> Dict[str, Any]:
        return self._ensure(service_id, "stop")

    def restart(self, service_id: str) -> Dict[str, Any]:
        """启动 + 停止组合：先停再确保运行。stop 失败但服务本就没跑时也继续。"""
        self.stop(service_id)
        return self.start(service_id)

    def save(self, service_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Persist command/autostart/etc. Brain owns discovery and process start."""
        body = {"id": str(service_id or "").strip()}
        body.update(patch or {})
        try:
            return _request("/external-services", body)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error") or ""
            except Exception:
                detail = ""
            return {"ok": False, "error": f"HTTP {exc.code}: {detail or exc.reason}"}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
