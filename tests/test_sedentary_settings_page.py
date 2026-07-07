import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.settings_pages import sedentary_page
from modules.gui.settings_pages.sedentary_page import SedentarySettingsPage


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_sedentary_settings_page_collects_and_saves_state(monkeypatch):
    _app()
    saved_patches = []
    applied_patches = []

    monkeypatch.setattr(
        sedentary_page,
        "load_runtime_settings",
        lambda: {
            "sedentary_reminder_minutes": 25,
            "sedentary_break_minutes": 7,
            "sedentary_cooldown_minutes": 12,
            "sedentary_popup_enabled": False,
            "sedentary_status_visible": False,
            "sedentary_popup_title": "休息一下",
            "sedentary_popup_message": "{app_name}:{active_minutes}",
            "sedentary_popup_image_path": "D:/meme.png",
            "sedentary_popup_snooze_minutes": 3,
            "sedentary_popup_auto_close_seconds": 8,
        },
    )
    monkeypatch.setattr(
        sedentary_page,
        "update_runtime_settings",
        lambda patch: saved_patches.append(dict(patch)),
    )

    main_app = SimpleNamespace(
        apply_external_settings=lambda patch: applied_patches.append(dict(patch)) or {}
    )
    page = SedentarySettingsPage(main_app=main_app)

    page.sedentary_reminder_minutes.setValue(30)
    page.sedentary_enabled.setChecked(True)
    page.sedentary_status_visible.setChecked(True)
    page.sedentary_popup_title.setText("")

    state = page.collect_state()

    assert state["sedentary_reminder_minutes"] == 30
    assert state["sedentary_break_minutes"] == 7
    assert state["sedentary_popup_enabled"] is True
    assert state["sedentary_status_visible"] is True
    assert state["sedentary_popup_title"]

    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None
    )

    page.save_state()

    assert saved_patches == [state]
    assert applied_patches == [state]
