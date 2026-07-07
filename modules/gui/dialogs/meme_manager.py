from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import get_tool_dialog_styles


class MemeManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self.plugin_manager = getattr(main_app, "plugin_manager", None)
        self._current_asset_id: Optional[int] = None
        self.setWindowTitle("表情包库")
        self.resize(1040, 680)
        self.setMinimumSize(900, 560)
        self._setup_ui()
        self._refresh_table()

    def _plugin(self):
        manager = self.plugin_manager
        if manager is None:
            return None
        return getattr(manager, "plugins", {}).get("meme_pack")

    def _store(self):
        plugin = self._plugin()
        if plugin is None or not hasattr(plugin, "_store_obj"):
            return None
        return plugin._store_obj()

    def _setup_ui(self):
        self.setStyleSheet(get_tool_dialog_styles())
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("dialogShell")
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)
        container_layout.setSpacing(15)

        header_card = QtWidgets.QFrame()
        header_card.setObjectName("dialogHeader")
        header_layout = QtWidgets.QVBoxLayout(header_card)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(6)

        title_row = QtWidgets.QHBoxLayout()
        icon_label = QtWidgets.QLabel("🖼️")
        icon_label.setStyleSheet("font-size: 22px;")
        title_row.addWidget(icon_label)
        
        title = QtWidgets.QLabel("表情包库管理")
        title.setObjectName("dialogTitle")
        title_row.addWidget(title, 1)
        
        self.stats_label = QtWidgets.QLabel("读取中...")
        self.stats_label.setStyleSheet(
            "color: #3B82F6; font-size: 13px; font-weight: bold;"
        )
        title_row.addWidget(self.stats_label)
        header_layout.addLayout(title_row)

        desc = QtWidgets.QLabel(
            "本地多模态表情包库管理。图片文件保存在素材目录中，数据库保存索引、情绪及关联标签。"
        )
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        header_layout.addWidget(desc)
        container_layout.addWidget(header_card)

        # 工具栏
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(8)
        
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索文件名 / 描述 / 标签 / 情绪")
        
        self.chk_include_disabled = QtWidgets.QCheckBox("显示禁用")
        self.chk_include_disabled.setChecked(True)
        
        self.btn_refresh = QtWidgets.QPushButton("⟳ 刷新")
        self.btn_refresh.setObjectName("main_btn")
        
        self.btn_import_files = QtWidgets.QPushButton("📥 导入图片")
        self.btn_import_dir = QtWidgets.QPushButton("📂 导入目录")
        self.btn_stats = QtWidgets.QPushButton("📊 统计信息")

        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.chk_include_disabled)
        toolbar.addWidget(self.btn_refresh)
        toolbar.addWidget(self.btn_import_files)
        toolbar.addWidget(self.btn_import_dir)
        toolbar.addWidget(self.btn_stats)
        container_layout.addLayout(toolbar)

        split = QtWidgets.QSplitter()
        split.setOrientation(QtCore.Qt.Orientation.Horizontal)
        split.setStyleSheet("QSplitter::handle { background: #E5E7EB; width: 1px; }")
        container_layout.addWidget(split, 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "启用", "情绪", "标签", "描述", "使用", "最近使用", "文件"]
        )
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(36)
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.Stretch)
        left_layout.addWidget(self.table, 1)

        table_actions = QtWidgets.QHBoxLayout()
        table_actions.setSpacing(8)
        self.btn_enable = QtWidgets.QPushButton("🟢 批量启用")
        self.btn_disable = QtWidgets.QPushButton("🔴 批量禁用")
        
        self.btn_delete = QtWidgets.QPushButton("🗑️ 删除记录")
        self.btn_delete_files = QtWidgets.QPushButton("⚠️ 删除记录与文件")
        
        danger_qss = """
            QPushButton {
                color: #EF4444;
                border: 1px solid #FECACA;
                background-color: #FEF2F2;
            }
            QPushButton:hover {
                background-color: #EF4444;
                color: white;
                border-color: #EF4444;
            }
        """
        self.btn_delete.setStyleSheet(danger_qss)
        self.btn_delete_files.setStyleSheet(danger_qss)

        table_actions.addWidget(self.btn_enable)
        table_actions.addWidget(self.btn_disable)
        table_actions.addStretch()
        table_actions.addWidget(self.btn_delete)
        table_actions.addWidget(self.btn_delete_files)
        left_layout.addLayout(table_actions)
        split.addWidget(left)

        right = QtWidgets.QFrame()
        right.setObjectName("dialogSection")
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(10)

        preview_title = QtWidgets.QLabel("🖼️ 预览与编辑")
        preview_title.setStyleSheet("font-weight:700; color:#111827; font-size: 14px;")
        right_layout.addWidget(preview_title)

        self.preview = QtWidgets.QLabel("未选择")
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 220)
        self.preview.setStyleSheet(
            "QLabel { background:#FFFFFF; border:1px solid #E5E7EB; border-radius:8px; color:#9CA3AF; }"
        )
        right_layout.addWidget(self.preview)

        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(8)
        self.chk_enabled = QtWidgets.QCheckBox("启用")
        self.chk_banned = QtWidgets.QCheckBox("禁用/封存")
        self.edit_emotion = QtWidgets.QLineEdit()
        self.edit_emotion.setPlaceholderText("例如：调侃 / 亲近 / 安慰")
        self.edit_tags = QtWidgets.QLineEdit()
        self.edit_tags.setPlaceholderText("逗号分隔，例如：狡猾,偷笑")
        self.edit_desc = QtWidgets.QPlainTextEdit()
        self.edit_desc.setPlaceholderText("描述这个表情适合的语气/情境...")
        self.edit_desc.setFixedHeight(75)
        self.path_label = QtWidgets.QLabel("-")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.path_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        
        state_row = QtWidgets.QHBoxLayout()
        state_row.addWidget(self.chk_enabled)
        state_row.addWidget(self.chk_banned)
        state_row.addStretch()
        form.addRow("状态", state_row)
        form.addRow("情绪", self.edit_emotion)
        form.addRow("标签", self.edit_tags)
        form.addRow("描述", self.edit_desc)
        form.addRow("文件", self.path_label)
        right_layout.addLayout(form)

        self.btn_save = QtWidgets.QPushButton("💾 保存当前编辑")
        self.btn_save.setObjectName("primary_btn")
        right_layout.addWidget(self.btn_save)
        right_layout.addStretch()
        split.addWidget(right)
        split.setSizes([680, 340])

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch()
        self.btn_close = QtWidgets.QPushButton("关闭")
        self.btn_close.setObjectName("main_btn")
        self.btn_close.setMinimumWidth(80)
        bottom.addWidget(self.btn_close)
        container_layout.addLayout(bottom)
        
        layout.addWidget(self.container)

        self.search_input.textChanged.connect(self._refresh_table)
        self.chk_include_disabled.toggled.connect(self._refresh_table)
        self.btn_refresh.clicked.connect(self._refresh_table)
        self.btn_import_files.clicked.connect(self._import_files)
        self.btn_import_dir.clicked.connect(self._import_dir)
        self.btn_stats.clicked.connect(self._show_stats)
        self.btn_enable.clicked.connect(lambda: self._set_selected_enabled(True))
        self.btn_disable.clicked.connect(lambda: self._set_selected_enabled(False))
        self.btn_delete.clicked.connect(lambda: self._delete_selected(False))
        self.btn_delete_files.clicked.connect(lambda: self._delete_selected(True))
        self.btn_save.clicked.connect(self._save_current)
        self.btn_close.clicked.connect(self.close)
        self.table.itemSelectionChanged.connect(self._load_selected_asset)

    def _format_time(self, ts: float) -> str:
        if not ts:
            return "-"
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
        except Exception:
            return "-"

    def _refresh_table(self):
        store = self._store()
        if store is None:
            self.stats_label.setText("meme_pack 插件未加载")
            return
        query = self.search_input.text().strip() if hasattr(self, "search_input") else ""
        include_disabled = (
            self.chk_include_disabled.isChecked()
            if hasattr(self, "chk_include_disabled")
            else True
        )
        rows = store.search_assets(query, include_disabled=include_disabled, limit=800)
        stats = store.stats()
        self.stats_label.setText(
            f"共 {stats['total']} 张，可用 {stats['enabled']} 张，禁用 {stats['banned']} 张，累计使用 {stats['usage_count']} 次"
        )
        self.table.setRowCount(0)
        for asset in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                str(asset.id),
                "是" if asset.enabled and not asset.banned else "否",
                asset.emotion,
                ", ".join(asset.tags),
                asset.description,
                str(asset.usage_count),
                self._format_time(asset.last_used_at),
                asset.file_name,
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, asset.id)
                if col == 1 and value == "否":
                    item.setForeground(QtGui.QColor("#B91C1C"))
                self.table.setItem(row, col, item)

    def _selected_ids(self) -> list[int]:
        ids: list[int] = []
        for item in self.table.selectedItems():
            asset_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if asset_id is not None and int(asset_id) not in ids:
                ids.append(int(asset_id))
        return ids

    def _load_selected_asset(self):
        ids = self._selected_ids()
        if not ids:
            return
        store = self._store()
        if store is None:
            return
        asset = store.get_asset(ids[0])
        if asset is None:
            return
        self._current_asset_id = asset.id
        self.chk_enabled.setChecked(asset.enabled)
        self.chk_banned.setChecked(asset.banned)
        self.edit_emotion.setText(asset.emotion)
        self.edit_tags.setText(", ".join(asset.tags))
        self.edit_desc.setPlainText(asset.description)
        self.path_label.setText(asset.file_path)
        self._set_preview(asset.file_path)

    def _set_preview(self, path_text: str):
        path = Path(path_text)
        if not path.exists():
            self.preview.setText("文件不存在")
            self.preview.setPixmap(QtGui.QPixmap())
            return
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            self.preview.setText(path.name)
            self.preview.setPixmap(QtGui.QPixmap())
            return
        scaled = pixmap.scaled(
            self.preview.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(scaled)

    def _save_current(self):
        if self._current_asset_id is None:
            QtWidgets.QMessageBox.information(self, "表情包库", "先选择一张表情包。")
            return
        store = self._store()
        if store is None:
            return
        ok = store.update_asset(
            self._current_asset_id,
            description=self.edit_desc.toPlainText(),
            tags=[x.strip() for x in self.edit_tags.text().replace("，", ",").split(",")],
            emotion=self.edit_emotion.text(),
            enabled=self.chk_enabled.isChecked(),
            banned=self.chk_banned.isChecked(),
        )
        if ok:
            self._refresh_table()
            QtWidgets.QMessageBox.information(self, "表情包库", "已保存。")
        else:
            QtWidgets.QMessageBox.warning(self, "表情包库", "保存失败。")

    def _import_files(self):
        store = self._store()
        if store is None:
            return
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "导入表情包图片",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp)",
        )
        if not files:
            return
        tag_text, ok = QtWidgets.QInputDialog.getText(
            self, "导入标签", "给这批图片加标签（可留空，逗号分隔）："
        )
        if not ok:
            return
        tags = [x.strip() for x in str(tag_text).replace("，", ",").split(",") if x.strip()]
        imported = skipped = 0
        for path in files:
            ok_file, _ = store.import_file(path, tags=tags)
            if ok_file:
                imported += 1
            else:
                skipped += 1
        self._refresh_table()
        QtWidgets.QMessageBox.information(
            self, "导入表情包", f"导入完成：新增 {imported}，跳过 {skipped}。"
        )

    def _import_dir(self):
        store = self._store()
        if store is None:
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择表情包目录", str(Path.home())
        )
        if not directory:
            return
        tag_text, ok = QtWidgets.QInputDialog.getText(
            self, "导入标签", "给这个目录加标签（可留空，逗号分隔）："
        )
        if not ok:
            return
        tags = [x.strip() for x in str(tag_text).replace("，", ",").split(",") if x.strip()]
        stats = store.import_directory(directory, tags=tags)
        self._refresh_table()
        QtWidgets.QMessageBox.information(
            self,
            "导入表情包",
            f"导入完成：新增 {stats['imported']}，跳过 {stats['skipped']}，失败 {stats['failed']}。",
        )

    def _set_selected_enabled(self, enabled: bool):
        ids = self._selected_ids()
        if not ids:
            QtWidgets.QMessageBox.information(self, "表情包库", "先选择表情包。")
            return
        store = self._store()
        if store is None:
            return
        count = store.set_enabled(ids, enabled)
        self._refresh_table()
        QtWidgets.QMessageBox.information(
            self, "表情包库", f"已{'启用' if enabled else '禁用'} {count} 张。"
        )

    def _delete_selected(self, delete_files: bool):
        ids = self._selected_ids()
        if not ids:
            QtWidgets.QMessageBox.information(self, "表情包库", "先选择表情包。")
            return
        text = "确定删除选中记录和本地图片文件？" if delete_files else "确定删除选中数据库记录？"
        if (
            QtWidgets.QMessageBox.question(self, "删除表情包", text)
            != QtWidgets.QMessageBox.StandardButton.Yes
        ):
            return
        store = self._store()
        if store is None:
            return
        count = store.delete_assets(ids, delete_files=delete_files)
        self._current_asset_id = None
        self._refresh_table()
        QtWidgets.QMessageBox.information(self, "表情包库", f"已删除 {count} 条。")

    def _show_stats(self):
        store = self._store()
        if store is None:
            return
        stats = store.stats()
        QtWidgets.QMessageBox.information(
            self,
            "表情包统计",
            "\n".join(
                [
                    f"总数：{stats['total']}",
                    f"可用：{stats['enabled']}",
                    f"禁用：{stats['banned']}",
                    f"累计使用：{stats['usage_count']}",
                ]
            ),
        )
