import time
from datetime import datetime

import modules.screen_sensor as screen_sensor_module
from modules.gui.app import (
    WORK_SESSION_EMPTY_LABEL,
    WORK_SESSION_LABEL_WIDTH,
    WORK_SESSION_REFRESH_INTERVAL_MS,
    format_work_session_label,
)
from modules.screen_sensor import ScreenSensor


class DummyChatService:
    async def send_active_alert(self, app_name, active_minutes):
        return None


def test_format_work_session_label_uses_minutes():
    assert format_work_session_label({"active_minutes": 42, "app_name": "Code"}) == (
        "久坐时间 42 分钟"
    )


def test_format_work_session_label_shows_sub_minute_session():
    assert format_work_session_label({"active_seconds": 20, "app_name": "Code"}) == (
        "久坐时间 <1 分钟"
    )


def test_work_session_empty_label_is_visible_placeholder():
    assert WORK_SESSION_EMPTY_LABEL == "久坐时间 --"


def test_work_session_label_width_is_stable_for_top_bar():
    assert WORK_SESSION_LABEL_WIDTH >= 150


def test_work_session_refresh_interval_is_responsive():
    assert WORK_SESSION_REFRESH_INTERVAL_MS <= 10_000


def test_format_work_session_label_shows_collecting_for_empty_sensor_session():
    assert format_work_session_label(
        {"active_seconds": 0, "active_minutes": 0, "source": "rust-agent"}
    ) == "久坐时间 采集中"


def test_format_work_session_label_shows_zero_for_resting_session():
    assert format_work_session_label(
        {
            "active_seconds": 0,
            "active_minutes": 0,
            "source": "live2d-tauri",
            "state": "resting",
        }
    ) == "久坐时间 0 分钟"


def test_format_work_session_label_uses_hours_when_long():
    assert format_work_session_label({"active_minutes": 135, "app_name": "PyCharm"}) == (
        "久坐时间 2 小时 15 分钟"
    )


def test_screen_sensor_current_work_session_prefers_rust_sedentary_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 125
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "newest",
            "ts": _iso(now - 10),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 42,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
            },
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 42
    assert session["active_seconds"] == 42 * 60
    assert session["source"] == "live2d-sedentary"


def test_screen_sensor_current_work_session_accepts_live2d_tauri_sedentary_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "live2d-tauri",
            "ts": _iso(now - 5),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 17,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
            },
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 17
    assert session["source"] == "live2d-sedentary"


def test_screen_sensor_current_work_session_falls_back_to_latest_payload(monkeypatch):
    now = time.time()
    sensor = ScreenSensor(DummyChatService())
    latest = {
        "event_id": "live2d-latest",
        "ts": datetime.fromtimestamp(now).isoformat(),
        "kind": "activity_sample",
        "presence": "active",
        "source": "live2d-tauri",
        "sedentary": {
            "active_minutes": 5,
            "window_minutes": 60,
            "break_minutes": 5,
            "cooldown_minutes": 60,
            "rest_streak": 0,
        },
    }

    monkeypatch.setattr(sensor, "_current_rust_sedentary_session", lambda _now: None)
    monkeypatch.setattr(sensor, "_recent_rust_events", lambda limit=1: [latest])

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 5
    assert session["active_seconds"] == 300
    assert session["source"] == "live2d-sedentary"


def test_screen_sensor_parses_rust_nanosecond_rfc3339_timestamp():
    sensor = ScreenSensor(DummyChatService())

    parsed = sensor._parse_rust_event_ts(
        {"ts": "2026-07-04T14:49:04.595031900+00:00"}
    )

    assert parsed > 0


