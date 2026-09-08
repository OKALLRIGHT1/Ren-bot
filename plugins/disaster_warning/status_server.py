from __future__ import annotations

from pathlib import Path
from typing import Any

from .settings import FAN_APPLY_URL, WOLFX_DOCS_URL, clamp_ui_host, parse_watch_regions, regions_to_payload
from .watch import WatchRegion

WEB_DIR = Path(__file__).with_name("web")
INDEX_HTML = WEB_DIR / "index.html"


def public_status(plugin: Any) -> dict:
    cfg = plugin._cfg
    ws_running = getattr(plugin, "_ws_running", None)
    wolfx_running = getattr(plugin, "_wolfx_running", None)
    fan_running = getattr(plugin, "_fan_running", None)
    ws = "running" if callable(ws_running) and ws_running() else "idle"
    wolfx_ws = "running" if callable(wolfx_running) and wolfx_running() else "idle"
    fan_ws = "running" if callable(fan_running) and fan_running() else "idle"
    return {
        "ok": True,
        "status": {
            "enabled": bool(cfg.enabled),
            "ws": ws,
            "wolfx_enabled": bool(cfg.wolfx_eew_enabled),
            "wolfx_ws": wolfx_ws,
            "fan_ws": fan_ws,
            "has_api_key": bool(cfg.fanstudio_api_key),
            "app_id": cfg.fanstudio_app_id,
            "apply_url": FAN_APPLY_URL,
            "wolfx_docs_url": WOLFX_DOCS_URL,
            "owner_session": cfg.owner_session,
            "group_sessions": list(cfg.group_sessions),
            "push_sessions": cfg.push_sessions(),
            "min_magnitude": cfg.min_magnitude,
            "major_override_mag": cfg.major_override_mag,
            "silence_sec": cfg.silence_sec,
            "silence_remaining": plugin._state.silence_remaining(),
            "stats": dict(plugin._state.stats),
            "recent": plugin._state.recent_seen(5),
            "ui": f"http://127.0.0.1:{cfg.status_ui_port}/",
        },
        "regions": regions_to_payload(cfg.regions),
    }


def region_from_body(body: dict) -> WatchRegion:
    parsed = parse_watch_regions([body if isinstance(body, dict) else {}])
    if not parsed:
        label = str((body or {}).get("label") or "").strip()
        if not label:
            raise ValueError("地区名称不能为空")
        keywords = (body or {}).get("keywords") or [label]
        if isinstance(keywords, str):
            keywords = [part.strip() for part in keywords.replace("，", ",").split(",")]
        parsed = [
            WatchRegion(
                id=label,
                label=label,
                keywords=[k for k in keywords if k] or [label],
            )
        ]
    return parsed[0]


class StatusServer:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._runner = None
        self._site = None

    async def start(self) -> str:
        from aiohttp import web

        cfg = self.plugin._cfg
        host = clamp_ui_host(cfg.status_ui_host)
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/api/status", self._status)
        app.router.add_put("/api/push-settings", self._put_push)
        app.router.add_post("/api/regions", self._add_region)
        app.router.add_put("/api/regions", self._replace_regions)
        app.router.add_delete("/api/regions/{region_id}", self._delete_region)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, int(cfg.status_ui_port))
        await self._site.start()
        url = f"http://{host}:{cfg.status_ui_port}/"
        print(f"[DisasterWarning] web ui {url}")
        return url

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _index(self, request):
        from aiohttp import web

        del request
        html = INDEX_HTML.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def _json(self, request) -> dict:
        try:
            data = await request.json()
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    async def _status(self, request):
        from aiohttp import web

        del request
        return web.json_response(public_status(self.plugin))

    async def _put_push(self, request):
        from aiohttp import web

        body = await self._json(request)
        patch = {}
        if "enabled" in body:
            patch["enabled"] = bool(body.get("enabled"))
        if "wolfx_eew_enabled" in body:
            patch["wolfx_eew_enabled"] = bool(body.get("wolfx_eew_enabled"))
        if "owner_session" in body:
            patch["owner_session"] = str(body.get("owner_session") or "").strip()
        if "group_sessions" in body:
            patch["group_sessions"] = body.get("group_sessions")
        if "min_magnitude" in body:
            patch["min_magnitude"] = body.get("min_magnitude")
        if "major_override_mag" in body:
            patch["major_override_mag"] = body.get("major_override_mag")
        if "silence_sec" in body:
            patch["silence_sec"] = body.get("silence_sec")
        if str(body.get("fanstudio_api_key") or "").strip():
            patch["fanstudio_api_key"] = str(body.get("fanstudio_api_key") or "").strip()
        if body.get("clear_api_key"):
            patch["clear_api_key"] = True
        if not self.plugin.patch_settings(patch):
            return web.json_response({"ok": False, "error": "保存失败"}, status=500)
        return web.json_response(public_status(self.plugin))

    async def _add_region(self, request):
        from aiohttp import web

        try:
            region = region_from_body(await self._json(request))
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        regions = list(self.plugin._cfg.regions)
        if any(item.id == region.id or item.label == region.label for item in regions):
            return web.json_response({"ok": False, "error": "已存在同名关注"}, status=400)
        regions.append(region)
        if not self.plugin.replace_regions(regions):
            return web.json_response({"ok": False, "error": "保存失败"}, status=500)
        return web.json_response(public_status(self.plugin))

    async def _replace_regions(self, request):
        from aiohttp import web

        body = await self._json(request)
        items = body.get("regions") if isinstance(body.get("regions"), list) else body.get("items")
        regions = parse_watch_regions(items if isinstance(items, list) else [])
        if not self.plugin.replace_regions(regions):
            return web.json_response({"ok": False, "error": "保存失败"}, status=500)
        return web.json_response(public_status(self.plugin))

    async def _delete_region(self, request):
        from aiohttp import web

        region_id = str(request.match_info.get("region_id") or "").strip()
        regions = [item for item in self.plugin._cfg.regions if item.id != region_id]
        if len(regions) == len(self.plugin._cfg.regions):
            return web.json_response({"ok": False, "error": "未找到该关注"}, status=404)
        if not self.plugin.replace_regions(regions):
            return web.json_response({"ok": False, "error": "保存失败"}, status=500)
        return web.json_response(public_status(self.plugin))
