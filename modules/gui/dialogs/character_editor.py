import json
import os
import re

from PySide6 import QtWidgets, QtCore, QtGui
from modules.character_manager import character_manager, DEFAULT_EMOTION_KEYS

try:
    from modules.gui.styles import get_ui_palette
except Exception:
    def get_ui_palette():
        return {
            "accent": "#6366F1",
            "accent_hover": "#4F46E5",
            "accent_soft": "#EEF2FF",
            "bg_app": "#F5F7FB",
            "bg_card": "#FFFFFF",
            "bg_soft": "#F3F4F6",
            "bg_console": "#111827",
            "border": "#E5E7EB",
            "border_strong": "#D1D5DB",
            "text_primary": "#111827",
            "text_secondary": "#6B7280",
            "text_muted": "#9CA3AF",
            "success": "#10B981",
            "success_soft": "#D1FAE5",
            "warning": "#F59E0B",
            "danger": "#EF4444",
        }

try:
    from config import EMO_TO_LIVE2D
except Exception:
    EMO_TO_LIVE2D = {}


def get_character_editor_styles_v2() -> str:
    p = get_ui_palette()
    return f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei';
            color: {p['text_primary']};
        }}
        QFrame#charLeftCard, QFrame#charRightCard {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 16px;
        }}
        QLabel#charSectionTitle {{
            color: {p['text_primary']};
            font-size: 16px;
            font-weight: 700;
        }}
        QLabel#charHint {{
            color: {p['text_secondary']};
            font-size: 12px;
        }}
        QListWidget, QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QTableWidget {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 10px;
        }}
        QListWidget::item {{
            padding: 8px 10px;
            border-radius: 8px;
            margin: 2px 0;
            color: {p['text_secondary']};
        }}
        QListWidget::item:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
            font-weight: 700;
        }}
        QTabWidget::pane {{
            border: 1px solid {p['border']};
            border-radius: 12px;
            background: {p['bg_card']};
        }}
        QTabBar::tab {{
            background: {p['bg_soft']};
            color: {p['text_secondary']};
            border: 1px solid {p['border']};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
            font-weight: 700;
        }}
        QTableWidget {{
            gridline-color: {p['border']};
        }}
        QHeaderView::section {{
            background: {p['bg_card']};
            color: {p['text_secondary']};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p['border']};
        }}
        QPushButton {{
            background: {p['bg_card']};
            color: {p['text_primary']};
            border: 1px solid {p['border_strong']};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p['accent']};
            color: {p['accent_hover']};
        }}
        QPushButton#charPrimary {{
            background: {p['accent']};
            color: white;
            border: none;
        }}
        QPushButton#charPrimary:hover {{
            background: {p['accent_hover']};
            color: white;
        }}
        QPushButton#charDanger {{
            color: #DC2626;
            background: #FEF2F2;
            border: 1px solid #FECACA;
        }}
        QPushButton#charDanger:hover {{
            color: #B91C1C;
            background: #FEE2E2;
            border-color: #FCA5A5;
        }}
        QGroupBox {{
            border: 1px solid {p['border']};
            border-radius: 12px;
            margin-top: 10px;
            padding: 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            color: {p['text_secondary']};
        }}
    """


class CharacterEditorWidget(QtWidgets.QWidget):
    """嵌入在 SettingsDialog 中的角色管理页"""

    def __init__(self, main_app):
        super().__init__()
        self.main_app = main_app
        self.mgr = character_manager
        self.current_char_id = None
        self.current_costume_name = None
        self._current_motion_options = []
        self._current_expression_options = []

        self.setStyleSheet(get_character_editor_styles_v2())
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        left_panel = QtWidgets.QFrame()
        left_panel.setObjectName("charLeftCard")
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        left_title = QtWidgets.QLabel("角色列表")
        left_title.setObjectName("charSectionTitle")
        left_layout.addWidget(left_title)

        left_hint = QtWidgets.QLabel("这里放你当前角色与已创建角色，激活角色会带 ⭐ 标识。")
        left_hint.setObjectName("charHint")
        left_hint.setWordWrap(True)
        left_layout.addWidget(left_hint)

        self.char_list = QtWidgets.QListWidget()
        self.char_list.currentRowChanged.connect(self._on_char_selected)

        btn_add_char = QtWidgets.QPushButton("+ 新建角色")
        btn_add_char.setObjectName("charPrimary")
        btn_add_char.clicked.connect(self._add_character)

        left_layout.addWidget(self.char_list, 1)
        left_layout.addWidget(btn_add_char)

        self.right_shell = QtWidgets.QFrame()
        self.right_shell.setObjectName("charRightCard")
        right_shell_layout = QtWidgets.QVBoxLayout(self.right_shell)
        right_shell_layout.setContentsMargins(14, 14, 14, 14)
        right_shell_layout.setSpacing(10)

        right_title = QtWidgets.QLabel("角色详情")
        right_title.setObjectName("charSectionTitle")
        right_shell_layout.addWidget(right_title)

        right_hint = QtWidgets.QLabel("编辑人设、服装和情绪映射。左侧选择角色后，这里会显示详细配置。")
        right_hint.setObjectName("charHint")
        right_hint.setWordWrap(True)
        right_shell_layout.addWidget(right_hint)

        self.right_panel = QtWidgets.QTabWidget()
        self.right_panel.setVisible(False)

        self.tab_persona = QtWidgets.QWidget()
        self._init_tab_persona()
        self.right_panel.addTab(self.tab_persona, "人设与提示词")

        self.tab_costume = QtWidgets.QWidget()
        self._init_tab_costume()
        self.right_panel.addTab(self.tab_costume, "服装管理")

        right_shell_layout.addWidget(self.right_panel, 1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.right_shell)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

    def _init_tab_persona(self):
        layout = QtWidgets.QVBoxLayout(self.tab_persona)

        form = QtWidgets.QFormLayout()
        self.edit_name = QtWidgets.QLineEdit()
        self.edit_name.textChanged.connect(self._save_current_char)
        form.addRow("角色名称:", self.edit_name)
        layout.addLayout(form)

        layout.addWidget(QtWidgets.QLabel("人设提示词 (System Prompt):"))
        self.edit_prompt = QtWidgets.QTextEdit()
        self.edit_prompt.textChanged.connect(self._save_current_char)
        layout.addWidget(self.edit_prompt)

        self.btn_activate = QtWidgets.QPushButton("🚀 切换为此角色")
        self.btn_activate.setObjectName("charPrimary")
        self.btn_activate.clicked.connect(self._activate_character)
        layout.addWidget(self.btn_activate)

        btn_del = QtWidgets.QPushButton("🗑️ 删除此角色")
        btn_del.setObjectName("charDanger")
        btn_del.clicked.connect(self._delete_current_char)
        layout.addWidget(btn_del, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

    def _init_tab_costume(self):
        layout = QtWidgets.QVBoxLayout(self.tab_costume)

        self.costume_list = QtWidgets.QListWidget()
        self.costume_list.itemDoubleClicked.connect(self._wear_selected_costume)
        self.costume_list.currentItemChanged.connect(self._on_costume_changed)
        layout.addWidget(self.costume_list)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_import = QtWidgets.QPushButton("📂 导入模型 (.model3.json)")
        btn_import.clicked.connect(self._import_costume)
        btn_wear = QtWidgets.QPushButton("👕 立即换穿")
        btn_wear.setObjectName("charPrimary")
        btn_wear.clicked.connect(self._wear_selected_costume)
        btn_del_cos = QtWidgets.QPushButton("✕ 删除")
        btn_del_cos.setObjectName("charDanger")
        btn_del_cos.clicked.connect(self._delete_costume)

        btn_layout.addWidget(btn_import)
        btn_layout.addWidget(btn_wear)
        btn_layout.addWidget(btn_del_cos)
        layout.addLayout(btn_layout)

        self.lbl_motion_summary = QtWidgets.QLabel("动作: -")
        self.lbl_expr_summary = QtWidgets.QLabel("表情: -")
        self.lbl_motion_summary.setObjectName("charHint")
        self.lbl_expr_summary.setObjectName("charHint")
        layout.addWidget(self.lbl_motion_summary)
        layout.addWidget(self.lbl_expr_summary)

        preview_group = QtWidgets.QGroupBox("动作/表情预览与映射")
        preview_layout = QtWidgets.QGridLayout(preview_group)

        self.combo_motion = QtWidgets.QComboBox()
        self.combo_motion_type = QtWidgets.QComboBox()
        self.combo_motion_type.addItem("动作类型 0", 0)
        self.combo_motion_type.addItem("动作类型 1", 1)

        self.combo_expression = QtWidgets.QComboBox()

        btn_preview_motion = QtWidgets.QPushButton("▶ 预览动作")
        btn_preview_motion.clicked.connect(self._preview_selected_motion)
        btn_preview_expr = QtWidgets.QPushButton("▶ 预览表情")
        btn_preview_expr.clicked.connect(self._preview_selected_expression)
        btn_apply_selected = QtWidgets.QPushButton("✅ 应用下拉到选中情绪")
        btn_apply_selected.clicked.connect(self._apply_dropdown_to_selected_emotion)

        preview_layout.addWidget(QtWidgets.QLabel("动作"), 0, 0)
        preview_layout.addWidget(self.combo_motion, 0, 1)
        preview_layout.addWidget(self.combo_motion_type, 0, 2)
        preview_layout.addWidget(btn_preview_motion, 0, 3)

        preview_layout.addWidget(QtWidgets.QLabel("表情"), 1, 0)
        preview_layout.addWidget(self.combo_expression, 1, 1, 1, 2)
        preview_layout.addWidget(btn_preview_expr, 1, 3)

        preview_layout.addWidget(btn_apply_selected, 2, 0, 1, 4)
        layout.addWidget(preview_group)

        self.emo_table = QtWidgets.QTableWidget()
        self.emo_table.setMinimumHeight(100)
        self.emo_table.setColumnCount(4)
        self.emo_table.setHorizontalHeaderLabels(["情绪", "动作(mtn)", "表情(exp)", "来源"])
        self.emo_table.horizontalHeader().setStretchLastSection(True)
        self.emo_table.verticalHeader().setVisible(False)
        self.emo_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.emo_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.emo_table, 1)

        emo_btn_layout = QtWidgets.QHBoxLayout()
        btn_set = QtWidgets.QPushButton("✏️ 设置当前情绪映射")
        btn_set.setObjectName("charPrimary")
        btn_set.clicked.connect(self._edit_selected_emotion_override)
        btn_clear = QtWidgets.QPushButton("🧹 清除当前情绪映射")
        btn_clear.setObjectName("charDanger")
        btn_clear.clicked.connect(self._clear_selected_emotion_override)
        emo_btn_layout.addWidget(btn_set)
        emo_btn_layout.addWidget(btn_clear)
        layout.addLayout(emo_btn_layout)

    # --- 逻辑 ---

    def _refresh_list(self):
        chars = self.mgr.get_all_characters()
        self.char_list.clear()
        active_id = self.mgr.data.get("active_id")

        for cid, data in chars.items():
            name = data.get("name", cid)
            prefix = "⭐ " if cid == active_id else ""
            item = QtWidgets.QListWidgetItem(f"{prefix}{name}")
            item.setData(QtCore.Qt.UserRole, cid)
            self.char_list.addItem(item)

    def _on_char_selected(self, row):
        item = self.char_list.currentItem()
        if not item:
            return
        cid = item.data(QtCore.Qt.UserRole)
        self.current_char_id = cid
        self._load_char_to_ui(cid)
        self.right_panel.setVisible(True)

    def _load_char_to_ui(self, cid):
        data = self.mgr.get_character(cid)
        if not data:
            return

        self.edit_name.blockSignals(True)
        self.edit_prompt.blockSignals(True)

        self.edit_name.setText(data.get("name", ""))
        self.edit_prompt.setPlainText(data.get("prompt", ""))

        self.edit_name.blockSignals(False)
        self.edit_prompt.blockSignals(False)

        self.costume_list.clear()
        costumes = data.get("costumes", {})
        for cname, cdata in costumes.items():
            item = QtWidgets.QListWidgetItem(cname)
            item.setData(QtCore.Qt.UserRole, cdata.get("path"))
            self.costume_list.addItem(item)

        preferred_costume = self.mgr.get_current_costume_name(cid)
        target_row = 0
        if preferred_costume:
            for index in range(self.costume_list.count()):
                if self.costume_list.item(index).text() == preferred_costume:
                    target_row = index
                    break
        if self.costume_list.count() > 0:
            self.costume_list.setCurrentRow(target_row)
        else:
            self.current_costume_name = None
            self._refresh_costume_detail_ui()

        is_active = (cid == self.mgr.data.get("active_id"))
        self.btn_activate.setEnabled(not is_active)
        self.btn_activate.setText("当前已激活" if is_active else "🚀 切换为此角色")

    def _save_current_char(self):
        if not self.current_char_id:
            return
        data = self.mgr.get_character(self.current_char_id)
        data["name"] = self.edit_name.text()
        data["prompt"] = self.edit_prompt.toPlainText()
        self.mgr.save()
        item = self.char_list.currentItem()
        if item:
            item.setText(data["name"])

    def _add_character(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "新建角色", "请输入角色名称:")
        if ok and name:
            import uuid
            cid = f"char_{uuid.uuid4().hex[:6]}"
            self.mgr.add_character(cid, name, "你是一个AI助手。")
            self._refresh_list()

    def _delete_current_char(self):
        if not self.current_char_id:
            return
        ret = QtWidgets.QMessageBox.question(self, "确认", "确定要删除这个角色吗？")
        if ret == QtWidgets.QMessageBox.StandardButton.Yes:
            self.mgr.delete_character(self.current_char_id)
            self._refresh_list()
            self.right_panel.setVisible(False)

    def _activate_character(self):
        if not self.current_char_id:
            return

        self.mgr.set_active_character(self.current_char_id)

        if hasattr(self.main_app, "plugin_manager"):
            pass

        self._refresh_list()
        self._load_char_to_ui(self.current_char_id)
        QtWidgets.QMessageBox.information(
            self,
            "成功",
            "角色已切换！\n提示词已更新。\n(请手动换穿该角色的一件衣服以同步Live2D)"
        )

    def _extract_expression_id(self, name: str, file_name: str):
        for text in [name or "", file_name or ""]:
            nums = re.findall(r"\d+", text)
            if nums:
                try:
                    return int(nums[-1])
                except Exception:
                    pass
        return None

    def _normalize_motion_name(self, raw_motion_name: str):
        name = str(raw_motion_name or "").strip()
        if not name:
            return ""
        if ":" in name:
            return name
        return f"Motion:{name}"

    def _iter_motion_groups(self, raw_motion_refs):
        if isinstance(raw_motion_refs, dict):
            for group_name, items in raw_motion_refs.items():
                if isinstance(items, list):
                    yield str(group_name), items
            return
        if isinstance(raw_motion_refs, list):
            yield "Motion", raw_motion_refs

    def _parse_model_meta(self, model_path: str):
        motions, expressions = [], []
        if not model_path:
            return motions, expressions

        path = str(model_path).replace("\\", "/")
        abs_path = os.path.abspath(path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            refs = data.get("FileReferences", {})

            motion_refs = refs.get("Motions", {}) if isinstance(refs, dict) else {}
            for group_name, motion_items in self._iter_motion_groups(motion_refs):
                for idx, item in enumerate(motion_items):
                    if not isinstance(item, dict):
                        continue
                    raw_name = item.get("Name") or item.get("name") or item.get("mtn") or item.get("File") or item.get("file")
                    motion_name = str(raw_name).strip() if raw_name else f"{group_name}:{idx}"
                    motion_name = self._normalize_motion_name(motion_name)
                    motions.append({
                        "name": motion_name,
                        "group": group_name,
                        "index": int(idx),
                    })

            expr_items = refs.get("Expressions", []) if isinstance(refs, dict) else []
            if isinstance(expr_items, list):
                for idx, item in enumerate(expr_items):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name") or item.get("name") or "").strip()
                    file_name = str(item.get("File") or item.get("file") or "").strip()
                    exp_id = int(idx)
                    label = name or file_name or f"exp_{idx}"
                    expressions.append({
                        "label": label,
                        "name": name,
                        "file": file_name,
                        "exp_id": exp_id,
                    })

            if not motions:
                legacy_motions = data.get("motions", {})
                for group_name, motion_items in self._iter_motion_groups(legacy_motions):
                    for idx, item in enumerate(motion_items):
                        if isinstance(item, dict):
                            raw_name = item.get("name") or item.get("Name") or item.get("file") or item.get("File") or item.get("mtn")
                        else:
                            raw_name = str(item)
                        motion_name = str(raw_name).strip() if raw_name else f"{group_name}:{idx}"
                        motion_name = self._normalize_motion_name(motion_name)
                        motions.append({
                            "name": motion_name,
                            "group": group_name,
                            "index": int(idx),
                        })

            if not expressions:
                legacy_expr = data.get("expressions", [])
                if isinstance(legacy_expr, list):
                    for idx, item in enumerate(legacy_expr):
                        if isinstance(item, dict):
                            name = str(item.get("name") or item.get("Name") or "").strip()
                            file_name = str(item.get("file") or item.get("File") or "").strip()
                        else:
                            name = ""
                            file_name = str(item)
                        exp_id = int(idx)
                        label = name or file_name or f"exp_{idx}"
                        expressions.append({
                            "label": label,
                            "name": name,
                            "file": file_name,
                            "exp_id": exp_id,
                        })
        except Exception:
            pass
        return motions, expressions

    def _refresh_preview_options(self, motions, expressions):
        self._current_motion_options = motions if isinstance(motions, list) else []
        self._current_expression_options = expressions if isinstance(expressions, list) else []

        self.combo_motion.clear()
        if self._current_motion_options:
            for item in self._current_motion_options:
                name = str(item.get("name") or "").strip()
                group = str(item.get("group") or "").strip()
                label = f"{name} [{group}]" if group else name
                self.combo_motion.addItem(label, name)
        else:
            self.combo_motion.addItem("(未解析到动作)", "")

        self.combo_expression.clear()
        if self._current_expression_options:
            for item in self._current_expression_options:
                label = str(item.get("label") or "").strip() or "(未命名表情)"
                exp_id = item.get("exp_id")
                suffix = f" (ID={exp_id})" if exp_id is not None else " (ID未识别)"
                self.combo_expression.addItem(f"{label}{suffix}", exp_id)
        else:
            self.combo_expression.addItem("(未解析到表情)", None)

    def _resolve_emotion_row(self, emotion: str, overrides: dict):
        default_cfg = EMO_TO_LIVE2D.get(emotion, {}) if isinstance(EMO_TO_LIVE2D, dict) else {}
        override_cfg = overrides.get(emotion, {}) if isinstance(overrides, dict) else {}

        if isinstance(override_cfg, dict) and override_cfg.get("mtn"):
            mtn = str(override_cfg.get("mtn", ""))
            exp = override_cfg.get("exp", "")
            source = "服装覆盖"
        else:
            mtn = str(default_cfg.get("mtn", "")) if isinstance(default_cfg, dict) else ""
            exp = default_cfg.get("exp", "") if isinstance(default_cfg, dict) else ""
            source = "默认"

        return mtn, "" if exp is None else str(exp), source

    def _refresh_costume_detail_ui(self):
        if not self.current_char_id or not self.current_costume_name:
            self.lbl_motion_summary.setText("动作: -")
            self.lbl_expr_summary.setText("表情: -")
            self.emo_table.setRowCount(0)
            self._refresh_preview_options([], [])
            return

        char = self.mgr.get_character(self.current_char_id) or {}
        costume = (char.get("costumes") or {}).get(self.current_costume_name) or {}
        model_path = costume.get("path", "")
        overrides = costume.get("emotion_map", {}) if isinstance(costume.get("emotion_map", {}), dict) else {}

        motions, expressions = self._parse_model_meta(model_path)
        motion_names = [str(item.get("name", "")) for item in motions if isinstance(item, dict)]
        expr_labels = [str(item.get("label", "")) for item in expressions if isinstance(item, dict)]
        self.lbl_motion_summary.setText(f"动作: {', '.join([x for x in motion_names if x]) if motion_names else '(未解析到)'}")
        self.lbl_expr_summary.setText(f"表情: {', '.join([x for x in expr_labels if x]) if expr_labels else '(未解析到)'}")
        self._refresh_preview_options(motions, expressions)

        rows = list(DEFAULT_EMOTION_KEYS)
        self.emo_table.setRowCount(len(rows))
        for row, emo in enumerate(rows):
            mtn, exp, source = self._resolve_emotion_row(emo, overrides)
            self.emo_table.setItem(row, 0, QtWidgets.QTableWidgetItem(emo))
            self.emo_table.setItem(row, 1, QtWidgets.QTableWidgetItem(mtn))
            self.emo_table.setItem(row, 2, QtWidgets.QTableWidgetItem(exp))
            self.emo_table.setItem(row, 3, QtWidgets.QTableWidgetItem(source))

    def _on_costume_changed(self, current, previous):
        if not current:
            self.current_costume_name = None
            self._refresh_costume_detail_ui()
            return

        self.current_costume_name = current.text()
        if self.current_char_id and self.current_costume_name:
            self.mgr.set_current_costume_name(self.current_char_id, self.current_costume_name)
            if self.main_app and hasattr(self.main_app, "_refresh_character_status"):
                self.main_app._refresh_character_status()
        self._refresh_costume_detail_ui()

    def _selected_emotion(self):
        row = self.emo_table.currentRow()
        if row < 0:
            return None
        item = self.emo_table.item(row, 0)
        return item.text().strip().lower() if item else None

    def _edit_selected_emotion_override(self):
        if not self.current_char_id or not self.current_costume_name:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(self, "提示", "请先在表格中选中一个情绪。")
            return
        self._apply_dropdown_to_selected_emotion()

    def _preview_selected_motion(self):
        if not self.main_app or not hasattr(self.main_app, "preview_motion"):
            return
        motion_name = str(self.combo_motion.currentData() or "").strip()
        if not motion_name:
            QtWidgets.QMessageBox.information(self, "提示", "当前服装没有可预览的动作。")
            return
        motion_type = int(self.combo_motion_type.currentData() or 0)
        self.main_app.preview_motion(motion_name, motion_type)

    def _preview_selected_expression(self):
        if not self.main_app or not hasattr(self.main_app, "preview_expression"):
            return
        exp_id = self.combo_expression.currentData()
        if exp_id is None:
            QtWidgets.QMessageBox.information(self, "提示", "该表情未识别到 exp ID，无法直接预览。")
            return
        self.main_app.preview_expression(int(exp_id))

    def _apply_dropdown_to_selected_emotion(self):
        if not self.current_char_id or not self.current_costume_name:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(self, "提示", "请先在表格中选中一个情绪。")
            return

        mtn = str(self.combo_motion.currentData() or "").strip()
        if not mtn:
            QtWidgets.QMessageBox.warning(self, "无效输入", "当前动作为空，无法应用。")
            return

        payload = {
            "mtn": mtn,
            "type": int(self.combo_motion_type.currentData() or 0),
        }
        exp_id = self.combo_expression.currentData()
        if exp_id is not None:
            payload["exp"] = int(exp_id)

        self.mgr.set_costume_emotion_override(self.current_char_id, self.current_costume_name, emo, payload)
        self._refresh_costume_detail_ui()

    def _clear_selected_emotion_override(self):
        if not self.current_char_id or not self.current_costume_name:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(self, "提示", "请先在表格中选中一个情绪。")
            return

        self.mgr.set_costume_emotion_override(self.current_char_id, self.current_costume_name, emo, None)
        self._refresh_costume_detail_ui()

    def _import_costume(self):
        if not self.current_char_id:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Live2D 模型定义文件", "", "Model3 JSON (*.model3.json)"
        )
        if path:
            name, ok = QtWidgets.QInputDialog.getText(self, "服装名称", "给这件衣服起个名字:")
            if ok and name:
                self.mgr.add_costume(self.current_char_id, name, path)
                self._load_char_to_ui(self.current_char_id)

    def _delete_costume(self):
        item = self.costume_list.currentItem()
        if not item:
            return
        name = item.text()
        self.mgr.delete_costume(self.current_char_id, name)
        self._load_char_to_ui(self.current_char_id)

    def _wear_selected_costume(self):
        item = self.costume_list.currentItem()
        if not item:
            return
        path = item.data(QtCore.Qt.UserRole)
        name = item.text()
        cfg = self.mgr.get_costume_runtime_config(self.current_char_id, name)
        self.mgr.set_current_costume_name(self.current_char_id, name)

        if self.main_app and self.main_app.on_costume_callback:
            self.main_app.on_costume_callback(path, cfg)
            if hasattr(self.main_app, "_refresh_character_status"):
                self.main_app._refresh_character_status()
            QtWidgets.QMessageBox.information(self, "换装", f"已发送换装指令: {name}")
