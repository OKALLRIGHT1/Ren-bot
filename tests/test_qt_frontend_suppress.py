from types import SimpleNamespace

from core.application import Live2DApplication
from modules.gui.app import QtChatTrayApp


def test_enhanced_hello_hides_qt_gui():
    calls = []
    app = Live2DApplication.__new__(Live2DApplication)
    app.qt_ui = SimpleNamespace(set_frontend_suppressed=lambda value: calls.append(value))
    app.gui_ws_server = SimpleNamespace(has_client=lambda name: name == "live2d-enhanced")
    app._keep_qt_gui_visible = lambda: False

    app._sync_qt_gui_visibility()

    assert calls == [True]


def test_keep_qt_gui_skips_hide():
    calls = []
    app = Live2DApplication.__new__(Live2DApplication)
    app.qt_ui = SimpleNamespace(set_frontend_suppressed=lambda value: calls.append(value))
    app.gui_ws_server = SimpleNamespace(has_client=lambda name: True)
    app._keep_qt_gui_visible = lambda: True

    app._sync_qt_gui_visibility()

    assert calls == []


def test_live2d_health_follows_enhanced_protocol_client():
    reports = []
    app = Live2DApplication.__new__(Live2DApplication)
    app.runtime_health = SimpleNamespace(
        report=lambda *args, **kwargs: reports.append((args, kwargs))
    )
    app.gui_ws_server = SimpleNamespace(
        has_capable_client=lambda cap: cap == "live2d.protocol.v1"
    )
    app._sync_live2d_frontend_health()
    assert reports
    assert reports[-1][0][:2] == ("live2d_ws", "healthy")

    app.gui_ws_server = SimpleNamespace(has_capable_client=lambda cap: False)
    app._sync_live2d_frontend_health()
    assert reports[-1][0][:2] == ("live2d_ws", "offline")


def test_disconnect_unsuppresses_qt_when_no_enhanced_client():
    calls = []
    app = Live2DApplication.__new__(Live2DApplication)
    app.qt_ui = SimpleNamespace(set_frontend_suppressed=lambda value: calls.append(value))
    app.gui_ws_server = SimpleNamespace(has_client=lambda name: False)
    app._keep_qt_gui_visible = lambda: False

    app._sync_qt_gui_visibility()

    assert calls == [False]


def test_apply_frontend_suppressed_hides_tray_keeps_remote_window():
    hidden = []
    tray = SimpleNamespace(
        hide=lambda: hidden.append("tray"),
        show=lambda: hidden.append("tray-show"),
    )
    ui = QtChatTrayApp.__new__(QtChatTrayApp)
    ui._frontend_suppressed = False
    ui._hotkey_listener = SimpleNamespace(stop=lambda: hidden.append("hotkey"))
    ui._settings_dialog = SimpleNamespace(hide=lambda: hidden.append("settings"))
    ui._win = SimpleNamespace(
        hide=lambda: hidden.append("win"),
        isVisible=lambda: True,
        show=lambda: hidden.append("win-show"),
        raise_=lambda: hidden.append("raise"),
        activateWindow=lambda: hidden.append("activate"),
    )
    ui._tray = tray
    ui.cfg = SimpleNamespace(start_minimized_to_tray=True)
    ui._start_global_listeners = lambda: hidden.append("hotkey-start")
    ui._hide_to_tray = lambda: hidden.append("hide-to-tray")
    ui.refresh_work_session_status = lambda: None
    ui._ensure_on_screen = lambda: None

    ui._apply_frontend_suppressed(True)
    assert ui._frontend_suppressed is True
    assert "win" not in hidden
    assert "win-show" in hidden
    assert "settings" not in hidden
    assert "tray" in hidden
    assert "hotkey" in hidden
    assert ui._hotkey_listener is None

    ui.toggle_show_hide()
    assert "win" in hidden

    ui._apply_frontend_suppressed(False)
    assert ui._frontend_suppressed is False
    assert "tray-show" in hidden
    assert "hotkey-start" in hidden


def test_show_hud_raises_existing_window():
    shown = []
    ui = QtChatTrayApp.__new__(QtChatTrayApp)
    ui._win = SimpleNamespace(
        show=lambda: shown.append("show"),
        raise_=lambda: shown.append("raise"),
        activateWindow=lambda: shown.append("activate"),
    )
    ui.refresh_work_session_status = lambda: shown.append("work")
    ui._ensure_on_screen = lambda: shown.append("onscreen")
    ui._show_hud_ui()
    assert shown[:3] == ["show", "raise", "activate"]
