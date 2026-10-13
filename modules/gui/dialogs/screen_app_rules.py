from __future__ import annotations

import re
from typing import Iterable

from PySide6 import QtCore, QtWidgets

from modules.screen_app_registry import (
    APP_RULES_PATH,
    AppCategoryRule,
    ScreenAppRegistry,
    save_rules,
)


CATEGORIES = ("coding", "gaming", "video", "social", "work", "design", "browser", "other", "self")


def _split_patterns(text: str) -> tuple[str, ...]:
    parts = re.split(r"[,;；，\n]+", str(text or ""))
    return tuple(part.strip() for part in parts if part.strip())


def _join_patterns(values: Iterable[str]) -> str:
    return "; ".join(str(item).strip() for item in values if str(item or "").strip())


class ScreenAppRulesDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self.registry = ScreenAppRegistry()
        self.setWindowTitle("应用识别规则")
        self.resize(920, 620)
        # 独立窗口可用下限；嵌入设置页时由 apply_embedded_mode 清零。
        self.setMinimumSize(640, 420)
        self._setup_ui()
        self._load_rules()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        self._main_layout = layout
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._title_label = QtWidgets.QLabel("应用识别规则")
        self._title_label.setStyleSheet("font-size: 20px; font-weight: 700; color: #111827;")
        layout.addWidget(self._title_label)

        self._desc_label = QtWidgets.QLabel(
            "屏幕传感器会先按这些本地规则识别 app/title/domain，再交给 AI 分类。"
            "关键词支持逗号、分号或换行分隔。保存后下一次屏幕事件会自动热加载。"
        )
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #4B5563; font-size: 12px;")
        layout.addWidget(self._desc_label)

        self.path_label = QtWidgets.QLabel(f"配置文件: {APP_RULES_PATH}")
        self.path_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(self.path_label)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["名称", "分类", "显示名", "App 关键词", "标题关键词", "域名关键词", "备注"]
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(68)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 92)
        self.table.setColumnWidth(1, 104)
        self.table.setColumnWidth(2, 92)
        self.table.setColumnWidth(5, 105)
        self.table.setColumnWidth(6, 130)
        layout.addWidget(self.table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_add = QtWidgets.QPushButton("新增规则")
        self.btn_add.clicked.connect(self._add_rule)
        btn_row.addWidget(self.btn_add)

        self.btn_delete = QtWidgets.QPushButton("删除选中")
        self.btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.btn_delete)

        self.btn_reload = QtWidgets.QPushButton("重新读取")
        self.btn_reload.clicked.connect(self._load_rules)
        btn_row.addWidget(self.btn_reload)

        btn_row.addStretch()

        self.btn_save = QtWidgets.QPushButton("保存规则")
        self.btn_save.setObjectName("primaryAction")
        self.btn_save.clicked.connect(self._save_rules)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

        hint = QtWidgets.QLabel(
            "例子：Endfield 可填 category=gaming，App 关键词=Endfield.exe；"
            "学习喵群聊可填 category=social，标题关键词=学习喵。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(hint)

    def set_embedded_mode(self, embedded: bool = True) -> None:
        is_emb = bool(embedded)
        if hasattr(self, "_title_label"):
            self._title_label.setVisible(not is_emb)
        if hasattr(self, "_desc_label"):
            self._desc_label.setVisible(not is_emb)
        if hasattr(self, "_main_layout"):
            m = 0 if is_emb else 16
            self._main_layout.setContentsMargins(m, m, m, m)
            self._main_layout.setSpacing(8 if is_emb else 10)

    def _load_rules(self):
        self.registry.reload(force=True)
        self.table.setRowCount(0)
        for rule in self.registry.rules:
            self._append_rule(rule)
        self._fit_columns()

    def _append_rule(self, rule: AppCategoryRule):
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = {
            0: rule.name,
            2: rule.display_name,
            3: _join_patterns(rule.app_contains),
            4: _join_patterns(rule.title_contains),
            5: _join_patterns(rule.domain_contains),
            6: rule.note,
        }
        for col, text in cells.items():
            item = QtWidgets.QTableWidgetItem(text)
            if text:
                item.setToolTip(text)
            self.table.setItem(row, col, item)
        self._set_category_combo(row, rule.category)

    def _set_category_combo(self, row: int, category: str):
        combo = QtWidgets.QComboBox()
        combo.addItems(CATEGORIES)
        idx = combo.findText(str(category or "other"))
        combo.setCurrentIndex(idx if idx >= 0 else combo.findText("other"))
        combo.setStyleSheet(
            "QComboBox { padding: 1px 6px; font-size: 12px; border-radius: 6px; }"
            "QComboBox::drop-down { width: 18px; }"
        )
        self.table.setCellWidget(row, 1, combo)

    def _add_rule(self):
        self._append_rule(
            AppCategoryRule(
                name="新规则",
                category="other",
                display_name="新应用",
                app_contains=(),
                title_contains=(),
                domain_contains=(),
                note="",
            )
        )
        self._fit_columns()

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.table.removeRow(row)

    def _collect_rules(self) -> list[AppCategoryRule]:
        rules: list[AppCategoryRule] = []
        for row in range(self.table.rowCount()):
            name = self._item_text(row, 0) or self._item_text(row, 2)
            if not name:
                continue
            combo = self.table.cellWidget(row, 1)
            category = combo.currentText() if isinstance(combo, QtWidgets.QComboBox) else "other"
            display_name = self._item_text(row, 2) or name
            rules.append(
                AppCategoryRule(
                    name=name,
                    category=category,
                    display_name=display_name,
                    app_contains=_split_patterns(self._item_text(row, 3)),
                    title_contains=_split_patterns(self._item_text(row, 4)),
                    domain_contains=_split_patterns(self._item_text(row, 5)),
                    note=self._item_text(row, 6),
                )
            )
        return rules

    def _save_rules(self):
        rules = self._collect_rules()
        if not rules:
            QtWidgets.QMessageBox.warning(self, "应用识别规则", "至少保留一条规则。")
            return
        try:
            save_rules(rules)
            self.registry.reload(force=True)
            QtWidgets.QMessageBox.information(self, "应用识别规则", "已保存，下一次屏幕事件会自动生效。")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "应用识别规则", f"保存失败: {exc}")

    def _item_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def _fit_columns(self):
        self.table.setColumnWidth(1, 104)
        for col in (0, 2):
            self.table.resizeColumnToContents(col)
            self.table.setColumnWidth(col, min(max(self.table.columnWidth(col), 76), 120))
        self.table.setColumnWidth(5, min(max(self.table.columnWidth(5), 96), 130))
        self.table.setColumnWidth(6, min(max(self.table.columnWidth(6), 110), 150))