def test_screen_sensor_recent_rust_events_are_not_limited_to_local_date(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    sensor.current_day = "2026-06-24"
    calls = []

    class FakeStore:
        def list_activity_events(self, *, limit, date_str="", source=""):
            calls.append({"source": source, "date_str": date_str})
            if date_str:
                return []
            return [
                {
                    "event_id": source,
                    "ts": "2026-06-23T16:05:00+00:00",
                    "kind": "activity_sample",
                    "presence": "active",
                    "source": source,
                    "app": {"name": "Code.exe"},
                    "window_title": "Code",
                    "browser": {},
                }
            ]

    monkeypatch.setattr(screen_sensor_module, "get_memory_store", lambda: FakeStore())

    events = sensor._recent_rust_events(limit=5)

    assert {event["source"] for event in events} == {"live2d-tauri"}
    assert calls
    assert all(call["date_str"] == "" for call in calls)


def test_screen_sensor_recent_rust_events_include_latest_activity_state(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()

    class FakeStore:
        def get_latest_activity_event(self, *, source=""):
            assert source == "live2d-tauri"
            return {
                "event_id": "latest-sample",
                "ts": _iso(now),
                "kind": "activity_sample",
                "presence": "active",
                "source": "live2d-tauri",
                "app": {"name": "Code.exe"},
                "window_title": "Code",
                "browser": {},
                "sedentary": {
                    "active_minutes": 9,
                    "window_minutes": 60,
                    "break_minutes": 5,
                    "cooldown_minutes": 60,
                    "boundary_minute": int(now // 60),
                    "rest_streak": 0,
                },
            }

        def list_activity_events(self, *, limit, date_str="", source=""):
            return [
                {
                    "event_id": "switch-1",
                    "ts": _iso(now - 30),
                    "kind": "foreground_changed",
                    "presence": "active",
                    "source": source,
                    "app": {"name": "Chrome.exe"},
                    "window_title": "Chrome",
                    "browser": {},
                }
            ]

    monkeypatch.setattr(screen_sensor_module, "get_memory_store", lambda: FakeStore())

    events = sensor._recent_rust_events(limit=5)
    session = sensor.get_current_work_session(now_ts=now)

    assert [event["event_id"] for event in events] == ["latest-sample", "switch-1"]
    assert session["active_minutes"] == 9


def test_screen_sensor_live2d_tauri_session_ignores_older_sidecar_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 2 * 60 * 60
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "live2d-new",
            "ts": _iso(now - 5),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 2,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        },
        {
            "event_id": "sidecar-old",
            "ts": _iso(now - 90),
            "kind": "activity_sample",
            "presence": "active",
            "source": "rust-agent",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 38,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now - 90) // 60),
                "rest_streak": 0,
            },
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 2
    assert session["active_seconds"] == 2 * 60
    assert session["source"] == "live2d-sedentary"


def test_screen_sensor_current_work_session_does_not_bridge_rust_payload_after_short_restart():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 1800
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "after-restart",
            "ts": _iso(now - 4),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 1,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        },
        {
            "event_id": "before-restart",
            "ts": _iso(now - 80),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 29,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now - 80) // 60),
                "rest_streak": 0,
            },
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 1
    assert session["source"] == "live2d-sedentary"


def test_screen_sensor_without_live2d_sedentary_payload_stays_collecting():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = 0.0
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "newest",
            "ts": _iso(now - 12),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert session["source"] == "live2d-tauri"


def test_screen_sensor_local_timer_resets_after_rust_confirmed_break():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 3600
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "idle-now",
            "ts": _iso(now - 5),
            "kind": "activity_sample",
            "presence": "idle",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 0,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 5,
            },
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert session["source"] == "live2d-tauri"


def test_screen_sensor_current_work_session_ignores_existing_window_start_time():
    sensor = ScreenSensor(DummyChatService())
    sensor._recent_rust_events = lambda limit=120: []
    sensor._recent_activity_events = lambda limit=120, source="": []
    sensor.current_window_start_time = time.time() - 125
    sensor.last_app_name = "Code"
    sensor.last_category = "coding"

    session = sensor.get_current_work_session(now_ts=time.time())

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert session["app_name"] == "电脑"
    assert session["category"] == "computer_active"


def test_screen_sensor_sedentary_alert_minutes_use_computer_session_not_window_stay():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor._recent_rust_events = lambda limit=120: []
    sensor._recent_activity_events = lambda limit=120, source="": []
    sensor.sedentary_session_start_ts = now - 3700
    sensor.current_window_start_time = now - 120

    assert sensor._sedentary_alert_minutes(now) == 0


