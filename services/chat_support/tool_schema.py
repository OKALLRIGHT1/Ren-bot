from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set


TOOL_SEARCH_SCHEMA = {
    "name": "tool_search",
    "description": "当候选工具不够时，按关键词检索可用工具。不要在闲聊时调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要查找的能力或关键词",
            }
        },
        "required": ["query"],
    },
}


def _as_function_schema(raw: Any, fallback_name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    function = raw.get("function") if isinstance(raw.get("function"), dict) else raw
    name = str(function.get("name") or fallback_name or "").strip()
    if not name:
        return None
    description = str(function.get("description") or "").strip()
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


def iter_plugin_tool_schemas(plugin: Any, fallback_name: str = "") -> List[Dict[str, Any]]:
    getter = getattr(plugin, "get_tool_schema", None)
    if not callable(getter):
        return []
    raw = getter()
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    schemas: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in items:
        schema = _as_function_schema(item, fallback_name)
        if schema is None or schema["name"] in seen:
            continue
        seen.add(schema["name"])
        schemas.append(schema)
    return schemas


def schema_for_tool(plugin: Any, tool_name: str) -> Optional[Dict[str, Any]]:
    name = str(tool_name or "").strip()
    fallback = str(
        getattr(plugin, "llm_command", "")
        or getattr(plugin, "plugin_trigger", "")
        or name
        or ""
    ).strip()
    schemas = iter_plugin_tool_schemas(plugin, fallback)
    if not schemas:
        return None
    if name:
        for schema in schemas:
            if schema["name"] == name:
                return schema
    if len(schemas) == 1:
        return schemas[0]
    return None


def _plugin_type(plugin: Any) -> str:
    return str(getattr(plugin, "type", "react") or "react")


def plugin_executable_in_pass(
    plugin: Any,
    *,
    allowed_types: Optional[Set[str]] = None,
    invoked_name: str = "",
) -> bool:
    """Schema exposure and execution share this allowlist.

    A pass may run plugins of the allowed types. Extra command names on a
    plugin (for example weather on the daily card) stay callable. A delegate
    plugin is not offered or run just because a router mentioned its trigger.
    """
    allowed = {str(item).strip() for item in (allowed_types or set()) if str(item).strip()}
    if not allowed:
        return True
    if _plugin_type(plugin) in allowed:
        return True
    extras = {
        str(item).strip()
        for item in (getattr(plugin, "llm_commands", None) or [])
        if str(item).strip()
    }
    name = str(invoked_name or "").strip()
    return bool(name) and name in extras


def resolve_plugin(plugin_manager: Any, trigger: str) -> Any:
    key = str(trigger or "").strip()
    if not key or plugin_manager is None:
        return None
    llm_map = getattr(plugin_manager, "llm_command_map", None) or {}
    mapped = str(llm_map.get(key) or key)
    for mapping_name in ("react_map", "delegate_map", "direct_map", "plugins"):
        mapping = getattr(plugin_manager, mapping_name, None) or {}
        plugin = mapping.get(mapped) or mapping.get(key)
        if plugin is not None:
            return plugin
    return None


def build_openai_tools(
    plugin_manager: Any,
    triggers: Optional[Sequence[str]] = None,
    *,
    allowed_types: Optional[Set[str]] = None,
    include_tool_search: bool = True,
) -> List[Dict[str, Any]]:
    allowed = set(allowed_types or set())
    tools: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    for trigger in list(triggers or []):
        plugin = resolve_plugin(plugin_manager, trigger)
        if plugin is None:
            continue
        llm_cmd = str(
            getattr(plugin, "llm_command", "")
            or getattr(plugin, "plugin_trigger", "")
            or trigger
            or ""
        ).strip()
        schemas = iter_plugin_tool_schemas(plugin, llm_cmd)
        if not schemas:
            continue
        if allowed and not plugin_executable_in_pass(
            plugin,
            allowed_types=allowed,
            invoked_name=llm_cmd,
        ):
            schemas = [
                schema
                for schema in schemas
                if plugin_executable_in_pass(
                    plugin,
                    allowed_types=allowed,
                    invoked_name=str(schema.get("name") or ""),
                )
            ]
            if not schemas:
                continue
        for schema in schemas:
            if schema["name"] in seen_names:
                continue
            seen_names.add(schema["name"])
            tools.append({"type": "function", "function": schema})

    if include_tool_search and "tool_search" not in seen_names:
        tools.append({"type": "function", "function": dict(TOOL_SEARCH_SCHEMA)})
    return tools
