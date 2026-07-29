from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Dict, List

from PySide6 import QtCore, QtWidgets

from config import MEMORY_DB_PATH, MEMORY_SETTINGS
from modules.gui.styles import get_memory_dialog_styles
from modules.memory_core import MemoryCoreService
from modules.memory_core.categories import (
    CATEGORIES,
    CATEGORY_BY_ID,
    category_counts,
    category_matches,
    category_options,
    classify_memory_record,
)
from modules.memory_sqlite import get_memory_store


logger = logging.getLogger(__name__)


def get_character_catalog() -> Dict[str, Dict[str, Any]]:
    try:
        from modules.character_manager import character_manager

        return dict(character_manager.get_all_characters() or {})
    except Exception:
        return {}


def _msg(
    parent,
    title: str,
    text: str,
    icon=QtWidgets.QMessageBox.Icon.Information,
) -> None:
    message = QtWidgets.QMessageBox(parent)
    message.setIcon(icon)
    message.setWindowTitle(title)
    message.setText(text)
    message.exec()


class MemoryEditorDialog(QtWidgets.QDialog):
    """Manage SQLite Memory Core records and inspect legacy vector data."""

    def __init__(self, parent=None, embedded: bool = False, brain=None):
        super().__init__(parent)
        self.embedded = bool(embedded)
        self.brain = brain
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
        self.memory_core = MemoryCoreService(
            self.store,
            settings=MEMORY_SETTINGS,
            character_catalog_getter=get_character_catalog,
        )
        self.memory_core.initialize()
        self._memory_core_rows: List[Dict[str, Any]] = []
        self._all_memory_core_rows: List[Dict[str, Any]] = []
        self._memory_category_counts: Dict[str, int] = {}
        self._selected_memory_category_id = "all"
        self._transcript_rows: List[Dict[str, Any]] = []
        self._vector_rows: List[Dict[str, Any]] = []
        self._vector_initialized = False

        self.setWindowTitle("记忆与档案管理中心")
        self.resize(1100, 750)
        self.setStyleSheet(get_memory_dialog_styles())

        root = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs, 1)

        self.lbl_hint = QtWidgets.QLabel(
            "SQLite 是记忆单一事实源；向量页仅用于查看旧数据。"
        )
        root.addWidget(self.lbl_hint)

        self._build_profile_overview_tab()
        self._build_memory_core_tab()
        self._build_transcript_tab()
        self._build_vector_placeholder_tab()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._reload_memory_core_records()
        self._reload_transcript()

    def _build_profile_overview_tab(self) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        filters = QtWidgets.QHBoxLayout()
        filters.addWidget(QtWidgets.QLabel("人物"))
        self.profile_person_filter = QtWidgets.QComboBox()
        self.profile_person_filter.addItem("我的档案", "owner")
        self.profile_person_filter.currentIndexChanged.connect(
            self._reload_profile_overview
        )
        filters.addWidget(self.profile_person_filter, 1)
        refresh = QtWidgets.QPushButton("刷新")
        refresh.clicked.connect(self._reload_profile_overview)
        filters.addWidget(refresh)
        filters.addStretch(2)
        layout.addLayout(filters)

        self.profile_overview_tree = QtWidgets.QTreeWidget()
        self.profile_overview_tree.setObjectName("memoryProfileOverview")
        self.profile_overview_tree.setColumnCount(2)
        self.profile_overview_tree.setHeaderLabels(["分类与内容", "记录类型"])
        self.profile_overview_tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.profile_overview_tree.header().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.profile_overview_tree, 1)
        self.tabs.addTab(page, "档案概览")

    def _build_memory_core_tab(self) -> None:
        page = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.memory_category_tree = QtWidgets.QTreeWidget()
        self.memory_category_tree.setObjectName("memoryCategoryTree")
        self.memory_category_tree.setHeaderHidden(True)
        self.memory_category_tree.setMinimumWidth(210)
        self.memory_category_tree.setMaximumWidth(270)
        self.memory_category_tree.currentItemChanged.connect(
            self._on_memory_category_changed
        )
        splitter.addWidget(self.memory_category_tree)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        filters = QtWidgets.QHBoxLayout()
        self.memory_core_search = QtWidgets.QLineEdit()
        self.memory_core_search.setPlaceholderText("在当前分类中搜索")
        self.memory_core_search.textChanged.connect(self._reload_memory_core_records)
        filters.addWidget(self.memory_core_search, 3)

        self.memory_core_person_filter = QtWidgets.QComboBox()
        self.memory_core_person_filter.addItem("全部人物", "")
        self.memory_core_person_filter.addItem("owner", "owner")
        self.memory_core_person_filter.setCurrentIndex(1)
        self.memory_core_person_filter.currentIndexChanged.connect(
            self._reload_memory_core_records
        )
        filters.addWidget(self.memory_core_person_filter, 1)

        self.memory_core_status_filter = QtWidgets.QComboBox()
        self.memory_core_status_filter.addItems(
            ["active", "archived", "superseded", "全部状态"]
        )
        self.memory_core_status_filter.currentIndexChanged.connect(
            self._reload_memory_core_records
        )
        filters.addWidget(self.memory_core_status_filter, 1)

        refresh = QtWidgets.QPushButton("刷新")
        refresh.clicked.connect(self._reload_memory_core_records)
        filters.addWidget(refresh)
        right_layout.addLayout(filters)

        self.memory_content_splitter = QtWidgets.QSplitter(
            QtCore.Qt.Orientation.Vertical
        )
        self.memory_content_splitter.setChildrenCollapsible(False)
        self.memory_content_splitter.setHandleWidth(6)
        content_splitter = self.memory_content_splitter
        self.memory_core_table = QtWidgets.QTableWidget(0, 4)
        self.memory_core_table.setObjectName("memoryRecordTable")
        self.memory_core_table.setHorizontalHeaderLabels(
            ["内容", "分类", "人物", "置信度"]
        )
        self.memory_core_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.memory_core_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.memory_core_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.memory_core_table.itemSelectionChanged.connect(
            self._on_memory_core_select
        )
        content_splitter.addWidget(self.memory_core_table)

        editor = QtWidgets.QWidget()
        editor.setObjectName("memoryEditorPanel")
        editor_layout = QtWidgets.QVBoxLayout(editor)
        editor_layout.setContentsMargins(6, 6, 6, 6)
        editor_layout.setSpacing(4)

        primary_form = QtWidgets.QFormLayout()
        primary_form.setVerticalSpacing(4)
        self.memory_category_auto_label = QtWidgets.QLabel("自动分类：未分类")
        self.memory_category_combo = QtWidgets.QComboBox()
        self.memory_category_combo.addItem("自动分类", "")
        for category in category_options():
            self.memory_category_combo.addItem(
                self._memory_category_label(category.id),
                category.id,
            )
        category_row = QtWidgets.QHBoxLayout()
        category_row.addWidget(self.memory_category_combo, 1)
        reset_category = QtWidgets.QPushButton("恢复自动分类")
        reset_category.clicked.connect(self._reset_memory_category_override)
        category_row.addWidget(reset_category)
        primary_form.addRow("分类", category_row)
        primary_form.addRow("当前", self.memory_category_auto_label)

        self.memory_core_content = QtWidgets.QPlainTextEdit()
        self.memory_core_content.setMinimumHeight(60)
        self.memory_core_content.setMaximumHeight(90)
        primary_form.addRow("内容", self.memory_core_content)

        score_row = QtWidgets.QHBoxLayout()
        self.memory_core_confidence = QtWidgets.QDoubleSpinBox()
        self.memory_core_confidence.setRange(0.0, 1.0)
        self.memory_core_confidence.setSingleStep(0.05)
        self.memory_core_confidence.setValue(1.0)
        score_row.addWidget(QtWidgets.QLabel("置信度"))
        score_row.addWidget(self.memory_core_confidence)
        self.memory_core_importance = QtWidgets.QDoubleSpinBox()
        self.memory_core_importance.setRange(0.0, 1.0)
        self.memory_core_importance.setSingleStep(0.05)
        self.memory_core_importance.setValue(0.7)
        score_row.addWidget(QtWidgets.QLabel("重要度"))
        score_row.addWidget(self.memory_core_importance)
        self.memory_core_lock = QtWidgets.QCheckBox("人工锁定")
        score_row.addWidget(self.memory_core_lock)
        score_row.addStretch(1)
        primary_form.addRow("权重", score_row)
        editor_layout.addLayout(primary_form)

        advanced_group = QtWidgets.QGroupBox("高级信息")
        advanced_group.setCheckable(True)
        advanced_group.setChecked(False)
        advanced_layout = QtWidgets.QVBoxLayout(advanced_group)
        self.memory_advanced_content = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.memory_advanced_content)
        self.memory_core_id = QtWidgets.QLineEdit()
        self.memory_core_id.setReadOnly(True)
        form.addRow("记录 ID", self.memory_core_id)

        self.memory_core_kind = QtWidgets.QComboBox()
        self.memory_core_kind.addItems(
            ["profile", "fact", "preference", "rule", "episode", "summary", "other"]
        )
        form.addRow("类型", self.memory_core_kind)

        self.memory_core_key = QtWidgets.QLineEdit()
        form.addRow("稳定键", self.memory_core_key)
        self.memory_core_subject = QtWidgets.QLineEdit("owner")
        form.addRow("人物 ID", self.memory_core_subject)
        self.memory_core_session = QtWidgets.QLineEdit()
        form.addRow("会话范围", self.memory_core_session)
        self.memory_core_source = QtWidgets.QLabel("manual_gui")
        self.memory_core_source.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("来源", self.memory_core_source)
        advanced_layout.addWidget(self.memory_advanced_content)
        advanced_group.toggled.connect(self.memory_advanced_content.setVisible)
        self.memory_advanced_content.setVisible(False)
        editor_layout.addWidget(advanced_group)

        buttons = QtWidgets.QHBoxLayout()
        for text, handler in (
            ("新建", self._clear_memory_core_editor),
            ("保存", self._save_memory_core_record),
            ("归档", self._archive_memory_core_record),
            ("删除", self._delete_memory_core_record),
        ):
            button = QtWidgets.QPushButton(text)
            if text == "保存":
                button.setObjectName("memoryPrimaryAction")
            elif text == "删除":
                button.setObjectName("memoryDangerAction")
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.insertStretch(1)
        editor_layout.addLayout(buttons)

        content_splitter.addWidget(editor)
        content_splitter.setStretchFactor(0, 5)
        content_splitter.setStretchFactor(1, 2)
        content_splitter.setSizes([560, 220])
        right_layout.addWidget(content_splitter, 1)
        splitter.addWidget(right)
        splitter.setSizes([235, 850])
        root.addWidget(splitter, 1)
        self.tabs.addTab(page, "记忆记录")

    @staticmethod
    def _memory_category_label(category_id: str) -> str:
        category = CATEGORY_BY_ID.get(str(category_id or ""))
        if category is None:
            return "未分类"
        if category.parent_id:
            parent = CATEGORY_BY_ID.get(category.parent_id)
            if parent is not None:
                return f"{parent.label} / {category.label}"
        return category.label

    def _rebuild_memory_category_tree(self) -> None:
        self.memory_category_tree.blockSignals(True)
        self.memory_category_tree.clear()
        items: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        for category in CATEGORIES:
            count = self._memory_category_counts.get(category.id, 0)
            item = QtWidgets.QTreeWidgetItem([f"{category.label}  {count}"])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, category.id)
            items[category.id] = item
            if category.parent_id:
                parent = items.get(category.parent_id)
                if parent is not None:
                    parent.addChild(item)
                    continue
            self.memory_category_tree.addTopLevelItem(item)
        likes_item = items.get("likes")
        if likes_item is not None:
            likes_item.setExpanded(True)
        selected = items.get(self._selected_memory_category_id) or items.get("all")
        if selected is not None:
            self.memory_category_tree.setCurrentItem(selected)
        self.memory_category_tree.blockSignals(False)

    def _on_memory_category_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        _previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        category_id = str(
            current.data(0, QtCore.Qt.ItemDataRole.UserRole) or "all"
        )
        if category_id == self._selected_memory_category_id:
            return
        self._selected_memory_category_id = category_id
        self._refresh_memory_core_table()

    def _select_memory_category(self, category_id: str) -> None:
        target_id = category_id if category_id in CATEGORY_BY_ID else "all"
        iterator = QtWidgets.QTreeWidgetItemIterator(self.memory_category_tree)
        while iterator.value() is not None:
            item = iterator.value()
            if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == target_id:
                self.memory_category_tree.setCurrentItem(item)
                if target_id == self._selected_memory_category_id:
                    self._refresh_memory_core_table()
                return
            iterator += 1

    def memory_category_count(self, category_id: str) -> int:
        return int(self._memory_category_counts.get(category_id, 0))

    def _selected_memory_person_id(self) -> str:
        return str(self.memory_core_person_filter.currentData() or "").strip()

    def _memory_person_labels(self, rows: List[Dict[str, Any]]) -> Dict[str, str]:
        labels: Dict[str, str] = {"owner": "我的档案"}
        try:
            people = self.memory_core.list_persons()
        except Exception:
            people = []
        for person in people:
            person_id = str(person.get("person_id") or "").strip()
            if not person_id:
                continue
            display_name = str(person.get("display_name") or "").strip()
            if person_id == "owner":
                labels[person_id] = (
                    f"我的档案 · {display_name}" if display_name else "我的档案"
                )
            elif person_id.startswith("qq:"):
                labels[person_id] = (
                    f"QQ · {display_name}" if display_name else f"QQ · {person_id[3:]}"
                )
            else:
                labels[person_id] = display_name or person_id

        for character_id, payload in get_character_catalog().items():
            subject_id = self.memory_core.repository.character_subject_id(character_id)
            name = str((payload or {}).get("name") or character_id).strip()
            labels[subject_id] = f"角色 · {name}"

        for row in rows:
            subject_id = str(row.get("subject_id") or "owner").strip() or "owner"
            if subject_id in labels:
                continue
            character_id = self.memory_core.repository.character_id_from_subject(subject_id)
            labels[subject_id] = (
                f"历史角色 · {character_id}" if character_id else subject_id
            )

        def sort_key(item: tuple[str, str]) -> tuple[int, str]:
            subject_id, label = item
            if subject_id == "owner":
                rank = 0
            elif subject_id.startswith("qq:"):
                rank = 1
            elif subject_id.startswith("character:") and label.startswith("角色"):
                rank = 2
            elif subject_id.startswith("character:"):
                rank = 3
            else:
                rank = 4
            return rank, label

        return dict(sorted(labels.items(), key=sort_key))

    def _rebuild_memory_person_filter(self, rows: List[Dict[str, Any]]) -> None:
        selected_person_id = self._selected_memory_person_id()
        labels = self._memory_person_labels(rows)
        self.memory_core_person_filter.blockSignals(True)
        self.memory_core_person_filter.clear()
        self.memory_core_person_filter.addItem("全部人物", "")
        for person_id, label in labels.items():
            self.memory_core_person_filter.addItem(label, person_id)
        selected_index = self.memory_core_person_filter.findData(selected_person_id)
        if selected_index < 0:
            selected_index = self.memory_core_person_filter.findData("owner")
        self.memory_core_person_filter.setCurrentIndex(max(0, selected_index))
        self.memory_core_person_filter.blockSignals(False)

    def _reload_profile_overview(self, *_args) -> None:
        if not hasattr(self, "profile_overview_tree"):
            return
        selected_person_id = str(
            self.profile_person_filter.currentData() or "owner"
        ).strip() or "owner"
        rows = self.memory_core.list_memory_records(status="active", limit=1000)
        labels = self._memory_person_labels(rows)
        self.profile_person_filter.blockSignals(True)
        self.profile_person_filter.clear()
        for person_id, label in labels.items():
            self.profile_person_filter.addItem(label, person_id)
        selected_index = self.profile_person_filter.findData(selected_person_id)
        if selected_index < 0:
            selected_index = self.profile_person_filter.findData("owner")
        self.profile_person_filter.setCurrentIndex(max(0, selected_index))
        self.profile_person_filter.blockSignals(False)
        if hasattr(self, "vector_person_filter"):
            vector_person_id = str(
                self.vector_person_filter.currentData() or "owner"
            ).strip() or "owner"
            self.vector_person_filter.blockSignals(True)
            self.vector_person_filter.clear()
            for person_id, label in labels.items():
                self.vector_person_filter.addItem(label, person_id)
            vector_index = self.vector_person_filter.findData(vector_person_id)
            if vector_index < 0:
                vector_index = self.vector_person_filter.findData("owner")
            self.vector_person_filter.setCurrentIndex(max(0, vector_index))
            self.vector_person_filter.blockSignals(False)
        selected_person_id = str(
            self.profile_person_filter.currentData() or "owner"
        ).strip() or "owner"
        if selected_person_id == "owner":
            rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() in {"", "owner"}
            ]
        else:
            rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() == selected_person_id
            ]

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(classify_memory_record(row), []).append(row)

        self.profile_overview_tree.clear()
        section_ids = ("identity", "likes", "dislikes", "habits", "status", "interaction")
        for section_id in section_ids:
            section = CATEGORY_BY_ID[section_id]
            section_count = sum(
                len(items)
                for category_id, items in grouped.items()
                if category_matches(section_id, category_id)
            )
            section_item = QtWidgets.QTreeWidgetItem(
                [f"{section.label}  {section_count}", ""]
            )
            section_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, section_id)
            self.profile_overview_tree.addTopLevelItem(section_item)
            if section_id == "likes":
                for category in CATEGORIES:
                    if category.parent_id != "likes":
                        continue
                    records = grouped.get(category.id, [])
                    category_item = QtWidgets.QTreeWidgetItem(
                        [f"{category.label}  {len(records)}", ""]
                    )
                    category_item.setData(
                        0, QtCore.Qt.ItemDataRole.UserRole, category.id
                    )
                    section_item.addChild(category_item)
                    for row in records:
                        leaf = QtWidgets.QTreeWidgetItem(
                            [str(row.get("content") or ""), str(row.get("kind") or "")]
                        )
                        leaf.setData(
                            0, QtCore.Qt.ItemDataRole.UserRole, str(row.get("id") or "")
                        )
                        category_item.addChild(leaf)
                    category_item.setExpanded(bool(records))
            else:
                for row in grouped.get(section_id, []):
                    leaf = QtWidgets.QTreeWidgetItem(
                        [str(row.get("content") or ""), str(row.get("kind") or "")]
                    )
                    leaf.setData(
                        0, QtCore.Qt.ItemDataRole.UserRole, str(row.get("id") or "")
                    )
                    section_item.addChild(leaf)
            section_item.setExpanded(True)

    def _reload_memory_core_records(self) -> None:
        selected_record_id = self.memory_core_id.text().strip()
        status_text = self.memory_core_status_filter.currentText()
        status = "" if status_text == "全部状态" else status_text
        rows = self.memory_core.list_memory_records(
            status=status,
            limit=1000,
        )
        self._rebuild_memory_person_filter(rows)
        selected_person_id = self._selected_memory_person_id()
        if selected_person_id == "owner":
            rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() in {"", "owner"}
            ]
        elif selected_person_id:
            rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() == selected_person_id
            ]
        self._all_memory_core_rows = rows
        self._memory_category_counts = category_counts(rows)
        self._rebuild_memory_category_tree()
        self._refresh_memory_core_table(selected_record_id)
        self._reload_profile_overview()

    def _refresh_memory_core_table(self, selected_record_id: str = "") -> None:
        selected_record_id = (
            str(selected_record_id or "").strip()
            or self.memory_core_id.text().strip()
        )
        query = self.memory_core_search.text().strip().lower()
        rows = [
            row
            for row in self._all_memory_core_rows
            if category_matches(
                self._selected_memory_category_id,
                classify_memory_record(row),
            )
        ]
        if query:
            rows = [
                row
                for row in rows
                if query
                in " ".join(
                    str(row.get(key) or "")
                    for key in ("kind", "key", "content", "source_type", "source_id")
                ).lower()
            ]
        self._memory_core_rows = rows
        self.memory_core_table.blockSignals(True)
        self.memory_core_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                str(row.get("content") or "")[:120],
                self._memory_category_label(classify_memory_record(row)),
                row.get("subject_id"),
                f"{float(row.get('confidence') or 0):.2f}",
            )
            for column, value in enumerate(values):
                self.memory_core_table.setItem(
                    row_index,
                    column,
                    QtWidgets.QTableWidgetItem(str(value or "")),
                )
        restored_row = next(
            (
                index
                for index, row in enumerate(rows)
                if str(row.get("id") or "") == selected_record_id
            ),
            -1,
        )
        if restored_row >= 0:
            self.memory_core_table.setCurrentCell(restored_row, 0)
        self.memory_core_table.blockSignals(False)
        if restored_row >= 0:
            self._on_memory_core_select()

    def _on_memory_core_select(self) -> None:
        row_index = self.memory_core_table.currentRow()
        if row_index < 0 or row_index >= len(self._memory_core_rows):
            return
        row = self._memory_core_rows[row_index]
        self.memory_core_id.setText(str(row.get("id") or ""))
        self.memory_core_kind.setCurrentText(str(row.get("kind") or "other"))
        self.memory_core_key.setText(str(row.get("key") or ""))
        self.memory_core_subject.setText(str(row.get("subject_id") or ""))
        self.memory_core_session.setText(str(row.get("session_id") or ""))
        self.memory_core_confidence.setValue(float(row.get("confidence") or 0))
        self.memory_core_importance.setValue(float(row.get("importance") or 0))
        self.memory_core_lock.setChecked(bool(row.get("manual_lock")))
        self.memory_core_content.setPlainText(str(row.get("content") or ""))
        self.memory_core_source.setText(
            f"{row.get('source_type') or ''}:{row.get('source_id') or ''}"
        )
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        override = str(metadata.get("category_override") or "")
        automatic_row = dict(row)
        automatic_metadata = dict(metadata)
        automatic_metadata.pop("category_override", None)
        automatic_row["metadata"] = automatic_metadata
        automatic_category = classify_memory_record(automatic_row)
        self.memory_category_auto_label.setText(
            f"自动分类：{self._memory_category_label(automatic_category)}"
        )
        combo_index = self.memory_category_combo.findData(override)
        self.memory_category_combo.setCurrentIndex(max(0, combo_index))

    def _clear_memory_core_editor(self) -> None:
        self.memory_core_id.clear()
        self.memory_core_kind.setCurrentText("fact")
        self.memory_core_key.clear()
        self.memory_core_subject.setText(
            self._selected_memory_person_id() or "owner"
        )
        self.memory_core_session.clear()
        self.memory_core_confidence.setValue(1.0)
        self.memory_core_importance.setValue(0.7)
        self.memory_core_lock.setChecked(True)
        self.memory_core_content.clear()
        self.memory_core_source.setText("manual_gui")
        self.memory_category_combo.setCurrentIndex(0)
        self.memory_category_auto_label.setText("自动分类：未分类")

    def _save_memory_core_record(self) -> None:
        content = self.memory_core_content.toPlainText().strip()
        if not content:
            _msg(self, "无法保存", "记忆内容不能为空", QtWidgets.QMessageBox.Icon.Warning)
            return
        record_id = self.memory_core_id.text().strip()
        previous = self.memory_core.get_memory_record(record_id) if record_id else None
        previous_metadata = (
            previous.get("metadata")
            if previous and isinstance(previous.get("metadata"), dict)
            else {}
        )
        previous_override = str(previous_metadata.get("category_override") or "")
        selected_override = str(self.memory_category_combo.currentData() or "")
        payload = {
            "kind": self.memory_core_kind.currentText(),
            "key": self.memory_core_key.text().strip(),
            "subject_id": self.memory_core_subject.text().strip(),
            "session_id": self.memory_core_session.text().strip(),
            "content": content,
            "confidence": self.memory_core_confidence.value(),
            "importance": self.memory_core_importance.value(),
            "manual_lock": self.memory_core_lock.isChecked(),
        }
        try:
            if record_id:
                self.memory_core.update_memory_record(record_id, **payload)
            else:
                record_id = self.memory_core.upsert_memory_record(
                    **payload,
                    source_type="manual_gui",
                    source_id=uuid.uuid4().hex,
                )
                self.memory_core_id.setText(record_id)
            if selected_override != previous_override:
                self.memory_core.set_memory_category_override(
                    record_id,
                    selected_override,
                )
        except Exception as exc:
            _msg(self, "保存失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)
            return
        self._reload_memory_core_records()

    def _reset_memory_category_override(self) -> None:
        record_id = self.memory_core_id.text().strip()
        if not record_id:
            self.memory_category_combo.setCurrentIndex(0)
            return
        try:
            self.memory_core.set_memory_category_override(record_id, "")
        except Exception as exc:
            _msg(self, "恢复失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)
            return
        self.memory_category_combo.setCurrentIndex(0)
        self._reload_memory_core_records()

    def _archive_memory_core_record(self) -> None:
        record_id = self.memory_core_id.text().strip()
        if not record_id:
            return
        self.memory_core.update_memory_record(record_id, status="archived")
        self._clear_memory_core_editor()
        self._reload_memory_core_records()

    def _delete_memory_core_record(self) -> None:
        record_id = self.memory_core_id.text().strip()
        if not record_id:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            "确定永久删除这条记忆及其证据吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.memory_core.delete_memory_record(record_id)
        self._clear_memory_core_editor()
        self._reload_memory_core_records()

    def _build_transcript_tab(self) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        filters = QtWidgets.QHBoxLayout()
        self.transcript_search = QtWidgets.QLineEdit()
        self.transcript_search.setPlaceholderText("搜索原始对话")
        self.transcript_search.returnPressed.connect(self._reload_transcript)
        filters.addWidget(self.transcript_search, 2)
        self.transcript_role = QtWidgets.QComboBox()
        self.transcript_role.addItems(["全部角色", "user", "assistant", "system"])
        self.transcript_role.currentIndexChanged.connect(self._reload_transcript)
        filters.addWidget(self.transcript_role, 1)
        refresh = QtWidgets.QPushButton("刷新")
        refresh.clicked.connect(self._reload_transcript)
        filters.addWidget(refresh)
        layout.addLayout(filters)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.transcript_list = QtWidgets.QListWidget()
        self.transcript_list.currentRowChanged.connect(self._on_transcript_select)
        splitter.addWidget(self.transcript_list)
        self.transcript_view = QtWidgets.QPlainTextEdit()
        self.transcript_view.setReadOnly(True)
        splitter.addWidget(self.transcript_view)
        layout.addWidget(splitter, 1)
        self.tabs.addTab(page, "原始对话")

    def _reload_transcript(self) -> None:
        role = self.transcript_role.currentText()
        if role == "全部角色":
            role = None
        rows = self.store.list_transcript(
            role=role,
            query=self.transcript_search.text().strip(),
            limit=300,
            offset=0,
        )
        self._transcript_rows = [
            row for row in rows if not self._is_diary_archive_transcript(row)
        ]
        self.transcript_list.blockSignals(True)
        self.transcript_list.clear()
        for row in self._transcript_rows:
            timestamp = str(row.get("ts_iso") or "")[:19].replace("T", " ")
            content = str(row.get("content") or "").replace("\n", " ")
            self.transcript_list.addItem(
                f"[{timestamp}] {row.get('role') or ''}: {content[:90]}"
            )
        self.transcript_list.blockSignals(False)
        if self._transcript_rows:
            self.transcript_list.setCurrentRow(0)
        else:
            self.transcript_view.clear()

    @staticmethod
    def _is_diary_archive_transcript(row: Dict[str, Any]) -> bool:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if str(meta.get("type") or "").strip().lower() == "episodic_memory":
            return True
        content = str(row.get("content") or "").strip()
        return bool(re.match(r"^【日记\s+\d{4}-\d{2}-\d{2}】", content))

    def _on_transcript_select(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._transcript_rows):
            self.transcript_view.clear()
            return
        row = self._transcript_rows[row_index]
        self.transcript_view.setPlainText(
            f"ID: {row.get('id')}\n"
            f"时间: {row.get('ts_iso')}\n"
            f"角色: {row.get('role')}\n"
            f"元数据: {row.get('meta') or {}}\n\n"
            f"{row.get('content') or ''}"
        )

    def _build_vector_placeholder_tab(self) -> None:
        self._vector_page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self._vector_page)
        status_group = QtWidgets.QGroupBox("当前 Memory Core 向量索引")
        status_layout = QtWidgets.QVBoxLayout(status_group)
        selection_row = QtWidgets.QHBoxLayout()
        selection_row.addWidget(QtWidgets.QLabel("嵌入模型队列（按顺序尝试）"))
        status_layout.addLayout(selection_row)
        self.embedding_chain_edit = QtWidgets.QPlainTextEdit()
        self.embedding_chain_edit.setPlaceholderText(
            "每行一个模型 ID，例如：\nlocal-bge-m3\nsiliconflow-bge\n空 = 使用旧 EMBEDDING_* 配置"
        )
        self.embedding_chain_edit.setMaximumHeight(96)
        status_layout.addWidget(self.embedding_chain_edit)
        pick_row = QtWidgets.QHBoxLayout()
        pick_row.addWidget(QtWidgets.QLabel("快速加入"))
        self.embedding_model_combo = QtWidgets.QComboBox()
        self.embedding_model_combo.setMinimumWidth(260)
        pick_row.addWidget(self.embedding_model_combo, 1)
        add_selected = QtWidgets.QPushButton("加入队列")
        add_selected.clicked.connect(self._add_embedding_model_to_chain)
        pick_row.addWidget(add_selected)
        test_selected = QtWidgets.QPushButton("测试队列")
        test_selected.clicked.connect(self._test_embedding_connection)
        pick_row.addWidget(test_selected)
        save_selected = QtWidgets.QPushButton("保存队列")
        save_selected.clicked.connect(self._save_embedding_model_selection)
        pick_row.addWidget(save_selected)
        status_layout.addLayout(pick_row)
        self.embedding_selection_label = QtWidgets.QLabel()
        self.embedding_selection_label.setWordWrap(True)
        status_layout.addWidget(self.embedding_selection_label)
        self._load_embedding_model_options()
        self.vector_status_label = QtWidgets.QLabel()
        self.vector_status_label.setWordWrap(True)
        status_layout.addWidget(self.vector_status_label)
        refresh_status = QtWidgets.QPushButton("刷新状态")
        refresh_status.clicked.connect(self._refresh_vector_status)
        rebuild_index = QtWidgets.QPushButton("重建当前索引")
        rebuild_index.clicked.connect(self._rebuild_vector_index)
        status_actions = QtWidgets.QHBoxLayout()
        status_actions.addWidget(refresh_status)
        status_actions.addWidget(rebuild_index)
        status_actions.addStretch(1)
        status_layout.addLayout(status_actions)
        layout.addWidget(status_group)

        diagnostic_group = QtWidgets.QGroupBox("当前索引检索诊断")
        diagnostic_layout = QtWidgets.QVBoxLayout(diagnostic_group)
        diagnostic_controls = QtWidgets.QHBoxLayout()
        self.vector_person_filter = QtWidgets.QComboBox()
        self.vector_person_filter.addItem("我的档案", "owner")
        diagnostic_controls.addWidget(self.vector_person_filter, 1)
        self.current_vector_query = QtWidgets.QLineEdit()
        self.current_vector_query.setPlaceholderText("输入查询，查看当前向量候选")
        self.current_vector_query.returnPressed.connect(self._search_current_vector)
        diagnostic_controls.addWidget(self.current_vector_query, 3)
        search_current = QtWidgets.QPushButton("搜索")
        search_current.clicked.connect(self._search_current_vector)
        diagnostic_controls.addWidget(search_current)
        test_connection = QtWidgets.QPushButton("测试连接")
        test_connection.clicked.connect(self._test_embedding_connection)
        diagnostic_controls.addWidget(test_connection)
        diagnostic_layout.addLayout(diagnostic_controls)
        self.current_vector_results = QtWidgets.QListWidget()
        self.current_vector_results.setMinimumHeight(110)
        diagnostic_layout.addWidget(self.current_vector_results)
        layout.addWidget(diagnostic_group, 1)

        legacy_group = QtWidgets.QGroupBox("旧向量库（只读）")
        legacy_group.setCheckable(True)
        legacy_group.setChecked(False)
        legacy_outer_layout = QtWidgets.QVBoxLayout(legacy_group)
        self._legacy_vector_content = QtWidgets.QWidget()
        self._legacy_vector_layout = QtWidgets.QVBoxLayout(
            self._legacy_vector_content
        )
        self._legacy_vector_layout.setContentsMargins(0, 0, 0, 0)
        legacy_outer_layout.addWidget(self._legacy_vector_content)
        label = QtWidgets.QLabel("旧 Chroma 数据仅用于核对，不参与当前记忆召回。")
        label.setWordWrap(True)
        self._legacy_vector_layout.addWidget(label)
        load_legacy = QtWidgets.QPushButton("加载旧向量库")
        load_legacy.clicked.connect(self._load_legacy_vector_tab)
        self._legacy_vector_layout.addWidget(load_legacy, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        legacy_group.toggled.connect(self._legacy_vector_content.setVisible)
        self._legacy_vector_content.setVisible(False)
        layout.addWidget(legacy_group, 0)
        self.tabs.addTab(self._vector_page, "向量与检索")
        self._refresh_vector_status()

    def _load_embedding_model_options(self) -> None:
        from config import MODELS
        from modules.embeddings import embedding_model_ids_from_runtime
        from modules.model_catalog import list_model_options
        from modules.runtime_settings import load_runtime_settings

        runtime = load_runtime_settings()
        chain = embedding_model_ids_from_runtime(runtime)
        selected = chain[0] if chain else str(runtime.get("embedding_model_id") or "").strip()
        if hasattr(self, "embedding_chain_edit"):
            self.embedding_chain_edit.setPlainText("\n".join(chain))
        self.embedding_model_combo.clear()
        self.embedding_model_combo.addItem("（从下拉加入）", "")
        known_ids = set()
        for option in list_model_options(MODELS, purposes="embedding"):
            model_id = str(option.get("id") or "").strip()
            if not model_id:
                continue
            known_ids.add(model_id)
            self.embedding_model_combo.addItem(
                str(option.get("label") or model_id),
                model_id,
            )
        if selected and selected not in known_ids:
            self.embedding_model_combo.addItem(
                f"{selected}（模型已不存在或未标记向量用途）",
                selected,
            )
        index = self.embedding_model_combo.findData(selected)
        self.embedding_model_combo.setCurrentIndex(max(0, index))

    def _selected_embedding_model_ids(self) -> list[str]:
        if hasattr(self, "embedding_chain_edit"):
            raw = self.embedding_chain_edit.toPlainText()
            parts = []
            for line in str(raw or "").replace(",", "\n").splitlines():
                item = line.strip()
                if item and item not in parts:
                    parts.append(item)
            return parts
        selected = str(self.embedding_model_combo.currentData() or "").strip()
        return [selected] if selected else []

    def _selected_embedding_model_id(self) -> str:
        chain = self._selected_embedding_model_ids()
        return chain[0] if chain else ""

    def _add_embedding_model_to_chain(self) -> None:
        model_id = str(self.embedding_model_combo.currentData() or "").strip()
        if not model_id or not hasattr(self, "embedding_chain_edit"):
            return
        chain = self._selected_embedding_model_ids()
        if model_id not in chain:
            chain.append(model_id)
        self.embedding_chain_edit.setPlainText("\n".join(chain))

    def _save_embedding_model_selection(self) -> None:
        from config import EMBEDDING_CONFIG, MODELS
        from modules.embeddings import resolve_embedding_config
        from modules.runtime_settings import save_embedding_model_selection

        chain = self._selected_embedding_model_ids()
        try:
            resolve_embedding_config(
                model_ids=chain,
                models=MODELS,
                legacy_config=EMBEDDING_CONFIG,
            )
            save_embedding_model_selection(model_ids=chain)
        except Exception as exc:
            _msg(
                self,
                "保存失败",
                str(exc),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        self._refresh_vector_status()
        _msg(self, "已保存", "嵌入模型队列将在重启主程序后生效。")

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self._vector_page:
            self._refresh_vector_status()

    def _refresh_vector_status(self) -> None:
        from modules.embeddings import embedding_model_ids_from_runtime
        from modules.runtime_settings import load_runtime_settings

        saved_chain = embedding_model_ids_from_runtime(load_runtime_settings())
        pending_chain = self._selected_embedding_model_ids()
        selection_lines = [
            f"已保存队列：{' -> '.join(saved_chain) if saved_chain else '旧 EMBEDDING_* 配置'}",
        ]
        if pending_chain != saved_chain:
            selection_lines.append(
                f"待保存队列：{' -> '.join(pending_chain) if pending_chain else '旧 EMBEDDING_* 配置'}"
            )
        self.embedding_selection_label.setText("；".join(selection_lines))
        if self.brain is None or not hasattr(self.brain, "get_memory_vector_status"):
            self.vector_status_label.setText("当前向量索引未连接；SQLite 记忆仍可正常使用。")
            return
        try:
            status = dict(self.brain.get_memory_vector_status() or {})
        except Exception as exc:
            self.vector_status_label.setText(f"读取向量状态失败：{exc}")
            return
        jobs = dict(status.get("jobs") or {})
        embedding = dict(status.get("embedding") or {})
        state = {
            "ready": "可用",
            "unverified": "未验证",
            "error": "错误",
            "disabled": "已禁用",
            "unconfigured": "未配置",
        }.get(str(embedding.get("state") or ""), "不可用")
        error = str(embedding.get("last_error") or "").strip()
        chain = embedding.get("chain_model_ids") or []
        chain_text = " -> ".join(str(x) for x in chain) if isinstance(chain, list) and chain else ""
        lines = [
            f"Embedding：{embedding.get('active_model_id') or embedding.get('model_id') or '旧配置'} / "
            f"{embedding.get('model') or '未配置'} / "
            f"{embedding.get('dimension') or '未知维度'} / {state}",
            f"索引 {int(status.get('collection_count') or 0)} · "
            f"待处理 {int(jobs.get('pending') or 0)} · "
            f"失败 {int(jobs.get('failed') or 0)}",
            f"Embedding 调用 {int(embedding.get('calls') or 0)} · "
            f"失败调用 {int(embedding.get('failures') or 0)}",
        ]
        if chain_text:
            lines.append(f"运行队列：{chain_text}")
        if error:
            lines.append(f"最后错误：{error}")
        if status.get("rebuild_required"):
            lines.append(
                "当前嵌入模型与已索引数据不兼容，必须重建当前索引后才能检索。"
            )
        self.vector_status_label.setText("\n".join(lines))

    def _test_embedding_connection(self) -> None:
        from config import EMBEDDING_CONFIG, MODELS
        from modules.embeddings import build_configured_embedding_service

        chain = self._selected_embedding_model_ids()
        try:
            service = build_configured_embedding_service(
                models=MODELS,
                runtime_settings={"embedding_model_ids": chain},
                legacy_config=EMBEDDING_CONFIG,
            )
            service.embed(["Live2D-Suzu embedding connection test"])
            status = dict(service.status() or {})
        except Exception as exc:
            self._refresh_vector_status()
            _msg(
                self,
                "连接失败",
                str(exc),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        self._refresh_vector_status()
        chain_text = " -> ".join(str(x) for x in (status.get("chain_model_ids") or []))
        _msg(
            self,
            "连接成功",
            f"{status.get('active_model_id') or status.get('model_id') or status.get('model') or 'Embedding'} / "
            f"{status.get('dimension') or '未知维度'}"
            + (f"\n队列：{chain_text}" if chain_text else ""),
        )

    def _search_current_vector(self) -> None:
        query = self.current_vector_query.text().strip()
        if not query:
            return
        if self.brain is None or not hasattr(self.brain, "query_memory_vector"):
            _msg(
                self,
                "无法搜索",
                "当前向量索引未连接。",
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        person_id = str(
            self.vector_person_filter.currentData() or "owner"
        ).strip() or "owner"
        try:
            rows = self.brain.query_memory_vector(
                query,
                person_id=person_id,
                limit=10,
            )
        except Exception as exc:
            self._refresh_vector_status()
            _msg(
                self,
                "搜索失败",
                str(exc),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        self.current_vector_results.clear()
        for row in rows or []:
            score = float(row.get("vector_score") or 0.0)
            document = str(row.get("document") or "").replace("\n", " ")
            self.current_vector_results.addItem(
                f"{score:.3f}  {str(row.get('id') or '')}  {document[:140]}"
            )
        self._refresh_vector_status()

    def _rebuild_vector_index(self) -> None:
        if self.brain is None or not hasattr(
            self.brain, "rebuild_memory_vector_index"
        ):
            _msg(
                self,
                "无法重建",
                "当前向量索引未连接；SQLite 记忆没有受到影响。",
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认重建",
            "这会删除当前派生向量索引，并从 SQLite 重新生成。继续吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            result = dict(self.brain.rebuild_memory_vector_index() or {})
        except Exception as exc:
            _msg(
                self,
                "重建失败",
                str(exc),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        self._refresh_vector_status()
        _msg(self, "已开始重建", f"已重新排队 {int(result.get('queued') or 0)} 条记录。")

    def _replace_vector_layout_with_message(self, text: str) -> None:
        layout = self._legacy_vector_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label, 1)

    def _load_legacy_vector_tab(self) -> None:
        try:
            self._initialize_vector_tab()
        except Exception as exc:
            logger.exception("Failed to initialize legacy vector viewer")
            self._replace_vector_layout_with_message(f"旧向量库加载失败：{exc}")

    def _initialize_vector_tab(self) -> None:
        if self._vector_initialized:
            return
        import chromadb

        from config import EMBEDDING_CONFIG, MODELS
        from modules.embeddings import (
            ChromaEmbeddingFunction,
            build_configured_embedding_service,
        )
        from modules.runtime_settings import load_runtime_settings

        layout = self._legacy_vector_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        controls = QtWidgets.QHBoxLayout()
        self.vector_query = QtWidgets.QLineEdit()
        self.vector_query.setPlaceholderText("搜索旧聊天向量")
        self.vector_query.returnPressed.connect(self._vector_search)
        controls.addWidget(self.vector_query, 3)
        self.vector_limit = QtWidgets.QSpinBox()
        self.vector_limit.setRange(1, 50)
        self.vector_limit.setValue(10)
        controls.addWidget(self.vector_limit)
        search = QtWidgets.QPushButton("搜索")
        search.clicked.connect(self._vector_search)
        controls.addWidget(search)
        list_some = QtWidgets.QPushButton("列出一些")
        list_some.clicked.connect(self._vector_list_some)
        controls.addWidget(list_some)
        layout.addLayout(controls)

        self.vector_list = QtWidgets.QListWidget()
        self.vector_list.currentRowChanged.connect(self._on_vector_select)
        layout.addWidget(self.vector_list, 1)
        self.vector_view = QtWidgets.QPlainTextEdit()
        self.vector_view.setReadOnly(True)
        layout.addWidget(self.vector_view, 1)

        service = getattr(self.brain, "embedding_service", None)
        if service is None:
            service = build_configured_embedding_service(
                models=MODELS,
                runtime_settings=load_runtime_settings(),
                legacy_config=EMBEDDING_CONFIG,
            )
        embedding_fn = ChromaEmbeddingFunction(service)
        client = chromadb.PersistentClient(path=MEMORY_DB_PATH)
        self._memory_collection = client.get_or_create_collection(
            name="waifu_memory_advanced",
            embedding_function=embedding_fn,
        )
        self._vector_initialized = True
        self.lbl_hint.setText(
            "SQLite 是记忆单一事实源；旧向量库条目数："
            f"{self._memory_collection.count()}"
        )

    def _vector_search(self) -> None:
        query = self.vector_query.text().strip()
        if not query:
            return
        try:
            result = self._memory_collection.query(
                query_texts=[query],
                n_results=int(self.vector_limit.value()),
                include=["documents", "metadatas", "distances"],
            )
            ids = result.get("ids", [[]])[0]
            documents = result.get("documents", [[]])[0]
            metadata = result.get("metadatas", [[]])[0]
            distances = result.get("distances", [[]])[0]
            self._vector_rows = [
                {
                    "id": item_id,
                    "document": documents[index] if index < len(documents) else "",
                    "metadata": metadata[index] if index < len(metadata) else {},
                    "distance": distances[index] if index < len(distances) else None,
                }
                for index, item_id in enumerate(ids)
            ]
            self._render_vector_rows()
        except Exception as exc:
            _msg(self, "搜索失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)

    def _vector_list_some(self) -> None:
        try:
            result = self._memory_collection.get(
                include=["documents", "metadatas"],
                limit=30,
            )
            ids = result.get("ids", [])
            documents = result.get("documents", [])
            metadata = result.get("metadatas", [])
            self._vector_rows = [
                {
                    "id": item_id,
                    "document": documents[index] if index < len(documents) else "",
                    "metadata": metadata[index] if index < len(metadata) else {},
                    "distance": None,
                }
                for index, item_id in enumerate(ids)
            ]
            self._render_vector_rows()
        except Exception as exc:
            _msg(self, "读取失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)

    def _render_vector_rows(self) -> None:
        self.vector_list.blockSignals(True)
        self.vector_list.clear()
        for row in self._vector_rows:
            distance = row.get("distance")
            prefix = f"{distance:.3f} " if isinstance(distance, (int, float)) else ""
            document = str(row.get("document") or "").replace("\n", " ")
            self.vector_list.addItem(
                f"{prefix}{str(row.get('id') or '')[:28]} {document[:80]}"
            )
        self.vector_list.blockSignals(False)
        if self._vector_rows:
            self.vector_list.setCurrentRow(0)
        else:
            self.vector_view.clear()

    def _on_vector_select(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._vector_rows):
            self.vector_view.clear()
            return
        row = self._vector_rows[row_index]
        self.vector_view.setPlainText(
            f"ID: {row.get('id')}\n"
            f"距离: {row.get('distance')}\n"
            f"元数据: {row.get('metadata') or {}}\n\n"
            f"{row.get('document') or ''}"
        )
