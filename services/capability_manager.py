from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional


NON_FORCE_EXECUTABLE_CAPABILITIES = {"capability:mcp_tools.domain_call"}


def is_force_executable_capability(route_reason: str) -> bool:
    reason = str(route_reason or "").strip()
    return reason.startswith("capability:") and reason not in NON_FORCE_EXECUTABLE_CAPABILITIES


# ToolCapabilityManager is the lightweight routing capability layer used by
# ToolRouter. The older CapabilityManager below is the runtime health/install
# inspector kept for compatibility with the agent_runtime plugin.
@dataclass(frozen=True)
class ToolCapabilityMatch:
    capability_id: str
    plugin: str
    score: float
    args: Optional[Dict[str, Any]] = None
    raw_text: str = ""
    reason: str = ""
    available: bool = True
    unavailable_reason: str = ""
    trigger_mode: str = ""


@dataclass(frozen=True)
class ToolCapability:
    id: str
    plugin: str
    trigger_mode: str
    match: Callable[[str, Dict[str, Any]], Optional[Any]]
    check_available: Optional[Callable[[Dict[str, Any]], Any]] = None
    description: str = ""
    examples: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCapabilityRouteResult:
    selected: Optional[ToolCapabilityMatch]
    candidates: List[ToolCapabilityMatch] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = "no_match"


