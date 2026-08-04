# Runtime Health Center and Rust-Only Screen Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留 Rust/Tauri 活动事件驱动的统计、主动文本互动、久坐提醒和日记，同时彻底切断屏幕事件的 Python 截图、视觉识别和本地窗口探测，并补齐连接恢复、模型限流冷却、全局健康观测、日志轮转与标准测试入口。

**Architecture:** 新增一个进程内、线程安全、只观测不决策的 RuntimeHealthCenter，业务组件继续持有自己的重试和冷却状态，只把脱敏快照上报给健康中心。屏幕链路在 ScreenSensor 和 ChatService 两层固定为纯文本，并让文本事件生成不再调用本地焦点复核；Live2D 连接池和 LLM 路由分别维护自己的退避与冷却状态。

**Tech Stack:** Python 3.10+、asyncio、websockets、aiohttp、pytest/pytest-asyncio、标准库 threading/log rotation、npm scripts、Git

---

## 文件职责与提交边界

- 新建 `services/runtime_health.py`：健康记录、脱敏、新鲜度、overall 聚合和进程级访问入口。
- 修改 `modules/screen_sensor.py`：Rust 事件健康状态转换、纯文本主动互动。
- 修改 `services/chat_service.py` 与 `services/chat_support/sensor_event_service.py`：屏幕事件入口强制纯文本，移除文本链路的本地窗口复核。
- 修改 `modules/live2d.py`：5 秒超时、指数退避、成功复位与连接健康上报。
- 修改 `modules/llm.py`：流式/同步共享的模型冷却表与备用模型跳转。
- 修改 `core/application.py` 与 `integrations/gui_http.py`：初始化可发现组件健康状态，扩展只读运行状态接口。
- 修改 `core/console_capture.py`：stdout/stderr 共享的线程安全轮转写入器。
- 新建 `pytest.ini`，修改 `package.json`：标准测试入口只收集 `tests/`。
- 新建或扩展对应测试文件；每个任务先看到目标测试按预期失败，再写最小实现。

### Task 1: 线程安全的只读运行健康中心

**Files:**
- Create: `services/runtime_health.py`
- Create: `tests/test_runtime_health.py`

- [ ] **Step 1: 写入健康中心失败测试**

```python
from concurrent.futures import ThreadPoolExecutor

from services.runtime_health import RuntimeHealthCenter


def test_snapshot_sanitizes_details_and_marks_stale():
    health = RuntimeHealthCenter(clock=lambda: 100.0)
    health.report(
        "live2d_ws",
        "healthy",
        "已连接",
        details={
            "host": "127.0.0.1:10086",
            "api_key": "secret-value",
            "nested": {"authorization": "Bearer secret", "attempts": 2},
        },
        stale_after_seconds=10,
        updated_at=80.0,
    )

    snapshot = health.snapshot()

    component = snapshot["components"]["live2d_ws"]
    assert component["details"] == {
        "host": "127.0.0.1:10086",
        "api_key": "[REDACTED]",
        "nested": {"authorization": "[REDACTED]", "attempts": 2},
    }
    assert component["stale"] is True
    assert component["effective_state"] == "degraded"
    assert snapshot["overall"] == "degraded"


def test_report_clear_and_snapshot_are_thread_safe():
    health = RuntimeHealthCenter(clock=lambda: 200.0)

    def write(index: int):
        health.report(
            f"model:model-{index}",
            "cooldown" if index % 2 else "healthy",
            "模型状态",
            details={"attempt": index},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))

    snapshot = health.snapshot()
    assert len(snapshot["components"]) == 40
    assert snapshot["overall"] == "degraded"

    health.clear("model:model-1")
    assert "model:model-1" not in health.snapshot()["components"]


def test_offline_component_makes_overall_offline_but_disabled_does_not():
    health = RuntimeHealthCenter(clock=lambda: 300.0)
    health.report("asr", "disabled", "未启用")
    assert health.snapshot()["overall"] == "healthy"

    health.report("rust_activity", "offline", "等待活动源")
    assert health.snapshot()["overall"] == "offline"
```

- [ ] **Step 2: 运行测试确认因模块不存在而失败**

Run: `python -m pytest tests/test_runtime_health.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'services.runtime_health'`

- [ ] **Step 3: 实现最小健康中心 API**

