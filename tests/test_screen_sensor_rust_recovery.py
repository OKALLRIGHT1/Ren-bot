import types

import modules.screen_sensor as screen_sensor_module
from modules.screen_sensor import ScreenSensor


class DummyChatService:
    async def send_active_alert(self, app_name, active_minutes):
        return None


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
