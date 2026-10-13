from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from PySide6 import QtCore, QtWidgets

from config import MEMORY_SETTINGS
from modules.gui.styles import get_memory_dialog_styles
from modules.gui.utils import FlowLayout, FlowWidget
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


class MemoryTagChip(QtWidgets.QFrame):
    clicked = QtCore.Signal(object)
    deleted = QtCore.Signal(object)

    def __init__(
        self,
        record: Dict[str, Any],
        category_id: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.record = record
        self.category_id = category_id
        self._content = str(record.get("content") or "").strip()
        self.setObjectName("memoryTagChip")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._build_ui()

    def _build_ui(self):
        style = self._get_chip_style(self.category_id)
        self.setStyleSheet(style)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 6, 3)
        layout.setSpacing(6)

        clean_content = " ".join(self._content.split())
        display_text = clean_content if len(clean_content) <= 36 else clean_content[:33] + "..."
        self.label = QtWidgets.QLabel(display_text)
        self.label.setStyleSheet(
            "border: none; background: transparent; font-size: 12px; font-weight: 500;"
        )
        self.label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.label)

        self.btn_delete = QtWidgets.QPushButton("×")
        self.btn_delete.setToolTip("删除此项记忆")
        self.btn_delete.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #9CA3AF; "
            "font-size: 13px; font-weight: bold; width: 14px; height: 14px; padding: 0; } "
            "QPushButton:hover { color: #EF4444; background: rgba(239, 68, 68, 0.15); border-radius: 7px; }"
        )
        self.btn_delete.clicked.connect(lambda: self.deleted.emit(self.record))
        layout.addWidget(self.btn_delete)
        self.setFixedHeight(28)
        self.setMaximumWidth(380)

        created = str(self.record.get("created_at") or self.record.get("updated_at") or "")
        confidence = self.record.get("confidence")
        conf_str = f"置信度: {int(float(confidence)*100)}%" if confidence is not None else ""
        kind = str(self.record.get("kind") or "")
        kind_label = {
            "profile": "身份设定",
            "preference": "偏好喜好",
            "fact": "事实",
            "rule": "互动规则",
        }.get(kind, kind)
        tip_lines = [
            f"内容: {self._content}",
            f"类型: {kind_label}" if kind_label else "",
            conf_str,
            f"记录时间: {created[:19]}" if created else "",
            "💡 点击可编辑详情，点击 × 删除",
        ]
        self.setToolTip("\n".join([line for line in tip_lines if line]))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit(self.record)
            event.accept()
            return
        super().mousePressEvent(event)

    @staticmethod
    def _get_chip_style(category_id: str) -> str:
        palettes = {
            "likes.music": ("#EFF6FF", "#BFDBFE", "#1E40AF", "#DBEAFE"),
            "likes.anime": ("#FAF5FF", "#E9D5FF", "#6B21A8", "#F3E8FF"),
            "likes.games": ("#ECFDF5", "#A7F3D0", "#065F46", "#D1FAE5"),
            "likes.food": ("#FFF7ED", "#FED7AA", "#9A3412", "#FFEDD5"),
            "likes.art": ("#FDF2F8", "#FBCFE8", "#9D174D", "#FCE7F3"),
            "likes": ("#FFF1F2", "#FECDD3", "#9F1239", "#FFE4E6"),
            "likes.other": ("#FFF1F2", "#FECDD3", "#9F1239", "#FFE4E6"),
            "dislikes": ("#FEF2F2", "#FECACA", "#991B1B", "#FEE2E2"),
            "habits": ("#F0FDF4", "#BBF7D0", "#166534", "#DCFCE7"),
            "status": ("#F0F9FF", "#BAE6FD", "#0369A1", "#E0F2FE"),
            "interaction": ("#EEF2FF", "#C7D2FE", "#3730A3", "#E0E7FF"),
            "identity": ("#FEF3C7", "#FDE68A", "#92400E", "#FDE68A"),
            "events": ("#FFFBEB", "#FCD34D", "#78350F", "#FEF3C7"),
            "tasks": ("#FEF9C3", "#FACC15", "#713F12", "#FEF08A"),
        }
        bg, border, text, hover_bg = palettes.get(
            category_id, ("#F3F4F6", "#E5E7EB", "#374151", "#E5E7EB")
        )
        return (
            f"QFrame#memoryTagChip {{ background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: 12px; }} "
            f"QFrame#memoryTagChip:hover {{ background-color: {hover_bg}; border-color: {text}; }} "
            f"QLabel {{ color: {text}; }}"
        )


