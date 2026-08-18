import asyncio
from concurrent.futures import Future
from datetime import datetime, timezone
import logging
import time
import types

import modules.screen_sensor as screen_sensor_module
from modules.screen_sensor import ScreenSensor, rust_activity_stale_threshold_sec
from services.runtime_health import RuntimeHealthCenter


class DummyChatService:
    async def send_active_alert(self, app_name, active_minutes):
        return None


def test_screen_reaction_duration_enables_vision(monkeypatch):
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
            "use_vision": True,
            "app_name": "Code.exe",
            "reason": "duration",
            "app_duration_sec": 7200.0,
            "current_stay_sec": 1800.0,
        }
    ]


def test_screen_reaction_switch_can_stay_text_only(monkeypatch):
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
    sensor.logger = logging.getLogger("screen-switch-text-test")
    sensor.last_reaction_time = 0.0
    sensor.category_reaction_times = {}
    sensor._last_rust_debug_key = ""
    sensor._last_rust_debug_at = 0.0
    sensor.debug_verbose = False
    sensor.daily_durations = {"Code.exe": 120.0}
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
        3,
        "Code.exe",
        reason="switch",
        app_duration_sec=120.0,
        current_stay_sec=5.0,
    )

    assert calls == [
        {
            "use_vision": False,
            "app_name": "Code.exe",
            "reason": "switch",
            "app_duration_sec": 120.0,
            "current_stay_sec": 5.0,
        }
    ]


def test_rust_activity_stale_threshold_is_injectable():
    assert rust_activity_stale_threshold_sec(10) == 200.0
    assert rust_activity_stale_threshold_sec(1) == 120.0


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


def test_should_use_rust_events_now_accepts_fresh_live2d_event():
    sensor = ScreenSensor(DummyChatService())
    sensor.use_rust_events_only = False
    sensor._last_rust_event_seen_at = 100.0
    sensor._recent_rust_events = types.MethodType(
        lambda self, limit=1: [
            {
                "event_id": "rust-1",
                "ts": "1970-01-01T00:01:40+00:00",
                "kind": "activity_sample",
                "app": {"name": "Code.exe"},
                "window_title": "Code",
                "browser": {},
            }
        ],
        sensor,
    )

    assert sensor._should_use_rust_events_now(now_ts=110.0, stale_threshold_sec=90.0)
    assert sensor.use_rust_events_only is True


def test_should_use_rust_events_now_waits_without_live2d_events():
    sensor = ScreenSensor(DummyChatService())
    sensor.use_rust_events_only = False
    sensor._last_rust_event_seen_at = 100.0
    sensor._recent_rust_events = types.MethodType(lambda self, limit=1: [], sensor)

    assert not sensor._should_use_rust_events_now(now_ts=110.0, stale_threshold_sec=90.0)
    assert sensor.use_rust_events_only is False


