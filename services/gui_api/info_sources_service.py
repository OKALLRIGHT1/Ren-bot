from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from services.gui_api.models_service import SecretUpdate


ALAPI_SECRET_PLUGIN_TRIGGER = "magic_daily"
ALAPI_SECRET_KEY = "api_token"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _client_endpoint_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or row.get("id") or ""),
        "method": str(row.get("method") or "GET").upper(),
        "path": str(row.get("path") or ""),
    }


def _client_endpoint(row: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(row or {})
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or data.get("id") or ""),
        "method": str(data.get("method") or "GET").upper(),
        "path": str(data.get("path") or ""),
        "params": _as_dict(data.get("params")),
        "cache_ttl_sec": int(data.get("cache_ttl_sec") or 0),
    }


def _client_provider(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or row.get("id") or ""),
        "base_url": str(row.get("base_url") or ""),
        "token_param": str(row.get("token_param") or "token"),
    }


class InfoSourcesGuiService:
    """Structured info-source endpoints/token management for Qt and /gui HTTP."""

    def __init__(
        self,
        *,
        source_root: str | Path,
        secret_store: Any = None,
        manager: Any = None,
        provider_id: str = "alapi",
    ) -> None:
        self.source_root = Path(source_root)
        self.secret_store = secret_store
        self.provider_id = str(provider_id or "alapi")
        if manager is not None:
            self.manager = manager
        else:
            from services.info_sources.config_manager import InfoSourceConfigManager

            self.manager = InfoSourceConfigManager.for_root(
                self.source_root, provider_id=self.provider_id
            )

    def _has_token(self) -> bool:
        if self.secret_store is None or not hasattr(self.secret_store, "get_secret"):
            return False
        try:
            value = self.secret_store.get_secret(
                ALAPI_SECRET_PLUGIN_TRIGGER, ALAPI_SECRET_KEY
            )
        except Exception:
            return False
        return bool(str(value or "").strip())

    def _read_token(self) -> str:
        if self.secret_store is None or not hasattr(self.secret_store, "get_secret"):
            return ""
        try:
            return str(
                self.secret_store.get_secret(
                    ALAPI_SECRET_PLUGIN_TRIGGER, ALAPI_SECRET_KEY
                )
                or ""
            ).strip()
        except Exception:
            return ""

    def list_overview(self, provider_id: str = "") -> Dict[str, Any]:
        provider = str(provider_id or self.provider_id or "alapi").strip() or "alapi"
        try:
            self.manager.set_provider(provider)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "invalid_provider"}
        try:
            providers = [_client_provider(item) for item in self.manager.list_providers()]
            endpoints = [
                _client_endpoint_summary(item) for item in self.manager.list_endpoints()
            ]
            provider_config = _client_provider(self.manager.load_provider_config(provider))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "data": {
                "provider_id": provider,
                "providers": providers,
                "provider": provider_config,
                "endpoints": endpoints,
                "has_alapi_token": self._has_token(),
            },
        }

    def get_endpoint(self, endpoint_id: str, *, provider_id: str = "") -> Dict[str, Any]:
        provider = str(provider_id or self.provider_id or "alapi").strip() or "alapi"
        endpoint_id = str(endpoint_id or "").strip()
        if not endpoint_id:
            return {"ok": False, "error": "invalid_id"}
        try:
            self.manager.set_provider(provider)
            data = self.manager.load_endpoint(endpoint_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "not_found"}
        return {"ok": True, "data": _client_endpoint(data)}

    def save_endpoint(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        provider = str(body.get("provider_id") or self.provider_id or "alapi").strip() or "alapi"
        try:
            self.manager.set_provider(provider)
            path = self.manager.save_endpoint(body)
            saved = self.manager.load_endpoint(str(body.get("id") or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "save_failed"}
        data = _client_endpoint(saved)
        data["file"] = str(path)
        data["provider_id"] = provider
        return {"ok": True, "data": data}

    def build_draft(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {"ok": False, "error": "empty_text"}
        try:
            draft = self.manager.build_alapi_draft_from_text(raw)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "draft_failed"}
        return {"ok": True, "data": _client_endpoint(draft)}

    def update_token(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        secret = SecretUpdate.parse(body.get("api_token", body.get("token")))
        if secret.action == "keep":
            return {
                "ok": True,
                "data": {"has_alapi_token": self._has_token(), "updated": False},
            }
        if self.secret_store is None or not hasattr(self.secret_store, "set_secret"):
            return {"ok": False, "error": "secret_store_unavailable"}
        value = "" if secret.action == "clear" else str(secret.value or "").strip()
        try:
            self.secret_store.set_secret(
                ALAPI_SECRET_PLUGIN_TRIGGER, ALAPI_SECRET_KEY, value
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "token_save_failed"}
        return {
            "ok": True,
            "data": {"has_alapi_token": bool(value), "updated": True},
        }

    async def test_endpoint(
        self,
        payload: Dict[str, Any],
        *,
        request_func: Any = None,
    ) -> Dict[str, Any]:
        body = _as_dict(payload)
        provider = str(body.get("provider_id") or self.provider_id or "alapi").strip() or "alapi"
        endpoint_id = str(body.get("id") or body.get("endpoint_id") or "").strip()
        params = _as_dict(body.get("params"))
        token = self._read_token()
        override = body.get("token")
        if isinstance(override, str) and override.strip():
            token = override.strip()
        try:
            self.manager.set_provider(provider)
            if body.get("endpoint") and isinstance(body.get("endpoint"), dict):
                result = await self.manager.test_endpoint_config(
                    body["endpoint"],
                    token=token,
                    params=params,
                    request_func=request_func,
                )
            else:
                if not endpoint_id:
                    return {"ok": False, "error": "invalid_id"}
                result = await self.manager.test_endpoint(
                    endpoint_id,
                    token=token,
                    params=params,
                    request_func=request_func,
                )
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "test_failed"}
        return {
            "ok": True,
            "data": {
                "ok": bool(getattr(result, "ok", False)),
                "provider": str(getattr(result, "provider", provider) or provider),
                "capability": str(getattr(result, "capability", endpoint_id) or endpoint_id),
                "summary": str(getattr(result, "summary", "") or ""),
                "error": str(getattr(result, "error", "") or ""),
                "data": getattr(result, "data", None),
            },
        }
