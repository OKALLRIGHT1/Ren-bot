"""Lifecycle tests for the plugins rewired onto PeriodicTask.

Guards the start/stop contract the host relies on (`start_all_plugins` /
`stop_all_plugins` in modules/plugin_manager.py), not the business logic.
"""

import asyncio

import pytest


async def _wait_until(pred, timeout=2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("等待条件超时")


@pytest.mark.asyncio
async def test_qq_reminder_start_stop_and_idempotency(tmp_path, monkeypatch):
    from plugins.qq_reminder.plugin import Plugin

    plugin = Plugin()
    plugin._settings["storage_path"] = str(tmp_path / "r.json")
    plugin._settings["scan_interval_sec"] = 5

    ticks = []

    async def fake_tick():
        ticks.append(1)

    monkeypatch.setattr(plugin._loop, "_tick", fake_tick)

    await plugin.start(None)
    assert plugin._loop.running
    await _wait_until(lambda: len(ticks) >= 1)

    # start twice must not spawn a second loop
    task = plugin._loop._task
    await plugin.start(None)
    assert plugin._loop._task is task

    await plugin.stop()
    assert not plugin._loop.running
    await plugin.stop()  # idempotent


@pytest.mark.asyncio
async def test_qq_reminder_interval_floor_still_applies():
    """Original code clamped scan_interval_sec to >= 5; keep that."""
    from plugins.qq_reminder.plugin import Plugin

    plugin = Plugin()
    plugin._settings["scan_interval_sec"] = 1
    assert plugin._loop._interval() == 5
    plugin._settings["scan_interval_sec"] = 42
    assert plugin._loop._interval() == 42


@pytest.mark.asyncio
async def test_life_manager_start_stop_sets_flag(monkeypatch, tmp_path):
    from plugins.life_manager.plugin import Plugin

    plugin = Plugin()
    plugin.db_path = str(tmp_path / "u.db")

    ticks = []

    async def fake_tick():
        ticks.append(1)

    monkeypatch.setattr(plugin._loop, "_tick", fake_tick)

    await plugin.start()
    assert plugin._loop.running
    assert plugin._stopping is False
    await _wait_until(lambda: len(ticks) >= 1)

    await plugin.stop()
    assert plugin._stopping is True
    assert not plugin._loop.running


@pytest.mark.asyncio
async def test_life_manager_stopping_flag_halts_loop(monkeypatch, tmp_path):
    """should_continue must honour _stopping even without cancellation."""
    from plugins.life_manager.plugin import Plugin

    plugin = Plugin()
    plugin.db_path = str(tmp_path / "u.db")
    monkeypatch.setattr(plugin._loop, "_interval", 0)

    async def fake_tick():
        return None

    monkeypatch.setattr(plugin._loop, "_tick", fake_tick)

    await plugin.start()
    plugin._stopping = True  # flip flag without calling stop()
    await _wait_until(lambda: not plugin._loop.running)


@pytest.mark.asyncio
async def test_task_manager_start_stop_return_messages(monkeypatch):
    """start/stop are user-facing actions; their strings are the contract."""
    from plugins.task_manager.plugin import Plugin

    plugin = Plugin()

    async def fake_tick():
        return None

    monkeypatch.setattr(plugin._loop, "_tick", fake_tick)

    assert "未运行" in await plugin.stop()

    started = await plugin.start()
    assert "已启动" in started
    assert plugin._running is True
    assert plugin._loop.running

    assert "已在运行中" in await plugin.start()

    stopped = await plugin.stop()
    assert "已停止" in stopped
    assert plugin._running is False
    assert not plugin._loop.running


@pytest.mark.asyncio
async def test_task_manager_sleeps_before_first_tick(monkeypatch):
    """tick_first=False preserved: no immediate tick on start."""
    from plugins.task_manager.plugin import Plugin

    plugin = Plugin()
    plugin._check_interval = 5
    ticks = []

    async def fake_tick():
        ticks.append(1)

    monkeypatch.setattr(plugin._loop, "_tick", fake_tick)

    await plugin.start()
    await asyncio.sleep(0.03)
    assert ticks == [], "启动后不应立即巡检，应先睡一个间隔"
    await plugin.stop()