class QuickMemoryEditDialog(QtWidgets.QDialog):
    def __init__(self, record: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.record = record
        self.setWindowTitle("编辑记忆详情")
        self.setFixedWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)

        self.content_edit = QtWidgets.QPlainTextEdit()
        self.content_edit.setPlainText(str(self.record.get("content") or ""))
        self.content_edit.setMaximumHeight(85)
        form.addRow("记忆内容", self.content_edit)

        self.category_combo = QtWidgets.QComboBox()
        self.category_combo.addItem("保持自动分类", "")
        for cat in category_options():
            self.category_combo.addItem(cat.label, cat.id)
        meta = self.record.get("metadata") if isinstance(self.record.get("metadata"), dict) else {}
        curr_cat = str(meta.get("category_override") or classify_memory_record(self.record))
        idx = self.category_combo.findData(curr_cat)
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        form.addRow("所属分类", self.category_combo)

        self.kind_combo = QtWidgets.QComboBox()
        self.kind_combo.addItems(["preference", "fact", "profile", "rule", "episode", "other"])
        curr_kind = str(self.record.get("kind") or "preference")
        kind_idx = self.kind_combo.findText(curr_kind)
        if kind_idx >= 0:
            self.kind_combo.setCurrentIndex(kind_idx)
        form.addRow("记录类型", self.kind_combo)

        layout.addLayout(form)

        btns = QtWidgets.QHBoxLayout()
        self.btn_delete = QtWidgets.QPushButton("删除")
        self.btn_delete.setObjectName("memoryDangerAction")
        self.deleted_requested = False
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_cancel = QtWidgets.QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)

        self.btn_save = QtWidgets.QPushButton("保存")
        self.btn_save.setObjectName("memoryPrimaryAction")
        self.btn_save.clicked.connect(self._on_save)
        btns.addWidget(self.btn_save)
        layout.addLayout(btns)

    def _on_delete(self):
        self.deleted_requested = True
        self.accept()

    def _on_save(self):
        if not self.content_edit.toPlainText().strip():
            _msg(self, "提示", "记忆内容不能为空", QtWidgets.QMessageBox.Icon.Information)
            return
        self.accept()

    def get_updated_data(self) -> Dict[str, Any]:
        return {
            "content": self.content_edit.toPlainText().strip(),
            "category_override": str(self.category_combo.currentData() or "").strip(),
            "kind": self.kind_combo.currentText(),
        }


class QuickMemoryAddDialog(QtWidgets.QDialog):
    def __init__(self, default_subject: str = "owner", parent=None):
        super().__init__(parent)
        self.default_subject = default_subject
        self.setWindowTitle("新增记忆与偏好")
        self.setFixedWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)

        self.content_edit = QtWidgets.QPlainTextEdit()
        self.content_edit.setPlaceholderText("例如：最喜欢的乐队是 Ave Mujica、喜欢喝无糖乌龙茶、叫我小明 等")
        self.content_edit.setMaximumHeight(85)
        form.addRow("记忆内容", self.content_edit)

        self.category_combo = QtWidgets.QComboBox()
        self.category_combo.addItem("自动推导分类", "")
        for cat in category_options():
            self.category_combo.addItem(cat.label, cat.id)
        form.addRow("指定分类", self.category_combo)

        self.kind_combo = QtWidgets.QComboBox()
        self.kind_combo.addItems(["preference (偏好/喜好)", "fact (事实信息)", "profile (身份/称呼)", "rule (互动规则)"])
        form.addRow("记录类型", self.kind_combo)

        layout.addLayout(form)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QtWidgets.QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)

        self.btn_save = QtWidgets.QPushButton("保存并收录")
        self.btn_save.setObjectName("memoryPrimaryAction")
        self.btn_save.clicked.connect(self._on_save)
        btns.addWidget(self.btn_save)
        layout.addLayout(btns)

    def _on_save(self):
        if not self.content_edit.toPlainText().strip():
            _msg(self, "提示", "请输入记忆内容", QtWidgets.QMessageBox.Icon.Information)
            return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        raw_kind = self.kind_combo.currentText().split()[0]
        return {
            "content": self.content_edit.toPlainText().strip(),
            "category_override": str(self.category_combo.currentData() or "").strip(),
            "kind": raw_kind,
        }


