import importlib.util
import json
import sys
from pathlib import Path

import pytest


PLUGIN_DIR = Path("plugins/agent_runtime")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_agent_runtime_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


class Runtime:
    def __init__(self):
        mail_plugin = type(
            "MailPlugin",
            (),
            {
                "settings": {"cli_path": {"default": "agently-cli"}},
                "access_control": {
                    "allow_remote_qq": True,
                    "allow_qq_owner": True,
                    "allow_qq_others": False,
                },
            },
        )()
        mcp_plugin = type(
            "McpPlugin",
            (),
            {
                "list_server_status": lambda self: [
                    {"name": "demo", "connected": True, "tool_count": 1, "error": ""}
                ]
            },
        )()
        self.plugin_manager = type(
            "PluginManager",
            (),
            {
                "plugins": {"agently_mail": mail_plugin, "mcp_tools": mcp_plugin},
                "load_errors": [{"plugin": "broken", "error": "boom"}],
            },
        )()

    def list_tools(self):
        return [
            {
                "trigger": "agently_mail",
                "source": "plugin",
                "name": "Agent Mail",
                "type": "direct",
                "description": "邮件工具",
                "examples": ["最近邮件"],
            },
            {
                "trigger": "mcp.demo.read",
                "source": "mcp",
                "name": "mcp.demo.read",
                "type": "mcp",
                "provider": "demo",
                "description": "读取 demo",
                "examples": [],
            },
        ]


class ChatService:
    agent_runtime = Runtime()
    app_root = "/mock/workspace/live2d-llm"


def runtime_ctx(**extra):
    host = ChatService()
    ctx = {
        "agent_runtime": host.agent_runtime,
        "app_root": host.app_root,
        "cwd": host.app_root,
    }
    ctx.update(extra)
    return ctx


def test_agent_runtime_config_allows_qq_owner_direct_tool():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8-sig"))

    assert config["trigger"] == "agent_runtime"
    assert config["type"] == "direct"
    assert config["access_control"]["allow_local"] is True
    assert config["access_control"]["allow_remote_qq"] is True
    assert config["access_control"]["allow_qq_owner"] is True
    assert config["access_control"]["allow_qq_others"] is False


def test_agent_runtime_plugin_contract():
    Plugin = load_plugin_class()
    plugin = Plugin()

    assert plugin.type == "direct"
    assert callable(plugin.should_handle_direct)
    assert callable(plugin.run)


@pytest.mark.asyncio
async def test_agent_runtime_direct_requires_command_prefix_through_manager():
    from modules.plugin_manager import PluginManager

    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
        "allow_group_without_at": False,
    }

    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"agent_runtime": plugin}
    manager.direct_map = {"agent 自检": plugin}

    handled, result = await manager.execute_direct_commands(
        "agent 自检", runtime_ctx(source="text_input")
    )
    assert handled is False
    assert result is None

    handled, result = await manager.execute_direct_commands(
        "/agent 自检", runtime_ctx(source="text_input")
    )
    assert handled is True
    assert "Agent 自检" in result


@pytest.mark.asyncio
async def test_agent_runtime_health_check_formats_runtime_status():
    Plugin = load_plugin_class()
    plugin = Plugin()

    result = await plugin.run("agent 自检", runtime_ctx())

    assert "Agent 自检" in result
    assert "工具数量: 2" in result
    assert "插件:" in result
    assert "Agent Mail:" in result
    assert "MCP: demo 已连接" in result


@pytest.mark.asyncio
async def test_agent_runtime_tools_lists_catalog():
    Plugin = load_plugin_class()
    plugin = Plugin()

    result = await plugin.run("agent 工具列表", runtime_ctx())

    assert "Agent 工具目录" in result
    assert "agently_mail" in result
    assert "mcp.demo.read" in result


@pytest.mark.asyncio
async def test_agent_runtime_reports_code_location():
    Plugin = load_plugin_class()
    plugin = Plugin()

    result = await plugin.run(
        "agent 位置",
        {
            "app_root": "/mock/workspace/live2d-llm",
            "cwd": "/mock/workspace/live2d-llm",
            "code_path": "/mock/workspace/other-project",
        },
    )

    assert "主程序代码目录: /mock/workspace/live2d-llm" in result
    assert "当前工作目录: /mock/workspace/live2d-llm" in result
    assert "指定目录: /mock/workspace/other-project" in result


@pytest.mark.asyncio
async def test_agent_runtime_install_plan_does_not_execute():
    Plugin = load_plugin_class()
    plugin = Plugin()

    result = await plugin.run("agent 安装计划 weather", runtime_ctx())

    assert "安装计划" in result
    assert "weather" in result
    assert "不会自动执行" in result
