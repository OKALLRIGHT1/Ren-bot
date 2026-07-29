from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.memory_sqlite import MemorySQLite


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_diary_manager_lists_only_daily_logs(tmp_path, monkeypatch):
    from modules.gui.dialogs import diary_manager

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    store.upsert_episode(
        {
            "id": "daily",
            "title": "2026-07-09 日记",
            "summary": "今天的正文",
            "tags": ["daily_log", "date:2026-07-09"],
        }
    )
    store.upsert_episode(
        {
            "id": "normal",
            "title": "普通事件",
            "summary": "不应该出现在日记窗口",
            "tags": ["event"],
        }
    )
    monkeypatch.setattr(diary_manager, "get_memory_store", lambda: store)

    dialog = diary_manager.DiaryManagerDialog(embedded=True)

    assert [row["id"] for row in dialog._rows] == ["daily"]
    assert dialog.diary_list.count() == 1
    dialog.close()


def test_diary_manager_saves_edited_entry(tmp_path, monkeypatch):
    from modules.gui.dialogs import diary_manager

    _app()
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    store.upsert_episode(
        {
            "id": "daily",
            "title": "2026-07-09 日记",
            "summary": "旧正文",
            "tags": ["daily_log", "date:2026-07-09"],
        }
    )
    monkeypatch.setattr(diary_manager, "get_memory_store", lambda: store)

    dialog = diary_manager.DiaryManagerDialog(embedded=True)
    dialog.diary_list.setCurrentRow(0)
    dialog.summary_edit.setPlainText("修改后的正文")
    dialog._save_current()

    assert store.get_episode("daily")["summary"] == "修改后的正文"
    dialog.close()
