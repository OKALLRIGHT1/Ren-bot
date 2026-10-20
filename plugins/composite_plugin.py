"""Shared helpers for merged plugins that wrap several inner Plugin classes."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


PLUGINS_ROOT = Path(__file__).resolve().parent


def setting_value(settings: Any, key: str, default: Any = None) -> Any:
    if not isinstance(settings, dict):
        return default
    raw = settings.get(key, default)
    if isinstance(raw, dict):
        if "value" in raw and raw.get("value") not in (None, ""):
            return raw.get("value")
        return raw.get("default", default)
    return default if raw is None else raw


def load_plugin_config(folder_name: str) -> dict:
    path = PLUGINS_ROOT / folder_name / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def attach_inner_config(inner: Any, folder_name: str) -> dict:
    """Copy config.json metadata onto an inner Plugin that PluginManager never loads."""
    config = load_plugin_config(folder_name)
    access = config.get("access_control") if isinstance(config.get("access_control"), dict) else {}
    inner.access_control = dict(access)
    inner.settings = (
        config.get("settings") if isinstance(config.get("settings"), dict) else {}
    )
    inner.aliases = list(config.get("aliases") or getattr(inner, "aliases", []) or [])
    inner.direct_command_aliases = list(
        config.get("direct_command_aliases")
        or getattr(inner, "direct_command_aliases", [])
        or []
    )
    inner.llm_command = str(config.get("llm_command") or getattr(inner, "llm_command", "") or "")
    inner.plugin_trigger = str(config.get("trigger") or folder_name)
    inner.name = str(config.get("name") or getattr(inner, "name", folder_name))
    inner.type = str(config.get("type") or getattr(inner, "type", "react") or "react")
    inner.timeout_sec = config.get("timeout_sec") or getattr(inner, "timeout_sec", None)
    inner.allow_natural_language_direct = bool(
        getattr(inner, "allow_natural_language_direct", False)
    )
    return config


def collect_tool_schemas(inners: Iterable[Any]) -> List[dict]:
    schemas: List[dict] = []
    for inner in inners:
        getter = getattr(inner, "get_tool_schema", None)
        if not callable(getter):
            continue
        raw = getter()
        if raw is None:
            continue
        if isinstance(raw, list):
            schemas.extend(item for item in raw if isinstance(item, dict))
        elif isinstance(raw, dict):
            schemas.append(raw)
    return schemas


def collect_capabilities(inners: Iterable[Any]) -> list:
    caps = []
    for inner in inners:
        getter = getattr(inner, "get_capabilities", None)
        if not callable(getter):
            continue
        caps.extend(list(getter() or []))
    return caps


async def start_inners(inners: Sequence[Any], context: Optional[dict] = None) -> None:
    seen = set()
    for inner in inners:
        inner_id = id(inner)
        if inner_id in seen:
            continue
        seen.add(inner_id)
        start = getattr(inner, "start", None)
        if not callable(start):
            continue
        if asyncio.iscoroutinefunction(start):
            try:
                params = inspect.signature(start).parameters
            except Exception:
                params = {}
            if context is not None and len(params) >= 1:
                await start(context)
            else:
                await start()
        else:
            start()


async def stop_inners(inners: Sequence[Any]) -> None:
    seen = set()
    for inner in inners:
        inner_id = id(inner)
        if inner_id in seen:
            continue
        seen.add(inner_id)
        stop = getattr(inner, "stop", None)
        if not callable(stop):
            continue
        if asyncio.iscoroutinefunction(stop):
            await stop()
        else:
            stop()


def format_inner_invocation(inner: Any, arguments: Optional[Dict[str, Any]], tool_name: str = "") -> Optional[str]:
    formatter = getattr(inner, "format_invocation", None)
    if not callable(formatter):
        return None
    payload = dict(arguments or {})
    try:
        formatted = formatter(payload, tool_name=tool_name)
    except TypeError:
        formatted = formatter(payload)
    if formatted is None:
        return None
    return str(formatted).strip()


class CompositeMixin:
    """Dispatch helpers shared by merged plugins."""

    _parts: Dict[str, Any]
    _alias_to_part: Dict[str, str]
    _tool_to_part: Dict[str, str]
    _default_part: str = ""

    def _all_parts(self) -> List[Any]:
        seen = []
        seen_ids = set()
        for inner in (self._parts or {}).values():
            inner_id = id(inner)
            if inner_id in seen_ids:
                continue
            seen_ids.add(inner_id)
            seen.append(inner)
        return seen

    def get_tool_schema(self):
        schemas = collect_tool_schemas(self._all_parts())
        return schemas or None

    def get_capabilities(self):
        return collect_capabilities(self._all_parts())

    async def start(self, ctx: Optional[dict] = None):
        await start_inners(self._all_parts(), ctx)

    async def stop(self):
        await stop_inners(self._all_parts())

    def format_invocation(self, arguments, tool_name=None):
        part = self._part_for_tool(str(tool_name or ""))
        inner = self._parts.get(part) if part else None
        if inner is None and len(self._all_parts()) == 1:
            inner = self._all_parts()[0]
        formatted = format_inner_invocation(inner, arguments, str(tool_name or ""))
        if formatted is not None:
            return formatted
        return None

    def collect_invocation_args(
        self,
        user_text: str,
        capability_id: str = "",
        initial_args: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cap = str(capability_id or "")
        inner = None
        for part_name, candidate in (self._parts or {}).items():
            if cap.startswith(part_name) or cap.startswith(str(getattr(candidate, "llm_command", ""))):
                inner = candidate
                break
            getter = getattr(candidate, "get_capabilities", None)
            if not callable(getter):
                continue
            for item in getter() or []:
                if str(getattr(item, "id", "") or "") == cap:
                    inner = candidate
                    break
            if inner is not None:
                break
        if inner is None:
            inner = self._parts.get(self._default_part)
        collector = getattr(inner, "collect_invocation_args", None)
        if callable(collector):
            collected = collector(
                user_text,
                capability_id=capability_id,
                initial_args=dict(initial_args or {}),
            )
            if isinstance(collected, dict):
                return dict(collected)
        args = dict(initial_args or {})
        if user_text and not args:
            args["query"] = str(user_text)
        return args

    def resolve_access_control(
        self,
        context: Optional[dict] = None,
        *,
        invoked_tool: str = "",
        invoked_alias: str = "",
        args: str = "",
    ) -> Optional[dict]:
        part = self._route_part(invoked_tool, invoked_alias, args, context or {})
        inner = self._parts.get(part) if part else None
        if inner is None:
            return getattr(self, "access_control", None)
        return getattr(inner, "access_control", None) or getattr(self, "access_control", None)

    def should_handle_direct(self, text: str, context: dict, key: str) -> bool:
        part = self._part_for_alias(key) or self._route_part("", key, text, context)
        inner = self._parts.get(part) if part else None
        handler = getattr(inner, "should_handle_direct", None) if inner is not None else None
        if callable(handler):
            return bool(handler(text, context, key))
        return True

    def resolve_gated_action(self, args: str, ctx: Optional[dict] = None) -> str:
        part = self._route_part(
            str((ctx or {}).get("invoked_tool") or ""),
            str((ctx or {}).get("invoked_alias") or ""),
            args,
            ctx or {},
        )
        inner = self._parts.get(part) if part else None
        resolver = getattr(inner, "resolve_gated_action", None) if inner is not None else None
        if callable(resolver):
            return str(resolver(args, ctx) or "")
        return str(getattr(inner, "gated_action", None) or getattr(self, "gated_action", "") or "")

    def _part_for_tool(self, tool_name: str) -> str:
        name = str(tool_name or "").strip()
        if not name:
            return ""
        mapped = (self._tool_to_part or {}).get(name)
        if mapped:
            return mapped
        if name in (self._parts or {}):
            return name
        return ""

    def _part_for_alias(self, alias: str) -> str:
        key = str(alias or "").strip()
        if not key:
            return ""
        mapped = (self._alias_to_part or {}).get(key)
        if mapped:
            return mapped
        lowered = key.lower()
        for alias_key, part in (self._alias_to_part or {}).items():
            if str(alias_key).lower() == lowered:
                return part
        return ""

    def _route_part(
        self,
        invoked_tool: str,
        invoked_alias: str,
        args: str,
        context: dict,
    ) -> str:
        part = self._part_for_tool(invoked_tool)
        if part:
            return part
        part = self._part_for_alias(invoked_alias)
        if part:
            return part
        return self._default_part

    async def _run_part(self, part: str, args: str, ctx: dict):
        inner = self._parts.get(part) or self._parts.get(self._default_part)
        if inner is None:
            return f"未找到合并插件子能力：{part or self._default_part}"
        return await inner.run(args, ctx)
