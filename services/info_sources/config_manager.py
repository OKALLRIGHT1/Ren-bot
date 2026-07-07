from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from services.info_sources.models import InfoSourceResult
from services.info_sources.providers.alapi import AlapiProvider, RequestFunc


class InfoSourceConfigManager:
    DEFAULT_PROVIDER = {
        "id": "alapi",
        "name": "ALAPI",
        "base_url": "https://v3.alapi.cn",
        "token_param": "token",
    }

    def __init__(
        self,
        endpoint_dir: str | Path,
        source_root: str | Path | None = None,
        provider_id: str | None = None,
    ):
        self.endpoint_dir = Path(endpoint_dir)
        self.source_root = Path(source_root) if source_root is not None else self.endpoint_dir.parent
        self.provider_id = provider_id or self.endpoint_dir.name

    @classmethod
    def for_root(
        cls,
        source_root: str | Path,
        provider_id: str = "alapi",
    ) -> "InfoSourceConfigManager":
        root = Path(source_root)
        safe_provider = cls._normalize_id(provider_id, "provider id")
        return cls(
            root / safe_provider,
            source_root=root,
            provider_id=safe_provider,
        )

    def set_provider(self, provider_id: str) -> None:
        safe_provider = self._normalize_id(provider_id, "provider id")
        self.provider_id = safe_provider
        self.endpoint_dir = self.source_root / safe_provider

    def list_providers(self) -> list[Dict[str, Any]]:
        providers: list[Dict[str, Any]] = []
        if self.source_root.exists():
            for path in sorted(self.source_root.iterdir()):
                if not path.is_dir():
                    continue
                provider = self.load_provider_config(path.name)
                provider["file"] = str(path / "provider.json")
                providers.append(provider)
        if providers:
            return providers
        fallback = dict(self.DEFAULT_PROVIDER)
        fallback["file"] = str(self.source_root / "alapi" / "provider.json")
        return [fallback]

    def load_provider_config(self, provider_id: str | None = None) -> Dict[str, Any]:
        safe_provider = self._normalize_id(provider_id or self.provider_id, "provider id")
        path = self.source_root / safe_provider / "provider.json"
        data: Dict[str, Any] = {}
        if path.exists():
            try:
                data = self._read_json(path)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        if safe_provider == "alapi":
            data = {**self.DEFAULT_PROVIDER, **data}
        data["id"] = safe_provider
        data.setdefault("name", safe_provider)
        data.setdefault("base_url", "")
        data.setdefault("token_param", "token")
        return data

    def save_provider_config(self, provider: Dict[str, Any]) -> Path:
        if not isinstance(provider, dict):
            raise ValueError("provider must be a JSON object")
        safe_provider = self._normalize_id(provider.get("id"), "provider id")
        data = {
            "id": safe_provider,
            "name": str(provider.get("name") or safe_provider).strip(),
            "base_url": str(provider.get("base_url") or "").strip(),
            "token_param": str(provider.get("token_param") or "token").strip(),
        }
        self.set_provider(safe_provider)
        self.endpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self.endpoint_dir / "provider.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def list_endpoints(self) -> list[Dict[str, Any]]:
        items = []
        if not self.endpoint_dir.exists():
            return items
        for path in sorted(self.endpoint_dir.glob("*.json")):
            if path.name == "provider.json":
                continue
            try:
                data = self._read_json(path)
            except Exception:
                continue
            endpoint_id = str(data.get("id") or path.stem).strip()
            if not endpoint_id:
                continue
            items.append(
                {
                    "id": endpoint_id,
                    "name": str(data.get("name") or endpoint_id),
                    "method": str(data.get("method") or "GET").upper(),
                    "path": str(data.get("path") or ""),
                    "file": str(path),
                }
            )
        return items

    def load_endpoint(self, endpoint_id: str) -> Dict[str, Any]:
        endpoint_id = self._normalize_endpoint_id(endpoint_id)
        path = self._endpoint_path(endpoint_id)
        data = self._read_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"endpoint config must be object: {path}")
        data.setdefault("id", str(endpoint_id or path.stem).strip())
        data.setdefault("name", data["id"])
        data.setdefault("method", "GET")
        data.setdefault("path", "")
        data.setdefault("params", {})
        return data

    def save_endpoint(self, endpoint: Dict[str, Any]) -> Path:
        data = self.validate_endpoint(endpoint)
        self.endpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self._endpoint_path(data["id"])
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def validate_endpoint(self, endpoint: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(endpoint, dict):
            raise ValueError("endpoint must be a JSON object")
        data = dict(endpoint)
        endpoint_id = self._normalize_endpoint_id(data.get("id"))
        method = str(data.get("method") or "GET").strip().upper()
        if method not in {"GET", "POST"}:
            raise ValueError("method must be GET or POST")
        path = str(data.get("path") or "").strip()
        if not path:
            raise ValueError("path is required")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        data["id"] = endpoint_id
        data["name"] = str(data.get("name") or endpoint_id).strip()
        data["method"] = method
        data["path"] = path
        data["params"] = params
        return data

    def build_alapi_draft_from_text(self, text: str) -> Dict[str, Any]:
        raw = str(text or "")
        method = "POST" if re.search(r"\bPOST\b", raw, re.IGNORECASE) else "GET"
        url = self._extract_url(raw)
        path = self._path_from_url(url) if url else self._extract_path(raw)
        endpoint_id = self._id_from_path(path)
        params = self._extract_params(raw)
        params.pop("token", None)
        if "format" not in params and re.search(r"\bformat\b", raw, re.IGNORECASE):
            params["format"] = {"type": "string", "required": False, "default": "json"}
        return {
            "id": endpoint_id,
            "name": endpoint_id.replace("_", " "),
            "method": method,
            "path": path,
            "params": params,
            "cache_ttl_sec": 600,
        }

    async def test_endpoint(
        self,
        endpoint_id: str,
        *,
        token: str = "",
        params: Optional[Dict[str, Any]] = None,
        request_func: Optional[RequestFunc] = None,
    ) -> InfoSourceResult:
        provider = AlapiProvider(
            endpoint_dir=self.endpoint_dir,
            token_getter=lambda: token,
            request_func=request_func,
        )
        return await provider.fetch(endpoint_id, **dict(params or {}))

    async def test_endpoint_config(
        self,
        endpoint: Dict[str, Any],
        *,
        token: str = "",
        params: Optional[Dict[str, Any]] = None,
        request_func: Optional[RequestFunc] = None,
    ) -> InfoSourceResult:
        data = self.validate_endpoint(endpoint)
        provider = AlapiProvider(
            endpoint_dir=self.endpoint_dir,
            token_getter=lambda: token,
            request_func=request_func,
            endpoint_configs=[data],
        )
        return await provider.fetch(data["id"], **dict(params or {}))

    def _endpoint_path(self, endpoint_id: str) -> Path:
        safe_id = self._normalize_endpoint_id(endpoint_id)
        return self.endpoint_dir / f"{safe_id}.json"

    def _normalize_endpoint_id(self, endpoint_id: Any) -> str:
        return self._normalize_id(endpoint_id, "id")

    @staticmethod
    def _normalize_id(value: Any, label: str) -> str:
        safe_id = str(value or "").strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", safe_id):
            raise ValueError(f"{label} must start with a letter and contain only letters, numbers, underscore")
        return safe_id

    def _read_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _extract_url(self, text: str) -> str:
        match = re.search(r"https?://[^\s,，;；]+", text)
        return match.group(0).strip() if match else ""

    def _path_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.path or "/"

    def _extract_path(self, text: str) -> str:
        match = re.search(r"(/api/[A-Za-z0-9_./-]+)", text)
        return match.group(1).strip() if match else "/api/endpoint"

    def _id_from_path(self, path: str) -> str:
        cleaned = str(path or "").strip("/").replace("api/", "", 1)
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", cleaned) if part]
        return "_".join(parts[-2:] if len(parts) >= 2 else parts) or "endpoint"

    def _extract_params(self, text: str) -> Dict[str, Dict[str, Any]]:
        params: Dict[str, Dict[str, Any]] = {}
        known = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", text)
        ignore = {
            "GET",
            "POST",
            "http",
            "https",
            "v3",
            "alapi",
            "cn",
            "api",
            "json",
            "image",
        }
        for name in known:
            if name in ignore or len(name) > 32:
                continue
            if name.lower() in {"token", "city", "city_id", "province", "ip", "lon", "lat", "format"}:
                params.setdefault(
                    name,
                    {
                        "type": "string",
                        "required": self._mentions_required(text, name),
                    },
                )
        for name in list(params):
            if name.lower() == "format" and re.search(r"format\s*(?:默认|default)?\s*json", text, re.IGNORECASE):
                params[name]["default"] = "json"
                params[name]["required"] = False
        return params

    def _mentions_required(self, text: str, name: str) -> bool:
        pattern = rf"{re.escape(name)}[^。\n;；,，]{{0,12}}(?:必填|required)"
        return bool(re.search(pattern, text, re.IGNORECASE))
