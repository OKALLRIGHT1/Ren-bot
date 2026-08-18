from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List

from PySide6 import QtCore, QtWidgets

from modules.gui.styles import get_tool_dialog_styles
from modules.gui.utils import FlowLayout
from modules.gui.dialogs.chat_record_import_wizard import ChatRecordImportWizardDialog


class KnowledgeImportWorker(QtCore.QObject):
    progress = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)

    def __init__(self, importer, paths):
        super().__init__()
        self.importer = importer
        self.paths = list(paths or [])

    def _import_file(self, path: str, progress_callback=None):
        importer = self.importer
        if hasattr(importer, "import_knowledge_from_file"):
            return importer.import_knowledge_from_file(
                path, progress_callback=progress_callback
            )
        if hasattr(importer, "import_file"):
            wrapped = importer.import_file(path, progress_callback=progress_callback)
            if isinstance(wrapped, dict) and "data" in wrapped:
                raw = (wrapped.get("data") or {}).get("result")
                if wrapped.get("ok") is False and not isinstance(raw, dict):
                    return {"ok": False, "error": wrapped.get("error") or "import_failed"}
                return raw
            return wrapped
        return importer(path, progress_callback=progress_callback)

    @QtCore.Slot()
    def run(self):
        from services.gui_api.knowledge_service import ingest_knowledge_paths

        payload = ingest_knowledge_paths(
            self.paths,
            import_file=self._import_file,
            on_progress=self.progress.emit,
        )
        self.finished.emit(
            {
                "file_count": int(payload.get("file_count") or 0),
                "added": int(payload.get("added") or 0),
                "skipped": int(payload.get("skipped") or 0),
                "failed": int(payload.get("failed") or 0),
                "results": list(payload.get("results") or []),
            }
        )


class KnowledgeDocGeneratorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, dirs: List[dict] | None = None):
        super().__init__(parent)
        self._dirs = dirs or []
        self.setWindowTitle("生成知识文档")
        self.resize(680, 560)
        self.setMinimumSize(600, 480)
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet(get_tool_dialog_styles())
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("dialogShell")
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)
        container_layout.setSpacing(12)

        header_card = QtWidgets.QFrame()
        header_card.setObjectName("dialogHeader")
        header_layout = QtWidgets.QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)
        
        title_label = QtWidgets.QLabel("✍️ 新建知识文档")
        title_label.setObjectName("dialogTitle")
        header_layout.addWidget(title_label)
        
        desc_label = QtWidgets.QLabel("输入事实和设定，按行整理成专属知识片段并直接导入。")
        desc_label.setObjectName("dialogDesc")
        desc_label.setWordWrap(True)
        header_layout.addWidget(desc_label)
        container_layout.addWidget(header_card)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.inp_title = QtWidgets.QLineEdit()
        self.inp_title.setPlaceholderText("例如：五十铃怜设定补充")
        form.addRow("标题", self.inp_title)

        self.inp_source = QtWidgets.QLineEdit()
        self.inp_source.setPlaceholderText("例如：manual / wiki / user_note")
        form.addRow("来源", self.inp_source)

        self.inp_tags = QtWidgets.QLineEdit()
        self.inp_tags.setPlaceholderText("用逗号分隔，例如：设定, 口吻, 用户偏好")
        form.addRow("标签", self.inp_tags)

        target_row = QtWidgets.QHBoxLayout()
        self.cmb_target_dir = QtWidgets.QComboBox()
        self._load_target_dirs()
        self.btn_browse = QtWidgets.QPushButton("选择目录")
        target_row.addWidget(self.cmb_target_dir, 1)
        target_row.addWidget(self.btn_browse)
        form.addRow("保存到", target_row)

        container_layout.addLayout(form)

        hint = QtWidgets.QLabel(
            "建议一行写一条事实。当前知识库会按非空行切成知识片段，短而完整的句子更容易检索。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B7280; font-size: 12px;")
        container_layout.addWidget(hint)

        self.txt_content = QtWidgets.QPlainTextEdit()
        self.txt_content.setPlaceholderText(
            "五十铃怜说话冷静克制，避免客服式总结。\n"
            "用户更喜欢她像身边人一样自然接话。\n"
            "回答设定问题时，优先遵守角色边界，不提自己是 AI。"
        )
        container_layout.addWidget(self.txt_content, 1)

        self.chk_ingest_now = QtWidgets.QCheckBox("生成后立即学习到知识库")
        self.chk_ingest_now.setChecked(True)
        container_layout.addWidget(self.chk_ingest_now)

        btns_layout = QtWidgets.QHBoxLayout()
        btns_layout.addStretch()
        self.btn_cancel = QtWidgets.QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_save = QtWidgets.QPushButton("生成文档")
        self.btn_save.setObjectName("primary_btn")
        self.btn_save.clicked.connect(self.accept)
        
        btns_layout.addWidget(self.btn_cancel)
        btns_layout.addWidget(self.btn_save)
        container_layout.addLayout(btns_layout)

        layout.addWidget(self.container)

        self.btn_browse.clicked.connect(self._browse_dir)

    def _load_target_dirs(self):
        known = set()
        for item in self._dirs:
            path = str((item or {}).get("path") or "").strip()
            if path and path not in known:
                known.add(path)
                self.cmb_target_dir.addItem(path, path)
        default_dir = str(Path.cwd() / "knowledge_docs")
        if default_dir not in known:
            self.cmb_target_dir.addItem(default_dir, default_dir)

    def _browse_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择知识文档保存目录")
        if not path:
            return
        idx = self.cmb_target_dir.findData(path)
        if idx < 0:
            self.cmb_target_dir.addItem(path, path)
            idx = self.cmb_target_dir.findData(path)
        self.cmb_target_dir.setCurrentIndex(max(0, idx))

    def _knowledge_lines(self) -> List[str]:
        lines = []
        for raw in self.txt_content.toPlainText().splitlines():
            text = raw.strip().strip("-* \t")
            if text and text not in lines:
                lines.append(text)
        return lines

    def accept(self):
        if not self.inp_title.text().strip():
            QtWidgets.QMessageBox.warning(self, "校验失败", "标题不能为空。")
            return
        if not self._knowledge_lines():
            QtWidgets.QMessageBox.warning(self, "校验失败", "正文至少需要一条知识。")
            return
        super().accept()

    def payload(self) -> dict:
        title = self.inp_title.text().strip()
        source = self.inp_source.text().strip() or "manual"
        tags = [
            item.strip()
            for item in re.split(r"[,，;；\s]+", self.inp_tags.text().strip())
            if item.strip()
        ]
        target_dir = str(self.cmb_target_dir.currentData() or "").strip()
        return {
            "title": title,
            "source": source,
            "tags": tags,
            "target_dir": target_dir,
            "lines": self._knowledge_lines(),
            "ingest_now": self.chk_ingest_now.isChecked(),
        }


class KnowledgeManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, main_app=None, knowledge_gui=None):
        super().__init__(parent)
        self.main_app = main_app
        self.plugin_manager = getattr(main_app, "plugin_manager", None)
        if knowledge_gui is not None:
            self.knowledge_gui = knowledge_gui
        else:
            from services.gui_api.knowledge_service import KnowledgeGuiService

            self.knowledge_gui = KnowledgeGuiService(
                plugin_manager=self.plugin_manager,
                brain=self._brain(),
            )
        self._knowledge_import_progress = None
        self._knowledge_job_kind = ""
        self.setWindowTitle("知识库管理")
        self.resize(920, 640)
        # 独立窗口可用下限；嵌入设置页时由 apply_embedded_mode 清零。
        self.setMinimumSize(680, 420)
        self._setup_ui()
        self._load_dirs_from_plugin()
        self._refresh_stats()

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
        icon_label = QtWidgets.QLabel("📚")
        icon_label.setStyleSheet("font-size: 22px;")
        title_row.addWidget(icon_label)
        
        title = QtWidgets.QLabel("本地知识库管理")
        title.setObjectName("dialogTitle")
        title_row.addWidget(title, 1)
        
        self.stats_label = QtWidgets.QLabel("读取中...")
        self.stats_label.setStyleSheet(
            "color: #3B82F6; font-size: 13px; font-weight: bold;"
        )
        title_row.addWidget(self.stats_label)
        header_layout.addLayout(title_row)

        desc = QtWidgets.QLabel(
            "在这里管理知识来源目录，一键学习并生成语义检索库。支持 .md、.txt、.py、.json。"
            "升级后的旧按行碎片不会自动迁移：先点「重建索引库」清空，再点「一键学习」。"
            "未改文件且已是新分块时会跳过；标题栏出现「需要重建」时必须先重建。"
        )
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        header_layout.addWidget(desc)
        container_layout.addWidget(header_card)

        split = QtWidgets.QSplitter()
        split.setOrientation(QtCore.Qt.Orientation.Vertical)
        split.setStyleSheet("QSplitter::handle { background: #E5E7EB; height: 1px; }")
        container_layout.addWidget(split, 1)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        self.dir_table = QtWidgets.QTableWidget(0, 2)
        self.dir_table.setHorizontalHeaderLabels(["启用", "知识目录"])
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
        self.dir_table.setShowGrid(False)
        self.dir_table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.dir_table.verticalHeader().setDefaultSectionSize(40)
        self.dir_table.setMinimumHeight(100)
        top_layout.addWidget(self.dir_table, 1)

        action_panel = QtWidgets.QFrame()
        action_panel.setObjectName("dialogSection")
        action_layout = QtWidgets.QVBoxLayout(action_panel)
        action_layout.setContentsMargins(14, 12, 14, 12)
        action_layout.setSpacing(10)

        self.btn_generate_doc = QtWidgets.QPushButton("✍️ 生成知识文档")
        self.btn_import_file = QtWidgets.QPushButton("📥 导入知识文件")
        self.btn_import_chat = QtWidgets.QPushButton("💬 导入聊天记录")
        self.btn_add_dir = QtWidgets.QPushButton("➕ 添加目录")
        self.btn_remove_dir = QtWidgets.QPushButton("➖ 移除选中")
        self.btn_save_dirs = QtWidgets.QPushButton("💾 保存目录")
        self.btn_learn = QtWidgets.QPushButton("⚡ 一键学习")
        self.btn_delete_selected = QtWidgets.QPushButton("🗑️ 清理选中目录知识")
        self.btn_rebuild = QtWidgets.QPushButton("⚠️ 重建索引库")

        self.btn_learn.setObjectName("primary_btn")

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
            QPushButton:pressed {
                background-color: #DC2626;
                color: white;
            }
        """
        self.btn_delete_selected.setStyleSheet(danger_qss)
        self.btn_rebuild.setStyleSheet(danger_qss)

        def _action_section(title: str, title_color: str, buttons: list[QtWidgets.QWidget]):
            section = QtWidgets.QWidget()
            section_layout = QtWidgets.QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(6)
            label = QtWidgets.QLabel(title)
            label.setStyleSheet(f"color:{title_color}; font-weight:600;")
            section_layout.addWidget(label)
            row_wrap = QtWidgets.QWidget()
            row = FlowLayout(row_wrap, margin=0, h_spacing=8, v_spacing=8)
            for btn in buttons:
                row.addWidget(btn)
            section_layout.addWidget(row_wrap)
            return section

        action_layout.addWidget(
            _action_section(
                "导入与生成",
                "#374151",
                [self.btn_generate_doc, self.btn_import_file, self.btn_import_chat],
            )
        )
        action_layout.addWidget(
            _action_section(
                "目录管理",
                "#374151",
                [
                    self.btn_add_dir,
                    self.btn_remove_dir,
                    self.btn_save_dirs,
                    self.btn_learn,
                ],
            )
        )
        action_layout.addWidget(
            _action_section(
                "危险操作",
                "#B91C1C",
                [self.btn_delete_selected, self.btn_rebuild],
            )
        )

        top_layout.addWidget(action_panel)

        recent_layout = QtWidgets.QHBoxLayout()
        recent_label = QtWidgets.QLabel("📋 最近学习目录")
        recent_label.setStyleSheet("color: #4B5563; font-size: 12px; font-weight: bold;")
        recent_layout.addWidget(recent_label)
        recent_layout.addStretch()
        top_layout.addLayout(recent_layout)

        self.recent_box = QtWidgets.QPlainTextEdit()
        self.recent_box.setReadOnly(True)
        self.recent_box.setMaximumHeight(70)
        self.recent_box.setStyleSheet("background-color: #F9FAFB; font-size: 12px; color: #4B5563;")
        top_layout.addWidget(self.recent_box)

        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(10)

        search_row = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 输入关键词检索验证知识库内容...")
        self.search_input.setStyleSheet("padding: 8px; font-size: 13px;")
        
        self.btn_search = QtWidgets.QPushButton("🔍 检索验证")
        self.btn_search.setObjectName("primary_btn")
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.btn_search)
        bottom_layout.addLayout(search_row)

        self.result_box = QtWidgets.QPlainTextEdit()
        self.result_box.setReadOnly(True)
        self.result_box.setStyleSheet("""
            font-family: 'Cascadia Mono', 'Consolas', 'Fira Code', monospace;
            font-size: 12px;
            padding: 8px;
        """)
        bottom_layout.addWidget(self.result_box, 1)

        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([350, 240])

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        self.btn_close = QtWidgets.QPushButton("关闭")
        self.btn_close.setObjectName("main_btn")
        self.btn_close.setMinimumWidth(100)
        footer.addWidget(self.btn_close)
        container_layout.addLayout(footer)

        layout.addWidget(self.container)

        self.btn_generate_doc.clicked.connect(self._generate_knowledge_doc)
        self.btn_import_file.clicked.connect(self._import_knowledge_files)
        self.btn_import_chat.clicked.connect(self._import_chat_records)
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

    def _gui(self):
        service = self.knowledge_gui
        if getattr(service, "brain", None) is None:
            service.brain = self._brain()
        if getattr(service, "plugin_manager", None) is None:
            service.plugin_manager = self.plugin_manager
        return service

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
        dummy_item = QtWidgets.QTableWidgetItem()
        dummy_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
        self.dir_table.setItem(row, 0, dummy_item)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        cb = QtWidgets.QCheckBox()
        cb.setChecked(enabled)
        cb.stateChanged.connect(lambda *_: self._refresh_recent_dirs())
        layout.addWidget(cb)
        self.dir_table.setCellWidget(row, 0, container)

        path_item = QtWidgets.QTableWidgetItem(path)
        path_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        self.dir_table.setItem(row, 1, path_item)

    def _collect_dirs(self) -> List[dict]:
        dirs = []
        known = set()
        for row in range(self.dir_table.rowCount()):
            cell_widget = self.dir_table.cellWidget(row, 0)
            enabled = True
            if cell_widget:
                cb = cell_widget.findChild(QtWidgets.QCheckBox)
                if cb:
                    enabled = cb.isChecked()
            path_item = self.dir_table.item(row, 1)
            text = path_item.text().strip() if path_item else ""
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
        listed = self._gui().stats()
        stats = dict(listed.get("data") or {})
        if not listed.get("ok") or not stats.get("available", True):
            self.stats_label.setText("知识片段数：未知")
            self._refresh_recent_dirs()
            return
        try:
            chunk_count = int(stats.get("chunk_count", 0))
            embedding = dict(stats.get("embedding") or {})
            state = {
                "ready": "可用",
                "unverified": "未验证",
                "error": "错误",
                "disabled": "已禁用",
                "unconfigured": "未配置",
            }.get(str(embedding.get("state") or ""), "未知")
            rebuild_text = " · 需要重建" if stats.get("rebuild_required") else ""
            hint = ""
            if chunk_count <= 1 and not stats.get("rebuild_required"):
                dirs = self._collect_dirs()
                enabled = [
                    str(item.get("path") or "")
                    for item in dirs
                    if item.get("enabled", True)
                ]
                only_default = bool(enabled) and all(
                    Path(path).name == "knowledge_docs" for path in enabled
                )
                if only_default or not enabled:
                    hint = " · 当前几乎没资料，加目录再学"
            self.stats_label.setText(
                f"知识片段数：{chunk_count} · "
                f"{embedding.get('model') or 'Embedding 未配置'}/"
                f"{embedding.get('dimension') or '?'} · {state} · "
                f"调用 {int(embedding.get('calls') or 0)} / "
                f"失败 {int(embedding.get('failures') or 0)}"
                f"{rebuild_text}{hint}"
            )
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

    def _save_dirs(self, *, show_message: bool = True) -> bool:
        saved = self._gui().save_dirs(self._collect_dirs())
        ok = bool(saved.get("ok"))
        if ok:
            if show_message:
                QtWidgets.QMessageBox.information(self, "成功", "知识目录已保存。")
            self._refresh_recent_dirs()
        else:
            if show_message:
                error = str(saved.get("error") or "保存知识目录失败。")
                if error == "plugin_manager_unavailable":
                    error = "当前上下文没有插件管理器。"
                QtWidgets.QMessageBox.warning(self, "失败", error)
        return ok

    def _safe_filename(self, title: str) -> str:
        base = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(title or "").strip())
        base = re.sub(r"_+", "_", base).strip("._")
        if not base:
            base = "knowledge"
        return base[:60]

    def _ensure_dir_configured(self, path: str):
        path = str(path or "").strip()
        if not path:
            return
        norm = str(QtCore.QFileInfo(path).absoluteFilePath())
        existing = [str(item.get("path") or "") for item in self._collect_dirs()]
        if norm not in existing and path not in existing:
            self._append_dir_row(norm, True)
            self._refresh_recent_dirs()

    def _build_knowledge_markdown(self, payload: dict) -> str:
        title = str(payload.get("title") or "").strip()
        source = str(payload.get("source") or "manual").strip() or "manual"
        tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        lines = [str(item).strip() for item in payload.get("lines", []) if str(item).strip()]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        out = [
            f"# {title}",
            f"来源：{source}",
            f"标签：{', '.join(tags) if tags else '未分类'}",
            f"整理时间：{now}",
            "",
            "## 知识条目",
        ]
        for item in lines:
            out.append(f"- {title}：{item}")
        out.append("")
        return "\n".join(out)

    def _write_knowledge_doc(self, payload: dict) -> str:
        target_dir = Path(str(payload.get("target_dir") or "")).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self._safe_filename(payload.get('title'))}_{stamp}.md"
        path = target_dir / filename
        path.write_text(self._build_knowledge_markdown(payload), encoding="utf-8")
        return str(path)

    def _generate_knowledge_doc(self):
        dlg = KnowledgeDocGeneratorDialog(self, self._collect_dirs())
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        payload = dlg.payload()
        try:
            path = self._write_knowledge_doc(payload)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "生成失败", f"写入知识文档失败：{e}")
            return

        target_dir = str(Path(path).parent)
        self._ensure_dir_configured(target_dir)
        self._save_dirs(show_message=False)

        message = f"✅ 已生成知识文档：\n{path}"
        if payload.get("ingest_now"):
            imported = self._gui().import_file(path)
            if not imported.get("ok"):
                error = str(imported.get("error") or "brain_unavailable")
                if error == "brain_unavailable":
                    message += "\n\n⚠️ brain 未就绪，稍后可点“一键学习”。"
                else:
                    message += f"\n\n⚠️ 自动学习失败：{error}"
            else:
                self._refresh_stats()
                message += f"\n\n学习结果：{(imported.get('data') or {}).get('result')}"
        self.result_box.setPlainText(message)
        QtWidgets.QMessageBox.information(self, "知识文档", message)

    def _import_chat_records(self):
        dlg = ChatRecordImportWizardDialog(
            self,
            main_app=self.main_app,
            dirs=self._collect_dirs(),
            default_target="knowledge",
        )
        dlg.exec()
        self._refresh_stats()
        self._refresh_recent_dirs()

    def _import_knowledge_files(self):
        if self._gui().brain is None:
            QtWidgets.QMessageBox.warning(self, "知识库", "brain 未就绪，暂时不能导入知识文件。")
            return
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "选择知识文件",
            str(Path.cwd()),
            "Knowledge Files (*.md *.txt *.py *.json);;Markdown (*.md);;Text (*.txt);;Python (*.py);;JSON (*.json);;All Files (*.*)",
        )
        if not paths:
            return
        self._start_knowledge_import_job(
            self._gui(),
            paths,
            kind="import",
            title="知识库导入",
            ready_text="准备导入知识文件…",
        )

    def _update_import_knowledge_progress(
        self, info: dict,
    ):
        stage = str(info.get("stage") or "").strip()
        batch = int(info.get("batch") or 0)
        batches = max(1, int(info.get("batches") or 1))
        total = int(info.get("total") or 0)
        added = int(info.get("added") or 0)
        skipped = int(info.get("skipped") or 0)
        file_index = int(info.get("file_index") or 1)
        file_count = max(1, int(info.get("file_count") or 1))
        file_path = str(info.get("file_path") or "")
        file_name = Path(file_path).name
        stage_text = {
            "prepared": "已解析文件",
            "embedding": "正在生成向量并写入",
            "batch_done": "已完成一批",
        }.get(stage, "正在导入")
        file_base = max(0, file_index - 1) / max(1, file_count)
        file_part = (batch / batches) / max(1, file_count)
        percent = int(max(0, min(100, (file_base + file_part) * 100)))
        message = (
            f"{stage_text}: {file_name}\n"
            f"文件 {file_index}/{file_count}，批次 {batch}/{batches}，条目 {total}\n"
            f"本文件已新增 {added} 条，跳过 {skipped} 条。"
        )
        if self._knowledge_import_progress is not None:
            self._knowledge_import_progress.setValue(percent)
            self._knowledge_import_progress.setLabelText(message)
        self.result_box.setPlainText(message)

    def _knowledge_busy_buttons(self):
        return [
            self.btn_import_file,
            self.btn_learn,
            self.btn_rebuild,
            self.btn_delete_selected,
        ]

    def _set_knowledge_job_busy(self, busy: bool, *, kind: str = ""):
        self._knowledge_job_kind = kind if busy else ""
        for btn in self._knowledge_busy_buttons():
            btn.setEnabled(not busy)
        if busy:
            if kind == "learn":
                self.btn_learn.setText("学习中…")
            else:
                self.btn_import_file.setText("导入中…")
            return
        self.btn_learn.setText("⚡ 一键学习")
        self.btn_import_file.setText("📥 导入知识文件")

    def _start_knowledge_import_job(self, importer, paths, *, kind: str, title: str, ready_text: str):
        self._set_knowledge_job_busy(True, kind=kind)
        progress = QtWidgets.QProgressDialog(ready_text, "", 0, 100, self)
        progress.setWindowTitle(title)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setValue(0)
        progress.show()
        self._knowledge_import_progress = progress
        self.result_box.setPlainText(ready_text)
        self._knowledge_import_thread = QtCore.QThread(self)
        self._knowledge_import_worker = KnowledgeImportWorker(importer, paths)
        self._knowledge_import_worker.moveToThread(self._knowledge_import_thread)
        self._knowledge_import_thread.started.connect(self._knowledge_import_worker.run)
        self._knowledge_import_worker.progress.connect(self._update_import_knowledge_progress)
        self._knowledge_import_worker.finished.connect(self._finish_import_knowledge_files)
        self._knowledge_import_worker.finished.connect(self._knowledge_import_thread.quit)
        self._knowledge_import_worker.finished.connect(self._knowledge_import_worker.deleteLater)
        self._knowledge_import_thread.finished.connect(self._knowledge_import_thread.deleteLater)
        self._knowledge_import_thread.start()

    def _finish_import_knowledge_files(
        self,
        result: dict,
    ):
        kind = self._knowledge_job_kind or "import"
        if self._knowledge_import_progress is not None:
            self._knowledge_import_progress.close()
            self._knowledge_import_progress = None
        self._set_knowledge_job_busy(False)
        self._refresh_stats()
        file_count = int(result.get("file_count", 0) or 0)
        added = int(result.get("added", 0) or 0)
        skipped = int(result.get("skipped", 0) or 0)
        failed = int(result.get("failed", 0) or 0)
        results = list(result.get("results") or [])
        message = (
            f"已处理 {file_count} 个文件：新增 {added} 条，跳过 {skipped} 条，失败 {failed} 个文件。"
            f"\n\n" + "\n".join(results[:80])
        )
        self.result_box.setPlainText(message)
        box_title = "学习结果" if kind == "learn" else "知识库导入"
        QtWidgets.QMessageBox.information(self, box_title, message)

    def _learn_dirs(self):
        if not self._save_dirs(show_message=False):
            return
        plugin = self._knowledge_plugin()
        gui = self._gui()
        if plugin is None or gui.brain is None:
            QtWidgets.QMessageBox.warning(self, "失败", "知识库插件或 brain 未就绪。")
            return
        stats = dict((gui.stats().get("data") or {}) if gui.stats().get("ok") else {})
        if stats.get("rebuild_required"):
            QtWidgets.QMessageBox.warning(
                self,
                "需要先重建",
                "当前知识库向量与嵌入模型不兼容，或缺少模型元数据。"
                "请先点「重建索引库」清空旧库，再点「一键学习」。",
            )
            return
        list_files = getattr(plugin, "list_configured_learn_files", None)
        paths = list(list_files()) if callable(list_files) else []
        if not paths:
            QtWidgets.QMessageBox.information(
                self,
                "一键学习",
                "配置目录里没有可学习的 .md/.txt/.py/.json 文件。",
            )
            return
        self._start_knowledge_import_job(
            gui,
            paths,
            kind="learn",
            title="一键学习",
            ready_text="准备学习知识目录…",
        )

    def _rebuild_knowledge(self):
        gui = self._gui()
        if gui.brain is None:
            QtWidgets.QMessageBox.warning(self, "失败", "brain 不支持重建知识库。")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "确认",
            "这会清空当前知识库和导入清单，旧的按行碎片会一起删掉。"
            "重建完成后还要再点一次「一键学习」，才会按新的段落分块重新导入。是否继续？",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        rebuilt = gui.rebuild()
        ok = bool(rebuilt.get("ok"))
        if ok:
            self.result_box.setPlainText(
                "✅ 已清空并重建知识库。现在请点「一键学习」，从配置目录重新导入。"
            )
            self._refresh_stats()
            QtWidgets.QMessageBox.information(
                self,
                "完成",
                "知识库已清空重建。接下来点「一键学习」才会重新导入文档。",
            )
        else:
            QtWidgets.QMessageBox.warning(self, "失败", "知识库重建失败。")

    def _delete_selected_dirs_from_knowledge(self):
        gui = self._gui()
        if gui.brain is None:
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
        deleted = gui.delete_by_dirs(targets)
        if not deleted.get("ok"):
            QtWidgets.QMessageBox.warning(
                self, "失败", str(deleted.get("error") or "delete_failed")
            )
            return
        removed = int((deleted.get("data") or {}).get("removed") or 0)
        self.result_box.setPlainText(f"✅ 已按目录删除 {removed} 条知识片段。")
        self._refresh_stats()
        QtWidgets.QMessageBox.information(
            self, "完成", f"已删除 {removed} 条知识片段。"
        )

    def _search_knowledge(self):
        query = self.search_input.text().strip()
        if not query:
            QtWidgets.QMessageBox.information(self, "提示", "请先输入查询词。")
            return
        found = self._gui().search(query, limit=5)
        if not found.get("ok"):
            error = str(found.get("error") or "search_failed")
            if error == "brain_unavailable":
                QtWidgets.QMessageBox.warning(self, "失败", "brain 未就绪。")
                return
            self.result_box.setPlainText(f"❌ 搜索失败: {error}")
            return
        results = list((found.get("data") or {}).get("results") or [])
        if not results:
            self.result_box.setPlainText("📭 知识库中没有找到相关内容。")
            return
        self.result_box.setPlainText("\n\n---\n\n".join(results))