class MemoryEditorDialog(QtWidgets.QDialog):
    """Manage SQLite Memory Core records and inspect legacy vector data."""

    def __init__(
        self,
        parent=None,
        embedded: bool = False,
        brain=None,
        memory_core=None,
        memory_gui=None,
    ):
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

        self.memory_core, owned_core = self._resolve_memory_core(memory_core, brain)
        if owned_core or not getattr(self.memory_core, "_initialized", False):
            self.memory_core.initialize()
        self.store = getattr(self.memory_core, "store", None) or get_memory_store()
        if memory_gui is not None:
            self.memory_gui = memory_gui
        else:
            from services.gui_api.memory_service import MemoryGuiService

            self.memory_gui = MemoryGuiService(
                memory_core=self.memory_core, brain=brain
            )
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

    @staticmethod
    def _resolve_memory_core(memory_core, brain):
        if memory_core is not None:
            return memory_core, False
        live = getattr(brain, "memory_core", None) if brain is not None else None
        if live is not None:
            return live, False
        store = get_memory_store()
        return (
            MemoryCoreService(
                store,
                settings=MEMORY_SETTINGS,
                character_catalog_getter=get_character_catalog,
            ),
            True,
        )

    def _build_profile_overview_tab(self) -> None:
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(10)

        # 1. Filters & Search Bar
        filters = QtWidgets.QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(QtWidgets.QLabel("人物档案："))

        self.profile_person_filter = QtWidgets.QComboBox()
        self.profile_person_filter.addItem("我的档案", "owner")
        self.profile_person_filter.setMinimumWidth(160)
        self.profile_person_filter.currentIndexChanged.connect(
            self._reload_profile_overview
        )
        filters.addWidget(self.profile_person_filter)

        self.profile_search_input = QtWidgets.QLineEdit()
        self.profile_search_input.setPlaceholderText("🔍 搜索记忆标签与偏好...")
        self.profile_search_input.textChanged.connect(self._on_profile_search_changed)
        filters.addWidget(self.profile_search_input, 1)

        refresh = QtWidgets.QPushButton("刷新")
        refresh.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        refresh.clicked.connect(self._reload_profile_overview)
        filters.addWidget(refresh)

        btn_add = QtWidgets.QPushButton("+ 新增记忆")
        btn_add.setObjectName("memoryPrimaryAction")
        btn_add.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._quick_add_memory)
        filters.addWidget(btn_add)
        page_layout.addLayout(filters)

        # 2. Hero Card (Dossier Header)
        self.profile_hero_card = QtWidgets.QFrame()
        self.profile_hero_card.setObjectName("memoryHeroCard")
        hero_layout = QtWidgets.QVBoxLayout(self.profile_hero_card)
        hero_layout.setContentsMargins(16, 12, 16, 12)
        hero_layout.setSpacing(8)

        hero_top = QtWidgets.QHBoxLayout()
        self.profile_hero_title = QtWidgets.QLabel("📖 TA 眼中的你")
        self.profile_hero_title.setObjectName("memoryHeroTitle")
        self.profile_hero_badge = QtWidgets.QLabel("已记录 0 项偏好与事实")
        self.profile_hero_badge.setObjectName("memoryHeroBadge")
        hero_top.addWidget(self.profile_hero_title)
        hero_top.addWidget(self.profile_hero_badge)
        hero_top.addStretch(1)
        hero_layout.addLayout(hero_top)

        self.profile_identity_row = QtWidgets.QHBoxLayout()
        self.profile_identity_label = QtWidgets.QLabel("认知称呼：")
        self.profile_identity_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.profile_identity_chips_wrap = QtWidgets.QWidget()
        self.profile_identity_chips_layout = QtWidgets.QHBoxLayout(
            self.profile_identity_chips_wrap
        )
        self.profile_identity_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.profile_identity_chips_layout.setSpacing(6)
        self.profile_identity_row.addWidget(self.profile_identity_label)
        self.profile_identity_row.addWidget(self.profile_identity_chips_wrap)
        self.profile_identity_row.addStretch(1)
        hero_layout.addLayout(self.profile_identity_row)
        page_layout.addWidget(self.profile_hero_card)

        # 3. Category Wall inside ScrollArea
        self.profile_wall_scroll = QtWidgets.QScrollArea()
        self.profile_wall_scroll.setObjectName("memoryWallScroll")
        self.profile_wall_scroll.setWidgetResizable(True)
        self.profile_wall_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.profile_wall_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.profile_wall_container = QtWidgets.QWidget()
        self.profile_wall_container.setObjectName("memoryWallContent")
        self.profile_wall_layout = QtWidgets.QVBoxLayout(self.profile_wall_container)
        self.profile_wall_layout.setContentsMargins(0, 4, 0, 8)
        self.profile_wall_layout.setSpacing(10)

        self.profile_empty_label = QtWidgets.QLabel("暂无匹配的记忆标签")
        self.profile_empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.profile_empty_label.setStyleSheet(
            "color: #9CA3AF; padding: 40px; font-size: 14px;"
        )
        self.profile_empty_label.setVisible(False)
        self.profile_wall_layout.addWidget(self.profile_empty_label)

        self.profile_wall_layout.addStretch(1)
        self.profile_wall_scroll.setWidget(self.profile_wall_container)
        page_layout.addWidget(self.profile_wall_scroll, 1)

        # Hidden tree widget for backward compatibility
        self.profile_overview_tree = QtWidgets.QTreeWidget()
        self.profile_overview_tree.setVisible(False)

        self._profile_chips: List[MemoryTagChip] = []
        self._profile_card_widgets: List[QtWidgets.QFrame] = []

        self.tabs.addTab(page, "档案概览")

    def _build_memory_core_tab(self) -> None:
        page = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.memory_category_tree = QtWidgets.QTreeWidget()
        self.memory_category_tree.setObjectName("memoryCategoryTree")
        self.memory_category_tree.setHeaderHidden(True)
        self.memory_category_tree.setMinimumWidth(140)
        self.memory_category_tree.setMaximumWidth(240)
        self.memory_category_tree.currentItemChanged.connect(
            self._on_memory_category_changed
        )
        splitter.addWidget(self.memory_category_tree)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        filters_wrap = QtWidgets.QWidget()
        filters = FlowLayout(filters_wrap, margin=0, h_spacing=8, v_spacing=8)
        self.memory_core_search = QtWidgets.QLineEdit()
        self.memory_core_search.setPlaceholderText("在当前分类中搜索")
        self.memory_core_search.setMinimumWidth(140)
        self.memory_core_search.textChanged.connect(self._reload_memory_core_records)
        filters.addWidget(self.memory_core_search)

        self.memory_core_person_filter = QtWidgets.QComboBox()
        self.memory_core_person_filter.addItem("全部人物", "")
        self.memory_core_person_filter.addItem("owner", "owner")
        self.memory_core_person_filter.setCurrentIndex(1)
        self.memory_core_person_filter.currentIndexChanged.connect(
            self._reload_memory_core_records
        )
        filters.addWidget(self.memory_core_person_filter)

        self.memory_core_status_filter = QtWidgets.QComboBox()
        self.memory_core_status_filter.addItems(
            ["active", "archived", "superseded", "全部状态"]
        )
        self.memory_core_status_filter.currentIndexChanged.connect(
            self._reload_memory_core_records
        )
        filters.addWidget(self.memory_core_status_filter)

        refresh = QtWidgets.QPushButton("刷新")
        refresh.clicked.connect(self._reload_memory_core_records)
        filters.addWidget(refresh)
        right_layout.addWidget(filters_wrap)

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
        self.memory_core_table.setShowGrid(False)
        self.memory_core_table.setAlternatingRowColors(True)
        self.memory_core_table.verticalHeader().setVisible(False)
        self.memory_core_table.verticalHeader().setDefaultSectionSize(32)
        self.memory_core_table.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
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
        self.memory_core_content.setMinimumHeight(48)
        self.memory_core_content.setMaximumHeight(120)
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
            raw_display = str(person.get("display_name") or "").strip()
            display_name = re.sub(r"[\u25a0-\u25ff\ufffd]+", "", raw_display).strip()
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
            raw_name = str((payload or {}).get("name") or character_id).strip()
            name = re.sub(r"[\u25a0-\u25ff\ufffd]+", "", raw_name).strip()
            labels[subject_id] = f"角色 · {name or character_id}"

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
        if not hasattr(self, "profile_overview_tree") or not hasattr(
            self, "profile_wall_container"
        ):
            return
        selected_person_id = str(
            self.profile_person_filter.currentData() or "owner"
        ).strip() or "owner"
        if hasattr(self.memory_core, "list_current_memory_records"):
            rows = self.memory_core.list_current_memory_records(limit=1000)
        else:
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
        selected_label = labels.get(selected_person_id, selected_person_id)
        if selected_person_id == "owner":
            filtered_rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() in {"", "owner"}
            ]
        else:
            filtered_rows = [
                row
                for row in rows
                if str(row.get("subject_id") or "").strip() == selected_person_id
            ]

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in filtered_rows:
            grouped.setdefault(classify_memory_record(row), []).append(row)

        # 1. Update hidden tree for compatibility
        self.profile_overview_tree.clear()
        section_ids = ("identity", "likes", "dislikes", "habits", "status", "interaction")
        for section_id in section_ids:
            section = CATEGORY_BY_ID.get(section_id)
            if not section:
                continue
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

        # 2. Update Hero Card
        if selected_person_id == "owner":
            hero_name = "你"
        else:
            hero_name = (
                selected_label.replace("角色 · ", "")
                .replace("QQ · ", "")
                .replace("我的档案 · ", "")
                .strip()
            )
        self.profile_hero_title.setText(f"📖 TA 眼中的{hero_name}")
        self.profile_hero_badge.setText(f"已记录 {len(filtered_rows)} 项偏好与事实")

        # Rebuild Identity chips in Hero Card
        while self.profile_identity_chips_layout.count():
            item = self.profile_identity_chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._profile_chips.clear()
        identity_records = grouped.get("identity", [])
        if identity_records:
            for r in identity_records:
                chip = MemoryTagChip(r, category_id="identity")
                chip.clicked.connect(self._open_tag_detail)
                chip.deleted.connect(self._quick_delete_tag)
                self.profile_identity_chips_layout.addWidget(chip)
                self._profile_chips.append(chip)
        else:
            empty_lbl = QtWidgets.QLabel("(暂无专属称呼或身份设定)")
            empty_lbl.setStyleSheet("color: #9CA3AF; font-size: 11px;")
            self.profile_identity_chips_layout.addWidget(empty_lbl)

        # 3. Rebuild Category Wall
        for card in self._profile_card_widgets:
            self.profile_wall_layout.removeWidget(card)
            card.deleteLater()
        self._profile_card_widgets.clear()

        # Fold generic 'likes' into 'likes.other'
        direct_likes = grouped.get("likes", [])
        if direct_likes:
            grouped.setdefault("likes.other", []).extend(direct_likes)

        display_sections = [
            ("likes.music", "🎵", "音乐偏好"),
            ("likes.anime", "🌸", "追番与二次元"),
            ("likes.games", "🎮", "游戏偏好"),
            ("likes.food", "🍱", "饮食偏好"),
            ("likes.art", "🎨", "艺术与审美"),
            ("likes.other", "⭐", "其他喜好"),
            ("dislikes", "⚡", "雷区与讨厌"),
            ("habits", "☕", "作息与习惯"),
            ("status", "📌", "近期状态"),
            ("interaction", "💬", "互动与回复规则"),
            ("events", "📅", "重要经历与事件"),
            ("tasks", "📋", "待办与承诺"),
            ("uncategorized", "💡", "其他零散记忆"),
        ]

        for cat_id, emoji, default_title in display_sections:
            records = grouped.get(cat_id, [])
            if not records:
                continue

            card = QtWidgets.QFrame()
            card.setObjectName("memoryCategoryCard")

            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(10)

            header_layout = QtWidgets.QHBoxLayout()
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(8)

            header_lbl = QtWidgets.QLabel(f"{emoji} {default_title}")
            header_lbl.setObjectName("memoryCategoryHeader")
            header_layout.addWidget(header_lbl)

            count_badge = QtWidgets.QLabel(f"{len(records)} 项")
            count_badge.setObjectName("memoryCategoryCountBadge")
            header_layout.addWidget(count_badge)

            header_layout.addStretch(1)
            card_layout.addLayout(header_layout)

            flow_widget = FlowWidget(card, margin=0, h_spacing=8, v_spacing=8)
            for row in records:
                chip = MemoryTagChip(row, category_id=cat_id, parent=flow_widget)
                chip.clicked.connect(self._open_tag_detail)
                chip.deleted.connect(self._quick_delete_tag)
                flow_widget.addWidget(chip)
                self._profile_chips.append(chip)

            card_layout.addWidget(flow_widget)

            self._profile_card_widgets.append(card)
            self.profile_wall_layout.insertWidget(
                max(0, self.profile_wall_layout.count() - 1),
                card,
            )

        self._on_profile_search_changed(self.profile_search_input.text().strip())

    def _on_profile_search_changed(self, text: str = "") -> None:
        query = (text or "").strip().lower()
        visible_total = 0

        # Filter hero identity chips
        for i in range(self.profile_identity_chips_layout.count()):
            item = self.profile_identity_chips_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, MemoryTagChip):
                match = not query or (query in str(w.record.get("content") or "").lower())
                w.setVisible(match)
                if match:
                    visible_total += 1

        # Filter card chips & hide empty cards
        for card in self._profile_card_widgets:
            card_visible_chips = 0
            chips = card.findChildren(MemoryTagChip)
            for chip in chips:
                match = not query or (query in str(chip.record.get("content") or "").lower())
                chip.setVisible(match)
                if match:
                    card_visible_chips += 1
            card.setVisible(card_visible_chips > 0)
            badge = card.findChild(QtWidgets.QLabel, "memoryCategoryCountBadge")
            if badge:
                badge.setText(f"{card_visible_chips} 项" if query else f"{len(chips)} 项")
            visible_total += card_visible_chips

        if hasattr(self, "profile_empty_label"):
            has_records = len(self._profile_chips) > 0
            if not has_records:
                self.profile_empty_label.setText("当前档案暂无记忆记录，可点击上方「+ 新增记忆」添加")
                self.profile_empty_label.setVisible(True)
            elif visible_total == 0 and query:
                self.profile_empty_label.setText(f"未找到与 \"{query}\" 相关的记忆标签")
                self.profile_empty_label.setVisible(True)
            else:
                self.profile_empty_label.setVisible(False)

    def _open_tag_detail(self, record: Dict[str, Any]) -> None:
        if not record or not record.get("id"):
            return
        dlg = QuickMemoryEditDialog(record, parent=self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            if dlg.deleted_requested:
                self._quick_delete_tag(record)
                return
            updated = dlg.get_updated_data()
            record_id = record["id"]
            try:
                self.memory_core.update_memory_record(
                    record_id,
                    content=updated["content"],
                    kind=updated["kind"],
                )
                override = updated.get("category_override", "")
                self.memory_core.set_memory_category_override(record_id, override)
                self._reload_memory_core_records()
            except Exception as exc:
                _msg(self, "修改失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)

    def _quick_delete_tag(self, record: Dict[str, Any]) -> None:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            return
        content = str(record.get("content") or "")
        short_content = content if len(content) <= 30 else content[:27] + "..."
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认删除记忆",
            f"确定要删除这条记忆标签吗？\n\n「{short_content}」",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.memory_core.delete_memory_record(record_id)
            self._reload_memory_core_records()
        except Exception as exc:
            _msg(self, "删除失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)

    def _quick_add_memory(self) -> None:
        selected_person_id = str(
            self.profile_person_filter.currentData() or "owner"
        ).strip() or "owner"
        dlg = QuickMemoryAddDialog(default_subject=selected_person_id, parent=self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["content"]:
                return
            try:
                record_id = self.memory_core.upsert_memory_record(
                    content=data["content"],
                    kind=data["kind"],
                    subject_id=selected_person_id,
                    confidence=1.0,
                    importance=0.7,
                    manual_lock=True,
                    source_type="manual_gui",
                    source_id=uuid.uuid4().hex,
                )
                if data.get("category_override"):
                    self.memory_core.set_memory_category_override(
                        record_id,
                        data["category_override"],
                    )
                self._reload_memory_core_records()
            except Exception as exc:
                _msg(self, "添加失败", str(exc), QtWidgets.QMessageBox.Icon.Warning)

    def _reload_memory_core_records(self) -> None:
        selected_record_id = self.memory_core_id.text().strip()
        status_text = self.memory_core_status_filter.currentText()
        status = "" if status_text == "全部状态" else status_text
        if status == "active" and hasattr(self.memory_core, "list_current_memory_records"):
            rows = self.memory_core.list_current_memory_records(limit=1000)
        else:
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
        listed = self.memory_gui.list_transcript(
            role=role or "",
            query=self.transcript_search.text().strip(),
            limit=300,
            offset=0,
        )
        rows = list((listed.get("data") or {}).get("records") or []) if listed.get("ok") else []
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
        status_payload = self.memory_gui.vector_status()
        if not status_payload.get("ok"):
            self.vector_status_label.setText(
                f"读取向量状态失败：{status_payload.get('error') or 'unknown'}"
            )
            return
        status = dict(status_payload.get("data") or {})
        if not status.get("available"):
            self.vector_status_label.setText("当前向量索引未连接；SQLite 记忆仍可正常使用。")
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
        person_id = str(
            self.vector_person_filter.currentData() or "owner"
        ).strip() or "owner"
        found = self.memory_gui.query_vector(query, person_id=person_id, limit=10)
        if not found.get("ok"):
            self._refresh_vector_status()
            _msg(
                self,
                "搜索失败" if found.get("error") != "brain_unavailable" else "无法搜索",
                "当前向量索引未连接。"
                if found.get("error") == "brain_unavailable"
                else str(found.get("error") or "search_failed"),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        rows = list((found.get("data") or {}).get("records") or [])
        self.current_vector_results.clear()
        for row in rows or []:
            score = float(row.get("vector_score") or 0.0)
            document = str(row.get("document") or "").replace("\n", " ")
            self.current_vector_results.addItem(
                f"{score:.3f}  {str(row.get('id') or '')}  {document[:140]}"
            )
        self._refresh_vector_status()

    def _rebuild_vector_index(self) -> None:
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
        rebuilt = self.memory_gui.rebuild_vector_index()
        if not rebuilt.get("ok"):
            _msg(
                self,
                "无法重建" if rebuilt.get("error") == "brain_unavailable" else "重建失败",
                "当前向量索引未连接；SQLite 记忆没有受到影响。"
                if rebuilt.get("error") == "brain_unavailable"
                else str(rebuilt.get("error") or "rebuild_failed"),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        result = dict(rebuilt.get("data") or {})
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

        listed = self.memory_gui.list_legacy_vectors(limit=1)
        if not listed.get("ok"):
            raise RuntimeError(listed.get("error") or "legacy_vector_unavailable")
        count = int((listed.get("data") or {}).get("count") or 0)
        self._vector_initialized = True
        self.lbl_hint.setText(
            "SQLite 是记忆单一事实源；旧向量库条目数："
            f"{count}"
        )

    def _vector_search(self) -> None:
        query = self.vector_query.text().strip()
        if not query:
            return
        listed = self.memory_gui.list_legacy_vectors(
            query=query, limit=int(self.vector_limit.value())
        )
        if not listed.get("ok"):
            _msg(
                self,
                "搜索失败",
                str(listed.get("error") or "search_failed"),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        self._vector_rows = list((listed.get("data") or {}).get("records") or [])
        self._render_vector_rows()

    def _vector_list_some(self) -> None:
        listed = self.memory_gui.list_legacy_vectors(limit=30)
        if not listed.get("ok"):
            _msg(
                self,
                "读取失败",
                str(listed.get("error") or "list_failed"),
                QtWidgets.QMessageBox.Icon.Warning,
            )
            return
        self._vector_rows = list((listed.get("data") or {}).get("records") or [])
        self._render_vector_rows()

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
