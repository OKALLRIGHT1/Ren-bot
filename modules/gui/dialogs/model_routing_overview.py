from __future__ import annotations

from PySide6 import QtCore, QtWidgets, QtGui

from config import LLM_ROUTER, MODELS
from modules.gui.styles import get_tool_dialog_styles, get_ui_palette
from modules.task_registry import (
    CALLER_PATTERN_DESCRIPTIONS,
    CALLER_TASK_PATTERNS,
    CALLER_TASK_REGISTRY,
    get_caller_description,
)


class ModelChainWidget(QtWidgets.QWidget):
    """自定义的模型调用链展示组件（包含带箭头的精致圆角胶囊标签）"""
    def __init__(self, chain: list[str], palette: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumHeight(34)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding, QtWidgets.QSizePolicy.Policy.Fixed)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        
        if not chain:
            empty_label = QtWidgets.QLabel("(未配置)")
            empty_label.setStyleSheet(f"""
                color: {palette.get('text_muted', '#9CA3AF')};
                font-style: italic;
                font-family: 'Segoe UI', 'Microsoft YaHei';
                font-size: 11px;
            """)
            layout.addWidget(empty_label)
        else:
            for i, model in enumerate(chain):
                if i > 0:
                    arrow_label = QtWidgets.QLabel("➔")
                    arrow_label.setStyleSheet(f"color: {palette.get('text_muted', '#9CA3AF')}; font-weight: bold; font-size: 11px;")
                    layout.addWidget(arrow_label)
                
                model_label = QtWidgets.QLabel(model)
                model_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
                model_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
                model_label.setStyleSheet(f"""
                    background-color: {palette.get('accent_soft', '#EEF2FF')};
                    color: {palette.get('accent_hover', '#4F46E5')};
                    border: 1px solid {palette.get('border_strong', '#D1D5DB')};
                    border-radius: 6px;
                    padding: 2px 8px;
                    font-weight: 600;
                    font-size: 11px;
                    font-family: 'Segoe UI', 'Microsoft YaHei';
                """)
                layout.addWidget(model_label)
        layout.addStretch()