```python
# services/runtime_health.py
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


VALID_STATES = {
    "healthy",
    "degraded",
    "reconnecting",
    "cooldown",
    "offline",
    "disabled",
}
REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "prompt",
    "response",
    "window_title",
)


def _sanitize(value: Any, key: str = "") -> Any:
    lowered = str(key).strip().lower()
    if lowered and any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _iso_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class RuntimeHealthCenter:
    def __init__(self, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self._components: Dict[str, Dict[str, Any]] = {}

    def report(
        self,
        component: str,
        state: str,
        summary: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        stale_after_seconds: Optional[float] = None,
        updated_at: Optional[float] = None,
    ) -> None:
        component_key = str(component or "").strip()
        state_key = str(state or "").strip().lower()
        if not component_key:
            raise ValueError("component is required")
        if state_key not in VALID_STATES:
            raise ValueError(f"invalid runtime health state: {state_key}")
        timestamp = self._clock() if updated_at is None else float(updated_at)
        stale_after = (
            None
            if stale_after_seconds is None
            else max(0.0, float(stale_after_seconds))
        )
        record = {
            "state": state_key,
            "summary": str(summary or "")[:300],
            "details": _sanitize(details or {}),
            "stale_after_seconds": stale_after,
            "_updated_epoch": timestamp,
        }
        with self._lock:
            self._components[component_key] = record

    def clear(self, component: str) -> None:
        with self._lock:
            self._components.pop(str(component or "").strip(), None)

    def snapshot(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        current = self._clock() if now is None else float(now)
        with self._lock:
            records = {key: dict(value) for key, value in self._components.items()}
        components: Dict[str, Dict[str, Any]] = {}
        effective_states = []
        for key, record in sorted(records.items()):
            updated_epoch = float(record.pop("_updated_epoch"))
            stale_after = record.get("stale_after_seconds")
            stale = stale_after is not None and current - updated_epoch > stale_after
            state = str(record["state"])
            effective = "degraded" if stale and state == "healthy" else state
            record.update(
                {
                    "updated_at": _iso_timestamp(updated_epoch),
                    "stale": stale,
                    "effective_state": effective,
                }
            )
            components[key] = record
            if effective != "disabled":
                effective_states.append(effective)
        if "offline" in effective_states:
            overall = "offline"
        elif any(
            state in {"degraded", "reconnecting", "cooldown"}
            for state in effective_states
        ):
            overall = "degraded"
        else:
            overall = "healthy"
        return {"overall": overall, "components": components}


_RUNTIME_HEALTH = RuntimeHealthCenter()


def get_runtime_health() -> RuntimeHealthCenter:
    return _RUNTIME_HEALTH


def report_runtime_health(*args: Any, **kwargs: Any) -> bool:
    try:
        _RUNTIME_HEALTH.report(*args, **kwargs)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: 运行健康中心测试并检查现有导入**

Run: `python -m pytest tests/test_runtime_health.py -q`

Expected: `3 passed`

Run: `python -m pytest tests/test_runtime_status.py -q`

Expected: existing runtime status tests PASS.

- [ ] **Step 5: 提交健康中心**

```powershell
git add services/runtime_health.py tests/test_runtime_health.py
git commit -m "feat: add runtime health center"
```

### Task 2: 将屏幕事件收窄为 Rust 数据加纯文本互动

**Files:**
- Modify: `modules/screen_sensor.py:405-417,1765-1940`
- Modify: `services/chat_service.py:4930-5000`
- Modify: `services/chat_support/sensor_event_service.py:624-704`
- Modify: `tests/test_screen_sensor_rust_recovery.py`
- Modify: `tests/test_sensor_event_service.py`

- [ ] **Step 1: 写入 Rust-only 边界和状态转换失败测试**

在 `tests/test_screen_sensor_rust_recovery.py` 追加：

```python
import asyncio
from concurrent.futures import Future
import logging

from modules.screen_sensor import ScreenSensor
from services.runtime_health import RuntimeHealthCenter


def test_screen_reaction_always_uses_text_path(monkeypatch):
    calls = []

    class ChatService:
        async def handle_sensor_event(self, *args, **kwargs):
            calls.append(kwargs)
            return True

    class RunningLoop:
        def is_running(self):
            return True

    loop = RunningLoop()
    sensor = ScreenSensor.__new__(ScreenSensor)
    sensor.chat_service = ChatService()
    sensor._loop = loop
    sensor.logger = logging.getLogger("screen-rust-only-test")
    sensor.last_reaction_time = 0.0
    sensor.category_reaction_times = {}
    sensor._last_rust_debug_key = ""
    sensor._last_rust_debug_at = 0.0
    sensor.debug_verbose = False
    sensor.daily_durations = {"Code.exe": 7200.0}
    sensor.current_window_start_time = 0.0

    monkeypatch.setattr("modules.screen_sensor.time.time", lambda: 10_000.0)
    def run_immediately(coroutine, target_loop):
        assert target_loop is loop
        completed = Future()
        completed.set_result(asyncio.run(coroutine))
        return completed

    monkeypatch.setattr(
        "modules.screen_sensor.asyncio.run_coroutine_threadsafe",
        run_immediately,
    )

    sensor._try_trigger_reaction(
        "main.py - Code",
        "coding",
        30,
        "Code.exe",
        reason="duration",
        app_duration_sec=7200.0,
        current_stay_sec=1800.0,
    )

    assert calls == [
        {
            "use_vision": False,
            "app_name": "Code.exe",
            "reason": "duration",
            "app_duration_sec": 7200.0,
            "current_stay_sec": 1800.0,
        }
    ]
def test_rust_activity_warning_logs_once_and_recovery_reports_healthy(caplog):
    health = RuntimeHealthCenter(clock=lambda: 500.0)
    sensor = ScreenSensor.__new__(ScreenSensor)
    sensor.logger = logging.getLogger("rust-health-test")
    sensor.runtime_health = health
    sensor._rust_health_state = ""
    sensor._last_rust_event_seen_at = 100.0

    with caplog.at_level(logging.INFO, logger="rust-health-test"):
        sensor._set_rust_activity_health(
            state="degraded", now_ts=300.0, stale_threshold_sec=90.0
        )
        sensor._set_rust_activity_health(
            state="degraded", now_ts=301.0, stale_threshold_sec=90.0
        )
        sensor._set_rust_activity_health(
            state="healthy", now_ts=302.0, stale_threshold_sec=90.0
        )

    messages = [record.getMessage() for record in caplog.records]
    assert sum("activity events stale" in message for message in messages) == 1
    assert sum("activity events resumed" in message for message in messages) == 1
    component = health.snapshot(now=302.0)["components"]["rust_activity"]
    assert component["state"] == "healthy"
    assert "window_title" not in component["details"]
```

在 `tests/test_sensor_event_service.py` 将旧的 focus mismatch 测试改成明确断言纯文本路径不调用本地窗口 getter、截图或图像模型：

```python
@pytest.mark.asyncio
async def test_text_event_generation_never_probes_focus_or_vision():
    calls = {"focus": 0, "capture": 0, "vision": 0}

    def active_title_getter():
        calls["focus"] += 1
        raise AssertionError("local foreground lookup must stay disabled")

    def take_screenshot_base64(**kwargs):
        calls["capture"] += 1
        raise AssertionError("screen capture must stay disabled")

    async def analyze_image(*args, **kwargs):
        calls["vision"] += 1
        raise AssertionError("vision model must stay disabled")

    def chat_with_ai(messages, *, task_type, caller):
        if caller == "sensor_gatekeeper":
            return '{"allowed": true, "reason": "interesting"}'
        return "只根据 Rust 事件生成的文本回复"

    result = await _service().run_event_generation(
        clean_title="main.py - Code",
        display_app="Code.exe",
        category="coding",
        count=3,
        reason="switch",
        use_vision=False,
        vision_mode="separate",
        app_duration_sec=10,
        current_stay_sec=4,
        chat_with_ai=chat_with_ai,
        analyze_image=analyze_image,
        active_title_getter=active_title_getter,
        take_screenshot_base64=take_screenshot_base64,
    )

    assert result.branch == "text"
    assert result.reply == "只根据 Rust 事件生成的文本回复"
    assert calls == {"focus": 0, "capture": 0, "vision": 0}
