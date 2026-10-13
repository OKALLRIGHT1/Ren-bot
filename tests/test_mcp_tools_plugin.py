from plugins.mcp_tools.plugin import Plugin


def test_schema_hidden_without_servers():
    plugin = Plugin()

    assert plugin.get_tool_schema() is None


def test_schema_and_invocation_when_servers_configured():
    plugin = Plugin()
    plugin.settings = {"servers": [{"name": "demo", "enabled": False, "command": "python"}]}

    schema = plugin.get_tool_schema()

    assert schema["name"] == "mcp_tools"
    assert schema["parameters"]["properties"]["action"]["enum"] == [
        "list_tools",
        "server_status",
        "call_tool",
    ]
    assert "mcp.demo" not in schema["description"]
    assert plugin.format_invocation({"action": "list_tools"}) == "list_tools"
    assert (
        plugin.format_invocation(
            {"action": "call_tool", "tool_name": "mcp.demo.read", "arguments": {"q": 1}}
        )
        == 'call_tool ||| mcp.demo.read ||| {"q": 1}'
    )


def test_schema_lists_only_allowed_remote_tools():
    plugin = Plugin()
    plugin.settings = {
        "servers": [{"name": "demo", "enabled": False}],
        "allowed_server_names": ["demo"],
    }
    plugin._ensure_bridge()

    class Spec:
        def __init__(self, name, description):
            self.name = name
            self.description = description

    plugin._bridge.list_tools = lambda provider=None: [
        Spec("mcp.demo.read", "读取"),
        Spec("mcp.other.write", "写入"),
    ]

    schema = plugin.get_tool_schema()

    assert "mcp.demo.read" in schema["description"]
    assert "mcp.other.write" not in schema["description"]
