from services.agent_tool_providers import McpToolProvider, PluginToolProvider


class Spec:
    def __init__(self, name, provider="local", description="", input_schema=None):
        self.name = name
        self.provider = provider
        self.description = description
        self.input_schema = input_schema or {}


class Bridge:
    def list_tools(self):
        return [
            Spec("plugin.list", provider="local", description="List plugins"),
            Spec("mcp.demo.read", provider="demo", description="Read demo"),
        ]


class Plugin:
    name = "Agent Mail"
    type = "direct"
    description = "邮件工具"
    aliases = ["邮件"]
    tool_examples = ["最近邮件"]


def test_mcp_provider_lists_bridge_tools():
    provider = McpToolProvider(lambda: Bridge())

    rows = provider.list_tools()

    assert rows[0]["trigger"] == "plugin.list"
    assert rows[0]["source"] == "mcp"
    assert rows[1]["trigger"] == "mcp.demo.read"
    assert rows[1]["provider"] == "demo"


def test_plugin_provider_lists_plugins():
    manager = type("Manager", (), {"plugins": {"agently_mail": Plugin()}})()
    provider = PluginToolProvider(manager)

    rows = provider.list_tools()

    assert rows == [
        {
            "trigger": "agently_mail",
            "source": "plugin",
            "name": "Agent Mail",
            "type": "direct",
            "description": "邮件工具",
            "aliases": ["邮件"],
            "examples": ["最近邮件"],
        }
    ]
