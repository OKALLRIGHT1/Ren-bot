import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.dialogs.settings import SettingsDialog
from modules.gui.user_ia import SETTINGS_PAGES


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _nav_texts(dialog):
    import re
    return [
        re.sub(r"^[^\w\u4e00-\u9fff]+\s*", "", dialog.nav_list.item(row).text())
        for row in range(dialog.nav_list.count())
    ]


def _stub_pages(monkeypatch):
    monkeypatch.setattr(
        SettingsDialog,
        "_safe_init_page",
        lambda self, fn, label: self.stack.addWidget(QtWidgets.QWidget()),
    )


def test_settings_nav_groups_and_hides_developer(monkeypatch):
    _app()
    monkeypatch.setattr("modules.gui.dialogs.settings.is_developer_mode", lambda: False)
    _stub_pages(monkeypatch)
    dialog = SettingsDialog(parent=None, main_app=None)
    texts = _nav_texts(dialog)
    assert "开始使用" in texts
    assert "形象与人设" in texts
    assert "聊天模型" in texts
    assert "能力" in texts
    assert "QQ" in texts
    assert "角色" in texts
    assert "对话" in texts
    assert "记忆与资料" in texts
    assert "依赖体检" not in texts
    assert "提供商" not in texts
    assert "状态屏" not in texts
    assert dialog._current_page_id == "start"
    assert set(dialog._page_stack_index) == {page["id"] for page in SETTINGS_PAGES}
    assert dialog.stack.count() == len(SETTINGS_PAGES)
    dialog.close()


def test_settings_nav_developer_toggle(monkeypatch):
    _app()
    monkeypatch.setattr("modules.gui.dialogs.settings.is_developer_mode", lambda: False)
    monkeypatch.setattr(
        "modules.gui.dialogs.settings.set_developer_mode", lambda enabled: {}
    )
    _stub_pages(monkeypatch)
    dialog = SettingsDialog(parent=None, main_app=None)
    assert "依赖体检" not in _nav_texts(dialog)
    monkeypatch.setattr("modules.gui.dialogs.settings.is_developer_mode", lambda: True)
    dialog._rebuild_nav(select_id="start")
    texts = _nav_texts(dialog)
    assert "开发者" in texts
    assert "依赖体检" in texts
    assert "提供商" in texts
    dialog.close()


def test_settings_nav_accordion_collapse(monkeypatch):
    _app()
    monkeypatch.setattr("modules.gui.dialogs.settings.is_developer_mode", lambda: True)
    _stub_pages(monkeypatch)
    dialog = SettingsDialog(parent=None, main_app=None)

    # Find group headers
    group_rows = {}
    for r in range(dialog.nav_list.count()):
        d = dialog._nav_item_data(r)
        if d and d.get("kind") == "group":
            group_rows[d["group"]] = r

    assert "character" in group_rows
    assert "developer" in group_rows

    # Collapse character group
    char_header = dialog.nav_list.item(group_rows["character"])
    dialog._on_nav_item_clicked(char_header)
    assert "character" in dialog._collapsed_groups
    assert dialog.nav_list.item(group_rows["character"] + 1).isHidden()

    # Now click the LAST group header (developer)
    dev_header = dialog.nav_list.item(group_rows["developer"])
    dialog._on_nav_item_clicked(dev_header)

    # Developer should now be collapsed, and character MUST STILL BE COLLAPSED!
    assert "developer" in dialog._collapsed_groups
    assert "character" in dialog._collapsed_groups, "Character group must not be reopened when clicking developer header"
    assert dialog.nav_list.item(group_rows["character"] + 1).isHidden()

    dialog.close()


def test_settings_nav_collapse_preserves_scroll_position(monkeypatch):
    _app()
    monkeypatch.setattr("modules.gui.dialogs.settings.is_developer_mode", lambda: True)
    _stub_pages(monkeypatch)
    dialog = SettingsDialog(parent=None, main_app=None)
    dialog.resize(800, 320)
    dialog.show()

    sb = dialog.nav_list.verticalScrollBar()
    assert sb is not None
    # Scroll down to max
    sb.setValue(sb.maximum())
    val_before = sb.value()
    assert val_before > 0

    group_rows = {}
    for r in range(dialog.nav_list.count()):
        d = dialog._nav_item_data(r)
        if d and d.get("kind") == "group":
            group_rows[d["group"]] = r

    dev_header = dialog.nav_list.item(group_rows["developer"])
    dialog._on_nav_item_clicked(dev_header)

    # Value should NOT jump to 0 (top)!
    val_after = sb.value()
    assert val_after > 0, "Collapse must not reset scrollbar to top"
    assert val_after <= sb.maximum()

    dialog.close()


def test_start_page_lists_missing_model(monkeypatch):
    _app()
    from modules.gui.settings_pages.start_page import StartSettingsPage

    monkeypatch.setattr(
        "modules.gui.settings_pages.start_page.chat_setup_status",
        lambda **_kwargs: {
            "character_ok": False,
            "character_name": "",
            "model_ok": False,
            "model_label": "还没有可用的聊天模型",
            "model_local_only": False,
            "live2d_state": "offline",
            "ready": False,
        },
    )
    page = StartSettingsPage(main_app=None)
    assert "还不能聊天" in page._status.text()
    assert page._btn_talk.isEnabled() is False
    page.close()


def test_open_page_by_id(monkeypatch):
    _app()
    monkeypatch.setattr("modules.gui.dialogs.settings.is_developer_mode", lambda: False)
    _stub_pages(monkeypatch)
    dialog = SettingsDialog(parent=None, main_app=None)
    dialog.open_page("memory")
    assert dialog._current_page_id == "memory"
    assert dialog.page_title.text() == "记忆"
    dialog.open_page("plugins")
    assert dialog._current_page_id == "plugins"
    dialog.close()
