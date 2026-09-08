import asyncio

import pytest

from modules.plugin_scheduler import PeriodicTask, _resolve_interval


def test_resolve_interval_accepts_number_callable_and_bad_values():
    assert _resolve_interval(5, 10.0) == 5.0
    assert _resolve_interval(lambda: 7, 10.0) == 7.0
    # callable raising -> fallback
    assert _resolve_interval(lambda: 1 / 0, 10.0) == 10.0
    # negative / non-numeric -> fallback
    assert _resolve_interval(-3, 10.0) == 10.0
    assert _resolve_interval("nope", 10.0) == 10.0


@pytest.mark.asyncio
async def test_ticks_repeatedly_then_stops_cleanly():
    calls = []
    loop = PeriodicTask(
        lambda: _record(calls), interval=0, name="t1"
    )
    await loop.start()
    assert loop.running
    await _wait_until(lambda: len(calls) >= 3)
    await loop.stop()
    assert not loop.running
    settled = len(calls)
    await asyncio.sleep(0.02)
    assert len(calls) == settled, "stop() 后不应再 tick"


@pytest.mark.asyncio
async def test_start_is_idempotent_and_does_not_double_tick():
    calls = []
    loop = PeriodicTask(lambda: _record(calls), interval=0, name="t2")
    await loop.start()
    first = loop._task
    await loop.start()
    assert loop._task is first, "重复 start 不应新建 task"
    await loop.stop()


@pytest.mark.asyncio
async def test_stop_before_start_and_double_stop_are_safe():
    loop = PeriodicTask(lambda: _noop(), interval=0, name="t3")
    await loop.stop()  # never started
    await loop.start()
    await loop.stop()
    await loop.stop()  # already stopped
    assert not loop.running


@pytest.mark.asyncio
async def test_tick_exception_backs_off_and_loop_survives():
    calls = []

    async def flaky():
        calls.append(len(calls))
        if len(calls) == 1:
            raise ValueError("boom")

    loop = PeriodicTask(flaky, interval=0, error_backoff=0, name="t4")
    await loop.start()
    await _wait_until(lambda: len(calls) >= 3)
    await loop.stop()
    assert len(calls) >= 3, "单次 tick 异常不应终止循环"


@pytest.mark.asyncio
async def test_shutdown_runtimeerror_exits_loop_instead_of_retrying():
    calls = []

    async def dying():
        calls.append(1)
        raise RuntimeError("cannot schedule new futures after shutdown")

    loop = PeriodicTask(dying, interval=0, error_backoff=0, name="t5")
    await loop.start()
    await _wait_until(lambda: not loop.running)
    assert calls == [1], "关闭信号应直接退出，不再重试"
    await loop.stop()


@pytest.mark.asyncio
async def test_should_continue_false_ends_loop_before_ticking():
    calls = []
    loop = PeriodicTask(
        lambda: _record(calls),
        interval=0,
        should_continue=lambda: False,
        name="t6",
    )
    await loop.start()
    await _wait_until(lambda: not loop.running)
    assert calls == [], "should_continue 为假时不应 tick"


@pytest.mark.asyncio
async def test_tick_first_false_sleeps_before_first_tick():
    calls = []
    loop = PeriodicTask(
        lambda: _record(calls), interval=5, tick_first=False, name="t7"
    )
    await loop.start()
    await asyncio.sleep(0.02)
    assert calls == [], "tick_first=False 应先睡再 tick"
    await loop.stop()


@pytest.mark.asyncio
async def test_interval_callable_is_reread_each_cycle():
    seen = []
    box = {"v": 0}

    def interval():
        seen.append(box["v"])
        return 0

    loop = PeriodicTask(lambda: _noop(), interval=interval, name="t8")
    await loop.start()
    await _wait_until(lambda: len(seen) >= 1)
    box["v"] = 99
    await _wait_until(lambda: 99 in seen)
    await loop.stop()
    assert 99 in seen, "interval 可调用对象应每轮重新求值"


async def _noop():
    return None


async def _record(calls):
    calls.append(1)


async def _wait_until(pred, timeout=2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("等待条件超时")
