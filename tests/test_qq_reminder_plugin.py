import pytest

from plugins.qq_reminder.plugin import Plugin


def _qq_ctx(user_id="1132824061"):
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "user_id": user_id,
            "session_id": f"private:{user_id}",
            "is_owner": True,
        },
    }


def test_qq_reminder_handles_natural_disable_and_list():
    plugin = Plugin()
    ctx = _qq_ctx()
    assert plugin.should_handle_direct("关闭打卡提醒", ctx, "提醒")
    assert plugin.should_handle_direct("把打卡提醒关了", ctx, "打卡提醒")
    assert plugin.should_handle_direct("提醒列表", ctx, "提醒")
    assert plugin.should_handle_direct("/提醒删除 1", ctx, "/提醒删除")
    assert not plugin.should_handle_direct("这个提醒很烦", ctx, "提醒")
    assert not plugin.should_handle_direct("邮件这个词只是举例", ctx, "提醒")


@pytest.mark.asyncio
async def test_qq_reminder_disables_matching_clock_in(tmp_path, monkeypatch):
    plugin = Plugin()
    plugin._settings["storage_path"] = str(tmp_path / "qq_reminders.json")
    plugin._reminders = [
        {
            "id": "a1",
            "owner_user_id": "1132824061",
            "target_session": "private:1132824061",
            "weekdays": [0, 1, 2, 3, 4],
            "hour": 17,
            "minute": 20,
            "content": "提醒我打卡",
            "enabled": True,
            "last_sent_date": "",
        }
    ]
    plugin._save_reminders()

    async def no_scheduler():
        return None

    monkeypatch.setattr(plugin, "_ensure_scheduler", no_scheduler)
    result = await plugin.run("关闭打卡提醒", _qq_ctx())
    assert "已关闭" in result
    assert "打卡" in result
    assert plugin._reminders == []


@pytest.mark.asyncio
async def test_execute_direct_handles_disable_clock_in_without_slash():
    from modules.plugin_manager import PluginManager

    plugin = Plugin()
    plugin.access_control = {
        "allow_local": False,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
        "allow_group_without_at": False,
    }

    async def fake_run(args, ctx):
        del ctx
        return f"handled:{args}"

    plugin.run = fake_run
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"qq提醒": plugin}
    manager.direct_map = {alias: plugin for alias in plugin.aliases}

    handled, result = await manager.execute_direct_commands(
        "关闭打卡提醒", _qq_ctx()
    )
    assert handled is True
    assert result == "handled:关闭打卡提醒"

    skipped, empty = await manager.execute_direct_commands(
        "这个提醒很烦", _qq_ctx()
    )
    assert skipped is False
    assert empty is None
