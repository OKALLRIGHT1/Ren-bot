"""A pass may only be offered the tools that pass will actually run."""

from __future__ import annotations

import pytest

from modules.plugin_manager import PluginManager
from services.chat_support.tool_schema import build_openai_tools, openai_tool_names


class _SchemaPlugin:
    def __init__(self, name: str, kind: str):
        self.name = name
        self.type = kind
        self.llm_command = name
        self.plugin_trigger = name

    def get_tool_schema(self):
        return {
            "name": self.name,
            "description": self.name,
            "parameters": {"type": "object", "properties": {}},
        }


def _manager():
    react = _SchemaPlugin("read_clock", "react")
    delegate = _SchemaPlugin("web_lookup", "delegate")
    manager = type(
        "Maps",
        (),
        {
            "llm_command_map": {"read_clock": "read_clock", "web_lookup": "web_lookup"},
            "react_map": {"read_clock": react},
            "delegate_map": {"web_lookup": delegate},
            "direct_map": {},
            "plugins": {},
        },
    )()
    return manager


def test_react_pass_hides_delegate_schema():
    tools = build_openai_tools(
        _manager(),
        ["read_clock", "web_lookup"],
        allowed_types={"react"},
        include_tool_search=False,
    )
    assert openai_tool_names(tools) == ["read_clock"]


def test_delegate_pass_hides_react_schema():
    tools = build_openai_tools(
        _manager(),
        ["read_clock", "web_lookup"],
        allowed_types={"delegate"},
        include_tool_search=False,
    )
    assert openai_tool_names(tools) == ["web_lookup"]


@pytest.mark.asyncio
async def test_react_pass_refuses_delegate_execution():
    manager = PluginManager.__new__(PluginManager)
    maps = _manager()
    manager.llm_command_map = maps.llm_command_map
    manager.react_map = maps.react_map
    manager.delegate_map = maps.delegate_map
    manager.plugins = maps.plugins
    outputs, used, found = await PluginManager._run_named_tool(
        manager,
        "web_lookup",
        "",
        {},
        {"react"},
    )
    assert found is False
    assert used == []
    assert outputs
    assert "当前轮次不允许这个工具" in outputs[0]
