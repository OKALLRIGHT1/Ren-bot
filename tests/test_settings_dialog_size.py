import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from modules.gui.dialogs.settings import SettingsDialog


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class FakeScreen:
    def availableGeometry(self):
        return QtCore.QRect(0, 0, 1920, 1080)


def test_settings_dialog_uses_compact_default_height():
    _app()
    with patch("modules.gui.dialogs.settings.QtWidgets.QApplication.primaryScreen") as screen, patch.object(
        SettingsDialog, "_safe_init_page", lambda *args, **kwargs: None
    ):
        screen.return_value = FakeScreen()
        dialog = SettingsDialog(parent=None, main_app=None)

    assert dialog.height() <= 620
    assert dialog.minimumHeight() == 320


def test_settings_dialog_can_be_clamped_to_screen():
    _app()
    with patch("modules.gui.dialogs.settings.QtWidgets.QApplication.primaryScreen") as screen, patch.object(
        SettingsDialog, "_safe_init_page", lambda *args, **kwargs: None
    ):
        screen.return_value = FakeScreen()
        dialog = SettingsDialog(parent=None, main_app=None)

    dialog.move(-200, -120)
    dialog.ensure_on_screen()

    assert dialog.frameGeometry().top() >= 0
    assert dialog.frameGeometry().left() >= 0


def test_settings_dialog_clamp_accounts_for_window_frame(monkeypatch):
    _app()
    with patch("modules.gui.dialogs.settings.QtWidgets.QApplication.primaryScreen") as screen, patch.object(
        SettingsDialog, "_safe_init_page", lambda *args, **kwargs: None
    ):
        screen.return_value = FakeScreen()
        dialog = SettingsDialog(parent=None, main_app=None)

    moves = []
    monkeypatch.setattr(dialog, "move", lambda x, y: moves.append((x, y)))
    monkeypatch.setattr(dialog, "geometry", lambda: QtCore.QRect(0, -6, 1040, 620))
    monkeypatch.setattr(dialog, "frameGeometry", lambda: QtCore.QRect(0, -36, 1040, 650))

    dialog.ensure_on_screen()

    assert moves[-1] == (8, 38)
