from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

from .event import DisasterEvent
from .push import format_text, send_alert
from .settings import FAN_APPLY_URL, PluginSettings, regions_to_payload
from .sources.fanstudio_ws import FanStudioClient
from .sources.parse_eew import (
    extract_cea_payload,
    parse_cea_event,
    snapshot_cea_events,
)
from .query import (
    extract_place,
    infer_action,
    looks_like_disaster_query,
    parse_invocation,
)
from .sources.cenc_eqlist import CencEqListClient, filter_eqlist, format_eqlist
from .sources.nmc_alerts import (
    NmcClient,
    alert_to_event,
    filter_alerts,
    format_alerts,
    format_typhoons,
)
from .sources.parse_wolfx import parse_wolfx_eew
from .sources.wolfx_ws import WolfxClient
from .state import WarningState
from .watch import WatchRegion, should_push_alert, should_push_eew

COMMAND_ALIASES = ("/灾害预警状态", "/灾害预警", "/地震预警")
SNAPSHOT_TYPES = {"initial", "initial_all", "query_response"}
AUTH_TYPES = {"auth_success", "auth_ok", "authenticated"}
PREAUTH_ONLY_KEYS = {"fssn", "fssn-cmt"}


class Plugin:
    type = "react"
    allow_natural_language_direct = False
    aliases = ["/灾害预警", "/地震预警"]
    direct_command_aliases = ["/灾害预警", "/地震预警", "/灾害预警状态"]
    name = "灾害预警"

    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self.settings: Dict[str, Any] = {}
        self._cfg = PluginSettings()
        self._gateway = None
        self._task: Optional[asyncio.Task] = None
        self._fan_task: Optional[asyncio.Task] = None
        self._wolfx_task: Optional[asyncio.Task] = None
        self._client: Optional[FanStudioClient] = None
        self._fan_client: Optional[FanStudioClient] = None
        self._wolfx_client: Optional[WolfxClient] = None
        self._make_fan_client = FanStudioClient
        self._make_wolfx_client = WolfxClient
        self._ui = None
        self._state = WarningState(self._cfg.state_path)
        self._pending: Deque[Tuple[DisasterEvent, dict]] = deque(maxlen=3)
        self._last_ctx: Optional[Dict[str, Any]] = None
        self._eq_client = CencEqListClient()
        self._nmc_client = NmcClient()
        self._alert_task: Optional[asyncio.Task] = None
        self.reload_config()

    def reload_config(self):
        disk: Dict[str, Any] = {}
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8-sig"))
            raw = payload.get("settings")
            if isinstance(raw, dict):
                disk = raw
        except Exception:
            disk = {}
        injected = getattr(self, "settings", None)
        if isinstance(injected, dict) and injected:
            merged = dict(disk)
            merged.update(injected)
            source = merged
        else:
            source = disk
        prev = getattr(self, "_cfg", None)
        self._cfg = PluginSettings.from_raw(source)
        new_state = WarningState(self._cfg.state_path)
        old_state = getattr(self, "_state", None)
        if old_state is None or old_state.path != new_state.path:
            self._state = new_state
        reconnect = False
        if prev is not None:
            reconnect = (
                prev.enabled != self._cfg.enabled
                or prev.fanstudio_api_key != self._cfg.fanstudio_api_key
                or prev.fanstudio_app_id != self._cfg.fanstudio_app_id
                or prev.ws_url != self._cfg.ws_url
                or prev.ws_backup_url != self._cfg.ws_backup_url
                or prev.wolfx_eew_enabled != self._cfg.wolfx_eew_enabled
                or prev.wolfx_ws_url != self._cfg.wolfx_ws_url
                or prev.wolfx_ws_backup_url != self._cfg.wolfx_ws_backup_url
                or prev.alert_poll_enabled != self._cfg.alert_poll_enabled
                or int(prev.alert_poll_sec) != int(self._cfg.alert_poll_sec)
            )
        if reconnect:
            self._schedule_watch_restart()

    def _capture_context(self, ctx: Optional[Dict[str, Any]]) -> None:
        if not isinstance(ctx, dict):
            return
        self._last_ctx = ctx
        from services.plugin_ports import resolve_gateway

        gateway = resolve_gateway(ctx)
        if gateway is not None:
            self._gateway = gateway

    async def start(self, ctx: Optional[Dict[str, Any]] = None):
        self._capture_context(ctx)
        await self._ensure_ui()
        await self._ensure_loop()

    async def stop(self):
        await self._stop_ui()
        await self._stop_watch_clients()

    def _iter_watch_tasks(self):
        return (self._wolfx_task, self._fan_task, self._alert_task, self._task)

    def _ws_running(self) -> bool:
        return any(task is not None and not task.done() for task in self._iter_watch_tasks())

    def _wolfx_running(self) -> bool:
        task = self._wolfx_task
        return task is not None and not task.done()

    def _fan_running(self) -> bool:
        task = self._fan_task
        return task is not None and not task.done()

    async def _stop_watch_clients(self):
        for client in (self._wolfx_client, self._fan_client, self._client):
            if client is None:
                continue
            try:
                client.request_stop()
            except Exception:
                pass
            closer = getattr(client, "close_socket", None)
            if callable(closer):
                try:
                    await closer()
                except Exception:
                    pass
        self._wolfx_client = None
        self._fan_client = None
        self._client = None
        tasks = [task for task in self._iter_watch_tasks() if task is not None]
        self._wolfx_task = None
        self._fan_task = None
        self._alert_task = None
        self._task = None
        for task in tasks:
            if task.done():
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _ensure_ui(self):
        if self._ui is not None:
            return
        if not self._cfg.status_ui_enabled:
            return
        from .status_server import StatusServer

        server = StatusServer(self)
        try:
            await server.start()
        except Exception as exc:
            print(f"[DisasterWarning] web ui failed: {exc}")
            return
        self._ui = server

    async def _stop_ui(self):
        ui = self._ui
        self._ui = None
        if ui is None:
            return
        try:
            await ui.stop()
        except Exception:
            pass

    def _schedule_watch_restart(self) -> None:
        self._watch_apply_gen = int(getattr(self, "_watch_apply_gen", 0)) + 1
        gen = self._watch_apply_gen
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _apply():
            await asyncio.sleep(0.05)
            if gen != self._watch_apply_gen:
                return
            await self._restart_watch()

        loop.create_task(_apply())

    async def _restart_watch(self):
        await self._stop_watch_clients()
        await self._ensure_loop()

    async def _ensure_loop(self):
        if not self._cfg.enabled:
            print("[DisasterWarning] enabled=false, skip ws")
            return
        if self._cfg.wolfx_eew_enabled and (
            self._wolfx_task is None or self._wolfx_task.done()
        ):
            self._wolfx_task = asyncio.create_task(self._wolfx_loop())
        if self._cfg.fanstudio_api_key and (
            self._fan_task is None or self._fan_task.done()
        ):
            self._fan_task = asyncio.create_task(self._fan_loop())
        if self._cfg.alert_poll_enabled and (
            self._alert_task is None or self._alert_task.done()
        ):
            self._alert_task = asyncio.create_task(self._alert_loop())
        self._task = self._wolfx_task or self._fan_task or self._alert_task
        if self._wolfx_task is None and self._fan_task is None and self._alert_task is None:
            print("[DisasterWarning] no eew source enabled")

    async def _wolfx_loop(self):
        while True:
            try:
                if not self._cfg.enabled or not self._cfg.wolfx_eew_enabled:
                    return
                self._capture_context(self._last_ctx)
                client = self._make_wolfx_client(
                    ws_url=self._cfg.wolfx_ws_url,
                    ws_backup_url=self._cfg.wolfx_ws_backup_url,
                    on_message=self.handle_wolfx_message,
                    on_ready=self._on_source_ready,
                )
                self._wolfx_client = client
                await client.run_forever()
                if client.stopped():
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DisasterWarning] wolfx loop error: {exc!r}")
                self._state.bump("ws_reconnect")
                self._state.save()
                await asyncio.sleep(5)

    async def _fan_loop(self):
        while True:
            try:
                if not self._cfg.enabled or not self._cfg.fanstudio_api_key:
                    return
                self._capture_context(self._last_ctx)
                client = self._make_fan_client(
                    ws_url=self._cfg.ws_url,
                    ws_backup_url=self._cfg.ws_backup_url,
                    app_id=self._cfg.fanstudio_app_id,
                    api_key=self._cfg.fanstudio_api_key,
                    on_message=self.handle_fan_message,
                    on_auth_success=self._on_source_ready,
                )
                self._fan_client = client
                self._client = client
                await client.run_forever()
                if client.stopped():
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DisasterWarning] fan loop error: {exc!r}")
                self._state.bump("ws_reconnect")
                self._state.save()
                await asyncio.sleep(5)

    async def _alert_loop(self):
        primed = False
        while True:
            try:
                if not self._cfg.enabled or not self._cfg.alert_poll_enabled:
                    return
                self._capture_context(self._last_ctx)
                await self._poll_alerts(seed=not primed)
                primed = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[DisasterWarning] alert poll error: {exc!r}")
                self._state.bump("ws_reconnect")
                self._state.save()
            await asyncio.sleep(max(30, int(self._cfg.alert_poll_sec or 300)))

    async def _poll_alerts(self, *, seed: bool) -> int:
        alerts = await self._nmc_client.list_alerts(limit=20)
        pushed = 0
        for item in alerts:
            event = alert_to_event(item)
            if seed:
                self._state.mark_seen(event)
                continue
            text = await self._handle_alert_update(event)
            if text:
                pushed += 1
        if seed:
            self._state.save()
        return pushed

    async def _on_source_ready(self):
        self._state.arm_silence(self._cfg.silence_sec)
        print(
            f"[DisasterWarning] source ready silence={self._state.silence_remaining()}s"
        )

    async def _on_auth_success(self):
        await self._on_source_ready()

    def _is_preauth_partial(self, message: dict) -> bool:
        if str(message.get("type") or "").strip() != "initial_all":
            return False
        keys = [
            str(key)
            for key in message
            if str(key) not in {"type", "message", "md5", "ver", "id", "timestamp"}
        ]
        return bool(keys) and all(key in PREAUTH_ONLY_KEYS for key in keys)

    async def handle_fan_message(self, message: dict) -> Optional[str]:
        if not isinstance(message, dict):
            return None
        msg_type = str(message.get("type") or "").strip().lower()
        if msg_type in AUTH_TYPES:
            return None
        if msg_type == "heartbeat":
            return None
        if msg_type in SNAPSHOT_TYPES:
            if self._is_preauth_partial(message):
                return None
            for event in snapshot_cea_events(message):
                self._state.mark_seen(event)
            self._state.save()
            return None
        if msg_type != "update":
            return None
        payload = extract_cea_payload(message)
        if payload is None:
            return None
        event = parse_cea_event(payload, source_id="cea")
        if event is None:
            return None
        return await self._handle_update(event)

    async def handle_wolfx_message(
        self, message: dict, *, snapshot: bool = False
    ) -> Optional[str]:
        if not isinstance(message, dict):
            return None
        event = parse_wolfx_eew(message)
        if event is None:
            return None
        if snapshot:
            self._state.mark_seen(event)
            self._state.save()
            return None
        return await self._handle_update(event)

    async def _handle_update(self, event: DisasterEvent) -> Optional[str]:
        if not self._cfg.enabled:
            self._state.mark_seen(event)
            return None
        if self._state.in_silence():
            self._state.mark_seen(event)
            self._state.bump("drop_silence")
            self._state.save()
            return None
        if self._state.should_skip_duplicate(event):
            self._state.bump("drop_dedup")
            self._state.save()
            return None
        ok, reason, extra = should_push_eew(
            event,
            self._cfg.regions,
            min_magnitude=self._cfg.min_magnitude,
            major_override_mag=self._cfg.major_override_mag,
        )
        if not ok:
            self._state.mark_seen(event)
            self._state.bump("drop_watch")
            self._state.save()
            print(f"[DisasterWarning] drop reason={reason} id={event.event_id}")
            return None
        text = format_text(event, extra)
        sent = await self._try_send(text)
        if sent:
            self._state.mark_seen(event)
            self._state.bump("pushed")
            self._state.save()
            print(
                f"[DisasterWarning] send_text ok reason={reason} id={event.event_id}"
            )
            return text
        self._pending.append((event, extra))
        print("[DisasterWarning] gateway_unavailable, queued")
        return None

    async def _handle_alert_update(self, event: DisasterEvent) -> Optional[str]:
        if not self._cfg.enabled:
            self._state.mark_seen(event)
            return None
        if self._state.in_silence():
            self._state.mark_seen(event)
            self._state.bump("drop_silence")
            self._state.save()
            return None
        if self._state.should_skip_duplicate(event):
            self._state.bump("drop_dedup")
            self._state.save()
            return None
        ok, reason, extra = should_push_alert(event, self._cfg.regions)
        if not ok:
            self._state.mark_seen(event)
            self._state.bump("drop_watch")
            self._state.save()
            print(f"[DisasterWarning] alert drop reason={reason} id={event.event_id}")
            return None
        text = format_text(event, extra)
        sent = await self._try_send(text)
        if sent:
            self._state.mark_seen(event)
            self._state.bump("pushed")
            self._state.save()
            print(
                f"[DisasterWarning] alert send ok reason={reason} id={event.event_id}"
            )
            return text
        return None

    async def _try_send(self, text: str) -> bool:
        if not self._cfg.enabled:
            return False
        self._capture_context(self._last_ctx)
        gateway = self._gateway
        try:
            await send_alert(gateway, self._cfg.push_sessions(), text)
            return True
        except Exception as exc:
            print(f"[DisasterWarning] send_text fail: {exc}")
            self._state.bump("push_fail")
            self._state.save()
            return False

    async def _flush_pending(self) -> None:
        if not self._pending:
            return
        queued = list(self._pending)
        self._pending.clear()
        for event, extra in queued:
            await self._handle_update(event)

    def get_tool_schema(self):
        return {
            "name": "disaster_warning",
            "description": "查询最近地震或当前台风/官方气象预警。不是日常天气预报。城市未知时用关注地区，不要编造震级。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["eq_list", "typhoon", "weather_alert", "status"],
                    },
                    "place": {
                        "type": "string",
                        "description": "地名，如上海、吉林；可空，空则用 watch_regions",
                    },
                },
                "required": ["action"],
            },
        }

    def format_invocation(self, arguments):
        args = dict(arguments or {})
        action = str(args.get("action") or "eq_list").strip() or "eq_list"
        place = str(args.get("place") or "").strip()
        if place:
            return f"{action} place={place}"
        return action

    def collect_invocation_args(
        self,
        user_text: str,
        capability_id: str = "",
        initial_args: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        args = dict(initial_args or {})
        raw = str(user_text or "").strip()
        if not str(args.get("action") or "").strip():
            if str(capability_id or "").endswith("eq_list") or looks_like_disaster_query(raw):
                args["action"] = infer_action(raw)
            elif raw:
                parsed_action, _ = parse_invocation(raw)
                if parsed_action:
                    args["action"] = parsed_action
            else:
                args["action"] = "eq_list"
        if not str(args.get("place") or "").strip():
            place = extract_place(raw, self._cfg.regions)
            if place:
                args["place"] = place
        return args

    def get_capabilities(self):
        from services.capability_manager import ToolCapability, ToolCapabilityMatch

        def match_command(text: str, ctx: dict):
            del ctx
            if self._command_alias(str(text or "")):
                return ToolCapabilityMatch(
                    capability_id="disaster.command",
                    plugin="disaster_warning",
                    score=0.95,
                    raw_text=str(text or ""),
                    reason="slash_command",
                    trigger_mode="command_only",
                )
            return None

        def match_eq_list(text: str, ctx: dict):
            del ctx
            raw = str(text or "").strip()
            if self._command_alias(raw):
                return None
            if not looks_like_disaster_query(raw):
                return None
            action = infer_action(raw)
            place = extract_place(raw, self._cfg.regions)
            args = {"action": action}
            if place:
                args["place"] = place
            return ToolCapabilityMatch(
                capability_id="disaster.eq_list",
                plugin="disaster_warning",
                score=0.9,
                args=args,
                raw_text=raw,
                reason="disaster_query",
                trigger_mode="natural",
            )

        return [
            ToolCapability(
                id="disaster.command",
                plugin="disaster_warning",
                trigger_mode="command_only",
                match=match_command,
                description="灾害预警状态与关注地区",
                examples=["/灾害预警 状态"],
            ),
            ToolCapability(
                id="disaster.eq_list",
                plugin="disaster_warning",
                trigger_mode="natural",
                match=match_eq_list,
                description="查询最近地震、在编台风或官方气象预警。不是日常天气预报。",
                examples=["最近上海有地震吗", "现在有没有台风", "上海有暴雨预警吗"],
            ),
        ]

    def _command_alias(self, text: str) -> Optional[str]:
        raw = str(text or "").strip()
        for alias in COMMAND_ALIASES:
            if raw == alias or raw.startswith(alias + " "):
                return alias
        return None

    def should_handle_direct(self, user_text: str, context: dict, matched_alias: str) -> bool:
        del context, matched_alias
        return self._command_alias(user_text) is not None

    def _is_qq(self, ctx: dict) -> bool:
        source = str((ctx or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq"}

    def _session_id(self, ctx: dict) -> str:
        meta = (ctx or {}).get("channel_meta") or {}
        return str(meta.get("session_id") or "").strip()

    def _is_owner(self, ctx: dict) -> bool:
        meta = (ctx or {}).get("channel_meta") or {}
        if bool(meta.get("is_owner")):
            return True
        session = self._session_id(ctx)
        owner = self._cfg.owner_session
        return bool(owner) and session == owner

    def _allow_mutate(self, ctx: dict) -> bool:
        if not self._is_qq(ctx):
            return True
        return self._is_owner(ctx)

    async def run(self, args, ctx):
        self._capture_context(ctx if isinstance(ctx, dict) else {})
        await self._ensure_loop()
        await self._flush_pending()
        text = str(args or "").strip()
        alias = self._command_alias(text)
        rest = text[len(alias) :].strip() if alias else text
        if rest in {"", "状态"} and alias:
            return self._status_text()
        if rest == "关注" or rest.startswith("关注 列表"):
            return self._list_regions()
        if rest.startswith("关注 添加"):
            if not self._allow_mutate(ctx if isinstance(ctx, dict) else {}):
                return "只有主人可以修改关注地区。"
            name = rest[len("关注 添加") :].strip()
            return self._add_region(name)
        action, place = parse_invocation(rest)
        if not action and looks_like_disaster_query(rest):
            action, place = infer_action(rest), extract_place(rest, self._cfg.regions)
        if action:
            return await self._run_query(action, place)
        if not alias:
            return "用法：/灾害预警 状态  |  /灾害预警 最近地震  |  /灾害预警 台风  |  /灾害预警 关注 添加 上海"
        return "用法：/灾害预警 状态  |  /灾害预警 最近地震  |  /灾害预警 台风  |  /灾害预警 关注 添加 上海"

    async def _run_query(self, action: str, place: str) -> str:
        kind = str(action or "").strip() or "eq_list"
        if kind == "status":
            return self._status_text()
        if kind == "typhoon":
            try:
                storms = await self._nmc_client.list_typhoons()
            except Exception as exc:
                print(f"[DisasterWarning] typhoon list fail: {exc!r}")
                return "查不到台风（数据源暂时不可用）。"
            text = format_typhoons(storms)
            try:
                alerts = await self._nmc_client.list_alerts(limit=20)
            except Exception:
                return text
            related = [item for item in alerts if item.kind == "typhoon"]
            if place:
                related = filter_alerts(related, place)
            if related:
                text = text + "\n" + format_alerts(related, place=place)
            return text
        if kind == "weather_alert":
            needles = self._query_needles(place)
            try:
                alerts = await self._nmc_client.list_alerts(limit=20)
            except Exception as exc:
                print(f"[DisasterWarning] alarm list fail: {exc!r}")
                return "查不到气象预警（数据源暂时不可用）。"
            weather_items = [item for item in alerts if item.kind != "typhoon"]
            if needles:
                matched = []
                seen = set()
                for needle in needles:
                    for item in filter_alerts(weather_items, needle):
                        key = (item.url, item.title)
                        if key in seen:
                            continue
                        seen.add(key)
                        matched.append(item)
                label = str(place or "").strip() or "、".join(
                    region.label for region in self._cfg.regions if region.label
                )
                return format_alerts(matched[:10], place=label)
            return format_alerts(weather_items)
        if kind != "eq_list":
            return "用法：eq_list place=上海"
        needles = self._query_needles(place)
        try:
            items = await self._eq_client.list_events(limit=5)
        except Exception as exc:
            print(f"[DisasterWarning] eqlist fail: {exc!r}")
            return "查不到最近地震（数据源暂时不可用）。"
        if needles:
            matched = []
            seen = set()
            for needle in needles:
                for item in filter_eqlist(items, needle):
                    key = (item.time, item.location, item.magnitude)
                    if key in seen:
                        continue
                    seen.add(key)
                    matched.append(item)
            label = str(place or "").strip() or "、".join(
                region.label for region in self._cfg.regions if region.label
            )
            return format_eqlist(matched[:10], place=label)
        return format_eqlist(items)

    def _query_needles(self, place: str) -> list:
        needles = [str(place or "").strip()]
        if needles[0]:
            return needles
        out = []
        for region in self._cfg.regions:
            if region.label:
                out.append(region.label)
            out.extend(region.keywords)
        return [item for item in out if str(item).strip()]

    def _status_text(self) -> str:
        cfg = self._cfg
        if cfg.wolfx_eew_enabled:
            wolfx_line = (
                f"Wolfx：{'running' if self._wolfx_running() else 'idle'}"
                "（默认秒级，无 Key；民间源，不能替代官方 App）"
            )
        else:
            wolfx_line = "Wolfx：已关闭"
        if not cfg.fanstudio_api_key:
            fan_line = f"Fan：未配置（可选备源） {FAN_APPLY_URL}"
        else:
            fan_line = (
                f"Fan：已配置 / {'running' if self._fan_running() else 'idle'}"
            )
        ws = "running" if self._ws_running() else "idle"
        regions = "、".join(region.label for region in cfg.regions) or "（空，不推真实预警）"
        recent = self._state.recent_seen(3)
        recent_text = "；".join(recent) if recent else "无"
        return "\n".join(
            [
                "灾害预警状态",
                f"后台：{'开' if cfg.enabled else '关'} / WS {ws}",
                wolfx_line,
                fan_line,
                f"静默剩余：{self._state.silence_remaining()} 秒",
                f"关注：{regions}",
                f"主人会话：{cfg.owner_session or '未填'}",
                f"群：{'、'.join(cfg.group_sessions) or '无'}",
                f"计数：pushed={self._state.stats.get('pushed', 0)} "
                f"silence={self._state.stats.get('drop_silence', 0)} "
                f"watch={self._state.stats.get('drop_watch', 0)} "
                f"dedup={self._state.stats.get('drop_dedup', 0)}",
                f"最近：{recent_text}",
                f"本机页面：http://127.0.0.1:{cfg.status_ui_port}/"
                if cfg.status_ui_enabled
                else "本机页面：已关闭",
            ]
        )

    def _list_regions(self) -> str:
        if not self._cfg.regions:
            return "还没有关注地区。设置 watch_regions_json，或使用 /灾害预警 关注 添加 上海"
        lines = ["关注地区："]
        for region in self._cfg.regions:
            keys = "、".join(region.keywords) if region.keywords else "（无关键词）"
            geo = ""
            if region.lat is not None and region.lng is not None:
                geo = f" @ {region.lat},{region.lng} ≤{region.radius_km:g}km"
            lines.append(f"- {region.label} [{keys}]{geo}")
        return "\n".join(lines)

    def _add_region(self, name: str) -> str:
        label = str(name or "").strip()
        if not label:
            return "请指定地区名，例如 /灾害预警 关注 添加 上海"
        for region in self._cfg.regions:
            if region.label == label or label in region.keywords:
                return f"已关注 {label}"
        added = WatchRegion(id=label, label=label, keywords=[label])
        regions = list(self._cfg.regions) + [added]
        if not self._write_watch_regions(regions):
            return "保存关注地区失败。"
        self.reload_config()
        return f"已添加关注：{label}"

    def replace_regions(self, regions) -> bool:
        ok = self._write_watch_regions(regions)
        if ok:
            self.reload_config()
        return ok

    def patch_settings(self, patch: Dict[str, Any]) -> bool:
        allowed = {
            "enabled",
            "owner_session",
            "group_sessions",
            "min_magnitude",
            "major_override_mag",
            "silence_sec",
            "wolfx_eew_enabled",
            "alert_poll_enabled",
            "alert_poll_sec",
        }
        incoming = dict(patch or {})
        new_key = incoming.pop("fanstudio_api_key", None)
        clear_key = bool(incoming.pop("clear_api_key", False))
        if new_key is not None and str(new_key).strip():
            if not self._store_api_key(str(new_key).strip()):
                return False
        elif clear_key:
            if not self._store_api_key(""):
                return False
        clean = {key: value for key, value in incoming.items() if key in allowed}
        if not clean and new_key is None and not clear_key:
            return True
        if not clean:
            self.reload_config()
            return True
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        if not isinstance(raw, dict):
            return False
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            raw["settings"] = settings
        for key, value in clean.items():
            if key == "group_sessions":
                from .settings import parse_session_list

                value = ",".join(parse_session_list(value, default_kind="group"))
            current = settings.get(key)
            if isinstance(current, dict):
                updated = dict(current)
                updated["default"] = value
                settings[key] = updated
            else:
                settings[key] = value
            injected = getattr(self, "settings", None)
            if isinstance(injected, dict):
                cur = injected.get(key)
                if isinstance(cur, dict):
                    patched = dict(cur)
                    patched["default"] = value
                    injected[key] = patched
                else:
                    injected[key] = value
        try:
            self._config_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=4) + "\n",
                encoding="utf-8",
            )
        except Exception:
            return False
        self.reload_config()
        return True

    def _store_api_key(self, key: str) -> bool:
        trigger = str(getattr(self, "plugin_trigger", "") or "disaster_warning")
        try:
            from modules.plugin_secret_store import PluginSecretStore

            PluginSecretStore().set_secret(trigger, "fanstudio_api_key", key)
        except Exception as exc:
            print(f"[DisasterWarning] save api key failed: {exc}")
            return False
        injected = getattr(self, "settings", None)
        if isinstance(injected, dict):
            current = injected.get("fanstudio_api_key")
            if isinstance(current, dict):
                patched = dict(current)
                patched["default"] = key
                injected["fanstudio_api_key"] = patched
            else:
                injected["fanstudio_api_key"] = key
        return True

    def _write_watch_regions(self, regions) -> bool:
        payload = regions_to_payload(list(regions))
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        if not isinstance(raw, dict):
            return False
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            raw["settings"] = settings
        current = settings.get("watch_regions_json")
        blob = json.dumps(payload, ensure_ascii=False)
        if isinstance(current, dict):
            updated = dict(current)
            updated["default"] = blob
            settings["watch_regions_json"] = updated
        else:
            settings["watch_regions_json"] = {
                "type": "text",
                "label": "关注地区 JSON",
                "description": "watch_regions 数组。空数组=不推真实事件。",
                "default": blob,
            }
        try:
            self._config_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=4) + "\n",
                encoding="utf-8",
            )
        except Exception:
            return False
        injected = getattr(self, "settings", None)
        if isinstance(injected, dict):
            current_injected = injected.get("watch_regions_json")
            if isinstance(current_injected, dict):
                patched = dict(current_injected)
                patched["default"] = blob
                injected["watch_regions_json"] = patched
            else:
                injected["watch_regions_json"] = blob
        return True
