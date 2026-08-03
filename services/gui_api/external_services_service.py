from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from modules.external_services import (
    dump_services_settings,
    ensure_service,
    list_services_status,
    load_services_settings,
    service_status,
    stop_service,
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


class ExternalServicesGuiService:
    def __init__(
        self,
        *,
        load_runtime: Optional[Callable[[], Dict[str, Any]]] = None,
        update_runtime: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._load_runtime = load_runtime
        self._update_runtime = update_runtime

    def _runtime(self) -> Dict[str, Any]:
        if self._load_runtime is None:
            try:
                from modules.runtime_settings import load_runtime_settings

                return _as_dict(load_runtime_settings())
            except Exception:
                return {}
        try:
            return _as_dict(self._load_runtime())
        except Exception:
            return {}

    def _save_runtime(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        if self._update_runtime is not None:
            return _as_dict(self._update_runtime(patch))
        from modules.runtime_settings import update_runtime_settings

        return _as_dict(update_runtime_settings(patch))

    def list_services(self) -> Dict[str, Any]:
        runtime = self._runtime()
        data = list_services_status(runtime)
        return {"ok": True, "data": data}

    def get_service(self, service_id: str) -> Dict[str, Any]:
        runtime = self._runtime()
        return {"ok": True, "data": service_status(service_id, runtime)}

    def save_services(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        services_in = body.get("services")
        if not isinstance(services_in, dict):
            # allow single service payload
            sid = str(body.get("id") or body.get("service_id") or "").strip()
            if not sid:
                return {"ok": False, "error": "empty_services"}
            services_in = {sid: body}
        current = load_services_settings(self._runtime())
        for sid, raw in services_in.items():
            if not str(sid).strip():
                continue
            cur = dict(current.get(str(sid).lower()) or {})
            incoming = _as_dict(raw)
            cur.update(incoming)
            current[str(sid).lower()] = cur
        dumped = dump_services_settings(current)
        self._save_runtime(
            {
                "external_services": dumped,
                # keep legacy key in sync for ollama
                "ollama_autostart_enabled": bool(
                    (dumped.get("ollama") or {}).get("autostart_enabled")
                ),
            }
        )
        return self.list_services()

    def ensure(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        body = _as_dict(payload)
        sid = str(body.get("id") or body.get("service_id") or "").strip().lower()
        if not sid:
            return {"ok": False, "error": "invalid_service_id"}
        # optional inline save before ensure
        if any(
            key in body
            for key in (
                "autostart_enabled",
                "autostop_enabled",
                "command",
                "args",
                "cwd",
                "health_url",
                "host",
                "port",
            )
        ):
            self.save_services({"services": {sid: body}})
        runtime = self._runtime()
        result = ensure_service(
            sid,
            runtime,
            force=bool(body.get("force", True)),
            wait_seconds=body.get("wait_seconds"),
        )
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "ensure_failed"), "data": result}
        return {"ok": True, "data": result}

    def stop(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        body = _as_dict(payload)
        sid = str(body.get("id") or body.get("service_id") or "").strip().lower()
        if not sid:
            return {"ok": False, "error": "invalid_service_id"}
        result = stop_service(
            sid,
            only_if_started_by_us=bool(body.get("only_if_started_by_us", True)),
        )
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "stop_failed"), "data": result}
        return {"ok": True, "data": result}
