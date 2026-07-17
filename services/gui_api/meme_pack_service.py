from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

PREVIEW_MAX_BYTES = 450_000


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _preview_data_url(file_path: str) -> str:
    path = Path(str(file_path or "")).expanduser()
    try:
        if not path.is_file():
            return ""
        size = path.stat().st_size
        if size <= 0 or size > PREVIEW_MAX_BYTES:
            return ""
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/png"
        if not str(mime).startswith("image/"):
            return ""
        raw = path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""


def _asset_to_dict(asset: Any, *, with_preview: bool = False) -> Dict[str, Any]:
    if asset is None:
        return {}
    if isinstance(asset, dict):
        data = dict(asset)
    else:
        data = {
            "id": getattr(asset, "id", 0),
            "file_name": getattr(asset, "file_name", ""),
            "file_path": getattr(asset, "file_path", ""),
            "description": getattr(asset, "description", ""),
            "tags": list(getattr(asset, "tags", []) or []),
            "emotion": getattr(asset, "emotion", ""),
            "enabled": bool(getattr(asset, "enabled", True)),
            "banned": bool(getattr(asset, "banned", False)),
            "usage_count": int(getattr(asset, "usage_count", 0) or 0),
        }
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    file_path = str(data.get("file_path") or "")
    row = {
        "id": int(data.get("id") or 0),
        "file_name": str(data.get("file_name") or ""),
        "file_path": file_path,
        "description": str(data.get("description") or ""),
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
        "emotion": str(data.get("emotion") or ""),
        "enabled": bool(data.get("enabled", True)),
        "banned": bool(data.get("banned", False)),
        "usage_count": int(data.get("usage_count") or 0),
        "has_preview": False,
        "preview_data_url": "",
    }
    if with_preview:
        preview = _preview_data_url(file_path)
        row["preview_data_url"] = preview
        row["has_preview"] = bool(preview)
    return row


class MemePackGuiService:
    def __init__(
        self,
        *,
        store: Any = None,
        store_factory: Optional[Callable[[], Any]] = None,
        plugin_manager: Any = None,
        plugin_trigger: str = "meme_pack",
    ) -> None:
        self._store = store
        self._store_factory = store_factory
        self.plugin_manager = plugin_manager
        self.plugin_trigger = plugin_trigger

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        if self._store_factory is not None:
            self._store = self._store_factory()
            return self._store
        manager = self.plugin_manager
        if manager is not None:
            plugins = getattr(manager, "plugins", {}) or {}
            plugin = plugins.get(self.plugin_trigger)
            if plugin is not None:
                for attr in ("store", "meme_store", "_store"):
                    candidate = getattr(plugin, attr, None)
                    if candidate is not None:
                        self._store = candidate
                        return self._store
                getter = getattr(plugin, "get_store", None)
                if callable(getter):
                    self._store = getter()
                    return self._store
        raise RuntimeError("meme_store_unavailable")

    def list_assets(self, *, query: str = "", include_disabled: bool = True, limit: int = 500) -> Dict[str, Any]:
        try:
            store = self._get_store()
            if hasattr(store, "search_assets"):
                rows = store.search_assets(
                    str(query or ""),
                    include_disabled=bool(include_disabled),
                    limit=int(limit or 500),
                )
            else:
                rows = store.list_assets(enabled_only=not include_disabled, limit=int(limit or 500))
            stats = store.stats() if hasattr(store, "stats") else {}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "meme_store_unavailable"}
        assets = [_asset_to_dict(row, with_preview=True) for row in rows]
        return {
            "ok": True,
            "data": {
                "assets": assets,
                "count": len(assets),
                "stats": {
                    "total": int(stats.get("total") or 0),
                    "enabled": int(stats.get("enabled") or 0),
                    "banned": int(stats.get("banned") or 0),
                    "usage_count": int(stats.get("usage_count") or 0),
                },
            },
        }

    def get_asset(self, asset_id: int | str) -> Dict[str, Any]:
        try:
            store = self._get_store()
            asset = store.get_asset(int(asset_id))
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "meme_store_unavailable"}
        if not asset:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": _asset_to_dict(asset, with_preview=True)}

    def update_asset(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = _as_dict(payload)
        try:
            asset_id = int(body.get("id") or 0)
        except Exception:
            asset_id = 0
        if asset_id <= 0:
            return {"ok": False, "error": "invalid_id"}
        tags = body.get("tags") if isinstance(body.get("tags"), list) else []
        if isinstance(body.get("tags"), str):
            tags = [part.strip() for part in str(body.get("tags")).split(",") if part.strip()]
        try:
            store = self._get_store()
            ok = bool(
                store.update_asset(
                    asset_id,
                    description=str(body.get("description") or ""),
                    tags=tags,
                    emotion=str(body.get("emotion") or ""),
                    enabled=bool(body.get("enabled", True)),
                    banned=bool(body.get("banned", False)),
                )
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not ok:
            return {"ok": False, "error": "not_found"}
        return self.get_asset(asset_id)

    def set_enabled(self, asset_ids: Iterable[Any], enabled: bool) -> Dict[str, Any]:
        ids: List[int] = []
        for item in asset_ids or []:
            try:
                value = int(item)
            except Exception:
                continue
            if value > 0:
                ids.append(value)
        if not ids:
            return {"ok": False, "error": "empty_ids"}
        try:
            store = self._get_store()
            updated = int(store.set_enabled(ids, bool(enabled)) or 0)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "data": {"updated": updated, "enabled": bool(enabled)}}

    def delete_assets(self, asset_ids: Iterable[Any], *, delete_files: bool = False) -> Dict[str, Any]:
        ids: List[int] = []
        for item in asset_ids or []:
            try:
                value = int(item)
            except Exception:
                continue
            if value > 0:
                ids.append(value)
        if not ids:
            return {"ok": False, "error": "empty_ids"}
        try:
            store = self._get_store()
            deleted = int(store.delete_assets(ids, delete_files=bool(delete_files)) or 0)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "data": {"deleted": deleted, "delete_files": bool(delete_files)}}

    def stats(self) -> Dict[str, Any]:
        try:
            store = self._get_store()
            stats = store.stats() if hasattr(store, "stats") else {}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "meme_store_unavailable"}
        return {
            "ok": True,
            "data": {
                "total": int(stats.get("total") or 0),
                "enabled": int(stats.get("enabled") or 0),
                "banned": int(stats.get("banned") or 0),
                "usage_count": int(stats.get("usage_count") or 0),
            },
        }
