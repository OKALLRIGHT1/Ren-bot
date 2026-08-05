import pytest

from modules.plugin_manager import PluginManager
from services.agent_runtime import AgentRuntime
from services.security.pending_confirm import get_pending_confirm_store


class _HighPlugin:
    name = "High"
    plugin_trigger = "code"
    llm_command = "execute_code"
    type = "react"
    timeout_sec = 2
    gated_action = "system.exec_code"
    access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
    }

    def __init__(self):
        self.calls = []

    async def run(self, args, ctx):
        self.calls.append((args, dict(ctx or {})))
        return f"executed:{args}"


def _manager(plugin):
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {plugin.plugin_trigger: plugin}
    manager.react_map = {plugin.plugin_trigger: plugin}
    manager.llm_command_map = {plugin.llm_command: plugin.plugin_trigger}
    manager.direct_map = {}
    return manager


def _local_ctx():
    return {"source": "text_input"}


@pytest.fixture(autouse=True)
def _clear_pending():
    store = get_pending_confirm_store()
    store.clear()
    yield
    store.clear()


@pytest.mark.asyncio
async def test_gate_returns_confirmation_required_payload():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    out = await manager._run_with_timeout(plugin, "print(1)", _local_ctx())
    assert plugin.calls == []
    assert isinstance(out, dict)
    assert out["__agent_result__"] == "confirmation_required"
    assert out["payload"]["mode"] == "gate_rerun"
    assert out["payload"]["args"] == "print(1)"
    assert get_pending_confirm_store().has_pending() is True


@pytest.mark.asyncio
async def test_chat_confirm_reruns_gated_plugin():
    plugin = _HighPlugin()
    manager = _manager(plugin)

    class DirectShim(PluginManager):
        def __init__(self, inner, high):
            # minimal: reuse maps
            self.plugins = inner.plugins
            self.direct_map = {}
            self.react_map = inner.react_map
            self.llm_command_map = inner.llm_command_map
            self.default_timeout_sec = 2
            self.debug_enabled = False
            self.model_gateway = None
            self._high = high
            self._inner = inner

        async def execute_direct_commands(self, text, ctx):
            # simulate a direct entry that hits the gated plugin
            if "print" in str(text):
                result = await self._inner._run_with_timeout(self._high, "print(1)", ctx)
                return True, result
            return False, None

        async def _run_with_timeout(self, plugin, args, context):
            return await self._inner._run_with_timeout(plugin, args, context)

    runtime = AgentRuntime(plugin_manager=DirectShim(manager, plugin))
    first = await runtime.handle_direct_text("print(1)", _local_ctx())
    assert first.handled is True
    assert "确认" in str(first.reply)
    assert plugin.calls == []

    second = await runtime.handle_direct_text("确认", _local_ctx())
    assert second.handled is True
    assert second.reply == "executed:print(1)"
    assert len(plugin.calls) == 1
    assert plugin.calls[0][1].get("action_confirmed") is True


@pytest.mark.asyncio
async def test_cancel_clears_pending():
    plugin = _HighPlugin()
    manager = _manager(plugin)
    await manager._run_with_timeout(plugin, "rm -rf", _local_ctx())
    runtime = AgentRuntime(plugin_manager=manager)
    result = await runtime.handle_direct_text("取消", _local_ctx())
    assert "已取消" in result.reply
    assert get_pending_confirm_store().has_pending() is False