```

- [ ] **Step 2: 运行测试确认视觉升级和焦点复核导致失败**

Run: `python -m pytest tests/test_screen_sensor_rust_recovery.py tests/test_sensor_event_service.py -q`

Expected: FAIL because duration reactions pass `use_vision=True`, text generation calls `active_title_getter`, and `_set_rust_activity_health` does not exist.

- [ ] **Step 3: 固定 ScreenSensor 为纯文本并上报 Rust 活动健康**

在 `ScreenSensor.__init__` 初始化健康依赖和状态：

```python
from services.runtime_health import get_runtime_health

self.runtime_health = get_runtime_health()
self._rust_health_state = ""
```

用一个状态转换方法替换按分钟重复告警：

```python
def _set_rust_activity_health(
    self, *, state: str, now_ts: float, stale_threshold_sec: float
) -> None:
    previous = str(getattr(self, "_rust_health_state", "") or "")
    self._rust_health_state = state
    last_seen = float(getattr(self, "_last_rust_event_seen_at", 0.0) or 0.0)
    summary = (
        "Rust 活动事件正常"
        if state == "healthy"
        else "Rust 活动事件已过期"
    )
    try:
        self.runtime_health.report(
            "rust_activity",
            state,
            summary,
            details={
                "source": "live2d-tauri",
                "last_event_at": last_seen or None,
                "stale_for_seconds": max(0.0, now_ts - last_seen) if last_seen else None,
            },
            stale_after_seconds=stale_threshold_sec,
            updated_at=now_ts,
        )
    except Exception:
        pass
    if previous == state:
        return
    if state == "healthy" and previous:
        self.logger.info("[Screen] Live2D activity events resumed")
    elif state == "degraded":
        self.logger.warning(
            "[Screen] Live2D activity events stale; waiting for live2d-tauri source"
        )
```

在 `_should_use_rust_events_now` 返回 `True` 前调用 healthy 上报，在 `_warn_rust_events_stale` 内只调用 degraded 上报。删除 `_last_rust_stale_log_at` 的 60 秒刷屏逻辑。

将 `_try_trigger_reaction` 中从“核心修改：视觉判定逻辑分离”到概率判断结束的整段替换为：

```python
use_vision = False
self.logger.info(f"👀 [Screen] 触发 ChatService: {app_name} | Vision: False")
```

保留现有冷却、统计参数、`run_coroutine_threadsafe` 和 `_mark_reaction_if_sent`，不改变久坐和日记逻辑。

- [ ] **Step 4: 在 ChatService 边界禁止视觉导入，并让文本生成跳过本地焦点复核**

在 `ChatService._handle_sensor_event_inner` 中只导入文本 LLM，并无条件传 `use_vision=False`：

```python
from modules.llm import chat_with_ai

generation = await self.sensor_event_service.run_event_generation(
    clean_title=clean_title,
    display_app=display_app,
    category=category,
    count=count,
    reason=reason,
    use_vision=False,
    vision_mode=VISION_MODE,
    app_duration_sec=app_duration_sec,
    current_stay_sec=current_stay_sec,
    chat_with_ai=chat_with_ai,
    analyze_image=None,
)
```

在 `SensorEventService.run_event_generation` 中删除函数开头对 `revalidate_focus_for_sensor` 的调用。保留 `run_vision_generation` 本身供已有显式视觉单元测试使用，但屏幕事件入口永远不再到达它；QQ 图片识别代码不改。

- [ ] **Step 5: 运行屏幕相关回归测试**

Run: `python -m pytest tests/test_screen_sensor_rust_recovery.py tests/test_screen_sensor_sedentary_payload_guard.py tests/test_sensor_event_service.py tests/test_work_session_status.py tests/test_vision_capture_focus.py -q`

Expected: all PASS; the Rust-only test records one text event and zero focus/capture/vision calls.

- [ ] **Step 6: 检查屏幕入口没有视觉或本地窗口调用**

Run: `rg -n "use_vision = True|random\.random|get_active_window_title|take_screenshot_base64" modules/screen_sensor.py services/chat_service.py services/chat_support/sensor_event_service.py`

Expected: no `use_vision = True` or `random.random` in `modules/screen_sensor.py`; any remaining capture/focus symbols are confined to the unreachable explicit `run_vision_generation` helper and its direct visual tests, not the Rust text event path.

- [ ] **Step 7: 提交 Rust-only 屏幕链路**

```powershell
git add modules/screen_sensor.py services/chat_service.py services/chat_support/sensor_event_service.py tests/test_screen_sensor_rust_recovery.py tests/test_sensor_event_service.py
git commit -m "refactor: make screen activity rust-only"
```

### Task 3: Live2D WebSocket 5 秒超时与指数退避

**Files:**
- Modify: `modules/live2d.py:93-206`
- Modify: `tests/test_live2d_transport.py`

- [ ] **Step 1: 写入连接超时、退避、复位和健康上报失败测试**

在 `tests/test_live2d_transport.py` 追加：

```python
from services.runtime_health import RuntimeHealthCenter


def test_live2d_connection_timeout_is_five_seconds():
    import modules.live2d as live2d

    assert live2d.CONNECT_TIMEOUT == 5.0
    assert live2d.PING_TIMEOUT == 5.0


