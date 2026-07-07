import pytest

from modules.plugin_manager import PluginManager


class _Plugin:
    plugin_trigger = "mcp_tools"
    type = "react"
    timeout_sec = 1

    def __init__(self):
        self.calls = []

    async def run(self, args, ctx):
        self.calls.append(args)
        return "ok"


def test_extract_commands_preserves_json_arrays():
    manager = PluginManager(plugin_dir="plugins")
    text = '[CMD: mcp_tools | call_tool ||| tool_name ||| {"list":[1,2,3]}]'

    assert manager.extract_commands(text) == [
        ("mcp_tools", 'call_tool ||| tool_name ||| {"list":[1,2,3]}')
    ]


@pytest.mark.asyncio
async def test_execute_commands_preserves_json_arrays():
    plugin = _Plugin()
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"mcp_tools": plugin}
    manager.react_map = {"mcp_tools": plugin}
    manager.llm_command_map = {"mcp_tools": "mcp_tools"}

    triggered, clean, outputs, used = await manager.execute_commands(
        '[CMD: mcp_tools | call_tool ||| tool_name ||| {"list":[1,2,3]}]',
        {"source": "text_input"},
    )

    assert triggered is True
    assert clean == ""
    assert outputs == ["ok"]
    assert used == ["mcp_tools"]
    assert plugin.calls == ['call_tool ||| tool_name ||| {"list":[1,2,3]}']
