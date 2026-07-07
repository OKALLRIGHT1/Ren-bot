import tempfile
import socket
import urllib.request
from pathlib import Path

import pytest

from integrations.gui_http import GuiHttpServer
import modules.memory_sqlite as memory_sqlite


def _new_store(tmp_path: Path):
    old_profile = memory_sqlite.LEGACY_PROFILE_JSON
    old_events = memory_sqlite.LEGACY_EVENTS_DB
    memory_sqlite.LEGACY_PROFILE_JSON = str(tmp_path / "missing_profile.json")
    memory_sqlite.LEGACY_EVENTS_DB = str(tmp_path / "missing_events.db")
    try:
        return memory_sqlite.MemorySQLite(str(tmp_path / "memory.db"))
    finally:
        memory_sqlite.LEGACY_PROFILE_JSON = old_profile
        memory_sqlite.LEGACY_EVENTS_DB = old_events


def _close_store(store) -> None:
    conn = getattr(getattr(store, "_local", None), "conn", None)
    if conn is not None:
        conn.close()
        delattr(store._local, "conn")


def _reserve_port_pair():
    for port in range(18097, 18140):
        first = socket.socket()
        second = socket.socket()
        try:
            first.bind(("127.0.0.1", port))
            second.bind(("127.0.0.1", port + 1))
            second.close()
            return port, first
        except OSError:
            first.close()
            second.close()
    raise RuntimeError("no free test port pair")


def _reserve_port_range(count: int):
    for port in range(18150, 18280):
        sockets = []
        try:
            for offset in range(count):
                sock = socket.socket()
                sock.bind(("127.0.0.1", port + offset))
                sockets.append(sock)
            return port, sockets
        except OSError:
            for sock in sockets:
                sock.close()
    raise RuntimeError("no free test port range")


def test_gui_http_server_falls_back_when_activity_port_unavailable():
    port, occupied = _reserve_port_pair()
    server = GuiHttpServer(host="127.0.0.1", port=port, access_token="secret")
    try:
        server.start()
        assert server.port == port + 1
        assert server.activity_ingest_url().endswith(
            f":{port + 1}/gui/activity-ingest"
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.port}/gui/health", timeout=3
        ) as response:
            assert response.status == 200
    finally:
        server.stop()
        occupied.close()


def test_gui_http_server_uses_os_port_when_fixed_fallback_range_unavailable():
    fallback_count = GuiHttpServer.PORT_FALLBACK_COUNT + 1
    port, occupied = _reserve_port_range(fallback_count)
    server = GuiHttpServer(host="127.0.0.1", port=port, access_token="secret")
    try:
        server.start()
        assert server.port not in {port + offset for offset in range(fallback_count)}
        assert server.port > 0
        assert server.activity_ingest_url().endswith(
            f":{server.port}/gui/activity-ingest"
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.port}/gui/health", timeout=3
        ) as response:
            assert response.status == 200
    finally:
        server.stop()
        for sock in occupied:
            sock.close()


def test_activity_sample_updates_latest_without_growing_history():
    with tempfile.TemporaryDirectory(prefix="activity_latest_") as tmp:
        store = _new_store(Path(tmp))
        try:
            first = {
                "event_id": "sample-1",
                "ts": "2026-07-04T11:30:00+00:00",
                "kind": "activity_sample",
                "presence": "active",
                "source": "live2d-tauri",
                "app": {"name": "Code.exe"},
                "sedentary": {"active_minutes": 1, "rest_streak": 0},
            }
            second = {
                "event_id": "sample-2",
                "ts": "2026-07-04T11:31:00+00:00",
                "kind": "activity_sample",
                "presence": "active",
                "source": "live2d-tauri",
                "app": {"name": "Chrome.exe"},
                "sedentary": {"active_minutes": 2, "rest_streak": 0},
            }

            assert store.ingest_activity_event(first) == {
                "latest": True,
                "historized": False,
            }
            assert store.ingest_activity_event(second) == {
                "latest": True,
                "historized": False,
            }

            assert store.list_activity_events(limit=10, source="live2d-tauri") == []
            latest = store.get_latest_activity_event(source="live2d-tauri")
            assert latest["event_id"] == "sample-2"
            assert latest["app"]["name"] == "Chrome.exe"
            assert latest["sedentary"]["active_minutes"] == 2
        finally:
            _close_store(store)


def test_activity_ingest_keeps_meaningful_events_in_history_and_latest():
    with tempfile.TemporaryDirectory(prefix="activity_events_") as tmp:
        store = _new_store(Path(tmp))
        try:
            event = {
                "event_id": "switch-1",
                "ts": "2026-07-04T11:32:00+00:00",
                "kind": "foreground_changed",
                "presence": "active",
                "source": "live2d-tauri",
                "app": {"name": "Code.exe"},
                "sedentary": {"active_minutes": 3, "rest_streak": 0},
            }

            assert store.ingest_activity_event(event) == {
                "latest": True,
                "historized": True,
            }

            rows = store.list_activity_events(limit=10, source="live2d-tauri")
            assert [row["event_id"] for row in rows] == ["switch-1"]
            latest = store.get_latest_activity_event(source="live2d-tauri")
            assert latest["event_id"] == "switch-1"
        finally:
            _close_store(store)


@pytest.mark.asyncio
async def test_activity_ingest_requests_work_session_status_refresh():
    class Request:
        async def json(self, *, loads):
            return {
                "event_id": "sample-refresh",
                "kind": "activity_sample",
                "source": "live2d-tauri",
                "sedentary": {"active_minutes": 1},
            }

    class Store:
        def ingest_activity_event(self, payload):
            return {"latest": True, "historized": False}

    class Ui:
        def __init__(self):
            self.refresh_requests = 0

        def request_work_session_status_refresh(self):
            self.refresh_requests += 1

    class App:
        def __init__(self):
            self.qt_ui = Ui()

    app = App()
    server = GuiHttpServer(app_ref=app)
    server._get_memory_store = lambda: Store()

    response = await server._handle_activity_ingest(Request())

    assert response.status == 200
    assert app.qt_ui.refresh_requests == 1