@pytest.mark.asyncio
async def test_connection_pool_backs_off_and_resets_after_success(monkeypatch):
    import modules.live2d as live2d

    clock = {"now": 100.0}
    attempts = []
    health = RuntimeHealthCenter(clock=lambda: clock["now"])

    class LoopProxy:
        def time(self):
            return clock["now"]

    class FakeWs:
        async def close(self):
            return None

        async def ping(self):
            return None

    async def fake_resolve_host():
        return "ws://127.0.0.1:10086/api"

    async def fake_connect(host):
        attempts.append(host)
        if len(attempts) == 1:
            raise ConnectionRefusedError("refused")
        return FakeWs()

    monkeypatch.setattr(live2d, "_resolve_host", fake_resolve_host)
    monkeypatch.setattr(live2d, "_ws_connect", fake_connect)
    monkeypatch.setattr(live2d.asyncio, "get_running_loop", lambda: LoopProxy())

    pool = live2d.WebSocketConnectionPool(
        health=health, jitter=lambda delay: 0.0
    )

    with pytest.raises(ConnectionRefusedError):
        await pool.get_connection()
    assert pool._failure_count == 1
    assert pool._next_retry_at == 101.0

    with pytest.raises(live2d.Live2DConnectionBackoffError, match="1.0"):
        await pool.get_connection()
    assert len(attempts) == 1
    assert health.snapshot(now=100.0)["components"]["live2d_ws"]["state"] == "reconnecting"

    clock["now"] = 101.0
    connection = await pool.get_connection()
    assert isinstance(connection, FakeWs)
    assert pool._failure_count == 0
    assert pool._next_retry_at == 0.0
    assert health.snapshot(now=101.0)["components"]["live2d_ws"]["state"] == "healthy"


def test_connection_backoff_is_capped_at_fifteen_seconds():
    import modules.live2d as live2d

    pool = live2d.WebSocketConnectionPool(jitter=lambda delay: 0.0)
    assert [pool._backoff_delay(n) for n in range(1, 8)] == [1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0]
```

- [ ] **Step 2: 运行目标测试确认常量和构造器失败**

Run: `python -m pytest tests/test_live2d_transport.py -q`

Expected: FAIL because timeouts remain 1 second and the pool has no backoff state or injectable health/jitter.

- [ ] **Step 3: 实现连接池自持有的退避状态**

在 `modules/live2d.py` 定义：

```python
import random
from datetime import datetime, timezone
from urllib.parse import urlparse

from services.runtime_health import get_runtime_health

CONNECT_TIMEOUT = 5.0
PING_TIMEOUT = 5.0
MAX_RECONNECT_DELAY = 15.0


class Live2DConnectionBackoffError(ConnectionError):
    pass


def _safe_host_label(host: str) -> str:
    parsed = urlparse(str(host or ""))
    if not parsed.hostname:
        return "unknown"
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


class WebSocketConnectionPool:
    def __init__(self, *, health=None, jitter=None):
        self._connection = None
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._host = None
        self._created_at = None
        self._is_connected = False
        self._last_ping_at = 0.0
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._last_success_at = 0.0
        self._health = health or get_runtime_health()
        self._jitter = jitter or (lambda delay: random.uniform(0.0, min(0.25, delay * 0.1)))

    def _backoff_delay(self, failure_count: int) -> float:
        base = min(MAX_RECONNECT_DELAY, float(2 ** max(0, failure_count - 1)))
        return min(MAX_RECONNECT_DELAY, base + max(0.0, float(self._jitter(base))))

    def _report(self, state: str, summary: str, *, error: str = "") -> None:
        try:
            self._health.report(
                "live2d_ws",
                state,
                summary,
                details={
                    "host": _safe_host_label(self._host or LIVE2D_HOST),
                    "consecutive_failures": self._failure_count,
                    "next_retry_at": self._next_retry_at or None,
                    "last_success_at": self._last_success_at or None,
                    "error_category": error,
                },
            )
        except Exception:
            pass

    def _record_failure(self, exc: BaseException, now: float) -> None:
        self._failure_count += 1
        self._next_retry_at = now + self._backoff_delay(self._failure_count)
        self._is_connected = False
        self._report("reconnecting", "Live2D WebSocket 正在退避重连", error=type(exc).__name__)

    def _record_success(self, now: float) -> None:
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._last_success_at = now
        self._report("healthy", "Live2D WebSocket 已连接")
```

在 `get_connection` 持锁后先比较 loop time 与 `_next_retry_at`，在窗口内抛出带剩余秒数的 `Live2DConnectionBackoffError`。让 `_create_connection` 捕获建连异常并调用 `_record_failure` 后原样抛出；建连成功调用 `_record_success`。将 ping 的 `wait_for` timeout 改为 `PING_TIMEOUT`；ping 成功也调用成功复位，ping 失败时调用 `_record_failure` 并抛出 `Live2DConnectionBackoffError`，不得把已标坏的连接返回给调用方。`mark_broken()` 也用当前 loop time 建立一次退避，`close()` 清理连接后上报 `offline`，从而覆盖 `healthy`、`reconnecting`、`offline` 三个状态。

- [ ] **Step 4: 运行 Live2D 测试**

Run: `python -m pytest tests/test_live2d_transport.py tests/test_live2d_motion_candidates.py -q`

Expected: all PASS; first failure creates exactly one attempt, immediate retry raises the explicit backoff error, and success clears counters.

- [ ] **Step 5: 提交 WebSocket 恢复改动**

```powershell
git add modules/live2d.py tests/test_live2d_transport.py
git commit -m "feat: add live2d websocket backoff"
```

### Task 4: 流式与同步共享的模型限流冷却

**Files:**
- Modify: `modules/llm.py:43-70,474-716`
- Create: `tests/test_llm_cooldown.py`

- [ ] **Step 1: 写入冷却识别、跳过后备、共享状态和成功清除失败测试**

```python
# tests/test_llm_cooldown.py
import pytest

import modules.llm as llm
from services.runtime_health import RuntimeHealthCenter


