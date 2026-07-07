import sys
import time
import types
from datetime import datetime


fake_llm = types.ModuleType("modules.llm")
fake_llm.chat_with_ai = lambda *args, **kwargs: ""
sys.modules.setdefault("modules.llm", fake_llm)

from modules.screen_sensor import ScreenSensor


class DummyChatService:
    async def send_active_alert(self, app_name, active_minutes):
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat()


def test_rejects_unverifiable_oversized_rust_sedentary_payload():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 120
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
                "active_minutes": 23 * 60,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] < 23 * 60
    assert session["source"] != "live2d-sedentary"


def test_trusted_rust_payload_replaces_stale_local_session_start():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 160 * 60 * 60
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "fresh-live2d",
            "ts": _iso(now - 5),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 5,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 5
    assert session["source"] == "live2d-sedentary"
    assert 299 <= int(now - sensor.sedentary_session_start_ts) <= 300


def test_untrusted_rust_payload_does_not_keep_stale_local_session_start():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.sedentary_session_start_ts = now - 160 * 60 * 60
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "fresh-live2d",
            "ts": _iso(now - 90),
            "kind": "activity_sample",
            "presence": "active",
            "source": "live2d-tauri",
            "app": {"name": "Code.exe"},
            "window_title": "Code",
            "browser": {},
            "sedentary": {
                "active_minutes": 160 * 60,
                "window_minutes": 60,
                "break_minutes": 5,
                "cooldown_minutes": 60,
                "boundary_minute": int(now // 60),
                "rest_streak": 0,
            },
        }
    ]

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] < 10
    assert session["source"] != "live2d-sedentary"
    assert sensor.sedentary_session_start_ts == 0.0


def test_no_fresh_events_reset_stale_local_session_start():
    sensor = ScreenSensor(DummyChatService())
    now = time.time()
    sensor.use_rust_events_only = True
    sensor.current_window_start_time = now - 168 * 60 * 60
    sensor.sedentary_session_start_ts = now - 168 * 60 * 60
    sensor._recent_rust_events = lambda limit=120: [
        {
            "event_id": "week-old",
            "ts": _iso(now - 7 * 24 * 60 * 60),
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
                "boundary_minute": int((now - 7 * 24 * 60 * 60) // 60),
                "rest_streak": 0,
            },
        }
    ]
    sensor._recent_activity_events = lambda limit=120, source="": []

    session = sensor.get_current_work_session(now_ts=now)

    assert session["active_minutes"] == 0
    assert session["active_seconds"] == 0
    assert sensor.sedentary_session_start_ts == 0.0