def test_screen_sensor_rust_foreground_switch_does_not_reset_sedentary_alert_timer():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    original_next_alert = now + 5
    sensor.next_sedentary_alert_time = original_next_alert
    sensor.sedentary_session_start_ts = now - 3500
    sensor._try_trigger_reaction = lambda *args, **kwargs: None
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "switch-code",
            "ts": _iso(now - 1),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        }
    ]

    sensor._process_rust_events_for_reaction(now)

    assert sensor.next_sedentary_alert_time == original_next_alert


def test_screen_sensor_counts_same_app_title_changes_once():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    reactions = []
    sensor.daily_counts.clear()
    sensor.daily_durations.clear()
    sensor._analyze_window_context = lambda app="", title="", domain="": (
        "game",
        app or title or "unknown",
    )
    sensor._try_trigger_reaction = lambda *args, **kwargs: reactions.append(
        (args, kwargs)
    )
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "game-title-2",
            "ts": _iso(now - 1),
            "kind": "foreground_changed",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Game.exe"},
            "window_title": "Game - scene 2",
            "browser": {},
        },
        {
            "event_id": "game-title-1",
            "ts": _iso(now - 2),
            "kind": "foreground_changed",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Game.exe"},
            "window_title": "Game - scene 1",
            "browser": {},
        },
    ]

    sensor._process_rust_events_for_reaction(now)

    assert sensor.daily_counts.get("Game.exe") == 1
    assert len(reactions) == 1


def test_screen_sensor_rust_activity_sample_does_not_trigger_sedentary_popup(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    calls = []
    sensor.use_rust_events_only = True
    sensor.next_sedentary_alert_time = now - 1
    sensor._sedentary_startup_grace_until = now - 3 * 60 * 60
    sensor.sedentary_interval_sec = 60 * 60
    sensor.sedentary_cooldown_sec = 60 * 60
    sensor._try_trigger_reaction = lambda *args, **kwargs: None
    sensor.set_sedentary_popup_callback(
        lambda app_name, minutes, image_path="", on_result=None: calls.append(
            (app_name, minutes)
        )
    )
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "sample-due",
            "ts": _iso(now),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 61,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        }
    ]
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)

    sensor._process_rust_events_for_reaction(now)

    assert calls == []
    assert sensor.next_sedentary_alert_time == now - 1

    sensor._last_rust_event_id = ""
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "sample-cooldown",
            "ts": _iso(now + 30),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 62,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now + 30) // 60),
                "rest_streak": 0,
            },
        }
    ]

    sensor._process_rust_events_for_reaction(now + 30)

    assert calls == []


def test_screen_sensor_keeps_single_sedentary_popup_in_flight(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    calls = []
    result_callbacks = []
    sensor._sedentary_startup_grace_until = now - 1
    sensor.sedentary_cooldown_sec = 60 * 60
    sensor._loop = None

    def popup_callback(app_name, minutes, image_path="", on_result=None):
        calls.append((app_name, minutes))
        result_callbacks.append(on_result)

    sensor.set_sedentary_popup_callback(popup_callback)
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)

    sensor._trigger_sedentary_alert(
        now_ts=now,
        alert_app_name="电脑",
        active_minutes=60,
        app_name="Code.exe",
        category="coding",
        full_title="main.py",
        source="live2d-tauri",
        log_label="test",
    )
    sensor._trigger_sedentary_alert(
        now_ts=now + 2 * 60 * 60,
        alert_app_name="电脑",
        active_minutes=120,
        app_name="Code.exe",
        category="coding",
        full_title="main.py",
        source="live2d-tauri",
        log_label="test",
    )

    assert calls == [("电脑", 60)]
    assert len(result_callbacks) == 1


