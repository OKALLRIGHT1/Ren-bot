import modules.plugin_manager as plugin_manager_module
import plugins.plugin_utils as plugin_utils_module
from modules.plugin_manager import PluginManager


class ToolPlugin:
    name = "Mail Tool"
    plugin_trigger = "agently_mail"
    llm_command = "agently_mail"
    type = "direct"
    description = "查询和发送邮件"
    aliases = ["邮件", "邮箱"]
    tool_examples = ["最近邮件", "读邮件 msg_xxx"]


def test_search_tools_includes_direct_tool_examples():
    manager = PluginManager(plugin_dir="plugins")
    plugin = ToolPlugin()
    manager.plugins = {"agently_mail": plugin}
    manager.direct_map = {"邮件": plugin, "邮箱": plugin}
    manager.react_map = {}
    manager.delegate_map = {}

    rows = manager.search_tools("邮件", limit=5)

    assert rows[0]["trigger"] == "agently_mail"
    assert rows[0]["type"] == "direct"
    assert "最近邮件" in rows[0]["examples"]


def test_save_plugin_config_updates_tool_examples_in_memory(tmp_path):
    plugin_dir = tmp_path / "plugins"
    tool_dir = plugin_dir / "tool"
    tool_dir.mkdir(parents=True)
    config_path = tool_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    manager = PluginManager(plugin_dir=str(plugin_dir))
    plugin = ToolPlugin()
    manager.plugins = {"agently_mail": plugin}
    manager.plugin_dirs = {"agently_mail": "tool"}

    saved = manager.save_plugin_config(
        "agently_mail",
        {
            "name": "Agent Mail",
            "type": "direct",
            "description": "查询邮件",
            "aliases": ["邮件"],
            "tool_examples": ["搜索邮件 账单"],
            "access_control": {"allow_local": True},
        },
    )

    assert saved is True
    assert plugin.tool_examples == ["搜索邮件 账单"]


class GbkStream:
    encoding = "gbk"

    def __init__(self):
        self.writes = []

    def write(self, text):
        text.encode(self.encoding)
        self.writes.append(text)
        return len(text)

    def flush(self):
        pass


def test_plugin_manager_safe_print_handles_emoji_on_gbk_stdout(monkeypatch):
    stream = GbkStream()
    monkeypatch.setattr(plugin_manager_module.sys, "stdout", stream)

    plugin_manager_module._safe_print("🔌 [系统] 正在扫描插件目录")

    assert "".join(stream.writes) == "? [系统] 正在扫描插件目录\n"


def test_plugin_manager_debug_print_uses_safe_output(monkeypatch):
    stream = GbkStream()
    monkeypatch.setattr(plugin_manager_module.sys, "stdout", stream)
    manager = PluginManager(plugin_dir="plugins")
    manager.debug_enabled = True

    manager._dbg("🔌 debug")

    assert "".join(stream.writes) == "? debug\n"


def test_plugin_utils_safe_print_handles_emoji_on_gbk_stdout(monkeypatch):
    stream = GbkStream()
    monkeypatch.setattr(plugin_utils_module.sys, "stdout", stream)

    plugin_utils_module._safe_print("🔧 [插件] 开始执行")

    assert "".join(stream.writes) == "? [插件] 开始执行\n"
