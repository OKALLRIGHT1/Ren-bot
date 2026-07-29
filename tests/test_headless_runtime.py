import asyncio
import json
import threading
from types import SimpleNamespace

import core.application as application_module
from core.application import Live2DApplication
from integrations.gui_http import GuiHttpServer


def _make_run_app():
    app = Live2DApplication.__new__(Live2DApplication)
    app.logger = SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    app.initialize = lambda: None
    app.start_async_loop = lambda: None
    app.cleanup = lambda **_kwargs: None
    app._requested_exit_code = 0
    return app


def test_headless_backend_does_not_start_qt_or_tk(monkeypatch):
    app = _make_run_app()
    started = []
    app._run_headless = lambda: started.append("headless")
    app._run_qt_gui = lambda: (_ for _ in ()).throw(
        AssertionError("headless mode must not start Qt")
    )
    app._run_tk_gui = lambda: (_ for _ in ()).throw(
        AssertionError("headless mode must not start Tk")
    )
    monkeypatch.setattr(application_module.config, "GUI_BACKEND", "headless")

    app.run()

    assert started == ["headless"]


def test_headless_runtime_control_sets_exit_code_and_wakes_waiter():
    app = Live2DApplication.__new__(Live2DApplication)
    app._runtime_mode = "headless"
    app._headless_stop_event = threading.Event()
    app._requested_exit_code = 0

    ok, error = app.request_runtime_control("restart")

    assert ok is True
    assert error == ""
    assert app._requested_exit_code == 100
    assert app._headless_stop_event.is_set() is True


class _JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self, *, loads):
        return self.payload


def test_runtime_control_endpoint_delegates_to_application():
    calls = []

    class App:
        def request_runtime_control(self, action):
            calls.append(action)
            return True, ""

    server = GuiHttpServer(app_ref=App())
    response = asyncio.run(
        server._handle_runtime_control(_JsonRequest({"action": "shutdown"}))
    )

    assert response.status == 200
    assert json.loads(response.text) == {"ok": True, "action": "shutdown"}
    assert calls == ["shutdown"]


def test_runtime_control_endpoint_rejects_unknown_action():
    server = GuiHttpServer(app_ref=object())

    response = asyncio.run(
        server._handle_runtime_control(_JsonRequest({"action": "erase"}))
    )

    assert response.status == 400
    assert json.loads(response.text)["error"] == "invalid_action"
