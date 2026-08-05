import pytest

from modules.plugin_manager import PluginManager


class _GatedPlugin:
    name = "Gated"
    plugin_trigger = "open_app"
    llm_command = "open_app"
    type = "direct"
    timeout_sec = 1
    access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
    }

    def __init__(self):
        self.calls = 0

    def resolve_gated_action(self, args, ctx=None):
        return "system.spawn_process_trusted"

    async def run(self, args, ctx):
        self.calls += 1
        return "spawned"


class _HighPlugin:
    name = "High"
    plugin_trigger = "code"
    llm_command = "execute_code"
    type = "react"
    timeout_sec = 1
    gated_action = "system.exec_code"
    access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
    }
    calls = 0

    async def run(self, args, ctx):
        self.calls += 1
        return "executed"


def _manager(plugin):
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {plugin.plugin_trigger: plugin}
    manager.direct_map = {"open": plugin} if plugin.plugin_trigger == "open_app" else {}
    manager.react_map = {plugin.plugin_trigger: plugin}
    manager.llm_command_map = {plugin.llm_command: plugin.plugin_trigger}
    return manager


def _qq(*, owner=False, message_type="private"):
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "is_owner": owner,
            "message_type": message_type,
            "mentioned": True,
        },
    }


@pytest.mark.asyncio
async def test_owner_private_trusted_runs():
    plugin = _GatedPlugin()
    manager = _manager(plugin)
    handled, result = await manager.execute_direct_commands("/open calc", _qq(owner=True))
    assert handled is True
    assert result == "spawned"
    assert plugin.calls == 1


@pytest.mark.asyncio
async def test_owner_group_blocked():
    plugin = _GatedPlugin()
    manager = _manager(plugin)
    handled, result = await manager.execute_direct_commands(
        "/open calc", _qq(owner=True, message_type="group")
    )
    assert handled is True
    assert plugin.calls == 0
    assert "群聊" in str(result) or "无法执行" in str(result) or "不允许" in str(result)


@pytest.mark.asyncio
async def test_other_qq_blocked_by_access_or_gate():
    plugin = _GatedPlugin()
    manager = _manager(plugin)
    handled, result = await manager.execute_direct_commands("/open calc", _qq(owner=False))
    assert plugin.calls == 0
    assert handled is True
    assert result is not None


@pytest.mark.asyncio
async def test_high_without_confirm_does_not_run():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    # use execute path via _run_with_timeout
    out = await manager._run_with_timeout(plugin, "print(1)", _qq(owner=True))
    assert plugin.calls == 0
    if isinstance(out, dict):
        assert out.get("__agent_result__") == "confirmation_required"
        assert "确认" in str(out.get("summary") or "")
    else:
        assert "确认" in str(out) or "无法执行" in str(out)


@pytest.mark.asyncio
async def test_high_with_confirm_runs():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    ctx = _qq(owner=True)
    ctx["action_confirmed"] = True
    out = await manager._run_with_timeout(plugin, "print(1)", ctx)
    assert plugin.calls == 1
    assert out == "executed"


@pytest.mark.asyncio
async def test_local_popup_confirm_runs_without_chat_confirm():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    manager.local_confirm_handler = lambda title, summary: True
    out = await manager._run_with_timeout(plugin, "print(1)", {"source": "text_input"})
    assert plugin.calls == 1
    assert out == "executed"


@pytest.mark.asyncio
async def test_local_popup_cancel_does_not_run():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    manager.local_confirm_handler = lambda title, summary: False
    out = await manager._run_with_timeout(plugin, "print(1)", {"source": "text_input"})
    assert plugin.calls == 0
    assert "取消" in str(out)


@pytest.mark.asyncio
async def test_remote_does_not_use_local_popup():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    manager.local_confirm_handler = lambda title, summary: True
    out = await manager._run_with_timeout(plugin, "print(1)", _qq(owner=True))
    assert plugin.calls == 0
    assert isinstance(out, dict)
    assert out.get("__agent_result__") == "confirmation_required"