def test_screen_sensor_dismiss_sedentary_popup_starts_cooldown(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    dismissed_at = now + 30
    result_callbacks = []
    sensor._sedentary_startup_grace_until = now - 1
    sensor.sedentary_cooldown_sec = 60 * 60
    sensor._loop = None

    def popup_callback(app_name, minutes, image_path="", on_result=None):
        result_callbacks.append(on_result)

    sensor.set_sedentary_popup_callback(popup_callback)
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)
    monkeypatch.setattr(screen_sensor_module.time, "time", lambda: dismissed_at)

    sensor._trigger_sedentary_alert(
        now_ts=now,
        alert_app_name="电脑",
        active_minutes=60,
        app_name="Code.exe",
        category="coding",
        full_title="main.py",
        source="live2d-tauri",
        log_label="test",
    )
    result_callbacks[0]("dismiss")

    assert sensor.next_sedentary_alert_time == dismissed_at + sensor.sedentary_cooldown_sec


def test_screen_sensor_rust_activity_sample_does_not_catch_up_after_main_restart(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    calls = []
    sensor.use_rust_events_only = True
    sensor.next_sedentary_alert_time = now + sensor.sedentary_interval_sec
    sensor._sedentary_startup_grace_until = now - 1
    sensor._try_trigger_reaction = lambda *args, **kwargs: None
    sensor.set_sedentary_popup_callback(
        lambda app_name, minutes, image_path="", on_result=None: calls.append(
            (app_name, minutes)
        )
    )
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "sample-after-restart",
            "ts": _iso(now),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 107,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        }
    ]
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)

    sensor._process_rust_events_for_reaction(now)

    assert calls == []


def test_screen_sensor_current_work_session_ignores_python_events_across_apps():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = False
    sensor.current_window_start_time = now - 10
    sensor.last_app_name = "Browser"
    sensor.last_category = "browser"
    sensor._recent_rust_events = lambda limit=120: []
    sensor._recent_activity_events = lambda limit=120, source=None: [
        {
            "event_id": "browser",
            "ts": _iso(now - 10),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "Chrome"},
            "app_name": "Chrome",
            "window_title": "Search - Google Chrome",
            "browser": {},
            "domain": "",
        },
        {
            "event_id": "codex",
            "ts": _iso(now - 70),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "Codex"},
            "app_name": "Codex",
            "window_title": "Codex",
            "browser": {},
            "domain": "",
        },
        {
            "event_id": "im",
            "ts": _iso(now - 190),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "QQ"},
            "app_name": "QQ",
            "window_title": "Chat",
            "browser": {},
            "domain": "",
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert session["source"] == "live2d-tauri"


def test_screen_sensor_current_work_session_ignores_rust_events_without_sedentary_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.current_window_start_time = now - 10
    sensor.last_app_name = "Live2D-Suzu"
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "gui",
            "ts": _iso(now - 10),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "python.exe"},
            "window_title": "Live2D Agent",
            "browser": {},
        },
        {
            "event_id": "codex",
            "ts": _iso(now - 70),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "chrome",
            "ts": _iso(now - 190),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "DeepSeek - Google Chrome",
            "browser": {},
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert session["source"] == "live2d-tauri"


def test_screen_sensor_current_work_session_requires_payload_during_short_rest():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.current_window_start_time = now - 600
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "new-active",
            "ts": _iso(now - 10),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle",
            "ts": _iso(now - 70),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "old-active",
            "ts": _iso(now - 190),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "DeepSeek - Google Chrome",
            "browser": {},
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert session["source"] == "live2d-tauri"


def test_screen_sensor_current_work_session_resets_after_valid_rust_break():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.current_window_start_time = now - 600
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "idle-now",
            "ts": _iso(now - 30),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-1",
            "ts": _iso(now - 90),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-2",
            "ts": _iso(now - 150),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-3",
            "ts": _iso(now - 210),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-4",
            "ts": _iso(now - 330),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "old-active",
            "ts": _iso(now - 390),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "DeepSeek - Google Chrome",
            "browser": {},
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0


def test_screen_sensor_current_work_session_does_not_reset_for_many_short_idle_samples():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.current_window_start_time = now - 600
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "idle-now",
            "ts": _iso(now - 8),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-1",
            "ts": _iso(now - 16),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-2",
            "ts": _iso(now - 24),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-3",
            "ts": _iso(now - 32),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "idle-4",
            "ts": _iso(now - 40),
            "kind": "activity_sample",
            "presence": "idle",
            "app": {"name": "Codex.exe"},
            "window_title": "Codex",
            "browser": {},
        },
        {
            "event_id": "old-active",
            "ts": _iso(now - 190),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "DeepSeek - Google Chrome",
            "browser": {},
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0


def test_screen_sensor_current_work_session_bridges_short_restart_gap_without_system_rest(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.current_window_start_time = now - 30
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "new-active",
            "ts": _iso(now - 30),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "python.exe"},
            "window_title": "Live2D Agent",
            "browser": {},
        },
        {
            "event_id": "old-active",
            "ts": _iso(now - 620),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "DeepSeek - Google Chrome",
            "browser": {},
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0


def test_screen_sensor_current_work_session_resets_after_long_event_gap(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 7200
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "new-active",
            "ts": _iso(now - 30),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "python.exe"},
            "window_title": "Live2D Agent",
            "browser": {},
        },
        {
            "event_id": "old-active",
            "ts": _iso(now - 3600),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "DeepSeek - Google Chrome",
            "browser": {},
        },
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0


