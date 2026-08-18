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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("应用识别规则")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #111827;")
        layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "屏幕传感器会先按这些本地规则识别 app/title/domain，再交给 AI 分类。"
            "关键词支持逗号、分号或换行分隔。保存后下一次屏幕事件会自动热加载。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4B5563; font-size: 13px;")
        layout.addWidget(desc)

        self.path_label = QtWidgets.QLabel(f"配置文件: {APP_RULES_PATH}")
        self.path_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        layout.addWidget(self.path_label)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["名称", "分类", "显示名", "App 关键词", "标题关键词", "域名关键词", "备注"]
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        btn_row = QtWidgets.QHBoxLayout()
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
        hint.setStyleSheet("color: #6B7280; font-size: 12px;")
        layout.addWidget(hint)

    def _load_rules(self):
        self.registry.reload(force=True)
        self.table.setRowCount(0)
        for rule in self.registry.rules:
            self._append_rule(rule)
        self._fit_columns()

    def _append_rule(self, rule: AppCategoryRule):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(rule.name))
        self._set_category_combo(row, rule.category)
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(rule.display_name))
        self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(_join_patterns(rule.app_contains)))
        self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(_join_patterns(rule.title_contains)))
        self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(_join_patterns(rule.domain_contains)))
        self.table.setItem(row, 6, QtWidgets.QTableWidgetItem(rule.note))

    def _set_category_combo(self, row: int, category: str):
        combo = QtWidgets.QComboBox()
        combo.addItems(CATEGORIES)
        idx = combo.findText(str(category or "other"))
        combo.setCurrentIndex(idx if idx >= 0 else combo.findText("other"))
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
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(3, max(self.table.columnWidth(3), 160))
        self.table.setColumnWidth(4, max(self.table.columnWidth(4), 180))
        self.table.setColumnWidth(5, max(self.table.columnWidth(5), 150))