class ToolCapabilityManager:
    def __init__(
        self,
        capabilities: Optional[Iterable[ToolCapability]] = None,
        *,
        confident_score: float = 0.7,
    ):
        self.capabilities = list(capabilities or [])
        self.confident_score = float(confident_score)
        self.last_result: Optional[ToolCapabilityRouteResult] = None

    @classmethod
    def from_plugin_maps(
        cls,
        *,
        react_map: Optional[Dict[str, object]] = None,
        direct_map: Optional[Dict[str, object]] = None,
        delegate_map: Optional[Dict[str, object]] = None,
        confident_score: float = 0.7,
    ) -> "ToolCapabilityManager":
        capabilities: List[ToolCapability] = []
        seen_plugin_ids: set[int] = set()

        direct_keys_by_plugin: Dict[int, List[str]] = {}
        for trigger, plugin in (direct_map or {}).items():
            direct_keys_by_plugin.setdefault(id(plugin), []).append(str(trigger or ""))

        for map_kind, mapping in (
            ("react", react_map or {}),
            ("delegate", delegate_map or {}),
            ("direct", direct_map or {}),
        ):
            for trigger, plugin in mapping.items():
                plugin_id = id(plugin)
                if plugin_id in seen_plugin_ids:
                    continue
                seen_plugin_ids.add(plugin_id)
                getter = getattr(plugin, "get_capabilities", None)
                if callable(getter):
                    for capability in getter() or []:
                        normalized = cls._normalize_capability(capability)
                        if normalized is not None:
                            capabilities.append(normalized)
                    continue
                slash_command = cls._slash_alias_command_capability(
                    str(trigger or ""), plugin
                )
                if slash_command is not None:
                    capabilities.append(slash_command)
                if map_kind == "direct":
                    legacy = cls._legacy_direct_capability(
                        str(trigger or ""),
                        plugin,
                        direct_keys_by_plugin.get(plugin_id) or [str(trigger or "")],
                    )
                    if legacy is not None:
                        capabilities.append(legacy)
        return cls(capabilities, confident_score=confident_score)

    @staticmethod
    def _slash_alias_command_capability(
        trigger: str, plugin: object
    ) -> Optional[ToolCapability]:
        primary_trigger = str(trigger or "").strip()
        if not primary_trigger:
            return None
        aliases = []
        for source in (
            getattr(plugin, "direct_command_aliases", []),
            getattr(plugin, "aliases", []),
            [primary_trigger],
        ):
            if isinstance(source, str):
                values = [source]
            else:
                values = list(source or [])
            aliases.extend(str(value or "").strip() for value in values)
        slash_aliases = list(
            dict.fromkeys(alias for alias in aliases if alias.startswith("/"))
        )
        if not slash_aliases:
            return None

        def match(text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
            raw = str(text or "").strip()
            for alias in slash_aliases:
                if raw == alias or raw.startswith(alias + " "):
                    return ToolCapabilityMatch(
                        capability_id=f"{primary_trigger}.command",
                        plugin=primary_trigger,
                        score=1.0,
                        raw_text=raw,
                        reason="slash_alias_command",
                    )
            return None

        return ToolCapability(
            id=f"{primary_trigger}.command",
            plugin=primary_trigger,
            trigger_mode="command_only",
            match=match,
            description=f"Slash command aliases for {primary_trigger}.",
        )

    @staticmethod
    def _legacy_direct_capability(
        trigger: str,
        plugin: object,
        keys: Iterable[str],
    ) -> Optional[ToolCapability]:
        handler = getattr(plugin, "should_handle_direct", None)
        if not callable(handler):
            return None
        primary_trigger = str(trigger or "").strip()
        if not primary_trigger:
            return None
        route_keys = [str(key or "").strip() for key in keys if str(key or "").strip()]

        def match(text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
            for key in route_keys or [primary_trigger]:
                if handler(text, ctx, key):
                    return ToolCapabilityMatch(
                        capability_id=f"{primary_trigger}.direct",
                        plugin=primary_trigger,
                        score=0.85,
                        raw_text=str(text or ""),
                        reason="legacy_direct_handler",
                    )
            return None

        return ToolCapability(
            id=f"{primary_trigger}.direct",
            plugin=primary_trigger,
            trigger_mode="natural",
            match=match,
            description=f"Legacy direct handler for {primary_trigger}.",
        )

    @staticmethod
    def _normalize_capability(value: Any) -> Optional[ToolCapability]:
        if isinstance(value, ToolCapability):
            return value
        if not isinstance(value, dict):
            return None
        matcher = value.get("match")
        if not callable(matcher):
            return None
        return ToolCapability(
            id=str(value.get("id") or "").strip(),
            plugin=str(value.get("plugin") or "").strip(),
            trigger_mode=str(value.get("trigger_mode") or "natural").strip(),
            match=matcher,
            check_available=value.get("check_available"),
            description=str(value.get("description") or ""),
            examples=list(value.get("examples") or []),
        )

    def match(self, text: str, ctx: Optional[Dict[str, Any]] = None) -> ToolCapabilityRouteResult:
        context = dict(ctx or {})
        raw_text = str(text or "")
        candidates: List[ToolCapabilityMatch] = []
        for capability in self.capabilities:
            match = self._call_matcher(capability, raw_text, context)
            if match is None:
                continue
            match = self._apply_availability(capability, match, context)
            candidates.append(match)
            if match.available and match.score >= self.confident_score:
                result = ToolCapabilityRouteResult(
                    selected=match,
                    candidates=list(candidates),
                    ambiguous=False,
                    reason="confident_match",
                )
                self.last_result = result
                return result

        has_available_candidate = any(candidate.available for candidate in candidates)
        reason = "no_match"
        if candidates and not has_available_candidate:
            reason = "unavailable"
        elif candidates:
            reason = "ambiguous"
        result = ToolCapabilityRouteResult(
            selected=None,
            candidates=candidates,
            ambiguous=bool(candidates and has_available_candidate),
            reason=reason,
        )
        self.last_result = result
        return result

    def _call_matcher(
        self,
        capability: ToolCapability,
        text: str,
        ctx: Dict[str, Any],
    ) -> Optional[ToolCapabilityMatch]:
        try:
            raw = capability.match(text, ctx)
        except Exception:
            return None
        if raw is None:
            return None
        if isinstance(raw, ToolCapabilityMatch):
            match = raw
        elif isinstance(raw, dict):
            match = ToolCapabilityMatch(
                capability_id=str(raw.get("capability_id") or capability.id),
                plugin=str(raw.get("plugin") or capability.plugin),
                score=float(raw.get("score") or 0.0),
                args=raw.get("args"),
                raw_text=str(raw.get("raw_text") or text),
                reason=str(raw.get("reason") or ""),
                available=bool(raw.get("available", True)),
                unavailable_reason=str(raw.get("unavailable_reason") or ""),
                trigger_mode=str(raw.get("trigger_mode") or capability.trigger_mode),
            )
        else:
            return None
        if match.raw_text and match.trigger_mode:
            return match
        return ToolCapabilityMatch(
            capability_id=match.capability_id or capability.id,
            plugin=match.plugin or capability.plugin,
            score=match.score,
            args=match.args,
            raw_text=match.raw_text or text,
            reason=match.reason,
            available=match.available,
            unavailable_reason=match.unavailable_reason,
            trigger_mode=match.trigger_mode or capability.trigger_mode,
        )

    def _apply_availability(
        self,
        capability: ToolCapability,
        match: ToolCapabilityMatch,
        ctx: Dict[str, Any],
    ) -> ToolCapabilityMatch:
        checker = capability.check_available
        if not callable(checker):
            return match
        try:
            result = checker(ctx)
        except Exception as exc:
            return replace(match, available=False, unavailable_reason=str(exc))
        available = True
        reason = ""
        if isinstance(result, dict):
            available = bool(result.get("available", True))
            reason = str(result.get("reason") or result.get("unavailable_reason") or "")
        elif isinstance(result, tuple):
            available = bool(result[0]) if result else True
            reason = str(result[1]) if len(result) > 1 else ""
        elif result is not None:
            available = bool(result)
        if available:
            return match
        return replace(match, available=False, unavailable_reason=reason)


class CapabilityManager:
    def __init__(
        self,
        runtime_getter: Callable[[], Any],
        mcp_bridge_getter: Optional[Callable[[], Any]] = None,
    ):
        self.runtime_getter = runtime_getter
        self.mcp_bridge_getter = mcp_bridge_getter or (lambda: None)

    def health_check(self) -> Dict[str, Any]:
        runtime = self.runtime_getter()
        tools = runtime.list_tools() if runtime is not None else []
        plugin_manager = getattr(runtime, "plugin_manager", None)
        bridge = self.mcp_bridge_getter()
        mcp_servers = []
        if bridge is not None:
            try:
                mcp_servers = bridge.list_server_status()
            except Exception as exc:
                mcp_servers = [{"name": "mcp", "connected": False, "error": str(exc)}]
        plugins = self._plugin_status(plugin_manager)
        agent_mail = self._agent_mail_status(plugin_manager)
        runtime_status = self._runtime_status(runtime)
        return {
            "ok": True,
            "tool_count": len(tools),
            "tools": tools,
            "mcp_servers": mcp_servers,
            "plugins": plugins,
            "agent_mail": agent_mail,
            "runtime": runtime_status,
        }

    def _plugin_status(self, plugin_manager: Any) -> Dict[str, Any]:
        if plugin_manager is None:
            return {"loaded": 0, "failed": 0, "errors": []}
        errors = list(getattr(plugin_manager, "load_errors", []) or [])
        return {
            "loaded": len(getattr(plugin_manager, "plugins", {}) or {}),
            "failed": len(errors),
            "errors": errors[:8],
        }

    def _agent_mail_status(self, plugin_manager: Any) -> Dict[str, Any]:
        if plugin_manager is None:
            return {"configured": False, "reason": "plugin_manager_unavailable"}
        plugin = (getattr(plugin_manager, "plugins", {}) or {}).get("agently_mail")
        if plugin is None:
            return {"configured": False, "reason": "plugin_not_loaded"}
        settings = getattr(plugin, "settings", {}) or {}
        cli_path = self._setting(settings, "cli_path", "agently-cli")
        cli_exists = bool(cli_path and (cli_path == "agently-cli" or os.path.exists(str(cli_path))))
        access = getattr(plugin, "access_control", {}) or {}
        return {
            "configured": True,
            "cli_path": str(cli_path or ""),
            "cli_exists": cli_exists,
            "qq_owner_allowed": bool(
                access.get("allow_remote_qq") and access.get("allow_qq_owner")
            ),
            "qq_others_allowed": bool(
                access.get("allow_remote_qq") and access.get("allow_qq_others")
            ),
        }

    def _runtime_status(self, runtime: Any) -> Dict[str, Any]:
        chat_service = getattr(runtime, "chat_service", None)
        if chat_service is None:
            return {}
        result: Dict[str, Any] = {}
        result["qq_gateway"] = bool(getattr(chat_service, "chat_gateway", None))
        result["live2d"] = bool(getattr(chat_service, "live2d", None))
        sensor = getattr(chat_service, "screen_sensor_ref", None)
        if sensor is not None:
            result["screen_sensor"] = True
            try:
                events = sensor._recent_rust_events(limit=1)
                newest = events[0] if events else {}
                newest_ts = sensor._parse_rust_event_ts(newest) if newest else 0.0
                result["rust_activity_fresh"] = bool(
                    newest_ts and time.time() - newest_ts <= 120
                )
            except Exception as exc:
                result["rust_activity_error"] = str(exc)
        else:
            result["screen_sensor"] = False
        return result

    def _setting(self, settings: Dict[str, Any], key: str, default: Any) -> Any:
        raw = settings.get(key, default) if isinstance(settings, dict) else default
        if isinstance(raw, dict):
            return raw.get("value", raw.get("default", default))
        return raw

    def propose_install(self, capability_name: str) -> Dict[str, Any]:
        name = str(capability_name or "").strip()
        return {
            "action": "system.install_dependency",
            "capability": name,
            "requires_confirmation": True,
            "executed": False,
            "commands": [],
            "notes": [
                "这里只生成安装计划，不直接执行。",
                "真正安装必须由 ActionGate 确认后走单独执行任务。",
            ],
        }
