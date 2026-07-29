import pytest

from modules.plugin_manager import PluginManager


class _Plugin:
    name = "Access Tool"
    plugin_trigger = "access_tool"
    type = "react"
    timeout_sec = 1

    def __init__(self, access_control):
        self.access_control = access_control
        self.calls = []

    async def run(self, args, ctx):
        self.calls.append((args, ctx))
        return "tool-ok"


class _SlashCommandPlugin(_Plugin):
    def should_handle_direct(self, text, ctx, matched_alias):
        return str(text or "").strip().startswith(str(matched_alias or "").strip())


def _manager_with(plugin, *, direct_alias="access"):
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"access_tool": plugin}
    manager.react_map = {"access_tool": plugin}
    manager.direct_map = {direct_alias: plugin}
    manager.llm_command_map = {"access_tool": "access_tool"}
    return manager


def _local_ctx():
    return {"source": "text_input"}


def _qq_ctx(*, owner=False, message_type="private", mentioned=True):
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "is_owner": owner,
            "message_type": message_type,
            "mentioned": mentioned,
        },
    }


@pytest.mark.asyncio
async def test_local_user_can_run_local_only_plugin():
    plugin = _Plugin(
        {
            "allow_local": True,
            "allow_remote_qq": False,
            "allow_qq_owner": False,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands("/access now", _local_ctx())

    assert handled is True
    assert result == "tool-ok"
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_direct_keyword_without_prefix_does_not_trigger_by_default():
    plugin = _Plugin(
        {
            "allow_local": True,
            "allow_remote_qq": False,
            "allow_qq_owner": False,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands(
        "please access now", _local_ctx()
    )

    assert handled is False
    assert result is None
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_remote_qq_owner_can_run_owner_only_plugin():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin)

    triggered, clean, outputs, used = await manager.execute_commands(
        "[CMD: access_tool | now]",
        _qq_ctx(owner=True),
    )

    assert triggered is True
    assert clean == ""
    assert outputs == ["tool-ok"]
    assert used == ["access_tool"]
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_remote_qq_non_owner_is_denied_for_owner_only_plugin():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin)

    triggered, clean, outputs, used = await manager.execute_commands(
        "[CMD: access_tool | now]",
        _qq_ctx(owner=False),
    )

    assert triggered is True
    assert clean == ""
    assert used == []
    assert len(plugin.calls) == 0
    assert "不允许" in outputs[0]
    assert "其他 QQ 联系人" in outputs[0]


@pytest.mark.asyncio
async def test_remote_qq_direct_denied_plugin_is_silent_for_natural_text():
    plugin = _Plugin(
        {
            "allow_local": True,
            "allow_remote_qq": False,
            "allow_qq_owner": False,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands(
        "please access now", _qq_ctx(owner=True)
    )

    assert handled is False
    assert result is None
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_direct_command_prefix_can_run_allowed_plugin():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands(
        "/access now", _qq_ctx(owner=True)
    )

    assert handled is True
    assert result == "tool-ok"
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_direct_command_alias_with_prefix_can_run_allowed_plugin():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin, direct_alias="/日报")

    handled, result = await manager.execute_direct_commands(
        "/日报", _qq_ctx(owner=True)
    )

    assert handled is True
    assert result == "tool-ok"
    assert plugin.calls[0][0] == "/日报"
    assert plugin.calls[0][1]["source"] == "qq_gateway"
    assert plugin.calls[0][1]["channel_meta"] == _qq_ctx(owner=True)["channel_meta"]
    assert plugin.calls[0][1]["model_gateway"] is manager.model_gateway


@pytest.mark.asyncio
async def test_direct_command_should_handle_receives_raw_prefixed_text():
    plugin = _SlashCommandPlugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }
    )
    manager = _manager_with(plugin, direct_alias="/access")

    handled, result = await manager.execute_direct_commands(
        "/access now", _qq_ctx(owner=True)
    )

    assert handled is True
    assert result == "tool-ok"
    assert plugin.calls[0][0] == "/access now"
    assert plugin.calls[0][1]["source"] == "qq_gateway"
    assert plugin.calls[0][1]["channel_meta"] == _qq_ctx(owner=True)["channel_meta"]
    assert plugin.calls[0][1]["model_gateway"] is manager.model_gateway


@pytest.mark.asyncio
async def test_group_qq_plugin_requires_mention_by_default():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": True,
            "allow_group_without_at": False,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands(
        "access group command",
        _qq_ctx(owner=True, message_type="group", mentioned=False),
    )

    assert handled is False
    assert len(plugin.calls) == 0

    handled, result = await manager.execute_direct_commands(
        "/access group command",
        _qq_ctx(owner=True, message_type="group", mentioned=False),
    )

    assert handled is True
    assert "群聊" in result
    assert "@" in result


@pytest.mark.asyncio
async def test_group_qq_plugin_allows_mentioned_message():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": True,
            "allow_group_without_at": False,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands(
        "/access group command",
        _qq_ctx(owner=False, message_type="group", mentioned=True),
    )

    assert handled is True
    assert result == "tool-ok"
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_group_qq_plugin_can_opt_into_no_mention_trigger():
    plugin = _Plugin(
        {
            "allow_local": False,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": True,
            "allow_group_without_at": True,
        }
    )
    manager = _manager_with(plugin)

    handled, result = await manager.execute_direct_commands(
        "/access group command",
        _qq_ctx(owner=False, message_type="group", mentioned=False),
    )

    assert handled is True
    assert result == "tool-ok"
    assert len(plugin.calls) == 1