def test_start_does_not_spawn_duplicate_monitor_thread(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    started = []

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(self)

        def is_alive(self):
            return True

    monkeypatch.setattr(screen_sensor_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(sensor, "restore_recent_work_session", lambda: None)

    sensor.start(loop="first")
    sensor.start(loop="second")

    assert len(started) == 1
    assert sensor._loop == "second"
    assert sensor.use_rust_events_only is True


def test_rust_only_stale_warning_is_rate_limited(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    warnings = []
    sleep_calls = []
    clock = {"now": 1000.0}

    sensor.use_rust_events_only = True
    sensor.running = True
    sensor._last_rust_event_seen_at = 1.0
    sensor.logger.warning = lambda message: warnings.append(message)
    sensor._check_daily_reset = lambda: None
    sensor._save_stats = lambda: None
    sensor._recent_rust_events = lambda limit=1: []
    sensor._try_trigger_reaction = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("rust-only stale mode must not trigger screen commentary")
    )

    def fake_sleep(_seconds):
        sleep_calls.append(_seconds)
        clock["now"] += 2.0
        if len(sleep_calls) >= 3:
            sensor.running = False

    monkeypatch.setattr(screen_sensor_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(screen_sensor_module.time, "sleep", fake_sleep)

    sensor._monitor_loop()

    stale_warnings = [
        message for message in warnings if "Live2D activity events stale" in message
    ]
    assert len(stale_warnings) == 1


def test_rust_only_ignores_idle_foreground_placeholder():
    sensor = ScreenSensor(DummyChatService())
    reactions = []
    event = {
        "event_id": "idle-placeholder",
        "ts": "1970-01-01T00:20:00+00:00",
        "kind": "foreground_changed",
        "presence": "active",
        "source": "live2d-tauri",
        "app": {"name": "No foreground window (idle)"},
        "window_title": "No foreground window (idle)",
        "browser": {},
    }
    sensor.use_rust_events_only = True
    sensor.daily_counts = {}
    sensor.daily_durations = {}
    sensor.last_app_name = ""
    sensor._recent_rust_events = lambda limit=20: [event]
    sensor._try_trigger_reaction = lambda *args, **kwargs: reactions.append(args)

    sensor._process_rust_events_for_reaction(now_ts=1200.0)

    assert reactions == []
    assert sensor.daily_counts == {}
    assert sensor.last_app_name == ""
    assert sensor._last_rust_event_id == "idle-placeholder"


def test_same_batch_prefers_duration_over_earlier_switch():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    reactions = []
    sensor.daily_counts = {"Code.exe": 2}
    sensor.daily_durations = {"Code.exe": 0.0}
    sensor.last_app_name = ""
    sensor.last_window_title = ""
    sensor._analyze_window_context = lambda app="", title="", domain="": (
        "coding",
        app or title or "unknown",
    )
    sensor._try_trigger_reaction = lambda *args, **kwargs: reactions.append(
        (args, kwargs)
    )

    def _iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "sample-long",
            "ts": _iso(now),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
        {
            "event_id": "switch-code",
            "ts": _iso(now - 21 * 60),
            "kind": "foreground_changed",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
    ]

    sensor._process_rust_events_for_reaction(now)

    assert len(reactions) == 1
    _args, kwargs = reactions[0]
    assert kwargs.get("reason") == "duration"


def test_sanitize_screen_stats_removes_polluted_local_entries():
    sensor = ScreenSensor(DummyChatService())
    sensor.daily_counts = {
        "Codex": 2,
        "No foreground window (idle)": 32,
        "None": 16,
        "python": 26,
        "Live2D": 23,
        "Live2D-Suzu": 12,
    }
    sensor.daily_durations = {
        "video": 60.0,
        "No foreground window (idle)": 7140.0,
    }
    sensor.app_cache = {
        "Codex": ["Codex", "coding"],
        "python": ["python", "coding"],
        "app=python.exe|title=Live2D Agent|domain=": ["Live2D Agent", "self"],
    }
    sensor.observation_entries = [
        {"app": "video", "window_title": "Bilibili", "source": "vision"},
        {
            "app": "No foreground window (idle)",
            "window_title": "No foreground window (idle)",
            "source": "vision",
        },
        {"app": "None", "window_title": "No foreground window (active)", "source": "text"},
    ]

    sensor._sanitize_stats()

    assert sensor.daily_counts == {"Codex": 2}
    assert sensor.daily_durations == {"video": 60.0}
    assert sensor.app_cache == {"Codex": ["Codex", "coding"]}
    assert sensor.observation_entries == [
        {"app": "video", "window_title": "Bilibili", "source": "vision"}
    ]


def test_reconcile_screen_counts_caps_polluted_counts_to_raw_live2d_events(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    sensor.current_day = "2026-07-09"
    sensor.daily_counts = {
        "browser": 116,
        "linuxdo-accelerator.exe": 67,
        "Codex.exe": 1,
        "ghost.exe": 9,
    }

    raw_events = [
        {
            "event_id": f"chrome-{idx}",
            "ts": f"2026-07-09T10:00:0{idx}+08:00",
            "kind": "foreground_changed",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "chrome.exe"},
            "window_title": "Docs - Google Chrome",
            "browser": {},
        }
        for idx in range(3)
    ]
    raw_events.extend(
        {
            "event_id": f"linuxdo-{idx}",
            "ts": f"2026-07-09T10:01:0{idx}+08:00",
            "kind": "foreground_changed",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "linuxdo-accelerator.exe"},
            "window_title": "Linux.do Accelerator",
            "browser": {},
        }
        for idx in range(2)
    )
    raw_events.append(
        {
            "event_id": "codex",
            "ts": "2026-07-09T10:02:00+08:00",
            "kind": "foreground_changed",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        }
    )

    class Store:
        def list_activity_events(self, *, limit=200, date_str="", source=""):
            assert date_str == "2026-07-09"
            assert source == "live2d-tauri"
            return raw_events

    monkeypatch.setattr(screen_sensor_module, "get_memory_store", lambda: Store())
    sensor._analyze_window_context = lambda app="", title="", domain="": (
        "browser",
        "browser",
    ) if app == "chrome.exe" else ("other", app)

    sensor._reconcile_daily_counts_with_rust_events()

    # 连续同一 app 的 raw foreground 只算 1 个会话；linuxdo 两次事件若无夹别的 app 也只算 1
    # 本 fixture 里 chrome×3 连续 → browser 会话 cap=1；linuxdo×2 连续 → 1；但中间无切换间隔时
    # 会话重建按「切离再回来」计：chrome 段 1 次，linuxdo 段 1 次，codex 1 次。
    # 若 linuxdo 两条连续同 app，cap=1；与旧「raw 条数」不同。
    assert sensor.daily_counts == {
        "browser": 1,
        "linuxdo-accelerator.exe": 1,
        "Codex.exe": 1,
    }


def test_app_session_count_debounces_brief_refocus():
    sensor = ScreenSensor(DummyChatService())
    sensor.daily_counts = {}
    sensor._app_last_left_ts = {}

    assert sensor._maybe_increment_app_session_count("chrome.exe", event_ts=1000.0) is True
    assert sensor.daily_counts["chrome.exe"] == 1

    # 离开 chrome
    sensor._app_last_left_ts["chrome.exe"] = 1010.0
    # 60 秒内回来 → 同会话，不计次
    assert sensor._maybe_increment_app_session_count("chrome.exe", event_ts=1070.0) is False
    assert sensor.daily_counts["chrome.exe"] == 1

    sensor._app_last_left_ts["chrome.exe"] = 1100.0
    # 离开超过 90 秒再回来 → 新会话
    assert sensor._maybe_increment_app_session_count("chrome.exe", event_ts=1200.0) is True
    assert sensor.daily_counts["chrome.exe"] == 2


def test_count_sessions_from_foreground_ignores_rapid_bounce():
    sensor = ScreenSensor(DummyChatService())
    sensor._analyze_window_context = lambda app="", title="", domain="": ("browser", app)
    sensor._is_ignored_rust_screen_event = lambda **kwargs: False
    sensor._is_polluted_stats_key = lambda name: False
    sensor._parse_rust_event_ts = lambda item: float(item.get("_ts") or 0)

    # 同一页面挂机：chrome 与别的窗口快速来回，不应把 chrome 吹成十几次
    events = []
    t = 1000.0
    for i in range(6):
        events.append(
            {
                "kind": "foreground_changed",
                "app": {"name": "chrome.exe"},
                "window_title": "Docs",
                "browser": {},
                "_ts": t,
            }
        )
        t += 5
        events.append(
            {
                "kind": "foreground_changed",
                "app": {"name": "SearchHost.exe"},
                "window_title": "Search",
                "browser": {},
                "_ts": t,
            }
        )
        t += 5  # 来回间隔 5s，远小于 90s 会话阈值

    caps = sensor._count_sessions_from_foreground_events(events)
    # 首次 chrome + 首次 search，之后来回都在 gap 内 → 各 1 次
    assert caps.get("chrome.exe") == 1
    assert caps.get("SearchHost.exe") == 1


def test_formatted_report_uses_session_wording():
    sensor = ScreenSensor(DummyChatService())
    sensor.daily_counts = {"chrome.exe": 2}
    sensor.daily_durations = {"chrome.exe": 120.0}
    sensor.observation_entries = []
    sensor.activity_segments = []
    report = sensor.get_formatted_report()
    assert "段会话" in report
    assert "打开" not in report
    # 旧口径「(2 次)」不应再出现
    assert "(2 次)" not in report
    assert "(2 段会话)" in report


def test_session_gap_respects_config_60s(monkeypatch):
    monkeypatch.setattr(
        screen_sensor_module.config, "SCREEN_APP_SESSION_REOPEN_GAP_SEC", 60.0
    )
    sensor = ScreenSensor(DummyChatService())
    sensor.daily_counts = {}
    sensor._app_last_left_ts = {}

    assert sensor._maybe_increment_app_session_count("app.exe", event_ts=1000.0) is True
    sensor._app_last_left_ts["app.exe"] = 1000.0
    # 50s < 60s → 同会话
    assert sensor._maybe_increment_app_session_count("app.exe", event_ts=1050.0) is False
    sensor._app_last_left_ts["app.exe"] = 1000.0
    # 70s >= 60s → 新会话
    assert sensor._maybe_increment_app_session_count("app.exe", event_ts=1070.0) is True
    assert sensor.daily_counts["app.exe"] == 2


def test_session_gap_respects_config_180s(monkeypatch):
    monkeypatch.setattr(
        screen_sensor_module.config, "SCREEN_APP_SESSION_REOPEN_GAP_SEC", 180.0
    )
    sensor = ScreenSensor(DummyChatService())
    sensor.daily_counts = {}
    sensor._app_last_left_ts = {}

    assert sensor._maybe_increment_app_session_count("app.exe", event_ts=1000.0) is True
    sensor._app_last_left_ts["app.exe"] = 1000.0
    # 120s < 180s → 同会话（默认 90 时会算新会话）
    assert sensor._maybe_increment_app_session_count("app.exe", event_ts=1120.0) is False
    assert sensor.daily_counts["app.exe"] == 1
    sensor._app_last_left_ts["app.exe"] = 1000.0
    assert sensor._maybe_increment_app_session_count("app.exe", event_ts=1200.0) is True
    assert sensor.daily_counts["app.exe"] == 2