def test_screen_sensor_window_context_does_not_call_ai_classifier():
    sensor = ScreenSensor(DummyChatService())

    assert not hasattr(screen_sensor_module, "chat_with_ai")

    category, app_name = sensor._analyze_window_context(
        app="unknown-tool.exe",
        title="Unknown Work Window",
        domain="",
    )

    assert category == "other"
    assert app_name == "unknown-tool.exe"


def test_screen_sensor_current_work_session_reads_enough_events_for_busy_switching():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    events = [
        {
            "event_id": f"busy-{idx}",
            "ts": _iso(now - idx),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
        }
        for idx in range(160)
    ]
    requested_limits = []

    def recent_rust_events(limit=120):
        requested_limits.append(limit)
        return events[:limit]

    sensor._recent_rust_events = recent_rust_events

    session = sensor.get_current_work_session(now_ts=now)

    assert max(requested_limits) > 120
    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0


def test_screen_sensor_does_not_restore_work_session_without_live2d_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "sample-2",
            "ts": _iso(now - 30),
            "kind": "activity_sample",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
        {
            "event_id": "sample-1",
            "ts": _iso(now - 90),
            "kind": "activity_sample",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
        {
            "event_id": "switch-1",
            "ts": _iso(now - 180),
            "kind": "foreground_changed",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
    ]

    assert not sensor.restore_recent_work_session(now_ts=now)


def test_screen_sensor_restore_ignores_recent_events_without_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.sedentary_session_start_ts = 0.0
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "sample-2",
            "ts": _iso(now - 30),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
        {
            "event_id": "sample-1",
            "ts": _iso(now - 90),
            "kind": "activity_sample",
            "presence": "active",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        },
    ]

    assert not sensor.restore_recent_work_session(now_ts=now)
    assert sensor.sedentary_session_start_ts == 0.0


def test_screen_sensor_restore_keeps_computer_session_only_from_live2d_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.sedentary_session_start_ts = 0.0
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "sample-code",
            "ts": _iso(now - 30),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 6,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        },
        {
            "event_id": "sample-browser",
            "ts": _iso(now - 210),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "chrome.exe"},
            "window_title": "Docs - Google Chrome",
            "browser": {"domain": "example.com"},
        },
        {
            "event_id": "sample-chat",
            "ts": _iso(now - 390),
            "kind": "foreground_changed",
            "presence": "active",
            "app": {"name": "QQ.exe"},
            "window_title": "Chat",
            "browser": {},
        },
    ]

    assert sensor.restore_recent_work_session(now_ts=now)
    session = sensor.get_current_work_session(now_ts=now)

    assert 359 <= int(now - sensor.sedentary_session_start_ts) <= 360
    assert session["active_minutes"] == 6
    assert session["app_name"] == "电脑"
    assert session["category"] == "computer_active"


