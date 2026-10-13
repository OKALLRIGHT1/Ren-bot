from services.agent_tool_providers import PluginToolProvider


class Plugin:
    name = "Agent Mail"
    type = "direct"
    description = "邮件工具"
    aliases = ["邮件"]
    tool_examples = ["最近邮件"]


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
