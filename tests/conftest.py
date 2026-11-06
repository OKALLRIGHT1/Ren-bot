"""Keep GUI tests independent of desktop input and interpreter teardown order."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        yield
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield
    # Dispose widgets while their Python overrides and Qt are both still alive.
    # Leaving them to interpreter finalization can abort Windows after pytest's
    # successful summary. Keep the application alive throughout the whole suite.
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
    import shiboken6
    shiboken6.delete(app)


@pytest.fixture(autouse=True)
def no_real_desktop_hotkeys(monkeypatch):
    try:
        from modules.gui.app import QtChatTrayApp
    except ImportError:
        return
    # UI tests must not install native keyboard hooks or leave pynput workers
    # invoking Qt signals as Python exits. Hotkey logic can use explicit fakes.
    monkeypatch.setattr(QtChatTrayApp, "_start_global_listeners", lambda self: None)
