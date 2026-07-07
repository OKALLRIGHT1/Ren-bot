from __future__ import annotations

from typing import Any, Callable


class PluginToolProvider:
    def __init__(self, plugin_manager: Any):
        self.plugin_manager = plugin_manager

    def list_tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for trigger, plugin in getattr(self.plugin_manager, "plugins", {}).items():
            rows.append(
                {
                    "trigger": str(trigger),
                    "source": "plugin",
                    "name": str(getattr(plugin, "name", trigger) or trigger),
                    "type": str(getattr(plugin, "type", "react") or "react"),
                    "description": str(getattr(plugin, "description", "") or ""),
                    "aliases": list(getattr(plugin, "aliases", []) or []),
                    "examples": list(getattr(plugin, "tool_examples", []) or []),
                }
            )
        return rows


class McpToolProvider:
    def __init__(self, bridge_getter: Callable[[], Any]):
        self.bridge_getter = bridge_getter

    def list_tools(self) -> list[dict[str, Any]]:
        bridge = self.bridge_getter()
        if bridge is None:
            return []
        rows: list[dict[str, Any]] = []
        for spec in bridge.list_tools():
            rows.append(
                {
                    "trigger": str(getattr(spec, "name", "") or ""),
                    "source": "mcp",
                    "name": str(getattr(spec, "name", "") or ""),
                    "type": "mcp",
                    "provider": str(getattr(spec, "provider", "") or ""),
                    "description": str(getattr(spec, "description", "") or ""),
                    "input_schema": getattr(spec, "input_schema", {}) or {},
                    "aliases": [],
                    "examples": [],
                }
            )
        return [row for row in rows if row["trigger"]]
