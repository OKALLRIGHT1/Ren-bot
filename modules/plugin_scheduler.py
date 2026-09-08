"""Shared background-loop helper for plugins.

Several plugins (`qq_reminder`, `life_manager`, `task_manager`,
`disaster_warning`) each re-implemented the same shape: create an
`asyncio.Task`, loop forever, sleep between ticks, swallow-and-back-off on
error, cancel cleanly on shutdown. `PeriodicTask` holds that shape once.

Deliberately *not* handled here (kept in the plugins, where it belongs):
storage, gateway resolution, and `access_control`. Each plugin keeps its own
permission profile — this helper only owns loop mechanics.

Typical use::

    self._loop = PeriodicTask(
        self._tick_once,
        interval=lambda: max(5, int(self._settings.get("scan_interval_sec") or 20)),
        error_backoff=30.0,
        name="qq_reminder",
    )
    await self._loop.start()   # idempotent
    await self._loop.stop()    # idempotent, safe before start
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, Union

from core.logger import get_logger

logger = get_logger()

IntervalSpec = Union[float, int, Callable[[], Union[float, int]]]

# asyncio.to_thread raises this once the interpreter is tearing down; it is a
# shutdown signal, not a tick failure, so the loop exits instead of backing off.
_SHUTDOWN_MARKER = "after shutdown"


def _resolve_interval(spec: IntervalSpec, fallback: float) -> float:
    """Evaluate an interval that may be a constant or a callable."""
    try:
        value = spec() if callable(spec) else spec
        seconds = float(value)
    except Exception:
        return fallback
    if seconds != seconds or seconds < 0:  # NaN or negative
        return fallback
    return seconds


class PeriodicTask:
    """A cancellable background loop that calls `tick` on an interval.

    Args:
        tick: coroutine function invoked once per cycle.
        interval: seconds between cycles; a number, or a callable re-read every
            cycle so live setting changes take effect without a restart.
        error_backoff: seconds to sleep after `tick` raises. `None` falls back
            to `interval`.
        tick_first: True runs `tick` then sleeps; False sleeps then ticks
            (matches plugins that want a grace period before the first tick).
        should_continue: optional predicate checked before each cycle; return
            False to end the loop. Lets a plugin keep its own `_running` flag
            as the source of truth.
        name: label used in log lines.
    """

    def __init__(
        self,
        tick: Callable[[], Awaitable[None]],
        *,
        interval: IntervalSpec,
        error_backoff: Optional[float] = None,
        tick_first: bool = True,
        should_continue: Optional[Callable[[], bool]] = None,
        name: str = "plugin",
    ) -> None:
        self._tick = tick
        self._interval = interval
        self._error_backoff = error_backoff
        self._tick_first = tick_first
        self._should_continue = should_continue
        self._name = name
        self._task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the loop. No-op if already running."""
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name=f"{self._name}-loop")

    async def stop(self) -> None:
        """Cancel the loop and await its exit. Idempotent; safe before start."""
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # a tick failing during teardown must not raise
            logger.debug(f"[{self._name}] 后台循环退出时忽略异常: {exc}")

    async def _sleep(self, seconds: float) -> bool:
        """Sleep. Returns False if cancelled, so the caller can exit the loop.

        Always awaits, even for a zero/negative interval: `asyncio.sleep(0)` is
        the yield point that keeps a fast loop from starving the event loop.
        """
        try:
            await asyncio.sleep(max(0.0, seconds))
            return True
        except asyncio.CancelledError:
            return False

    async def _run(self) -> None:
        while True:
            if self._should_continue is not None:
                try:
                    if not self._should_continue():
                        return
                except Exception as exc:
                    logger.error(f"[{self._name}] should_continue 判定失败: {exc}")
                    return

            if not self._tick_first:
                if not await self._sleep(_resolve_interval(self._interval, 10.0)):
                    return
                # Re-check after waking: stop() may have flipped the flag while
                # we slept, and this loop ticks after sleeping.
                if self._should_continue is not None:
                    try:
                        if not self._should_continue():
                            return
                    except Exception:
                        return

            try:
                await self._tick()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if isinstance(exc, RuntimeError) and _SHUTDOWN_MARKER in str(exc):
                    logger.info(f"[{self._name}] 后台循环随主程序关闭退出")
                    return
                logger.error(f"[{self._name}] 后台循环出错: {exc}")
                backoff = (
                    self._error_backoff
                    if self._error_backoff is not None
                    else _resolve_interval(self._interval, 10.0)
                )
                if not await self._sleep(backoff):
                    return
                continue

            if self._tick_first:
                if not await self._sleep(_resolve_interval(self._interval, 10.0)):
                    return
