from __future__ import annotations

from typing import Any


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
