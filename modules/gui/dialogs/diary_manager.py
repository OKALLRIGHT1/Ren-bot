from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from PySide6 import QtCore, QtWidgets

from modules.gui.styles import get_memory_dialog_styles
from modules.memory_sqlite import get_memory_store


class DiaryManagerDialog(QtWidgets.QDialog):
    """View and maintain daily diary episodes in their own window."""

    def __init__(self, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.embedded = bool(embedded)
        if self.embedded:
            self.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
        else:
            self.setWindowFlags(
                QtCore.Qt.WindowType.Window
                | QtCore.Qt.WindowType.WindowMinMaxButtonsHint
                | QtCore.Qt.WindowType.WindowCloseButtonHint
            )
        self.store = get_memory_store()
        self._rows: List[Dict[str, Any]] = []
        self._standalone_window = None

        self.setWindowTitle("日记管理")
        self.resize(980, 700)
        self.setStyleSheet(get_memory_dialog_styles())
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("搜索日期、标题或正文")
        self.search_edit.textChanged.connect(self.reload)
        controls.addWidget(self.search_edit, 1)

        refresh = QtWidgets.QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        controls.addWidget(refresh)
        export_button = QtWidgets.QPushButton("导出 Markdown")
        export_button.clicked.connect(self._export_markdown)
        controls.addWidget(export_button)
        if self.embedded:
            standalone = QtWidgets.QPushButton("独立窗口打开")
            standalone.clicked.connect(self._open_standalone)
            controls.addWidget(standalone)
        root.addLayout(controls)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.diary_list = QtWidgets.QListWidget()
        self.diary_list.currentRowChanged.connect(self._on_select)
        splitter.addWidget(self.diary_list)

        editor = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(editor)
        self.id_edit = QtWidgets.QLineEdit()
        self.id_edit.setReadOnly(True)
        form.addRow("记录 ID", self.id_edit)
        self.title_edit = QtWidgets.QLineEdit()
        form.addRow("标题", self.title_edit)
        self.tags_label = QtWidgets.QLabel()
        self.tags_label.setWordWrap(True)
        self.tags_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("标签", self.tags_label)
        self.summary_edit = QtWidgets.QPlainTextEdit()
        self.summary_edit.setMinimumHeight(420)
        form.addRow("正文", self.summary_edit)

        buttons = QtWidgets.QHBoxLayout()
        save_button = QtWidgets.QPushButton("保存修改")
        save_button.clicked.connect(self._save_current)
        buttons.addWidget(save_button)
        delete_button = QtWidgets.QPushButton("删除日记")
        delete_button.clicked.connect(self._delete_current)
        buttons.addWidget(delete_button)
        form.addRow(buttons)
        splitter.addWidget(editor)
        splitter.setSizes([320, 660])
        root.addWidget(splitter, 1)

    @staticmethod
    def _is_diary(row: Dict[str, Any]) -> bool:
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        return "daily_log" in {str(tag).strip() for tag in tags}

    def reload(self) -> None:
        query = self.search_edit.text().strip() if hasattr(self, "search_edit") else ""
        rows = self.store.list_episodes(status="active", query=query, limit=500)
        self._rows = [row for row in rows if self._is_diary(row)]
        self.diary_list.blockSignals(True)
        self.diary_list.clear()
        for row in self._rows:
            title = str(row.get("title") or "未命名日记")
            self.diary_list.addItem(title)
        self.diary_list.blockSignals(False)
        if self._rows:
            self.diary_list.setCurrentRow(0)
        else:
            self._clear_editor()

    def _on_select(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._rows):
            self._clear_editor()
            return
        row = self._rows[row_index]
        self.id_edit.setText(str(row.get("id") or ""))
        self.title_edit.setText(str(row.get("title") or ""))
        self.summary_edit.setPlainText(str(row.get("summary") or ""))
        self.tags_label.setText("、".join(str(tag) for tag in row.get("tags") or []))

    def _clear_editor(self) -> None:
        if not hasattr(self, "id_edit"):
            return
        self.id_edit.clear()
        self.title_edit.clear()
        self.summary_edit.clear()
        self.tags_label.clear()

    def _current_row(self) -> Dict[str, Any] | None:
        row_index = self.diary_list.currentRow()
        if row_index < 0 or row_index >= len(self._rows):
            return None
        return self._rows[row_index]

    def _save_current(self) -> None:
        row = self._current_row()
        if row is None:
            return
        title = self.title_edit.text().strip()
        summary = self.summary_edit.toPlainText().strip()
        if not title or not summary:
            QtWidgets.QMessageBox.warning(self, "无法保存", "标题和正文不能为空。")
            return
        payload = dict(row)
        payload.update({"title": title, "summary": summary})
        tags = [str(tag).strip() for tag in row.get("tags") or [] if str(tag).strip()]
        if "daily_log" not in tags:
            tags.append("daily_log")
        payload["tags"] = tags
        self.store.upsert_episode(payload)
        selected_id = str(row.get("id") or "")
        self.reload()
        for index, item in enumerate(self._rows):
            if str(item.get("id") or "") == selected_id:
                self.diary_list.setCurrentRow(index)
                break

    def _delete_current(self) -> None:
        row = self._current_row()
        if row is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定删除《{row.get('title') or '未命名日记'}》吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.store.delete_episode(str(row.get("id") or ""))
        self.reload()

    def _export_markdown(self) -> None:
        if not self._rows:
            QtWidgets.QMessageBox.information(self, "没有日记", "当前没有可导出的日记。")
            return
        default_path = Path("output") / f"Diary_Export_{datetime.now():%Y%m%d_%H%M}.md"
        default_path.parent.mkdir(parents=True, exist_ok=True)
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出日记",
            str(default_path.resolve()),
            "Markdown (*.md)",
        )
        if not path:
            return
        lines = [f"# 角色日记\n\n> 导出时间：{datetime.now():%Y-%m-%d %H:%M}\n"]
        for row in self._rows:
            lines.append(f"\n## {row.get('title') or '未命名日记'}\n")
            lines.append(str(row.get("summary") or "").strip())
            lines.append("\n\n---\n")
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        QtWidgets.QMessageBox.information(self, "导出完成", path)

    def _open_standalone(self) -> None:
        self._standalone_window = DiaryManagerDialog(embedded=False)
        self._standalone_window.show()
        self._standalone_window.raise_()
        self._standalone_window.activateWindow()