class RateLimitedError(RuntimeError):
    status_code = 429

    def __init__(self, reset_seconds=120):
        super().__init__("rate limit reached")
        self.body = {"error": {"code": "model_cooldown", "reset_seconds": reset_seconds}}


@pytest.fixture(autouse=True)
def clear_cooldowns(monkeypatch):
    llm._MODEL_COOLDOWNS.clear()
    health = RuntimeHealthCenter(clock=lambda: 1000.0)
    monkeypatch.setattr(llm, "_RUNTIME_HEALTH", health)
    yield health
    llm._MODEL_COOLDOWNS.clear()


def test_rate_limit_delay_uses_structured_reset_and_caps_at_fifteen_minutes():
    assert llm._rate_limit_delay(RateLimitedError(120)) == 120.0
    assert llm._rate_limit_delay(RateLimitedError(5000)) == 900.0
    assert llm._rate_limit_delay(RuntimeError("rate limit")) == 60.0
    assert llm._rate_limit_delay(RuntimeError("socket closed")) is None


def test_sync_skips_cooled_model_and_uses_backup(monkeypatch, clear_cooldowns):
    now = {"value": 1000.0}
    calls = []

    class FakeResponse:
        class Choice:
            class Message:
                content = "backup reply"
            message = Message()
        choices = [Choice()]

    class FakeCompletions:
        def __init__(self, model_key):
            self.model_key = model_key
        def create(self, **kwargs):
            calls.append(self.model_key)
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = type("Chat", (), {"completions": FakeCompletions(base_url)})()

    monkeypatch.setattr(llm.time, "time", lambda: now["value"])
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm, "MODELS", {
        "primary": {"model": "one", "api_key": "x", "base_url": "primary"},
        "backup": {"model": "two", "api_key": "x", "base_url": "backup"},
    })
    monkeypatch.setattr(llm, "_build_attempt_order", lambda config, key: ["openai"])
    llm._start_model_cooldown("primary", RateLimitedError(120), now=now["value"])

    reply = llm.chat_with_ai(
        [{"role": "user", "content": "hello"}],
        caller="test",
        model_keys_override=["primary", "backup"],
    )

    assert reply == "backup reply"
    assert calls == ["backup"]
    component = clear_cooldowns.snapshot(now=1000.0)["components"]["model:primary"]
    assert component["state"] == "cooldown"
    assert "rate limit reached" not in str(component)


@pytest.mark.asyncio
async def test_stream_observes_same_sync_cooldown_and_recovers_after_expiry(monkeypatch):
    now = {"value": 2000.0}
    llm._set_model_cooldown("shared", until=2005.0, reason="rate_limit")
    monkeypatch.setattr(llm.time, "time", lambda: now["value"])
    monkeypatch.setattr(llm, "LLM_ROUTER", {"default": ["shared"]})
    monkeypatch.setattr(llm, "MODELS", {
        "shared": {"model": "one", "api_key": "x", "base_url": "shared"}
    })

    chunks = [chunk async for chunk in llm.chat_with_ai_stream([], caller="test")]
    assert chunks == ["（所有模型连接失败，请检查网络或 Key）"]

    now["value"] = 2006.0
    assert llm._model_cooldown_remaining("shared", now=now["value"]) == 0.0
    assert "shared" not in llm._MODEL_COOLDOWNS


def test_success_clears_existing_model_cooldown(monkeypatch):
    llm._set_model_cooldown("backup", until=4000.0, reason="rate_limit")
    llm._clear_model_cooldown("backup", summary="模型调用恢复")
    assert "backup" not in llm._MODEL_COOLDOWNS
```

- [ ] **Step 2: 运行冷却测试确认辅助 API 不存在**

Run: `python -m pytest tests/test_llm_cooldown.py -q`

Expected: FAIL because `_MODEL_COOLDOWNS` and the cooldown helpers are undefined.

- [ ] **Step 3: 实现共享冷却表和限流解析**

在 `modules/llm.py` 的全局锁附近新增：

```python
from services.runtime_health import get_runtime_health

_MODEL_COOLDOWN_LOCK = threading.RLock()
_MODEL_COOLDOWNS = {}
_DEFAULT_MODEL_COOLDOWN_SECONDS = 60.0
_MAX_MODEL_COOLDOWN_SECONDS = 900.0
_RUNTIME_HEALTH = get_runtime_health()


