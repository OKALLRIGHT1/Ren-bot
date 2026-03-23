from __future__ import annotations

import asyncio
from typing import List

from PySide6 import QtCore, QtWidgets


class KnowledgeManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self.plugin_manager = getattr(main_app, "plugin_manager", None)
        self.setWindowTitle("知识库管理")
        self.resize(760, 560)
        self.setMinimumSize(680, 500)
        self._setup_ui()
        self._load_dirs_from_plugin()
        self._refresh_stats()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("本地知识库")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #111827;")
        layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "在这里选择知识目录，一键学习，并直接搜索验证结果。支持 .md / .txt / .py / .json。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4B5563; font-size: 13px;")
        layout.addWidget(desc)

        self.stats_label = QtWidgets.QLabel("知识片段数：读取中...")
        self.stats_label.setStyleSheet(
            "color: #2563EB; font-size: 12px; font-weight: 600;"
        )
        layout.addWidget(self.stats_label)

        split = QtWidgets.QSplitter()
        split.setOrientation(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(split, 1)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.dir_table = QtWidgets.QTableWidget(0, 2)
        self.dir_table.setHorizontalHeaderLabels(["启用", "目录"])
        self.dir_table.horizontalHeader().setStretchLastSection(True)
        self.dir_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.dir_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.dir_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.dir_table.verticalHeader().setVisible(False)
        top_layout.addWidget(self.dir_table, 1)

        dir_btns = QtWidgets.QHBoxLayout()
        self.btn_add_dir = QtWidgets.QPushButton("添加目录")
        self.btn_remove_dir = QtWidgets.QPushButton("移除选中")
        self.btn_save_dirs = QtWidgets.QPushButton("保存目录")
        self.btn_learn = QtWidgets.QPushButton("一键学习")
        self.btn_delete_selected = QtWidgets.QPushButton("删除选中目录知识")
        self.btn_rebuild = QtWidgets.QPushButton("清空并重建")
        dir_btns.addWidget(self.btn_add_dir)
        dir_btns.addWidget(self.btn_remove_dir)
        dir_btns.addWidget(self.btn_delete_selected)
        dir_btns.addStretch()
        dir_btns.addWidget(self.btn_rebuild)
        dir_btns.addWidget(self.btn_save_dirs)
        dir_btns.addWidget(self.btn_learn)
        top_layout.addLayout(dir_btns)

        recent_label = QtWidgets.QLabel("最近学习目录")
        recent_label.setStyleSheet("color: #6B7280; font-size: 12px; font-weight: 600;")
        top_layout.addWidget(recent_label)
        self.recent_box = QtWidgets.QPlainTextEdit()
        self.recent_box.setReadOnly(True)
        self.recent_box.setMaximumHeight(88)
        top_layout.addWidget(self.recent_box)

        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        search_row = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("输入关键词验证知识库，例如：NapCat 配置")
        self.btn_search = QtWidgets.QPushButton("搜索验证")
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.btn_search)
        bottom_layout.addLayout(search_row)

        self.result_box = QtWidgets.QPlainTextEdit()
        self.result_box.setReadOnly(True)
        bottom_layout.addWidget(self.result_box, 1)

        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([260, 240])

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        self.btn_close = QtWidgets.QPushButton("关闭")
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.btn_add_dir.clicked.connect(self._add_dir)
        self.btn_remove_dir.clicked.connect(self._remove_selected_dirs)
        self.btn_save_dirs.clicked.connect(self._save_dirs)
        self.btn_learn.clicked.connect(self._learn_dirs)
        self.btn_delete_selected.clicked.connect(
            self._delete_selected_dirs_from_knowledge
        )
        self.btn_rebuild.clicked.connect(self._rebuild_knowledge)
        self.btn_search.clicked.connect(self._search_knowledge)
        self.btn_close.clicked.connect(self.accept)
        self.dir_table.itemChanged.connect(lambda *_: self._refresh_recent_dirs())

    def _knowledge_plugin(self):
        if self.plugin_manager is None:
            return None
        return self.plugin_manager.plugins.get("knowledge_base")

    def _brain(self):
        brain = getattr(self.main_app, "brain", None)
        if brain is not None:
            return brain
        plugin = self._knowledge_plugin()
        if plugin is not None:
            candidate = getattr(plugin, "brain", None)
            if candidate is not None:
                return candidate
        return None

    def _load_dirs_from_plugin(self):
        if self.plugin_manager is None:
            return
        config = self.plugin_manager.plugin_configs.get("knowledge_base") or {}
        settings = config.get("settings") or {}
        field = settings.get("knowledge_source_dirs") or {}
        dirs = field.get("default", []) if isinstance(field, dict) else field
        self.dir_table.setRowCount(0)
        if isinstance(dirs, list):
            for item in dirs:
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    enabled = bool(item.get("enabled", True))
                else:
                    path = str(item or "").strip()
                    enabled = True
                if path:
                    self._append_dir_row(path, enabled)

        legacy_dir = str((QtCore.QDir.currentPath()))
        legacy_path = QtCore.QDir(legacy_dir).filePath("knowledge_docs")
        if QtCore.QFileInfo(legacy_path).isDir():
            existing = [str(item.get("path") or "") for item in self._collect_dirs()]
            norm_legacy = str(QtCore.QFileInfo(legacy_path).absoluteFilePath())
            if norm_legacy not in existing:
                self._append_dir_row(norm_legacy, True)

    def _append_dir_row(self, path: str, enabled: bool = True):
        row = self.dir_table.rowCount()
        self.dir_table.insertRow(row)
        check_item = QtWidgets.QTableWidgetItem()
        check_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsUserCheckable
            | QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        check_item.setCheckState(
            QtCore.Qt.CheckState.Checked if enabled else QtCore.Qt.CheckState.Unchecked
        )
        path_item = QtWidgets.QTableWidgetItem(path)
        path_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        self.dir_table.setItem(row, 0, check_item)
        self.dir_table.setItem(row, 1, path_item)

    def _collect_dirs(self) -> List[dict]:
        dirs = []
        known = set()
        for row in range(self.dir_table.rowCount()):
            check_item = self.dir_table.item(row, 0)
            path_item = self.dir_table.item(row, 1)
            text = path_item.text().strip() if path_item else ""
            enabled = bool(
                check_item and check_item.checkState() == QtCore.Qt.CheckState.Checked
            )
            if text and text not in known:
                known.add(text)
                dirs.append({"path": text, "enabled": enabled})
        return dirs

    def _refresh_recent_dirs(self):
        dirs = self._collect_dirs()
        if not dirs:
            self.recent_box.setPlainText("暂无已配置知识目录。")
            return
        lines = []
        for item in dirs[-8:]:
            mark = "ON" if item.get("enabled", True) else "OFF"
            lines.append(f"[{mark}] {item.get('path', '')}")
        self.recent_box.setPlainText("\n".join(lines))

    def _refresh_stats(self):
        brain = self._brain()
        if brain is None or not hasattr(brain, "get_knowledge_stats"):
            self.stats_label.setText("知识片段数：未知")
            self._refresh_recent_dirs()
            return
        try:
            stats = brain.get_knowledge_stats()
            chunk_count = int(stats.get("chunk_count", 0))
            self.stats_label.setText(f"知识片段数：{chunk_count}")
        except Exception:
            self.stats_label.setText("知识片段数：读取失败")
        self._refresh_recent_dirs()

    def _add_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择知识目录")
        if path:
            existing = [str(item.get("path") or "") for item in self._collect_dirs()]
            if path not in existing:
                self._append_dir_row(path, True)

    def _remove_selected_dirs(self):
        rows = sorted(
            {index.row() for index in self.dir_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            self.dir_table.removeRow(row)

    def _selected_dir_paths(self) -> List[str]:
        paths = []
        rows = {index.row() for index in self.dir_table.selectionModel().selectedRows()}
        for row in sorted(rows):
            item = self.dir_table.item(row, 1)
            if item and item.text().strip():
                paths.append(item.text().strip())
        return paths

    def _save_dirs(self) -> bool:
        if self.plugin_manager is None:
            QtWidgets.QMessageBox.warning(self, "失败", "当前上下文没有插件管理器。")
            return False
        config = dict(self.plugin_manager.plugin_configs.get("knowledge_base") or {})
        settings = dict(config.get("settings") or {})
        field = dict(settings.get("knowledge_source_dirs") or {})
        field["default"] = self._collect_dirs()
        settings["knowledge_source_dirs"] = field
        config["settings"] = settings
        ok = self.plugin_manager.save_plugin_config("knowledge_base", config)
        if ok:
            QtWidgets.QMessageBox.information(self, "成功", "知识目录已保存。")
            self._refresh_recent_dirs()
        else:
            QtWidgets.QMessageBox.warning(self, "失败", "保存知识目录失败。")
        return bool(ok)

    def _learn_dirs(self):
        if not self._save_dirs():
            return
        plugin = self._knowledge_plugin()
        brain = self._brain()
        if plugin is None or brain is None:
            QtWidgets.QMessageBox.warning(self, "失败", "知识库插件或 brain 未就绪。")
            return
        try:
            result = asyncio.run(plugin.gui_ingest_configured_dirs({"brain": brain}))
            self.result_box.setPlainText(str(result))
            self._refresh_stats()
            QtWidgets.QMessageBox.information(self, "学习结果", str(result))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"学习失败: {e}")

    def _rebuild_knowledge(self):
        brain = self._brain()
        if brain is None or not hasattr(brain, "rebuild_knowledge_collection"):
            QtWidgets.QMessageBox.warning(self, "失败", "brain 不支持重建知识库。")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认",
            "这会清空当前知识库并重新创建集合，是否继续？",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        ok = bool(brain.rebuild_knowledge_collection())
        if ok:
            self.result_box.setPlainText(
                "✅ 已清空并重建知识库。你现在可以重新点“一键学习”。"
            )
            self._refresh_stats()
            QtWidgets.QMessageBox.information(self, "完成", "知识库已重建。")
        else:
            QtWidgets.QMessageBox.warning(self, "失败", "知识库重建失败。")

    def _delete_selected_dirs_from_knowledge(self):
        brain = self._brain()
        if brain is None or not hasattr(brain, "delete_knowledge_by_dirs"):
            QtWidgets.QMessageBox.warning(self, "失败", "brain 不支持按目录删除知识。")
            return
        targets = self._selected_dir_paths()
        if not targets:
            QtWidgets.QMessageBox.information(
                self, "提示", "请先在目录表里选中要删除知识的目录。"
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认",
            "这会删除这些目录已经导入的知识片段，但不会删除目录配置，是否继续？",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        removed = int(brain.delete_knowledge_by_dirs(targets))
        self.result_box.setPlainText(f"✅ 已按目录删除 {removed} 条知识片段。")
        self._refresh_stats()
        QtWidgets.QMessageBox.information(
            self, "完成", f"已删除 {removed} 条知识片段。"
        )

    def _search_knowledge(self):
        brain = self._brain()
        query = self.search_input.text().strip()
        if brain is None:
            QtWidgets.QMessageBox.warning(self, "失败", "brain 未就绪。")
            return
        if not query:
            QtWidgets.QMessageBox.information(self, "提示", "请先输入查询词。")
            return
        try:
            results = brain.search_knowledge(query, 5)
            if not results:
                self.result_box.setPlainText("📭 知识库中没有找到相关内容。")
                return
            text = "\n\n---\n\n".join(results)
            self.result_box.setPlainText(text)
        except Exception as e:
            self.result_box.setPlainText(f"❌ 搜索失败: {e}")