class BadgeWidget(QtWidgets.QWidget):
    """通用的圆角状态徽章组件"""
    def __init__(self, text: str, bg_color: str, fg_color: str, border_color: str, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumHeight(30)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        
        label = QtWidgets.QLabel(text)
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        label.setStyleSheet(f"""
            background-color: {bg_color};
            color: {fg_color};
            border: 1px solid {border_color};
            border-radius: 4px;
            padding: 1px 6px;
            font-weight: bold;
            font-size: 11px;
            font-family: 'Segoe UI', 'Microsoft YaHei';
        """)
        layout.addWidget(label)
        layout.addStretch()


class ModelRoutingOverviewDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("模型路由总览")
        self.resize(1000, 700)
        self.setMinimumSize(880, 580)
        self.setStyleSheet(get_tool_dialog_styles())
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # 头部说明卡片
        header = QtWidgets.QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(6)

        title = QtWidgets.QLabel("模型路由总览")
        title.setObjectName("dialogTitle")
        desc = QtWidgets.QLabel(
            "只读查看当前任务匹配 (task_type)、模型路由链和 Caller 归属。方便开发和诊断不同的对话和系统模块具体使用了哪些模型。"
        )
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        layout.addWidget(header)

        # 标签页容器
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)

        # 创建精美表格
        self.route_view = self._make_table_view(["任务类型 (Task Type)", "执行模型链 (Model Chain)"])
        self.caller_view = self._make_table_view(["调用者 (Caller Source)", "映射任务 (Target Task)", "匹配类型", "用途说明"])
        self.model_view = self._make_table_view(["模型标识 (Key)", "接口提供商 (Provider)", "模型代号 (Model)", "接口地址 (Base URL)"])

        # 调整各个表格的列宽拉伸
        self.route_view.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.route_view.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.caller_view.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.caller_view.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.caller_view.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.caller_view.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.model_view.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.model_view.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.model_view.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.model_view.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.tabs.addTab(self.route_view, "任务路由")
        self.tabs.addTab(self.caller_view, "Caller 注册表")
        self.tabs.addTab(self.model_view, "模型清单")

        # 底部控制区
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()

        btn_refresh = QtWidgets.QPushButton("刷新数据")
        btn_refresh.setObjectName("primary_btn")
        btn_refresh.clicked.connect(self._refresh)
        footer.addWidget(btn_refresh)

        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        footer.addWidget(btn_close)

        layout.addLayout(footer)

    def _make_table_view(self, headers: list[str]) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.verticalHeader().setMinimumSectionSize(38)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.horizontalHeader().setDefaultAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        table.horizontalHeader().setMinimumSectionSize(96)
        table.horizontalHeader().setSectionsMovable(True)
        table.horizontalHeader().setSectionsClickable(False)
        
        # 局部滚动和内边距微调
        table.setStyleSheet("""
            QTableWidget {
                border: 1px solid transparent;
            }
            QTableWidget::item {
                padding: 6px 12px;
            }
        """)
        return table

    def _make_sizer_item(self, text: str) -> QtWidgets.QTableWidgetItem:
        # setCellWidget() does not stop the item delegate from painting text below
        # transparent widgets. Keep the item text empty and use sizeHint only.
        item = QtWidgets.QTableWidgetItem("")
        width = self.fontMetrics().horizontalAdvance(str(text or "")) + 28
        item.setSizeHint(QtCore.QSize(max(64, width), 38))
        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
        return item

    def _refresh(self):
        palette = get_ui_palette()
        bold_font = QtGui.QFont()
        bold_font.setBold(True)
        
        # 1. 刷新任务路由 Tab
        self.route_view.setRowCount(0)
        sorted_tasks = sorted(LLM_ROUTER.keys())
        self.route_view.setRowCount(len(sorted_tasks))
        for row, task in enumerate(sorted_tasks):
            chain = LLM_ROUTER.get(task, [])
            if isinstance(chain, str):
                chain = [chain]
            
            # 任务名称
            task_item = QtWidgets.QTableWidgetItem(task)
            task_item.setFont(bold_font)
            self.route_view.setItem(row, 0, task_item)
            
            # 增加占位文字项以撑开列宽（包含胶囊及箭头额外的左右内边距）
            placeholder_text = "       " + "   ➔   ".join(chain) + "       " if chain else "   (未配置)   "
            placeholder_item = self._make_sizer_item(placeholder_text)
            self.route_view.setItem(row, 1, placeholder_item)
            
            # 模型流胶囊组件
            chain_widget = ModelChainWidget(chain, palette, self.route_view)
            self.route_view.setCellWidget(row, 1, chain_widget)

        # 2. 刷新 Caller 注册表 Tab
        self.caller_view.setRowCount(0)
        caller_data = []
        for caller, task in CALLER_TASK_REGISTRY.items():
            caller_data.append((caller, task, "精确", get_caller_description(caller)))
        for pattern, task in CALLER_TASK_PATTERNS.items():
            caller_data.append((pattern, task, "通配", CALLER_PATTERN_DESCRIPTIONS.get(pattern, "")))
            
        caller_data.sort(key=lambda x: x[0])
        self.caller_view.setRowCount(len(caller_data))
        
        italic_font = QtGui.QFont()
        italic_font.setItalic(True)
        
        for row, (caller, task, reg_type, description) in enumerate(caller_data):
            # 调用者名称
            caller_item = QtWidgets.QTableWidgetItem(caller)
            if reg_type == "通配":
                caller_item.setFont(italic_font)
                caller_item.setForeground(QtGui.QColor(palette.get("text_secondary", "#6B7280")))
            else:
                caller_item.setFont(bold_font)
            self.caller_view.setItem(row, 0, caller_item)
            
            # 目标任务
            task_item = QtWidgets.QTableWidgetItem(task)
            task_item.setFont(bold_font)
            self.caller_view.setItem(row, 1, task_item)
            
            # 增加占位文字项以撑开列宽
            placeholder_item = self._make_sizer_item("         " + reg_type + "         ")
            self.caller_view.setItem(row, 2, placeholder_item)
            
            # 匹配方式 Badge
            if reg_type == "精确":
                badge = BadgeWidget(
                    "精确", 
                    palette.get("success_soft", "#D1FAE5"), 
                    palette.get("success", "#10B981"), 
                    palette.get("success_soft", "#D1FAE5"),
                    self.caller_view
                )
            else:
                badge = BadgeWidget(
                    "通配", 
                    palette.get("accent_soft", "#EEF2FF"), 
                    palette.get("accent", "#6366F1"), 
                    palette.get("accent_soft", "#EEF2FF"),
                    self.caller_view
                )
            self.caller_view.setCellWidget(row, 2, badge)

            desc_item = QtWidgets.QTableWidgetItem(description or "-")
            desc_item.setToolTip(description or "")
            self.caller_view.setItem(row, 3, desc_item)

        # 3. 刷新模型清单 Tab
        self.model_view.setRowCount(0)
        sorted_models = sorted(MODELS.keys())
        self.model_view.setRowCount(len(sorted_models))
        
        for row, model_key in enumerate(sorted_models):
            cfg = MODELS.get(model_key) or {}
            
            # 模型标识 Key
            key_item = QtWidgets.QTableWidgetItem(model_key)
            key_item.setFont(bold_font)
            self.model_view.setItem(row, 0, key_item)
            
            # 提供商 Badge
            provider = cfg.get("provider") or cfg.get("api_style") or "custom"
            provider_str = str(provider).lower()
            
            provider_disp = "Custom"
            if "openai" in provider_str:
                provider_disp = "OpenAI"
            elif "deepseek" in provider_str:
                provider_disp = "DeepSeek"
            elif "gemini" in provider_str or "google" in provider_str:
                provider_disp = "Gemini"
            elif "local" in provider_str or "ollama" in provider_str:
                provider_disp = "Ollama"
            elif "glm" in provider_str or "bigmodel" in provider_str:
                provider_disp = "ChatGLM"
            else:
                provider_disp = str(provider).capitalize()
                
            # 增加占位文字以撑开列宽
            placeholder_item = self._make_sizer_item("           " + provider_disp + "           ")
            self.model_view.setItem(row, 1, placeholder_item)
            
            # 根据常见的 provider 渲染不同色调
            if "openai" in provider_str:
                badge = BadgeWidget("OpenAI", "#ECFDF5", "#10B981", "#A7F3D0", self.model_view)
            elif "deepseek" in provider_str:
                badge = BadgeWidget("DeepSeek", "#EFF6FF", "#3B82F6", "#BFDBFE", self.model_view)
            elif "gemini" in provider_str or "google" in provider_str:
                badge = BadgeWidget("Gemini", "#F5F3FF", "#8B5CF6", "#DDD6FE", self.model_view)
            elif "local" in provider_str or "ollama" in provider_str:
                badge = BadgeWidget("Ollama", "#FEF3C7", "#D97706", "#FDE68A", self.model_view)
            elif "glm" in provider_str or "bigmodel" in provider_str:
                badge = BadgeWidget("ChatGLM", "#FFF5F5", "#E53E3E", "#FED7D7", self.model_view)
            else:
                badge = BadgeWidget(provider_disp, "#F3F4F6", "#4B5563", "#E5E7EB", self.model_view)
                
            self.model_view.setCellWidget(row, 1, badge)
            
            # 模型真实代号
            model_name = cfg.get("model", "")
            name_item = QtWidgets.QTableWidgetItem(model_name)
            self.model_view.setItem(row, 2, name_item)
            
            # 接口地址 (加悬浮 Tooltip 提示全路径)
            base_url = cfg.get("base_url", "")
            url_item = QtWidgets.QTableWidgetItem(base_url)
            url_item.setToolTip(base_url)
            url_item.setForeground(QtGui.QColor(palette.get("text_muted", "#9CA3AF")))
            self.model_view.setItem(row, 3, url_item)

        self._fit_table_content()

    def _fit_table_content(self):
        for table in (self.route_view, self.caller_view, self.model_view):
            table.resizeColumnsToContents()
            for row in range(table.rowCount()):
                table.setRowHeight(row, 42)

        # 保留可读性下限；更长内容交给水平滚动条，而不是省略文本。
        self.route_view.setColumnWidth(0, max(self.route_view.columnWidth(0), 180))
        self.route_view.setColumnWidth(1, max(self.route_view.columnWidth(1), 360))

        self.caller_view.setColumnWidth(0, max(self.caller_view.columnWidth(0), 280))
        self.caller_view.setColumnWidth(1, max(self.caller_view.columnWidth(1), 180))
        self.caller_view.setColumnWidth(2, max(self.caller_view.columnWidth(2), 96))
        self.caller_view.setColumnWidth(3, max(self.caller_view.columnWidth(3), 360))

        self.model_view.setColumnWidth(0, max(self.model_view.columnWidth(0), 180))
        self.model_view.setColumnWidth(1, max(self.model_view.columnWidth(1), 120))
        self.model_view.setColumnWidth(2, max(self.model_view.columnWidth(2), 240))
        self.model_view.setColumnWidth(3, max(self.model_view.columnWidth(3), 360))