def _find_reset_seconds(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "reset_seconds":
                try:
                    return float(item)
                except (TypeError, ValueError):
                    return None
            found = _find_reset_seconds(item)
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_reset_seconds(item)
            if found is not None:
                return found
    return None


def _rate_limit_delay(exc: BaseException):
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    lowered = str(exc or "").lower()
    body = getattr(exc, "body", None)
    is_limited = status == 429 or any(
        marker in lowered for marker in ("model_cooldown", "rate limit", "rate_limit")
    )
    if not is_limited and body is not None:
        is_limited = any(
            marker in str(body).lower()
            for marker in ("model_cooldown", "rate limit", "rate_limit")
        )
    if not is_limited:
        return None
    reset = _find_reset_seconds(body)
    if reset is None:
        reset = _DEFAULT_MODEL_COOLDOWN_SECONDS
    return min(_MAX_MODEL_COOLDOWN_SECONDS, max(1.0, float(reset)))


def _set_model_cooldown(model_key: str, *, until: float, reason: str) -> None:
    with _MODEL_COOLDOWN_LOCK:
        _MODEL_COOLDOWNS[model_key] = {"until": float(until), "reason": str(reason)}
    try:
        _RUNTIME_HEALTH.report(
            f"model:{model_key}",
            "cooldown",
            "模型限流冷却中",
            details={"cooldown_until": float(until), "reason": str(reason)},
        )
    except Exception:
        pass


def _start_model_cooldown(model_key: str, exc: BaseException, *, now=None):
    delay = _rate_limit_delay(exc)
    if delay is None:
        return None
    current = time.time() if now is None else float(now)
    _set_model_cooldown(model_key, until=current + delay, reason="rate_limit")
    return delay


def _clear_model_cooldown(model_key: str, *, summary: str) -> None:
    with _MODEL_COOLDOWN_LOCK:
        existed = _MODEL_COOLDOWNS.pop(model_key, None) is not None
    if existed:
        try:
            _RUNTIME_HEALTH.report(f"model:{model_key}", "healthy", summary, details={})
        except Exception:
            pass


def _model_cooldown_remaining(model_key: str, *, now=None) -> float:
    current = time.time() if now is None else float(now)
    with _MODEL_COOLDOWN_LOCK:
        record = _MODEL_COOLDOWNS.get(model_key)
        if not record:
            return 0.0
        remaining = float(record["until"]) - current
        if remaining > 0:
            return remaining
        _MODEL_COOLDOWNS.pop(model_key, None)
    try:
        _RUNTIME_HEALTH.report(f"model:{model_key}", "healthy", "模型冷却已结束", details={})
    except Exception:
        pass
    return 0.0
```

- [ ] **Step 4: 将冷却判断接入两条候选循环**

在流式和同步的每个 `for ... key in model_keys` 取得配置之前加入：

```python
remaining = _model_cooldown_remaining(key)
if remaining > 0:
    _trace_log(f"[LLM] 跳过冷却模型 model={key} remaining={remaining:.1f}s")
    continue
```

两条异常分支在记录 metric 后执行：

```python
cooldown_delay = _start_model_cooldown(key, e)
if cooldown_delay is not None:
    break
continue
```

两条成功分支在 `record_success` 前执行：

```python
_clear_model_cooldown(key, summary="模型调用恢复")
```

这样限流会停止同一模型的其他 transport 尝试，但外层循环继续尝试备用模型；健康中心只展示，不参与跳过判断。

- [ ] **Step 5: 运行 LLM 冷却与路由回归测试**

Run: `python -m pytest tests/test_llm_cooldown.py tests/test_model_catalog.py tests/test_search_model_selection.py tests/test_plugin_model_gateway.py tests/test_chat_service_smoke.py -q`

Expected: all PASS; cooled primary is never invoked, backup succeeds, sync-created cooldown is visible to stream.

- [ ] **Step 6: 提交模型冷却**

```powershell
git add modules/llm.py tests/test_llm_cooldown.py
git commit -m "feat: cool down rate limited models"
```

### Task 5: 初始化全局组件状态并扩展 /runtime/status

**Files:**
- Modify: `core/application.py:190-245,1220-1425`
- Modify: `integrations/gui_http.py:788-820`
- Modify: `tests/test_runtime_status.py`

- [ ] **Step 1: 写入向后兼容和脱敏组件状态失败测试**

在 `tests/test_runtime_status.py` 追加：

```python
from services.runtime_health import RuntimeHealthCenter


def test_runtime_status_adds_health_snapshot_and_rust_stale_flag():
    health = RuntimeHealthCenter(clock=lambda: 200.0)
    health.report(
        "rust_activity",
        "degraded",
        "Rust 活动事件已过期",
        details={"stale_for_seconds": 120, "token": "must-not-leak"},
    )
    health.report(
        "model:primary",
        "cooldown",
        "模型限流冷却中",
        details={"cooldown_until": 260.0},
    )

    class App(_DummyApp):
        runtime_health = health

    status = GuiHttpServer(app_ref=App())._build_runtime_status()

    assert status["overall"] == "degraded"
    assert status["screen_sensor"] == {
        "bound": True,
        "use_rust_events_only": True,
        "mode": "rust_only",
        "activity_stale": True,
    }
    assert status["components"]["rust_activity"]["details"]["token"] == "[REDACTED]"
    assert status["components"]["model:primary"]["state"] == "cooldown"
    assert status["work_session"]["active_minutes"] == 12
    assert status["latest_rust_event"]["presence"] == "active"


def test_runtime_status_does_not_probe_components():
    class ReadOnlyHealth:
        def snapshot(self):
            return {"overall": "healthy", "components": {}}

    class App(_DummyApp):
        runtime_health = ReadOnlyHealth()

        def probe_services(self):
            raise AssertionError("status endpoint must not probe services")

    status = GuiHttpServer(app_ref=App())._build_runtime_status()
    assert status["overall"] == "healthy"
```

- [ ] **Step 2: 运行状态测试确认缺少新字段**

Run: `python -m pytest tests/test_runtime_status.py -q`

Expected: FAIL with missing `overall` and `components` keys.

- [ ] **Step 3: 扩展只读状态构建器**

在 `GuiHttpServer._build_runtime_status` 读取 app 已持有的健康中心，不发起网络或进程探测：

```python
health = getattr(app, "runtime_health", None) if app is not None else None
health_snapshot = {"overall": "healthy", "components": {}}
if health is not None and hasattr(health, "snapshot"):
    try:
        candidate = health.snapshot()
        if isinstance(candidate, dict):
            health_snapshot = candidate
    except Exception:
        health_snapshot = {"overall": "degraded", "components": {}}

rust_health = health_snapshot.get("components", {}).get("rust_activity", {})
rust_state = str(rust_health.get("effective_state") or rust_health.get("state") or "")

return {
    "overall": str(health_snapshot.get("overall") or "healthy"),
    "components": health_snapshot.get("components", {}),
    "screen_sensor": {
        "bound": sensor is not None,
        "use_rust_events_only": bool(getattr(sensor, "use_rust_events_only", False)) if sensor is not None else False,
        "mode": "rust_only" if sensor is not None else "disabled",
        "activity_stale": rust_state in {"degraded", "offline"},
    },
    "work_session": work_session,
    "latest_rust_event": latest_rust_event,
}
```

- [ ] **Step 4: 初始化应用可发现组件状态**

在 `Live2DApplication.__init__` 创建核心状态时加入：

```python
from services.runtime_health import get_runtime_health

self.runtime_health = get_runtime_health()
```

在 ScreenSensor 初始化结束后调用新方法：

```python
def _report_initial_runtime_health(self) -> None:
    from config import MODELS

    records = (
        ("live2d_ws", "offline", "等待 Live2D WebSocket 首次连接", {}),
        ("rust_activity", "offline", "等待 Rust 活动事件", {"source": "live2d-tauri"}),
        (
            "model_router",
            "healthy" if MODELS else "disabled",
            "模型路由已配置" if MODELS else "模型路由未配置",
            {"configured_models": len(MODELS)},
        ),
        ("qq_gateway", "offline", "等待 QQ 网关连接", {}),
        (
            "tts",
            "healthy" if self.tts is not None and self.tts_enabled else "disabled",
            "TTS 已启用" if self.tts is not None and self.tts_enabled else "TTS 未启用",
            {},
        ),
        (
            "asr",
            "healthy" if self.voice_sensor is not None else "disabled",
            "ASR 已启用" if self.voice_sensor is not None else "ASR 未启用",
            {},
        ),
        (
            "plugin_manager",
            "healthy" if self.plugin_manager is not None else "offline",
            "插件管理器已加载" if self.plugin_manager is not None else "插件管理器不可用",
            {"loaded_plugins": len(getattr(self.plugin_manager, "plugins", {}) or {})},
        ),
    )
    for component, state, summary, details in records:
        try:
            self.runtime_health.report(component, state, summary, details=details)
        except Exception:
            continue
```

在 `_init_voice_sensor_if_configured()` 调用之后执行 `_report_initial_runtime_health()`，使 ASR 初值反映实际本地对象。该方法只根据已存在的本地对象和配置赋初值，不连接外部服务。Live2D、Rust 活动和模型后续由各自任务中的上报覆盖。

- [ ] **Step 5: 运行状态和应用启动回归测试**

Run: `python -m pytest tests/test_runtime_status.py tests/test_headless_runtime.py tests/test_launcher.py tests/test_gui_status_screen_api.py -q`

Expected: all PASS;旧字段保持原结构，新字段为脱敏快照，状态读取不触发探测。

- [ ] **Step 6: 提交运行状态接口**

```powershell
git add core/application.py integrations/gui_http.py tests/test_runtime_status.py
git commit -m "feat: expose runtime health status"
```

### Task 6: console.log 共享写入与 10 MB 轮转

**Files:**
- Modify: `core/console_capture.py`
- Create: `tests/test_console_capture.py`

- [ ] **Step 1: 写入轮转、共享 writer、幂等和故障隔离失败测试**

```python
# tests/test_console_capture.py
import io
import sys

import core.console_capture as capture


def _reset_capture(monkeypatch):
    monkeypatch.setattr(capture, "_installed_path", None)
    monkeypatch.setattr(capture, "_installed_writer", None)


def test_stdout_and_stderr_share_rotating_writer(tmp_path, monkeypatch):
    _reset_capture(monkeypatch)
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    path = tmp_path / "console.log"

    capture.install_console_capture(str(path), max_bytes=32, backup_count=2)
    installed_stdout = sys.stdout
    installed_stderr = sys.stderr
    installed_stdout.write("a" * 24)
    installed_stderr.write("b" * 24)
    installed_stdout.flush()

    assert installed_stdout._writer is installed_stderr._writer
    assert path.exists()
    assert (tmp_path / "console.log.1").exists()
    assert stdout.getvalue() == "a" * 24
    assert stderr.getvalue() == "b" * 24


def test_install_is_idempotent_for_same_path(tmp_path, monkeypatch):
    _reset_capture(monkeypatch)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    path = tmp_path / "console.log"

    capture.install_console_capture(str(path))
    first_stdout = sys.stdout
    first_writer = sys.stdout._writer
    capture.install_console_capture(str(path))

    assert sys.stdout is first_stdout
    assert sys.stdout._writer is first_writer


def test_log_open_failure_preserves_original_streams(tmp_path, monkeypatch):
    _reset_capture(monkeypatch)
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    class BrokenWriter:
        def __init__(self, *args, **kwargs):
            raise OSError("disk unavailable")

    monkeypatch.setattr(capture, "RotatingTextWriter", BrokenWriter)
    result = capture.install_console_capture(str(tmp_path / "console.log"))

    assert result == (tmp_path / "console.log").resolve()
    assert sys.stdout is stdout
    assert sys.stderr is stderr
```

- [ ] **Step 2: 运行测试确认安装函数不接受轮转参数**

Run: `python -m pytest tests/test_console_capture.py -q`

Expected: FAIL because `RotatingTextWriter` and shared writer state do not exist.

- [ ] **Step 3: 实现线程安全轮转写入器**

将 `core/console_capture.py` 的文件写入职责提取为：

```python
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional, TextIO

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


class RotatingTextWriter:
    def __init__(self, path: Path, *, max_bytes: int, backup_count: int):
        self.path = path
        self.max_bytes = max(1, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.RLock()
        self._file = path.open("a", encoding="utf-8", buffering=1)

    def _rotate(self) -> None:
        self._file.close()
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                os.replace(source, target)
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def write(self, text: str) -> int:
        payload = str(text)
        with self._lock:
            self._file.seek(0, os.SEEK_END)
            current_size = self._file.tell()
            incoming_size = len(payload.encode("utf-8", errors="replace"))
            if current_size > 0 and current_size + incoming_size > self.max_bytes:
                self._rotate()
            self._file.write(payload)
        return len(payload)

    def flush(self) -> None:
        with self._lock:
            self._file.flush()


class TeeStream:
    def __init__(self, original: Optional[TextIO], writer: RotatingTextWriter):
        self._original = original
        self._writer = writer
        self.encoding = getattr(original, "encoding", "utf-8") or "utf-8"
        self.errors = getattr(original, "errors", "replace") or "replace"

    def write(self, text):
        payload = "" if text is None else str(text)
        if self._original is not None:
            try:
                self._original.write(payload)
            except Exception:
                pass
        try:
            self._writer.write(payload)
        except Exception:
            pass
        return len(payload)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass
        try:
            self._writer.flush()
        except Exception:
            pass
```

保留现有 `isatty`、`fileno`、`closed` 和 `__getattr__` 行为。安装入口改为：

```python
_installed_path: Optional[Path] = None
_installed_writer: Optional[RotatingTextWriter] = None


def install_console_capture(
    log_path: str = "./logs/console.log",
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    global _installed_path, _installed_writer
    path = Path(log_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if _installed_path == path and _installed_writer is not None:
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = RotatingTextWriter(
            path, max_bytes=max_bytes, backup_count=backup_count
        )
    except Exception:
        return path
    sys.stdout = TeeStream(sys.stdout, writer)
    sys.stderr = TeeStream(sys.stderr, writer)
    _installed_writer = writer
    _installed_path = path
    return path
```

- [ ] **Step 4: 运行轮转和 logger 回归测试**

Run: `python -m pytest tests/test_console_capture.py tests/test_core_logger.py -q`

Expected: all PASS; stdout/stderr 使用同一 writer，产生 `console.log.1`，重复安装对象不变，打开失败时原流不变。

- [ ] **Step 5: 提交日志轮转**

```powershell
git add core/console_capture.py tests/test_console_capture.py
git commit -m "feat: rotate captured console logs"
```

### Task 7: 修复 Python 与 npm 标准测试入口

**Files:**
- Create: `pytest.ini`
- Modify: `package.json`

- [ ] **Step 1: 记录当前标准入口失败证据**

Run: `python -m pytest -q`

Expected: FAIL during collection because pytest discovers Python 2 sample files under `data/`.

Run: `npm test`

Expected: FAIL with `Error: no test specified`.

- [ ] **Step 2: 限定 pytest 收集目录**

```ini
# pytest.ini
[pytest]
testpaths = tests
```

- [ ] **Step 3: 将 npm test 指向同一 Python 测试入口**

将 `package.json` 的 scripts 改为：

```json
"scripts": {
  "test": "python -m pytest -q"
}
```

- [ ] **Step 4: 验证两个标准入口均不再误收集 data**

Run: `python -m pytest -q`

Expected: PASS with tests collected only from `tests/`.

Run: `npm test`

Expected: PASS with the same pytest suite and exit code 0.

- [ ] **Step 5: 提交测试入口**

```powershell
git add pytest.ini package.json
git commit -m "test: fix standard test entrypoints"
```

### Task 8: 全量验证、代码审查与合并到本地 main

**Files:**
- Review: all files changed since `a19586a`
- Merge target: local `main`

- [ ] **Step 1: 运行所有目标测试集合**

Run: `python -m pytest tests/test_runtime_health.py tests/test_screen_sensor_rust_recovery.py tests/test_screen_sensor_sedentary_payload_guard.py tests/test_sensor_event_service.py tests/test_work_session_status.py tests/test_live2d_transport.py tests/test_llm_cooldown.py tests/test_runtime_status.py tests/test_console_capture.py -q`

Expected: all selected tests PASS.

- [ ] **Step 2: 运行完整标准入口**

Run: `python -m pytest -q`

Expected: full suite PASS, with only known intentional skips.

Run: `npm test`

Expected: same full suite PASS and exit code 0.

- [ ] **Step 3: 做静态 diff 自查**

Run: `rg -n "use_vision = True|random\.random" modules/screen_sensor.py`

Expected: no matches.

Run: `rg -n "get_active_window_title|take_screenshot_base64|analyze_image" modules/screen_sensor.py services/chat_service.py`

Expected: no matches in the two Rust screen entry files.

Run: `git diff --check a19586a..HEAD`

Expected: no whitespace errors.

Run: `git status --short --branch`

Expected: branch `codex/runtime-health-center` and clean worktree.

- [ ] **Step 4: 审查完整变更范围**

Run: `git diff --stat a19586a..HEAD`

Expected: only the files listed by Tasks 1-7 plus the approved spec/plan documents.

Run: `git diff a19586a..HEAD -- services/runtime_health.py modules/screen_sensor.py services/chat_service.py services/chat_support/sensor_event_service.py modules/live2d.py modules/llm.py core/application.py integrations/gui_http.py core/console_capture.py pytest.ini package.json`

Expected: no duplicate business state in health center, no screen visual call path, no secrets/raw prompts/raw responses in health details, no dependency version changes.

- [ ] **Step 5: 在隔离 worktree 外合并到本地 main**

先确认原运行目录仍在 `codex/agent-runtime-mail` 且不切换、不重启。随后为合并创建临时 main worktree，避免扰动正在运行的旧进程：

```powershell
$mergeWorktree = 'D:\Desktop\live2d-suzu\live2d-llm-main-merge-20260803'
git worktree add $mergeWorktree main
git -C $mergeWorktree merge --no-ff codex/runtime-health-center -m "merge: add runtime health and rust-only screen pipeline"
```

Expected: merge commit succeeds without changing the branch or files loaded by the running program in `D:\Desktop\live2d-suzu\live2d-llm`.

- [ ] **Step 6: 在 main 合并结果上重新跑完整验证**

Run from `D:\Desktop\live2d-suzu\live2d-llm-main-merge-20260803`: `python -m pytest -q`

Expected: full suite PASS.

Run from the same directory: `npm test`

Expected: full suite PASS and exit code 0.

Run: `git -C D:\Desktop\live2d-suzu\live2d-llm-main-merge-20260803 status --short --branch`

Expected: `## main` and clean worktree.

- [ ] **Step 7: 记录最终提交，不推送也不重启**

Run: `git -C D:\Desktop\live2d-suzu\live2d-llm-main-merge-20260803 log -3 --oneline`

Expected: top commit is `merge: add runtime health and rust-only screen pipeline`; local `main` contains all task commits. Do not push and do not restart the currently running Live2D program.
