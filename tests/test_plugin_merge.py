from __future__ import annotations

from tests.helpers.optional_plugins import require_optional_plugins

require_optional_plugins('Isuzu_news', 'desktop_hub', 'life_hub', 'owner_console', 'task_manager')

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
async def test_owner_console_developer_parts_follow_developer_mode(monkeypatch):
    from plugins.owner_console import plugin as owner_plugin
    from plugins.owner_console.plugin import Plugin

    monkeypatch.setattr(owner_plugin, "developer_parts_enabled", lambda: False)
    plugin = Plugin()
    denied = await plugin.run("agent 自检", {"invoked_alias": "agent 自检"})
    assert "显示开发者选项" in denied
    help_result = await plugin.run("/帮助", {"invoked_alias": "/帮助"})
    assert "开发者功能" not in str(help_result)
    restart = await plugin.run("/重启", {"invoked_alias": "/重启"})
    assert restart["__type__"] == "app_restart"
    names = {item.get("name") for item in (plugin.get_tool_schema() or [])}
    assert "llm_monitor" not in names
    assert plugin.public_description() == owner_plugin.USER_DESCRIPTION
    assert plugin.should_handle_direct("今天模型状态怎么样", {}, "模型状态") is False
    assert plugin.should_handle_direct("模型监控", {}, "模型监控") is True

    monkeypatch.setattr(owner_plugin, "developer_parts_enabled", lambda: True)
    names = {item.get("name") for item in (plugin.get_tool_schema() or [])}
    assert "llm_monitor" in names
    assert "Agent自检" in plugin.public_description()


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


def test_life_hub_sends_remind_me_to_the_alarm():
    from plugins.life_hub.plugin import Plugin

    plugin = Plugin()
    assert plugin._route_part("", "", "提醒我 10分钟后 关火", {"source": "text_input"}) == "life"
    assert plugin._to_alarm_args("提醒我 10分钟后 关火") == "add_reminder 10分钟后 关火"
    assert plugin._to_alarm_args("10 分钟后提醒我关火") == "add_reminder 10分钟后 关火"
    assert (
        plugin._route_part("task_manager", "", "提醒 10分钟后 关火", {})
        == "life"
    )
    assert plugin._route_part("", "", "/提醒 每周1到周5 17:20 打卡", {}) == "reminder"
    assert (
        plugin._route_part("", "", "提醒我 每天 08:00 起床", {"source": "qq_gateway"})
        == "reminder"
    )
    assert plugin.should_handle_direct("你刚提醒我啥", {}, "提醒") is False
    assert plugin.should_handle_direct("提醒我 10分钟后 关火", {}, "提醒") is True


@pytest.mark.asyncio
async def test_task_manager_reminder_does_not_list_todos():
    from plugins.task_manager.plugin import Plugin

    plugin = Plugin()
    result = await plugin.run("提醒 买牛奶", {})
    assert "到点提醒" in result
    assert "买牛奶" not in result


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


def test_desktop_hub_ignores_casual_open_mentions():
    from plugins.desktop_hub.plugin import Plugin

    plugin = Plugin()
    ctx = {"source": "text_input"}
    assert plugin.should_handle_direct("我没有打开maa啊", ctx, "打开") is False
    assert plugin.should_handle_direct("电脑启动好慢", ctx, "启动") is False
    assert plugin.should_handle_direct("打开了吗", ctx, "打开") is False
    assert plugin.should_handle_direct("不要打开计算器", ctx, "打开") is False
    assert plugin.should_handle_direct("打开计算器", ctx, "打开") is True
    assert plugin.should_handle_direct("帮我打开计算器", ctx, "打开") is True
    assert plugin.should_handle_direct("/打开 记事本", ctx, "/打开") is True
    assert plugin.should_handle_direct("启动计算器", ctx, "启动") is True


@pytest.mark.asyncio
async def test_desktop_hub_direct_skips_negated_open():
    manager = PluginManager(plugin_dir="plugins")
    manager.load_plugins()
    handled, _result = await manager.execute_direct_commands(
        "我没有打开maa啊",
        {"source": "text_input"},
    )
    assert handled is False


def test_same_store_cards_point_at_the_settings_page():
    expected = {
        "memory_tools": "记忆",
        "diary_export": "日记",
        "local_knowledge": "知识库",
        "meme_pack": "表情包",
    }
    for folder, noun in expected.items():
        config = json.loads(
            (Path("plugins") / folder / "config.json").read_text(encoding="utf-8-sig")
        )
        summary = str(config.get("card_summary") or "")
        description = str(config.get("description") or "")
        assert noun in summary, folder
        assert "同一份" in summary, folder
        assert "让她查" in summary, folder
        assert description and summary not in description, folder
