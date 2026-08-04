from integrations.gui_http import GuiHttpServer
from services.runtime_health import RuntimeHealthCenter


class _DummySensor:
    use_rust_events_only = True

    def get_current_work_session(self):
        return {
            "active_minutes": 12,
            "source": "live2d-sedentary",
            "app_name": "电脑",
        }

    def _recent_rust_events(self, limit=1):
        return [
            {
                "ts": "2026-06-22T07:29:21+00:00",
                "presence": "active",
                "sedentary": {
                    "active_minutes": 12,
                    "rest_streak": 0,
                    "break_minutes": 5,
                },
                "app": {"name": "Code.exe"},
            }
        ][:limit]


class _DummyApp:
    screen_sensor = _DummySensor()


def test_runtime_status_snapshot_reports_sensor_and_latest_live2d_event():
    server = GuiHttpServer(app_ref=_DummyApp())

    status = server._build_runtime_status()

    assert "activity_sidecar" not in status
    assert status["screen_sensor"]["bound"] is True
    assert status["screen_sensor"]["use_rust_events_only"] is True
    assert status["work_session"]["active_minutes"] == 12
    assert status["work_session"]["source"] == "live2d-sedentary"
    assert status["latest_rust_event"]["presence"] == "active"
    assert status["latest_rust_event"]["sedentary"]["active_minutes"] == 12


def test_runtime_status_snapshot_is_read_only_when_sensor_raises():
    class BrokenSensor:
        use_rust_events_only = False

        def get_current_work_session(self):
            raise RuntimeError("session unavailable")

        def _recent_rust_events(self, limit=1):
            raise RuntimeError("events unavailable")

    class App:
        screen_sensor = BrokenSensor()

    server = GuiHttpServer(app_ref=App())

    status = server._build_runtime_status()

    assert "activity_sidecar" not in status
    assert status["screen_sensor"]["bound"] is True
    assert status["screen_sensor"]["use_rust_events_only"] is False
    assert status["work_session"]["error"] == "session unavailable"
    assert status["latest_rust_event"]["error"] == "events unavailable"


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
