from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from modules.sensor_prompts import (
    PROMPT_FIELDS,
    default_sensor_prompts,
    format_template,
    load_sensor_prompts,
    save_sensor_prompts,
)
from services.chat_support.emotion_reply_service import live2d_emo_tag_choices


_PREVIEW_VALUES = {
    "clean_title": "Live2D Enhanced",
    "category": "self",
    "count": 1,
    "max_chars": 15,
    "active_name": "五十铃怜",
    "costume_line": "- 你当前穿着/模型服装：校服。",
    "description": "窗口里是代码编辑器，正在改 chat_service.py。",
    "context_block": "",
    "self_awareness_hint": "【Live2D自我识别】（预览）",
}


class SensorPromptsSettingsPage(QtWidgets.QWidget):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self._current_key = ""
        self._drafts = default_sensor_prompts()
        self._build_ui()
        self.load_state()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        hint = QtWidgets.QLabel(
            "屏幕吐槽用的提示词。改的是模板本身，保存后立即生效。"
            "花括号是运行时填入的变量，删掉就不会出现在实际提示词里。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("launchDesc")
        layout.addWidget(hint)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("settingsNav")
        self.list.setMinimumWidth(160)
        for field in PROMPT_FIELDS:
            item = QtWidgets.QListWidgetItem(field["title"])
            item.setData(QtCore.Qt.ItemDataRole.UserRole, field["key"])
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._on_row_changed)
        splitter.addWidget(self.list)

        editor_wrap = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(editor_wrap)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)

        self.title_label = QtWidgets.QLabel("")
        self.title_label.setObjectName("launchTitle")
        editor_layout.addWidget(self.title_label)

        self.desc_label = QtWidgets.QLabel("")
        self.desc_label.setObjectName("launchDesc")
        self.desc_label.setWordWrap(True)
        editor_layout.addWidget(self.desc_label)

        self.placeholder_label = QtWidgets.QLabel("")
        self.placeholder_label.setStyleSheet("color:#6B7280;")
        self.placeholder_label.setWordWrap(True)
        editor_layout.addWidget(self.placeholder_label)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setTabChangesFocus(False)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.editor.setFont(font)
        self.editor.textChanged.connect(self._update_preview)
        editor_layout.addWidget(self.editor, 3)

        preview_label = QtWidgets.QLabel("用示例变量展开后：")
        preview_label.setStyleSheet("color:#6B7280;")
        editor_layout.addWidget(preview_label)

        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(font)
        self.preview.setMaximumHeight(160)
        editor_layout.addWidget(self.preview, 2)

        splitter.addWidget(editor_wrap)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 640])
        layout.addWidget(splitter, 1)

        footer = QtWidgets.QHBoxLayout()
        restore_one = QtWidgets.QPushButton("恢复此项默认")
        restore_one.clicked.connect(self._restore_current)
        restore_all = QtWidgets.QPushButton("全部恢复默认")
        restore_all.clicked.connect(self._restore_all)
        save_btn = QtWidgets.QPushButton("保存提示词")
        save_btn.setObjectName("primaryAction")
        save_btn.clicked.connect(self.save_state)
        footer.addWidget(restore_one)
        footer.addWidget(restore_all)
        footer.addStretch(1)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def _flush_editor(self) -> None:
        if not self._current_key:
            return
        self._drafts[self._current_key] = self.editor.toPlainText()

    def _on_row_changed(self, row: int) -> None:
        self._flush_editor()
        item = self.list.item(row)
        if item is None:
            return
        key = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        self._show_key(key)

    def _show_key(self, key: str) -> None:
        field = next((item for item in PROMPT_FIELDS if item["key"] == key), None)
        if field is None:
            return
        self._current_key = key
        self.title_label.setText(field["title"])
        self.desc_label.setText(field["desc"])
        placeholders = field.get("placeholders") or "无运行时变量"
        self.placeholder_label.setText(f"变量：{placeholders}")
        self.editor.blockSignals(True)
        self.editor.setPlainText(self._drafts.get(key, field["default"]))
        self.editor.blockSignals(False)
        self._update_preview()

    def _preview_values(self) -> dict[str, str]:
        values = dict(_PREVIEW_VALUES)
        values["emo_tags"] = live2d_emo_tag_choices()
        return values

    def _update_preview(self) -> None:
        self.preview.setPlainText(
            format_template(self.editor.toPlainText(), **self._preview_values())
        )

    def load_state(self) -> None:
        self._drafts = load_sensor_prompts()
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        else:
            self._show_key(self._current_key)

    def collect_state(self) -> dict[str, str]:
        self._flush_editor()
        defaults = default_sensor_prompts()
        result = {}
        for key, default in defaults.items():
            text = str(self._drafts.get(key) or "").strip()
            result[key] = text or default
        return result

    def _restore_current(self) -> None:
        default = default_sensor_prompts().get(self._current_key, "")
        self._drafts[self._current_key] = default
        self.editor.setPlainText(default)

    def _restore_all(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "屏幕吐槽提示词",
            "把全部提示词恢复成程序默认？未保存的修改会丢掉。",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._drafts = default_sensor_prompts()
        self._show_key(self._current_key)

    def save_state(self) -> None:
        saved = save_sensor_prompts(self.collect_state())
        self._drafts = saved
        QtWidgets.QMessageBox.information(
            self,
            "屏幕吐槽提示词",
            "已保存。下一句屏幕吐槽就会用新稿，不用重启。",
        )
