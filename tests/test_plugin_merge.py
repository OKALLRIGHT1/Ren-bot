from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.plugin_manager import PluginManager
from services.chat_support.tool_schema import build_openai_tools, schema_for_tool


LIBRARY_TRIGGERS = {
    "database",
    "qq提醒",
    "task",
    "timer",
    "info_gateway",
    "app_control",
    "qq帮助",
    "agent_runtime",
    "delegate_status",
    "llm_monitor",
    "qq截图",
    "打开",
    "monitor",
}

COMPOSITE_TRIGGERS = {
    "life_hub",
    "magic_daily",
    "owner_console",
    "vision",
    "desktop_hub",
}


def _qq(*, owner=False, message_type="private"):
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "is_owner": owner,
            "message_type": message_type,
        },
    }


def test_library_plugins_are_not_registered():
    manager = PluginManager(plugin_dir="plugins")
    manager.load_plugins()

    for trigger in LIBRARY_TRIGGERS:
        assert trigger not in manager.plugins, trigger
    for trigger in COMPOSITE_TRIGGERS:
        assert trigger in manager.plugins, trigger


def test_extra_llm_commands_map_to_composites():
    manager = PluginManager(plugin_dir="plugins")
    manager.load_plugins()

    assert manager.llm_command_map["timer"] == "life_hub"
    assert manager.llm_command_map["task_manager"] == "life_hub"
    assert manager.llm_command_map["life_manager"] == "life_hub"
    assert manager.llm_command_map["info_gateway"] == "magic_daily"
    assert manager.llm_command_map["app_control"] == "owner_console"
    assert manager.llm_command_map["open_app"] == "desktop_hub"
    assert manager.llm_command_map["qq_screenshot"] == "vision"
    assert manager.react_map["timer"] is manager.plugins["life_hub"]
    assert manager.react_map["info_gateway"] is manager.plugins["magic_daily"]
    assert "/提醒" in manager.direct_map
    assert manager.direct_map["/提醒"] is manager.plugins["life_hub"]
    assert "/重启" in manager.direct_map
    assert manager.direct_map["/重启"] is manager.plugins["owner_console"]
    assert "截图发我" in manager.direct_map


def test_merged_tool_schemas_keep_old_function_names():
    manager = PluginManager(plugin_dir="plugins")
    manager.load_plugins()

    tools = build_openai_tools(
        manager,
        ["timer", "task_manager", "info_gateway", "check", "life_manager"],
        allowed_types={"react", "direct", "observe"},
        include_tool_search=False,
    )
    names = {item["function"]["name"] for item in tools}
    assert "timer" in names
    assert "task_manager" in names
    assert "info_gateway" in names
    assert "check" in names
    assert "life_manager" in names

    daily = manager.plugins["magic_daily"]
    schema = schema_for_tool(daily, "info_gateway")
    assert schema is not None
    assert schema["name"] == "info_gateway"


@pytest.mark.asyncio
async def test_owner_console_restart_stays_owner_only():
    from plugins.owner_console.plugin import Plugin

    plugin = Plugin()
    plugin.name = "主人控制台"
    plugin.access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": True,
        "allow_group_without_at": True,
    }
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"owner_console": plugin}
    manager.direct_map = {
        "/重启": plugin,
        "/远程重启": plugin,
        "/restart": plugin,
        "/帮助": plugin,
    }
    plugin.llm_commands = ["app_control", "qq_help"]

    handled, result = await manager.execute_direct_commands(
        "帮我重启一下", {"source": "text_input"}
    )
    assert handled is False

    handled, result = await manager.execute_direct_commands("/重启", _qq(owner=False))
    assert handled is True
    assert "不允许" in str(result)

    handled, result = await manager.execute_direct_commands("/重启", _qq(owner=True))
    assert handled is True
    assert result["__type__"] == "app_restart"


@pytest.mark.asyncio
async def test_daily_plugin_dispatches_weather_tool():
    from plugins.Isuzu_news.plugin import Plugin

    class Service:
        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                data={"city": params.get("city")},
                summary=f"{params.get('city')}天气：多云",
            )

    plugin = Plugin()
    plugin._weather._service_getter = lambda _ctx: Service()
    result = await plugin.run(
        "weather_now city=上海",
        {"invoked_tool": "info_gateway", "source": "text_input"},
    )
    assert "上海天气：多云" in result


@pytest.mark.asyncio
async def test_life_hub_dispatches_timer_schema():
    from plugins.life_hub.plugin import Plugin

    plugin = Plugin()
    sent = []

    async def fake_send(text):
        sent.append(text)

    result = await plugin.run(
        "25",
        {"invoked_tool": "timer", "send_bubble": fake_send},
    )
    assert "25" in result
    await plugin.stop()


def test_library_flag_in_source_configs():
    for folder in (
        "life_manager",
        "qq_reminder",
        "task_manager",
        "timer",
        "info_gateway",
        "app_control",
        "qq_help",
        "agent_runtime",
        "delegate_status",
        "llm_monitor",
        "qq_screenshot",
        "open_app",
        "system_monitor",
    ):
        config = json.loads(
            (Path("plugins") / folder / "config.json").read_text(encoding="utf-8-sig")
        )
        assert config.get("library") is True, folder