def test_screen_sensor_restore_marks_existing_live2d_events_consumed(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    calls = []
    sensor.use_rust_events_only = True
    sensor.next_sedentary_alert_time = now - 1
    sensor._try_trigger_reaction = lambda *args, **kwargs: None
    sensor._analyze_window_context = lambda **kwargs: ("coding", "Code")
    sensor.set_sedentary_popup_callback(
        lambda app_name, minutes, image_path="", on_result=None: calls.append(
            (app_name, minutes)
        )
    )
    events = [
        {
            "event_id": "newest-sample",
            "ts": _iso(now - 10),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 65,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        },
        {
            "event_id": "old-alert-2",
            "ts": _iso(now - 70),
            "kind": "sedentary_alert",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 64,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now - 70) // 60),
                "rest_streak": 0,
            },
        },
        {
            "event_id": "old-alert-1",
            "ts": _iso(now - 130),
            "kind": "sedentary_alert",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 63,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now - 130) // 60),
                "rest_streak": 0,
            },
        },
    ]
    sensor._recent_rust_events = lambda limit=120: events[:limit]
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)

    assert sensor.restore_recent_work_session(now_ts=now)
    sensor._process_rust_events_for_reaction(now)

    assert calls == []
    assert sensor._last_rust_event_id == "newest-sample"


def test_screen_sensor_suppresses_sedentary_popup_during_startup_grace(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    calls = []
    sensor.use_rust_events_only = True
    sensor._sedentary_startup_grace_until = now + 60
    sensor.next_sedentary_alert_time = now - 1
    sensor._try_trigger_reaction = lambda *args, **kwargs: None
    sensor._analyze_window_context = lambda **kwargs: ("coding", "Code")
    sensor.set_sedentary_popup_callback(
        lambda app_name, minutes, image_path="", on_result=None: calls.append(
            (app_name, minutes)
        )
    )
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "startup-alert",
            "ts": _iso(now),
            "kind": "sedentary_alert",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 289,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        }
    ]
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)

    sensor._process_rust_events_for_reaction(now)

    assert calls == []
    assert sensor._last_alert_app == "电脑"
    assert abs(sensor.next_sedentary_alert_time - (now + sensor.sedentary_cooldown_sec)) < 0.01


def test_screen_sensor_processes_backlogged_sedentary_alerts_as_one_popup(monkeypatch):
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    calls = []
    sensor.use_rust_events_only = True
    sensor._sedentary_startup_grace_until = now - 3 * 60 * 60
    sensor.sedentary_cooldown_sec = 60 * 60
    sensor.next_sedentary_alert_time = now - 1
    sensor._try_trigger_reaction = lambda *args, **kwargs: None
    sensor._analyze_window_context = lambda **kwargs: ("coding", "Code")
    sensor.set_sedentary_popup_callback(
        lambda app_name, minutes, image_path="", on_result=None: calls.append(
            (app_name, minutes)
        )
    )
    sensor._recent_rust_events = lambda limit=20: [
        {
            "event_id": "alert-new",
            "ts": _iso(now - 10),
            "kind": "sedentary_alert",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 180,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now - 10) // 60),
                "rest_streak": 0,
            },
        },
        {
            "event_id": "alert-old",
            "ts": _iso(now - 2 * 60 * 60),
            "kind": "sedentary_alert",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 120,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int((now - 2 * 60 * 60) // 60),
                "rest_streak": 0,
            },
        },
    ]
    monkeypatch.setattr(screen_sensor_module.config, "DND_MODE", False)

    sensor._process_rust_events_for_reaction(now)

    assert calls == [("电脑", 120)]
    assert sensor.next_sedentary_alert_time == now + sensor.sedentary_cooldown_sec


def test_screen_sensor_does_not_restore_stale_restart_work_session():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    original_start = sensor.current_window_start_time
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "old-sample",
            "ts": _iso(now - 1200),
            "kind": "activity_sample",
            "app": {"name": "Code.exe"},
            "window_title": "main.py - Visual Studio Code",
            "browser": {},
        }
    ]

    assert not sensor.restore_recent_work_session(now_ts=now)
    assert sensor.current_window_start_time == original_start


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat()
