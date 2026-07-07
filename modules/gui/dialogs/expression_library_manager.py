from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

from PySide6 import QtCore, QtWidgets

from modules.gui.styles import get_tool_dialog_styles
from modules.gui.dialogs.chat_record_import_wizard import ChatRecordImportWizardDialog

try:
    from modules.runtime_settings import load_runtime_settings, update_runtime_settings
except Exception:

    def load_runtime_settings():
        return {}

    def update_runtime_settings(patch):
        return patch or {}


def _read_import_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("latin1", errors="replace")


def _parse_possible_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw = str(value or "").strip()
        if not raw:
            return []
        items = []
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(raw)
            except Exception:
                continue
            if isinstance(parsed, list):
                items = [str(item).strip() for item in parsed if str(item).strip()]
                break
        if not items and raw:
            items = [raw]
    deduped: List[str] = []
    for item in items:
        if item and item not in deduped:
            deduped.append(item)
    return deduped[:12]


def _load_import_payload(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = _read_import_text(path)
    if suffix == ".xml":
        xml_text = re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>\s*", "", text, count=1)
        root = ET.fromstring(xml_text)
        table = None
        for candidate in root.findall(".//table"):
            name = str(candidate.findtext("name") or "").strip().lower()
            if name == "expression":
                table = candidate
                break
        if table is None:
            raise ValueError("XML 里没有找到 expression 表。")

        column_names = [
            str(col.findtext("name") or "").strip()
            for col in table.findall("./columns/column")
        ]
        if not column_names:
            raise ValueError("expression 表缺少列定义。")

        scene_names = {"chat", "sensor", "any", "*"}
        expressions: List[Dict[str, Any]] = []
        for row in table.findall("./rows/row"):
            values: Dict[str, Any] = {}
            for value in row.findall("./value"):
                try:
                    column_idx = int(value.attrib.get("column", "-1"))
                except Exception:
                    continue
                if column_idx < 0 or column_idx >= len(column_names):
                    continue
                column_name = column_names[column_idx]
                if value.attrib.get("null") == "true":
                    values[column_name] = ""
                else:
                    values[column_name] = value.text or ""

            raw_context = str(values.get("context") or "").strip()
            scene = raw_context.strip().lower()
            content_list = _parse_possible_list(values.get("content_list"))
            style_list = _parse_possible_list(values.get("style_list"))
            if not content_list and style_list:
                content_list = style_list

            expressions.append(
                {
                    "id": str(values.get("id") or "").strip(),
                    "situation": str(values.get("situation") or "").strip(),
                    "style": str(values.get("style") or "").strip(),
                    "scene": scene if scene in scene_names else "chat",
                    "context": raw_context,
                    "content_list": content_list,
                    "count": values.get("count", 0),
                    "checked": str(values.get("checked") or "0").strip() == "1",
                    "rejected": str(values.get("rejected") or "0").strip() == "1",
                    "last_active_time": str(values.get("last_active_time") or "").strip(),
                    "create_time": str(values.get("create_date") or "").strip(),
                    "chat_id": str(values.get("chat_id") or "").strip(),
                    "style_list": style_list,
                    "source": "maibot_xml",
                }
            )

        return {
            "type": "maibot.expression.xml",
            "source_chat_name": path.stem,
            "expressions": expressions,
        }

    return json.loads(text)


class ExpressionPatternEditDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, pattern: Dict[str, Any] | None = None):
        super().__init__(parent)
        self._pattern = dict(pattern or {})
        self.setWindowTitle("编辑表达学习条目" if pattern else "新增表达学习条目")
        self.resize(560, 460)
        self._setup_ui()
        self._load_pattern()

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
        
        title_label = QtWidgets.QLabel("🗣️ 编辑表达库条目" if self._pattern else "🗣️ 新建表达库条目")
        title_label.setObjectName("dialogTitle")
        header_layout.addWidget(title_label)
        
        desc_label = QtWidgets.QLabel("编辑说话风格和示例回复，让角色的对话更加生动、口语化。")
        desc_label.setObjectName("dialogDesc")
        desc_label.setWordWrap(True)
        header_layout.addWidget(desc_label)
        container_layout.addWidget(header_card)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.chk_enabled = QtWidgets.QCheckBox("启用这个条目")
        self.chk_enabled.setChecked(True)
        form.addRow("状态", self.chk_enabled)

        self.inp_character = QtWidgets.QLineEdit()
        self.inp_character.setPlaceholderText("留空表示通用，不绑定角色")
        form.addRow("角色", self.inp_character)

        self.cmb_scene = QtWidgets.QComboBox()
        self.cmb_scene.addItem("普通聊天", "chat")
        self.cmb_scene.addItem("屏幕吐槽", "sensor")
        self.cmb_scene.addItem("通用", "any")
        form.addRow("场景", self.cmb_scene)

        self.inp_situation = QtWidgets.QLineEdit()
        self.inp_situation.setPlaceholderText("例如：催他快去睡、看见他又在堆窗口")
        form.addRow("情境", self.inp_situation)

        self.txt_style = QtWidgets.QPlainTextEdit()
        self.txt_style.setPlaceholderText("描述这一类表达应该怎么说，例如：先吐槽半句，再轻轻催一下，别写成说明。")
        self.txt_style.setFixedHeight(80)
        form.addRow("表达风格", self.txt_style)

        self.txt_examples = QtWidgets.QPlainTextEdit()
        self.txt_examples.setPlaceholderText(
            "一行一个示例回复，例如：\n你又开这么多。\n先关几个。\n别把自己埋里面。"
        )
        self.txt_examples.setFixedHeight(100)
        form.addRow("示例回复", self.txt_examples)

        self.inp_source = QtWidgets.QLineEdit()
        self.inp_source.setPlaceholderText("manual / imported / character_profile ...")
        form.addRow("来源", self.inp_source)

        self.spin_quality = QtWidgets.QDoubleSpinBox()
        self.spin_quality.setRange(0.0, 10.0)
        self.spin_quality.setDecimals(1)
        self.spin_quality.setSingleStep(0.5)
        form.addRow("质量分", self.spin_quality)

        container_layout.addLayout(form)

        btns_layout = QtWidgets.QHBoxLayout()
        btns_layout.addStretch()
        
        self.btn_cancel = QtWidgets.QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = QtWidgets.QPushButton("保存")
        self.btn_save.setObjectName("primary_btn")
        self.btn_save.clicked.connect(self.accept)
        
        btns_layout.addWidget(self.btn_cancel)
        btns_layout.addWidget(self.btn_save)
        container_layout.addLayout(btns_layout)

        layout.addWidget(self.container)

    def _load_pattern(self):
        pattern = self._pattern
        self.chk_enabled.setChecked(bool(pattern.get("enabled", True)))
        self.inp_character.setText(str(pattern.get("character_name") or ""))
        scene = str(pattern.get("scene") or "chat").strip().lower() or "chat"
        idx = max(0, self.cmb_scene.findData(scene))
        self.cmb_scene.setCurrentIndex(idx)
        self.inp_situation.setText(str(pattern.get("situation") or ""))
        self.txt_style.setPlainText(str(pattern.get("style") or ""))
        content_list = pattern.get("content_list")
        if not isinstance(content_list, list):
            fallback_example = str(pattern.get("example") or "").strip()
            content_list = [fallback_example] if fallback_example else []
        self.txt_examples.setPlainText(
            "\n".join(str(item).strip() for item in content_list if str(item).strip())
        )
        self.inp_source.setText(str(pattern.get("source") or "manual"))
        try:
            self.spin_quality.setValue(float(pattern.get("quality_score", 0)))
        except Exception:
            self.spin_quality.setValue(0.0)

    def accept(self):
        style = self.txt_style.toPlainText().strip()
        examples = self._collect_examples()
        if not style and not examples:
            QtWidgets.QMessageBox.warning(self, "校验失败", "表达风格和示例回复至少填一个。")
            return
        super().accept()

    def _collect_examples(self) -> List[str]:
        lines = []
        for raw in self.txt_examples.toPlainText().splitlines():
            text = raw.strip()
            if text and text not in lines:
                lines.append(text)
        return lines[:12]

    def payload(self) -> Dict[str, Any]:
        content_list = self._collect_examples()
        payload = dict(self._pattern)
        payload.update(
            {
                "enabled": self.chk_enabled.isChecked(),
                "character_name": self.inp_character.text().strip(),
                "scene": str(self.cmb_scene.currentData() or "chat"),
                "situation": self.inp_situation.text().strip(),
                "style": self.txt_style.toPlainText().strip(),
                "example": content_list[0] if content_list else "",
                "content_list": content_list,
                "source": self.inp_source.text().strip() or "manual",
                "quality_score": float(self.spin_quality.value()),
            }
        )
        return payload


class ExpressionLibraryManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self.setWindowTitle("表达学习库")
        self.resize(980, 640)
        self.setMinimumSize(860, 560)
        self._setup_ui()
        self._load_runtime_controls()
        self._refresh_table()

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
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(6)

        title_row = QtWidgets.QHBoxLayout()
        icon_label = QtWidgets.QLabel("🗣️")
        icon_label.setStyleSheet("font-size: 22px;")
        title_row.addWidget(icon_label)
        
        title = QtWidgets.QLabel("表达学习库管理")
        title.setObjectName("dialogTitle")
        title_row.addWidget(title, 1)
        
        self.stats_label = QtWidgets.QLabel("条目数：0")
        self.stats_label.setStyleSheet(
            "color: #3B82F6; font-size: 13px; font-weight: bold;"
        )
        title_row.addWidget(self.stats_label)
        header_layout.addLayout(title_row)

        desc = QtWidgets.QLabel(
            "把更像真人说话的表达样式单独存起来。这里的条目会参与日常聊天 and 屏幕吐槽的自然化改写。"
        )
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        header_layout.addWidget(desc)
        container_layout.addWidget(header_card)

        # 运行时设置面板
        runtime_card = QtWidgets.QFrame()
        runtime_card.setObjectName("dialogSection")
        runtime_layout = QtWidgets.QGridLayout(runtime_card)
        runtime_layout.setContentsMargins(14, 12, 14, 12)
        runtime_layout.setHorizontalSpacing(16)
        runtime_layout.setVerticalSpacing(10)

        self.chk_runtime_enabled = QtWidgets.QCheckBox("启用表达学习库")
        self.chk_runtime_chat = QtWidgets.QCheckBox("普通聊天启用")
        self.chk_runtime_sensor = QtWidgets.QCheckBox("屏幕吐槽启用")
        
        self.spin_runtime_max = QtWidgets.QSpinBox()
        self.spin_runtime_max.setRange(1, 8)
        self.spin_runtime_max.setValue(4)
        
        self.btn_save_runtime = QtWidgets.QPushButton("💾 保存配置")
        self.btn_save_runtime.setObjectName("primary_btn")

        runtime_layout.addWidget(self.chk_runtime_enabled, 0, 0)
        runtime_layout.addWidget(self.chk_runtime_chat, 0, 1)
        runtime_layout.addWidget(self.chk_runtime_sensor, 0, 2)
        
        lbl_max = QtWidgets.QLabel("最多参考条数:")
        lbl_max.setStyleSheet("font-weight: 600;")
        runtime_layout.addWidget(lbl_max, 1, 0, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        runtime_layout.addWidget(self.spin_runtime_max, 1, 1)
        runtime_layout.addWidget(self.btn_save_runtime, 1, 2)
        container_layout.addWidget(runtime_card)

        # 过滤工具栏
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(8)
        
        self.inp_query = QtWidgets.QLineEdit()
        self.inp_query.setPlaceholderText("🔍 搜索角色 / 情境 / 风格 / 示例")
        
        self.inp_character_filter = QtWidgets.QLineEdit()
        self.inp_character_filter.setPlaceholderText("角色过滤...")
        
        self.cmb_scene_filter = QtWidgets.QComboBox()
        self.cmb_scene_filter.addItem("全部场景", "")
        self.cmb_scene_filter.addItem("普通聊天", "chat")
        self.cmb_scene_filter.addItem("屏幕吐槽", "sensor")
        self.cmb_scene_filter.addItem("通用", "any")
        
        self.chk_enabled_only = QtWidgets.QCheckBox("仅看启用")
        
        self.btn_refresh = QtWidgets.QPushButton("⟳ 刷新")
        self.btn_refresh.setObjectName("main_btn")
        
        toolbar.addWidget(self.inp_query, 2)
        toolbar.addWidget(self.inp_character_filter, 1)
        toolbar.addWidget(self.cmb_scene_filter)
        toolbar.addWidget(self.chk_enabled_only)
        toolbar.addWidget(self.btn_refresh)
        container_layout.addLayout(toolbar)

        # 表格
        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["启用", "角色", "场景", "情境", "表达风格", "示例回复", "来源", "使用/评分", "更新时间"]
        )
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(40)
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        container_layout.addWidget(self.table, 1)

        # 底部操作栏
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        
        self.btn_add = QtWidgets.QPushButton("➕ 新增")
        self.btn_edit = QtWidgets.QPushButton("📝 编辑")
        self.btn_enable = QtWidgets.QPushButton("🟢 批量启用")
        self.btn_disable = QtWidgets.QPushButton("🔴 批量禁用")
        
        self.btn_delete = QtWidgets.QPushButton("🗑️ 删除")
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

        self.btn_import = QtWidgets.QPushButton("📥 导入学习成果")
        self.btn_import_chat = QtWidgets.QPushButton("💬 从聊天记录学习")
        self.btn_export = QtWidgets.QPushButton("📤 导出 JSON")
        self.btn_close = QtWidgets.QPushButton("关闭")
        self.btn_close.setObjectName("main_btn")
        self.btn_close.setMinimumWidth(80)

        actions.addWidget(self.btn_add)
        actions.addWidget(self.btn_edit)
        actions.addWidget(self.btn_enable)
        actions.addWidget(self.btn_disable)
        actions.addWidget(self.btn_delete)
        actions.addStretch()
        actions.addWidget(self.btn_import)
        actions.addWidget(self.btn_import_chat)
        actions.addWidget(self.btn_export)
        actions.addWidget(self.btn_close)
        container_layout.addLayout(actions)

        layout.addWidget(self.container)

        self.btn_save_runtime.clicked.connect(self._save_runtime_controls)
        self.btn_refresh.clicked.connect(self._refresh_table)
        self.inp_query.returnPressed.connect(self._refresh_table)
        self.inp_character_filter.returnPressed.connect(self._refresh_table)
        self.chk_enabled_only.toggled.connect(self._refresh_table)
        self.cmb_scene_filter.currentIndexChanged.connect(lambda *_: self._refresh_table())
        self.btn_add.clicked.connect(self._add_pattern)
        self.btn_edit.clicked.connect(self._edit_selected_pattern)
        self.btn_enable.clicked.connect(lambda: self._set_selected_patterns_enabled(True))
        self.btn_disable.clicked.connect(lambda: self._set_selected_patterns_enabled(False))
        self.btn_delete.clicked.connect(self._delete_selected_pattern)
        self.btn_import.clicked.connect(self._import_patterns)
        self.btn_import_chat.clicked.connect(self._import_chat_records)
        self.btn_export.clicked.connect(self._export_patterns)
        self.btn_close.clicked.connect(self.accept)
        self.table.itemDoubleClicked.connect(lambda *_: self._edit_selected_pattern())

    def _get_memory_store(self):
        if self.main_app is None:
            return None
        store = getattr(self.main_app, "memory_store", None)
        if store is not None:
            return store
        brain = getattr(self.main_app, "brain", None)
        return getattr(brain, "sqlite_store", None)

    def _load_runtime_controls(self):
        runtime = load_runtime_settings()
        self.chk_runtime_enabled.setChecked(
            bool(runtime.get("expression_library_enabled", True))
        )
        self.chk_runtime_chat.setChecked(
            bool(runtime.get("expression_library_use_in_chat", True))
        )
        self.chk_runtime_sensor.setChecked(
            bool(runtime.get("expression_library_use_in_screen", True))
        )
        try:
            max_items = int(runtime.get("expression_library_max_prompt_items", 4))
        except Exception:
            max_items = 4
        self.spin_runtime_max.setValue(max(1, min(8, max_items)))

    def _save_runtime_controls(self):
        update_runtime_settings(
            {
                "expression_library_enabled": self.chk_runtime_enabled.isChecked(),
                "expression_library_use_in_chat": self.chk_runtime_chat.isChecked(),
                "expression_library_use_in_screen": self.chk_runtime_sensor.isChecked(),
                "expression_library_max_prompt_items": int(self.spin_runtime_max.value()),
            }
        )
        QtWidgets.QMessageBox.information(self, "表达学习库", "运行时开关已保存。")

    def _scene_text(self, scene: str) -> str:
        scene = str(scene or "").strip().lower()
        return {
            "chat": "普通聊天",
            "sensor": "屏幕吐槽",
            "any": "通用",
            "*": "通用",
            "": "通用",
        }.get(scene, scene)

    def _preview_examples(self, row: Dict[str, Any]) -> str:
        content_list = row.get("content_list") if isinstance(row.get("content_list"), list) else []
        examples = [str(item).strip() for item in content_list if str(item).strip()]
        if not examples:
            fallback = str(row.get("example") or "").strip()
            examples = [fallback] if fallback else []
        if not examples:
            return "-"
        preview = " / ".join(examples[:2])
        if len(examples) > 2:
            preview += f" 等{len(examples)}条"
        return preview

    def _selected_pattern_id(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        if item is None:
            return ""
        return str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "").strip()

    def _selected_pattern_ids(self) -> List[str]:
        ids: List[str] = []
        selection = self.table.selectionModel()
        if selection is not None:
            rows = sorted({index.row() for index in selection.selectedRows()})
            for row in rows:
                item = self.table.item(row, 0)
                pattern_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "").strip() if item else ""
                if pattern_id and pattern_id not in ids:
                    ids.append(pattern_id)
        if not ids:
            pattern_id = self._selected_pattern_id()
            if pattern_id:
                ids.append(pattern_id)
        return ids

    def _refresh_table(self):
        store = self._get_memory_store()
        if store is None or not hasattr(store, "list_expression_patterns"):
            self.table.setRowCount(0)
            self.stats_label.setText("条目数：0（当前记忆存储不可用）")
            return
        try:
            rows = store.list_expression_patterns(
                character_name=self.inp_character_filter.text().strip(),
                scene=str(self.cmb_scene_filter.currentData() or "").strip(),
                enabled_only=self.chk_enabled_only.isChecked(),
                query=self.inp_query.text().strip(),
                limit=500,
                offset=0,
            )
        except Exception as exc:
            self.table.setRowCount(0)
            self.stats_label.setText("条目数：0（读取失败）")
            QtWidgets.QMessageBox.warning(self, "表达学习库", f"读取失败：{exc}")
            return

        self.table.setRowCount(0)
        for row_idx, row in enumerate(rows):
            self.table.insertRow(row_idx)
            enabled_text = "ON" if row.get("enabled") else "OFF"
            enabled_item = QtWidgets.QTableWidgetItem(enabled_text)
            enabled_item.setData(QtCore.Qt.ItemDataRole.UserRole, row.get("id"))
            self.table.setItem(row_idx, 0, enabled_item)
            self.table.setItem(
                row_idx, 1, QtWidgets.QTableWidgetItem(str(row.get("character_name") or "-"))
            )
            self.table.setItem(
                row_idx, 2, QtWidgets.QTableWidgetItem(self._scene_text(row.get("scene")))
            )
            self.table.setItem(
                row_idx, 3, QtWidgets.QTableWidgetItem(str(row.get("situation") or "-"))
            )
            self.table.setItem(
                row_idx, 4, QtWidgets.QTableWidgetItem(str(row.get("style") or "-"))
            )
            self.table.setItem(
                row_idx, 5, QtWidgets.QTableWidgetItem(self._preview_examples(row))
            )
            self.table.setItem(
                row_idx, 6, QtWidgets.QTableWidgetItem(str(row.get("source") or "manual"))
            )
            self.table.setItem(
                row_idx,
                7,
                QtWidgets.QTableWidgetItem(
                    f"{int(row.get('use_count') or 0)} / {float(row.get('quality_score') or 0):.1f}"
                ),
            )
            self.table.setItem(
                row_idx, 8, QtWidgets.QTableWidgetItem(str(row.get("updated_at") or ""))
            )
        self.stats_label.setText(f"条目数：{len(rows)}")
        self.table.resizeRowsToContents()

    def _add_pattern(self):
        store = self._get_memory_store()
        if store is None:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "当前记忆存储不可用。")
            return
        dlg = ExpressionPatternEditDialog(self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            store.upsert_expression_pattern(dlg.payload())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "表达学习库", f"保存失败：{exc}")
            return
        self._refresh_table()

    def _edit_selected_pattern(self):
        store = self._get_memory_store()
        pattern_ids = self._selected_pattern_ids()
        if len(pattern_ids) != 1:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "编辑时请只选中一个条目。")
            return
        pattern_id = pattern_ids[0]
        if store is None or not pattern_id:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "请先选中一个条目。")
            return
        pattern = store.get_expression_pattern(pattern_id)
        if not pattern:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "未找到该条目。")
            return
        dlg = ExpressionPatternEditDialog(self, pattern=pattern)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            store.upsert_expression_pattern(dlg.payload())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "表达学习库", f"保存失败：{exc}")
            return
        self._refresh_table()

    def _set_selected_patterns_enabled(self, enabled: bool):
        store = self._get_memory_store()
        pattern_ids = self._selected_pattern_ids()
        if store is None or not pattern_ids:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "请先选中至少一个条目。")
            return
        changed = 0
        skipped = 0
        for pattern_id in pattern_ids:
            pattern = store.get_expression_pattern(pattern_id)
            if not pattern:
                skipped += 1
                continue
            if bool(pattern.get("enabled")) == bool(enabled):
                skipped += 1
                continue
            pattern["enabled"] = bool(enabled)
            try:
                store.upsert_expression_pattern(pattern)
                changed += 1
            except Exception:
                skipped += 1
        self._refresh_table()
        action_text = "启用" if enabled else "禁用"
        QtWidgets.QMessageBox.information(
            self,
            "表达学习库",
            f"{action_text}完成：更新 {changed} 条，跳过 {skipped} 条。",
        )

    def _delete_selected_pattern(self):
        store = self._get_memory_store()
        pattern_ids = self._selected_pattern_ids()
        if store is None or not pattern_ids:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "请先选中至少一个条目。")
            return
        count = len(pattern_ids)
        reply = QtWidgets.QMessageBox.question(
            self,
            "删除条目",
            f"确定删除选中的 {count} 个表达学习条目吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        failed = 0
        for pattern_id in pattern_ids:
            if store.delete_expression_pattern(pattern_id):
                deleted += 1
            else:
                failed += 1
        self._refresh_table()
        if failed:
            QtWidgets.QMessageBox.warning(
                self,
                "表达学习库",
                f"删除完成：成功 {deleted} 条，失败 {failed} 条。",
            )
            return
        QtWidgets.QMessageBox.information(
            self,
            "表达学习库",
            f"删除完成：成功删除 {deleted} 条。",
        )

    def _normalize_import_rows(self, payload: Any, *, source_name: str = "") -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        def normalize_content_list(value: Any) -> List[str]:
            return _parse_possible_list(value)

        def add_row(data: Dict[str, Any], *, default_character: str = "", default_scene: str = "chat"):
            if not isinstance(data, dict):
                return
            content_list = normalize_content_list(data.get("content_list"))
            fallback_example = str(
                data.get("example")
                or data.get("reply")
                or data.get("text")
                or data.get("content")
                or ""
            ).strip()
            if fallback_example and fallback_example not in content_list:
                content_list.insert(0, fallback_example)
            source = str(data.get("source") or source_name or "imported").strip() or "imported"
            if str(data.get("type") or "").strip() == "maibot.expression.export":
                source = "maibot_export"
            meta: Dict[str, Any] = {}
            if "count" in data:
                meta["maibot_count"] = data.get("count")
            if "checked" in data:
                meta["maibot_checked"] = bool(data.get("checked"))
            if "rejected" in data:
                meta["maibot_rejected"] = bool(data.get("rejected"))
            if data.get("modified_by") is not None:
                meta["maibot_modified_by"] = data.get("modified_by")
            if data.get("last_active_time"):
                meta["maibot_last_active_time"] = data.get("last_active_time")
            if data.get("create_time"):
                meta["maibot_create_time"] = data.get("create_time")
            if data.get("exported_at"):
                meta["maibot_exported_at"] = data.get("exported_at")
            if data.get("source_chat_name"):
                meta["maibot_source_chat_name"] = data.get("source_chat_name")
            if data.get("chat_id"):
                meta["maibot_chat_id"] = data.get("chat_id")
            if data.get("context"):
                meta["maibot_context"] = data.get("context")
            style_list = normalize_content_list(data.get("style_list"))
            if style_list:
                meta["maibot_style_list"] = style_list
            if content_list:
                meta["content_list"] = content_list
            row = {
                "id": str(data.get("id") or "").strip(),
                "character_name": str(
                    data.get("character_name") or data.get("character") or default_character
                ).strip(),
                "scene": str(
                    data.get("scene") or data.get("context") or default_scene
                ).strip().lower()
                or "chat",
                "situation": str(
                    data.get("situation") or data.get("trigger") or data.get("when") or ""
                ).strip(),
                "style": str(
                    data.get("style") or data.get("habit") or data.get("pattern") or ""
                ).strip(),
                "example": content_list[0] if content_list else "",
                "content_list": content_list,
                "source": source,
                "quality_score": data.get("quality_score", data.get("score", 0)),
                "use_count": data.get("use_count", data.get("count", 0)),
                "enabled": data.get("enabled", not bool(data.get("rejected", False))),
                "meta": meta,
            }
            if row["style"] or row["example"]:
                rows.append(row)

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    add_row(item)
                elif isinstance(item, str) and item.strip():
                    rows.append(
                        {
                            "character_name": "",
                            "scene": "chat",
                            "situation": "",
                            "style": item.strip(),
                            "example": "",
                            "content_list": [],
                            "source": source_name or "imported",
                            "enabled": True,
                        }
                    )
            return rows

        if not isinstance(payload, dict):
            return rows

        if isinstance(payload.get("patterns"), list):
            return self._normalize_import_rows(payload.get("patterns"), source_name=source_name)
        if isinstance(payload.get("items"), list):
            return self._normalize_import_rows(payload.get("items"), source_name=source_name)
        if isinstance(payload.get("expressions"), list):
            export_source = source_name or "maibot_export"
            source_chat_name = str(payload.get("source_chat_name") or "").strip()
            export_type = str(payload.get("type") or "").strip()
            exported_at = str(payload.get("exported_at") or "").strip()
            wrapped_rows = []
            for item in payload.get("expressions") or []:
                if not isinstance(item, dict):
                    continue
                enriched = dict(item)
                if source_chat_name:
                    enriched["source_chat_name"] = source_chat_name
                if export_type:
                    enriched["type"] = export_type
                if exported_at:
                    enriched["exported_at"] = exported_at
                wrapped_rows.append(enriched)
            return self._normalize_import_rows(wrapped_rows, source_name=export_source)

        expression_habits = payload.get("expression_habits")
        if isinstance(expression_habits, dict):
            default_character = str(payload.get("name") or payload.get("character_name") or "").strip()
            for scene, items in expression_habits.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    text = str(item or "").strip()
                    if text:
                        rows.append(
                            {
                                "character_name": default_character,
                                "scene": str(scene or "chat").strip().lower() or "chat",
                                "situation": "",
                                "style": text,
                                "example": "",
                                "content_list": [],
                                "source": source_name or "character_profile",
                                "enabled": True,
                            }
                        )
            return rows

        characters = payload.get("characters")
        if isinstance(characters, dict):
            for char in characters.values():
                if isinstance(char, dict):
                    rows.extend(self._normalize_import_rows(char, source_name=source_name))
            return rows

        scene_dict_candidates = {"chat", "sensor", "any", "*"}
        if any(key in payload for key in scene_dict_candidates):
            for scene, items in payload.items():
                if scene not in scene_dict_candidates or not isinstance(items, list):
                    continue
                for item in items:
                    text = str(item or "").strip()
                    if text:
                        rows.append(
                            {
                                "character_name": "",
                                "scene": str(scene or "chat").strip().lower() or "chat",
                                "situation": "",
                                "style": text,
                                "example": "",
                                "content_list": [],
                                "source": source_name or "imported",
                                "enabled": True,
                            }
                        )
            return rows

        add_row(payload)
        return rows

    def _import_patterns(self):
        store = self._get_memory_store()
        if store is None:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "当前记忆存储不可用。")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "导入学习成果",
            str(Path.cwd()),
            "Structured Files (*.json *.xml);;JSON Files (*.json);;XML Files (*.xml);;All Files (*.*)",
        )
        if not path:
            return
        try:
            payload = _load_import_payload(Path(path))
            rows = self._normalize_import_rows(payload, source_name=Path(path).name)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "表达学习库", f"导入失败：{exc}")
            return
        if not rows:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "没有识别到可导入的学习条目。")
            return
        stats = store.import_expression_patterns(rows, replace=False)
        self._refresh_table()
        QtWidgets.QMessageBox.information(
            self,
            "表达学习库",
            f"导入完成：新增 {stats.get('inserted', 0)} 条，跳过 {stats.get('skipped', 0)} 条。",
        )

    def _import_chat_records(self):
        dlg = ChatRecordImportWizardDialog(
            self,
            main_app=self.main_app,
            default_target="expression",
        )
        dlg.exec()
        self._refresh_table()

    def _export_patterns(self):
        store = self._get_memory_store()
        if store is None:
            QtWidgets.QMessageBox.warning(self, "表达学习库", "当前记忆存储不可用。")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出表达学习库",
            str(Path.cwd() / "expression_library_export.json"),
            "JSON Files (*.json)",
        )
        if not path:
            return
        rows = store.list_expression_patterns(
            character_name=self.inp_character_filter.text().strip(),
            scene=str(self.cmb_scene_filter.currentData() or "").strip(),
            enabled_only=self.chk_enabled_only.isChecked(),
            query=self.inp_query.text().strip(),
            limit=2000,
            offset=0,
        )
        payload = {
            "exported_at": QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.DateFormat.ISODate),
            "patterns": rows,
        }
        try:
            Path(path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "表达学习库", f"导出失败：{exc}")
            return
        QtWidgets.QMessageBox.information(self, "表达学习库", "导出完成。")
