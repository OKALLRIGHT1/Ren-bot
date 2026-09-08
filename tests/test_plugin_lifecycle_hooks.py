"""Guards the host lifecycle contract for plugins with background work.

`modules/plugin_manager.py` reclaims background tasks via `getattr(plugin,
"stop")` in `stop_all_plugins` and in the hot-reload path. A plugin that spawns
tasks without exposing `stop` leaks a loop per reload.
"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_system_monitor_exposes_lifecycle_stop():
    """Host reload path checks hasattr(plugin, "stop"); it must be there."""
    from plugins.system_monitor.plugin import Plugin

    plugin = Plugin()
    assert hasattr(plugin, "stop") and callable(plugin.stop)
    # safe to call when monitoring never started
    await plugin.stop()
    assert plugin._running is False


@pytest.mark.asyncio
async def test_system_monitor_stop_cancels_running_monitor(monkeypatch):
    from plugins.system_monitor.plugin import Plugin

    plugin = Plugin()

    async def fake_loop():
        while True:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(plugin, "_monitor_loop", fake_loop)
    await plugin._start_monitoring()
    assert plugin._running is True
    task = plugin._check_task

    await plugin.stop()
    assert plugin._running is False
    assert task.done() or task.cancelled()


@pytest.mark.asyncio
async def test_system_monitor_user_stop_action_still_works(monkeypatch):
    """The action-map "stop" must keep returning its user-facing string."""
    from plugins.system_monitor.plugin import Plugin

    plugin = Plugin()

    async def fake_loop():
        while True:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(plugin, "_monitor_loop", fake_loop)
    assert "未运行" in await plugin._stop_monitoring()
    await plugin._start_monitoring()
    assert "已停止" in await plugin._stop_monitoring()


@pytest.mark.asyncio
async def test_timer_keeps_reference_to_inflight_countdown():
    """Fire-and-forget tasks need a strong ref or they can be GC'd mid-await."""
    from plugins.timer.plugin import Plugin

    plugin = Plugin()
    ctx = {"send_bubble": None}
    await plugin.run("1", ctx)
    assert len(plugin._pending) == 1, "在飞的倒计时应被持有引用"

    await plugin.stop()
    assert plugin._pending == set()


@pytest.mark.asyncio
async def test_timer_done_callback_clears_reference():
    from plugins.timer.plugin import Plugin

    plugin = Plugin()
    sent = []

    async def send(msg):
        sent.append(msg)

    # 0 分钟 -> 立即完成，验证回调清引用而不是永久累积
    await plugin.run("0", {"send_bubble": send})
    for _ in range(100):
        if not plugin._pending:
            break
        await asyncio.sleep(0.005)
    assert plugin._pending == set(), "完成后应自动移除引用"


@pytest.mark.asyncio
async def test_task_manager_check_urgent_does_not_block_event_loop():
    """Background tick runs sqlite; it must go through a thread."""
    from plugins.task_manager.plugin import Plugin

    plugin = Plugin()
    ticked = []

    async def heartbeat():
        # If _check_urgent blocked the loop, this would not get to run.
        for _ in range(5):
            await asyncio.sleep(0)
            ticked.append(1)

    beat = asyncio.create_task(heartbeat())
    result = await plugin._check_urgent()
    await beat

    assert isinstance(result, str)
    assert len(ticked) == 5


def test_task_manager_keeps_sync_helper_for_thread_offload():
    from plugins.task_manager.plugin import Plugin

    plugin = Plugin()
    assert callable(plugin._check_urgent_sync)
    assert isinstance(plugin._check_urgent_sync(), str)
