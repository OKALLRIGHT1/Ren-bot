from integrations.gui_http import GuiHttpServer


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
