import functools
import os
import shutil

from PySide6 import QtWidgets, QtCore, QtGui
from modules.gui.styles import get_tool_dialog_styles
class PluginManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, plugin_manager=None, main_app=None, embedded: bool = False):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.main_app = main_app
        self.embedded = bool(embedded)
        if self.embedded:
            self.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setWindowTitle("插件管理")
        self.resize(700, 500)

        # 1. 现代化的样式表 (QSS)
        self.setStyleSheet(get_tool_dialog_styles())

        # 2. 布局结构
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("dialogShell")
        self.container.setObjectName("container")
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)
        container_layout.setSpacing(15)

        header_card = QtWidgets.QFrame()
        header_card.setObjectName("dialogHeader")
        header_layout = QtWidgets.QHBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)

        icon_label = QtWidgets.QLabel("🔌")
        icon_label.setStyleSheet("font-size: 20px;")
        header_layout.addWidget(icon_label)

        title = QtWidgets.QLabel("插件列表")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        import_btn = QtWidgets.QPushButton("📂 导入本地插件")
        import_btn.setObjectName("main_btn")
        import_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        import_btn.clicked.connect(self._import_local_plugin)
        header_layout.addWidget(import_btn)
        # 刷新按钮移到右上角，更符合操作习惯
        refresh_btn = QtWidgets.QPushButton("⟳ 刷新列表")
        refresh_btn.setObjectName("main_btn")
        refresh_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_plugins)
        header_layout.addWidget(refresh_btn)

        desc_label = QtWidgets.QLabel("这里集中查看插件启停、兼容入口和配置状态。")
        desc_label.setObjectName("dialogDesc")
        desc_label.setWordWrap(True)
        header_block = QtWidgets.QVBoxLayout()
        header_block.addLayout(header_layout)
        header_block.addWidget(desc_label)
        header_card.setLayout(header_block)
        container_layout.addWidget(header_card)

        # --- 表格区域 ---
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["名称 / 触发词", "类型", "状态", "操作"])

        # 表格交互设置
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)  # 去除网格线
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)  # 去除选中虚线框

        # 调整列宽模式
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)  # 名称自适应
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)

        self.table.setColumnWidth(1, 80)  # 类型
        self.table.setColumnWidth(2, 100)  # 状态
        self.table.setColumnWidth(3, 160)  # 操作

        # 增加行高，不再拥挤
        self.table.verticalHeader().setDefaultSectionSize(65)

        container_layout.addWidget(self.table)

        if not self.embedded:
            footer_layout = QtWidgets.QHBoxLayout()
            footer_layout.addStretch()

            close_btn = QtWidgets.QPushButton("关闭")
            close_btn.setObjectName("main_btn")
            close_btn.setMinimumWidth(100)
            close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            close_btn.clicked.connect(self.close)
            footer_layout.addWidget(close_btn)

            container_layout.addLayout(footer_layout)
        layout.addWidget(self.container)

        # 初始化数据
        self._refresh_plugins()

    def _refresh_plugins(self):
        self.table.setRowCount(0)
        if not self.plugin_manager:
            return

        plugins_info = self.plugin_manager.get_all_plugins_info()

        for row, info in enumerate(plugins_info):
            self.table.insertRow(row)

            # 1. 插件名称与触发词 (合并显示，更紧凑)
            name_widget = QtWidgets.QWidget()
            name_layout = QtWidgets.QVBoxLayout(name_widget)
            name_layout.setContentsMargins(10, 5, 0, 5)
            name_layout.setSpacing(2)
            name_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)

            lbl_name = QtWidgets.QLabel(info["name"])
            lbl_name.setStyleSheet("font-weight: bold; font-size: 14px; color: #1F2937;")

            lbl_trigger = QtWidgets.QLabel(f"触发: {info['trigger']}")
            lbl_trigger.setStyleSheet("font-size: 12px; color: #6B7280;")

            access_summary = str(info.get("access_summary") or "").strip()
            if access_summary:
                tooltip = f"触发权限：{access_summary}"
                name_widget.setToolTip(tooltip)
                lbl_name.setToolTip(tooltip)
                lbl_trigger.setToolTip(tooltip)

            name_layout.addWidget(lbl_name)
            name_layout.addWidget(lbl_trigger)
            self.table.setCellWidget(row, 0, name_widget)

            # 2. 类型
            type_item = QtWidgets.QTableWidgetItem(info["type"])
            type_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            type_item.setForeground(QtGui.QColor("#4B5563"))
            self.table.setItem(row, 1, type_item)

            # 3. 状态 (使用徽章样式 Widget)
            status_widget = QtWidgets.QWidget()
            status_layout = QtWidgets.QHBoxLayout(status_widget)
            status_layout.setContentsMargins(0, 0, 0, 0)
            status_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            lbl_status = QtWidgets.QLabel()
            if info["enabled"]:
                lbl_status.setText("● 已启用")
                lbl_status.setStyleSheet("""
                    background-color: #D1FAE5; color: #047857; 
                    padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 12px;
                """)
            else:
                lbl_status.setText("○ 已禁用")
                lbl_status.setStyleSheet("""
                    background-color: #F3F4F6; color: #6B7280; 
                    padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 12px;
                    border: 1px solid #E5E7EB;
                """)

            status_layout.addWidget(lbl_status)
            self.table.setCellWidget(row, 2, status_widget)

            # 4. 操作按钮组
            btn_widget = QtWidgets.QWidget()
            btn_layout = QtWidgets.QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(5, 5, 5, 5)
            btn_layout.setSpacing(8)
            btn_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            # 切换按钮
            toggle_btn = QtWidgets.QPushButton()
            toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            if info["enabled"]:
                toggle_btn.setText("禁用")
                # 红色边框样式
                toggle_btn.setStyleSheet("""
                    QPushButton {
                        background-color: white; color: #DC2626; border: 1px solid #FECACA;
                        border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 600;
                    }
                    QPushButton:hover { background-color: #FEF2F2; border-color: #DC2626; }
                """)
            else:
                toggle_btn.setText("启用")
                # 绿色填充样式
                toggle_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #059669; color: white; border: none;
                        border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 600;
                    }
                    QPushButton:hover { background-color: #047857; }
                """)
            toggle_btn.clicked.connect(functools.partial(self._toggle_plugin, info["trigger"]))

            # 编辑按钮
            edit_btn = QtWidgets.QPushButton("编辑")
            edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            edit_btn.setStyleSheet("""
                QPushButton {
                    background-color: white; color: #2563EB; border: 1px solid #BFDBFE;
                    border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 600;
                }
                QPushButton:hover { background-color: #EFF6FF; border-color: #2563EB; }
            """)
            edit_btn.clicked.connect(functools.partial(self._edit_plugin, info["trigger"]))

            btn_layout.addWidget(toggle_btn)
            btn_layout.addWidget(edit_btn)
            self.table.setCellWidget(row, 3, btn_widget)

    def _toggle_plugin(self, trigger: str):
        if not self.plugin_manager: return
        try:
            if self.plugin_manager.is_plugin_enabled(trigger):
                if self.plugin_manager.disable_plugin(trigger):
                    self._show_toast(f"🔌 插件 [{trigger}] 已禁用", False)
            else:
                if self.plugin_manager.enable_plugin(trigger):
                    self._show_toast(f"🔌 插件 [{trigger}] 已启用", True)
            self._refresh_plugins()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"操作失败: {str(e)}")

    def _show_toast(self, message, is_success):
        """简单的系统消息反馈"""
        if self.main_app:
            self.main_app.append("system", message)

    def _edit_plugin(self, trigger: str):
        if not self.plugin_manager: return
        try:
            dialog = PluginEditorDialog(parent=self, plugin_manager=self.plugin_manager, trigger=trigger)
            dialog.exec()
            self._refresh_plugins()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"打开编辑对话框失败: {str(e)}")
    def _import_local_plugin(self):
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择插件文件夹")
        if not dir_path: return

        # 简单校验：文件夹里有没有 plugin.py 和 config.json
        if not os.path.exists(os.path.join(dir_path, "plugin.py")):
            QtWidgets.QMessageBox.warning(self, "无效插件", "该文件夹下缺少 plugin.py")
            return

        folder_name = os.path.basename(dir_path)
        target_path = os.path.join("./plugins", folder_name)

        if os.path.exists(target_path):
            ret = QtWidgets.QMessageBox.question(
                self, "覆盖确认", f"插件 {folder_name} 已存在，是否覆盖？"
            )
            if ret != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(target_path)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "错误", f"无法删除旧文件: {e}")
                return

        try:
            shutil.copytree(dir_path, target_path)
            QtWidgets.QMessageBox.information(self, "成功", f"插件 {folder_name} 已导入！\n请点击刷新列表。")

            # 自动刷新并重载
            if self.plugin_manager:
                self.plugin_manager.load_plugins()
                self._refresh_plugins()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"导入失败: {e}")



class PluginEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, plugin_manager=None, trigger=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.trigger = trigger
        self.original_config = None
        self.config_fields = {}

        self.setWindowTitle(f"编辑插件 - {trigger}")
        self.setFixedSize(600, 500)

        # 样式
        self.setStyleSheet("""
            QDialog {
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 24px;
            }
            QLabel {
                color: #1F2937;
                font-size: 12px;
            }
            QLabel.section_title {
                font-size: 14px;
                font-weight: bold;
                color: #111827;
                padding: 5px 0;
                border-bottom: 2px solid #E5E7EB;
            }
            QLineEdit, QTextEdit, QSpinBox, QComboBox {
                background-color: #F9FAFB;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px;
                color: #1F2937;
            }
            QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 2px solid #3B82F6;
                background-color: #FFFFFF;
            }
            QTabWidget::pane {
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                background-color: #FFFFFF;
            }
            QTabBar::tab {
                background-color: #F3F4F6;
                color: #6B7280;
                padding: 8px 20px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #FFFFFF;
                color: #3B82F6;
                font-weight: bold;
            }
            QGroupBox {
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                color: #6B7280;
                font-size: 11px;
            }
            QPushButton {
                background-color: #059669;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:pressed {
                background-color: #065F46;
            }
            QPushButton#cancel_btn {
                background-color: #6B7280;
            }
            QPushButton#cancel_btn:hover {
                background-color: #4B5563;
            }
            QPushButton#reset_btn {
                background-color: #D97706;
            }
            QPushButton#reset_btn:hover {
                background-color: #B45309;
            }
            QPushButton#path_btn {
                background-color: #8B5CF6;
                padding: 5px 12px;
                font-size: 11px;
            }
            QPushButton#path_btn:hover {
                background-color: #7C3AED;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 创建标签页
        self.tab_widget = QtWidgets.QTabWidget()
        layout.addWidget(self.tab_widget)

        # 基本信息标签页
        self.basic_tab = QtWidgets.QWidget()
        self._setup_basic_tab()
        self.tab_widget.addTab(self.basic_tab, "基本信息")

        # 自定义配置标签页
        self.settings_tab = QtWidgets.QWidget()
        self._setup_settings_tab()
        self.tab_widget.addTab(self.settings_tab, "自定义配置")

        # 按钮区域
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()

        reset_btn = QtWidgets.QPushButton("🔄 重置")
        reset_btn.setObjectName("reset_btn")
        reset_btn.clicked.connect(self._reset_config)
        btn_layout.addWidget(reset_btn)

        cancel_btn = QtWidgets.QPushButton("取消")
        cancel_btn.setObjectName("cancel_btn")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QtWidgets.QPushButton("💾 保存")
        save_btn.clicked.connect(self._save_config)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        # 加载配置
        self._load_config()

    def _setup_basic_tab(self):
        """设置基本信息标签页"""
        layout = QtWidgets.QVBoxLayout(self.basic_tab)
        layout.setSpacing(15)

        # 标题
        title = QtWidgets.QLabel("📋 基本信息")
        title.setObjectName("section_title")
        layout.addWidget(title)

        form_layout = QtWidgets.QFormLayout()
        form_layout.setSpacing(12)

        # 名称
        self.name_input = QtWidgets.QLineEdit()
        form_layout.addRow("插件名称:", self.name_input)

        # 触发词
        self.trigger_input = QtWidgets.QLineEdit()
        self.trigger_input.setPlaceholderText("插件触发关键词")
        form_layout.addRow("触发词:", self.trigger_input)

        # 类型
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["react", "direct", "observe"])
        form_layout.addRow("类型:", self.type_combo)

        # 别名
        self.aliases_input = QtWidgets.QTextEdit()
        self.aliases_input.setMaximumHeight(80)
        self.aliases_input.setPlaceholderText("每行一个别名")
        form_layout.addRow("别名:", self.aliases_input)

        # 描述
        self.desc_input = QtWidgets.QTextEdit()
        self.desc_input.setMaximumHeight(80)
        self.desc_input.setPlaceholderText("插件功能描述")
        form_layout.addRow("描述:", self.desc_input)

        # 示例参数
        self.example_input = QtWidgets.QLineEdit()
        self.example_input.setPlaceholderText("示例使用参数")
        form_layout.addRow("示例参数:", self.example_input)

        # 超时时间
        self.timeout_input = QtWidgets.QSpinBox()
        self.timeout_input.setRange(1, 300)
        self.timeout_input.setSuffix(" 秒")
        form_layout.addRow("超时时间:", self.timeout_input)

        layout.addLayout(form_layout)

        access_group = QtWidgets.QGroupBox("触发权限")
        access_layout = QtWidgets.QVBoxLayout(access_group)
        access_layout.setSpacing(8)

        access_hint = QtWidgets.QLabel("控制插件能否被桌面本地入口、QQ 主人或其他 QQ 联系人触发。")
        access_hint.setWordWrap(True)
        access_hint.setStyleSheet("color: #6B7280; font-size: 12px;")
        access_layout.addWidget(access_hint)

        self.allow_local_checkbox = QtWidgets.QCheckBox("允许本地触发（桌面 / 语音 / 传感器）")
        self.allow_remote_qq_checkbox = QtWidgets.QCheckBox("允许 QQ 触发")
        self.allow_qq_owner_checkbox = QtWidgets.QCheckBox("允许 QQ 主人触发")
        self.allow_qq_others_checkbox = QtWidgets.QCheckBox("允许其他 QQ 联系人触发")

        access_layout.addWidget(self.allow_local_checkbox)
        access_layout.addWidget(self.allow_remote_qq_checkbox)
        access_layout.addWidget(self.allow_qq_owner_checkbox)
        access_layout.addWidget(self.allow_qq_others_checkbox)

        self.allow_remote_qq_checkbox.toggled.connect(self._sync_access_control_inputs)
        layout.addWidget(access_group)

        self._sync_access_control_inputs()
        layout.addStretch()

    def _normalize_access_control(self, access_control):
        if self.plugin_manager and hasattr(self.plugin_manager, "_normalize_access_control"):
            return self.plugin_manager._normalize_access_control(access_control)
        base = {
            "allow_local": True,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
        }
        if isinstance(access_control, dict):
            for key in base.keys():
                if key in access_control:
                    base[key] = bool(access_control.get(key))
        return base

    def _sync_access_control_inputs(self):
        qq_enabled = self.allow_remote_qq_checkbox.isChecked()
        self.allow_qq_owner_checkbox.setEnabled(qq_enabled)
        self.allow_qq_others_checkbox.setEnabled(qq_enabled)

    def _setup_settings_tab(self):
        """设置自定义配置标签页"""
        layout = QtWidgets.QVBoxLayout(self.settings_tab)
        layout.setSpacing(15)

        # 标题
        title = QtWidgets.QLabel("⚙️ 自定义配置")
        title.setObjectName("section_title")
        layout.addWidget(title)

        self.settings_scroll = QtWidgets.QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        layout.addWidget(self.settings_scroll)

        self.settings_container = QtWidgets.QWidget()
        self.settings_layout = QtWidgets.QVBoxLayout(self.settings_container)
        self.settings_layout.setSpacing(12)
        self.settings_layout.addStretch()
        self.settings_scroll.setWidget(self.settings_container)

    def _load_config(self):
        """加载插件配置"""
        if not self.plugin_manager or not self.trigger:
            return

        try:
            # 获取插件配置
            config = self.plugin_manager.get_plugin_config(self.trigger)
            if not config:
                return

            self.original_config = config.copy()

            # 加载基本信息
            self.name_input.setText(config.get("name", ""))
            self.trigger_input.setText(config.get("trigger", ""))
            self.type_combo.setCurrentText(config.get("type", "react"))

            aliases = config.get("aliases", [])
            if isinstance(aliases, list):
                self.aliases_input.setPlainText("\n".join(aliases))

            self.desc_input.setPlainText(config.get("description", ""))
            self.example_input.setText(config.get("example_arg", ""))
            self.timeout_input.setValue(config.get("timeout_sec", 6))

            access_control = self._normalize_access_control(config.get("access_control"))
            self.allow_local_checkbox.setChecked(access_control.get("allow_local", True))
            self.allow_remote_qq_checkbox.setChecked(access_control.get("allow_remote_qq", True))
            self.allow_qq_owner_checkbox.setChecked(access_control.get("allow_qq_owner", True))
            self.allow_qq_others_checkbox.setChecked(access_control.get("allow_qq_others", False))
            self._sync_access_control_inputs()

            # 加载自定义配置
            self._load_custom_settings(config)

        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"加载配置失败: {str(e)}")

    def _load_custom_settings(self, config):
        """加载自定义配置项（支持增强的元数据格式）"""
        # 清空现有配置项
        for i in reversed(range(self.settings_layout.count() - 1)):
            item = self.settings_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        settings = config.get("settings", {})
        if not settings:
            no_settings = QtWidgets.QLabel("此插件没有自定义配置项")
            no_settings.setStyleSheet("color: #9CA3AF; padding: 20px;")
            self.settings_layout.insertWidget(0, no_settings)
            return

        for key, setting_info in settings.items():
            # 支持两种格式：
            # 1. 简单格式: {"key": value}
            # 2. 增强格式: {"key": {"type": "...", "default": ..., "label": "...", "description": "..."}}

            if isinstance(setting_info, dict) and "type" in setting_info:
                # 增强格式
                setting_type = setting_info.get("type", "string")
                label = setting_info.get("label", key)
                description = setting_info.get("description", "")
                value = setting_info.get("default")
                min_val = setting_info.get("min", 0)
                max_val = setting_info.get("max", 10000)
                choices = setting_info.get("choices", [])
            else:
                # 简单格式（向后兼容）
                setting_type = self._infer_type(setting_info)
                label = key
                description = ""
                value = setting_info
                min_val = 0
                max_val = 10000
                choices = []

            # 创建配置项组
            group = QtWidgets.QGroupBox(label)
            group_layout = QtWidgets.QVBoxLayout(group)
            group_layout.setSpacing(8)

            # 添加说明文字
            if description:
                desc_label = QtWidgets.QLabel(description)
                desc_label.setStyleSheet("color: #6B7280; font-size: 11px; padding: 2px 0;")
                desc_label.setWordWrap(True)
                group_layout.addWidget(desc_label)

            # 根据类型创建对应的输入控件
            widget = self._create_setting_widget(
                key, setting_info, setting_type, value, min_val, max_val, choices
            )

            if widget:
                group_layout.addWidget(widget)

            self.settings_layout.insertWidget(self.settings_layout.count() - 1, group)

    def _infer_type(self, value):
        """推断值的类型"""
        if isinstance(value, list):
            return "list"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, (int, float)):
            return "number"
        elif isinstance(value, str):
            if ("\\" in value or "/" in value) and ("." in value):
                return "path" if "." not in value.split("\\")[-1].split("/")[-1] else "file"
            return "string"
        return "string"

    def _create_path_item(self, layout, path_value, path_inputs):
        """创建路径列表项（输入框 + 选择按钮 + 删除按钮）"""
        item_widget = QtWidgets.QWidget()
        item_layout = QtWidgets.QHBoxLayout(item_widget)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(5)

        # 路径输入框
        path_input = QtWidgets.QLineEdit()
        path_input.setText(str(path_value) if path_value else "")
        path_input.setPlaceholderText("选择或输入路径...")
        path_input.setStyleSheet("""
            QLineEdit {
                background-color: #F9FAFB;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px;
                color: #1F2937;
            }
            QLineEdit:focus {
                border: 2px solid #3B82F6;
                background-color: #FFFFFF;
            }
        """)

        # 选择按钮
        select_btn = QtWidgets.QPushButton("选择...")
        select_btn.setObjectName("path_btn")
        select_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        select_btn.clicked.connect(lambda: self._select_directory(path_input))

        # 删除按钮
        delete_btn = QtWidgets.QPushButton("✕")
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #EF4444; color: white; border: none;
                border-radius: 6px; padding: 8px 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #DC2626; }
        """)
        delete_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        delete_btn.setFixedSize(40, 35)
        delete_btn.clicked.connect(lambda: self._delete_path_item(layout, item_widget, path_input))

        item_layout.addWidget(path_input, 1)
        item_layout.addWidget(select_btn)
        item_layout.addWidget(delete_btn)

        # 插入到布局中（在添加按钮之前）
        layout.insertWidget(layout.count() - 1, item_widget)

        return path_input

    def _delete_path_item(self, layout, item_widget, path_input):
        """删除路径项"""
        item_widget.deleteLater()
        # 从 path_inputs 列表中移除
        if path_input in self.config_fields:
            for key, (field_type, widget) in self.config_fields.items():
                if field_type == "path_list" and isinstance(widget, list):
                    if path_input in widget:
                        widget.remove(path_input)
                        break

    def _create_app_list_item(self, layout, app_config, app_inputs):
        """创建应用列表项（别名输入框 + 程序路径输入框 + 选择按钮 + 删除按钮）"""
        # 解析配置：格式为 "别名|路径"
        alias = ""
        path = ""
        if app_config and "|" in str(app_config):
            parts = str(app_config).split("|", 1)
            alias = parts[0].strip()
            path = parts[1].strip() if len(parts) > 1 else ""

        item_widget = QtWidgets.QWidget()
        item_layout = QtWidgets.QHBoxLayout(item_widget)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(5)

        # 别名输入框
        alias_input = QtWidgets.QLineEdit()
        alias_input.setText(alias)
        alias_input.setPlaceholderText("别名")
        alias_input.setMinimumWidth(120)
        alias_input.setMaximumWidth(150)
        alias_input.setStyleSheet("""
            QLineEdit {
                background-color: #F9FAFB;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px;
                color: #1F2937;
            }
            QLineEdit:focus {
                border: 2px solid #3B82F6;
                background-color: #FFFFFF;
            }
        """)

        # 路径输入框
        path_input = QtWidgets.QLineEdit()
        path_input.setText(path)
        path_input.setPlaceholderText("程序路径")
        path_input.setStyleSheet("""
            QLineEdit {
                background-color: #F9FAFB;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px;
                color: #1F2937;
            }
            QLineEdit:focus {
                border: 2px solid #3B82F6;
                background-color: #FFFFFF;
            }
        """)

        # 选择程序按钮
        select_btn = QtWidgets.QPushButton("选择...")
        select_btn.setObjectName("path_btn")
        select_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        select_btn.clicked.connect(lambda: self._select_executable(path_input))

        # 删除按钮
        delete_btn = QtWidgets.QPushButton("✕")
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #EF4444; color: white; border: none;
                border-radius: 6px; padding: 8px 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #DC2626; }
        """)
        delete_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        delete_btn.setFixedSize(40, 35)
        delete_btn.clicked.connect(lambda: self._delete_app_list_item(layout, item_widget, app_inputs))

        item_layout.addWidget(alias_input)
        item_layout.addWidget(path_input, 1)
        item_layout.addWidget(select_btn)
        item_layout.addWidget(delete_btn)

        # 插入到布局中（在添加按钮之前）
        layout.insertWidget(layout.count() - 1, item_widget)

        # 存储引用
        app_inputs.append({"alias": alias_input, "path": path_input})
        return app_inputs[-1]

    def _delete_app_list_item(self, layout, item_widget, app_inputs):
        """删除应用列表项"""
        # 获取当前项的别名输入框
        alias_widget = item_widget.layout().itemAt(0).widget()
        path_widget = item_widget.layout().itemAt(1).widget()

        # 从 app_inputs 列表中移除
        for key, (field_type, widget) in self.config_fields.items():
            if field_type == "app_list" and isinstance(widget, list):
                # 找到对应的数据并移除（通过比较对象引用）
                for i, app_input in enumerate(widget):
                    if app_input["alias"] is alias_widget and app_input["path"] is path_widget:
                        widget.pop(i)
                        break
                break

        # 删除UI组件
        item_widget.deleteLater()

    def _create_setting_widget(self, key, setting_info, setting_type, value, min_val, max_val, choices):
        """根据类型创建配置控件"""
        widget = None

        if setting_type == "string" or setting_type == "text":
            # 文本类型
            if isinstance(value, list):
                text = "\n".join(str(v) for v in value)
            else:
                text = str(value) if value is not None else ""

            text_edit = QtWidgets.QTextEdit()
            text_edit.setMaximumHeight(80)
            text_edit.setPlaceholderText("请输入文本...")
            text_edit.setPlainText(text)
            self.config_fields[key] = ("text", text_edit)
            widget = text_edit

        elif setting_type == "number":
            # 数字类型
            spinbox = QtWidgets.QSpinBox()
            spinbox.setRange(min_val, max_val)
            spinbox.setValue(int(value) if value is not None else min_val)
            self.config_fields[key] = ("number", spinbox)
            widget = spinbox

        elif setting_type == "boolean" or setting_type == "bool":
            # 布尔类型
            checkbox = QtWidgets.QCheckBox("启用")
            checkbox.setChecked(bool(value) if value is not None else False)
            self.config_fields[key] = ("boolean", checkbox)
            widget = checkbox

        elif setting_type == "list":
            # 列表类型
            # 检查是否是 path 类型列表或 app_list 类型
            is_path_list = False
            is_app_list = False
            if isinstance(setting_info, dict) and "item_type" in setting_info:
                is_path_list = setting_info["item_type"] == "path"
                is_app_list = setting_info["item_type"] == "app_list"

            if is_path_list:
                # 路径列表：创建可动态添加/删除行的界面
                scroll = QtWidgets.QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setMaximumHeight(200)
                scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

                container = QtWidgets.QWidget()
                list_layout = QtWidgets.QVBoxLayout(container)
                list_layout.setSpacing(8)

                # 存储路径输入框的引用
                path_inputs = []

                # 添加现有路径
                if isinstance(value, list):
                    for path in value:
                        if path:
                            path_inputs.append(self._create_path_item(list_layout, path, path_inputs))

                # 添加"添加路径"按钮
                add_btn = QtWidgets.QPushButton("+ 添加路径")
                add_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #10B981; color: white; border: none;
                        border-radius: 6px; padding: 8px; font-weight: bold;
                    }
                    QPushButton:hover { background-color: #059669; }
                """)
                add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                add_btn.clicked.connect(lambda: path_inputs.append(
                    self._create_path_item(list_layout, "", path_inputs)
                ))
                list_layout.addWidget(add_btn)

                scroll.setWidget(container)
                self.config_fields[key] = ("path_list", path_inputs)
                widget = scroll
            elif is_app_list:
                # 应用列表：创建可动态添加/删除行的界面（包含别名和路径）
                scroll = QtWidgets.QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setMaximumHeight(200)
                scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

                container = QtWidgets.QWidget()
                list_layout = QtWidgets.QVBoxLayout(container)
                list_layout.setSpacing(8)

                # 存储应用输入框的引用
                app_inputs = []

                # 添加现有应用
                if isinstance(value, list):
                    for app in value:
                        if app:
                            self._create_app_list_item(list_layout, app, app_inputs)

                # 添加"添加应用"按钮
                add_btn = QtWidgets.QPushButton("+ 添加应用")
                add_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #10B981; color: white; border: none;
                        border-radius: 6px; padding: 8px; font-weight: bold;
                    }
                    QPushButton:hover { background-color: #059669; }
                """)
                add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                # 使用默认参数确保列表引用正确
                add_btn.clicked.connect(lambda checked=False, inputs=app_inputs: self._create_app_list_item(list_layout, "", inputs))
                list_layout.addWidget(add_btn)

                scroll.setWidget(container)
                self.config_fields[key] = ("app_list", app_inputs)
                widget = scroll
            else:
                # 普通列表：使用多行文本框
                if isinstance(value, list):
                    text = "\n".join(str(v) for v in value)
                else:
                    text = ""

                text_edit = QtWidgets.QTextEdit()
                text_edit.setMaximumHeight(100)
                text_edit.setPlaceholderText("每行一个项目...")
                text_edit.setPlainText(text)
                self.config_fields[key] = ("list", text_edit)
                widget = text_edit

        elif setting_type == "path":
            # 路径类型
            path_layout = QtWidgets.QHBoxLayout()
            line_edit = QtWidgets.QLineEdit()
            line_edit.setText(str(value) if value is not None else "")
            line_edit.setPlaceholderText("选择或输入路径...")

            path_btn = QtWidgets.QPushButton("选择...")
            path_btn.setObjectName("path_btn")
            path_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            path_btn.clicked.connect(lambda checked, le=line_edit: self._select_directory(le))

            path_layout.addWidget(line_edit, 1)
            path_layout.addWidget(path_btn)
            self.config_fields[key] = ("path", line_edit)

            # 创建容器widget返回
            container = QtWidgets.QWidget()
            container.setLayout(path_layout)
            widget = container

        elif setting_type == "file":
            # 文件类型
            file_layout = QtWidgets.QHBoxLayout()
            line_edit = QtWidgets.QLineEdit()
            line_edit.setText(str(value) if value is not None else "")
            line_edit.setPlaceholderText("选择或输入文件路径...")

            file_btn = QtWidgets.QPushButton("选择...")
            file_btn.setObjectName("path_btn")
            file_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            file_btn.clicked.connect(lambda checked, le=line_edit: self._select_file(le))

            file_layout.addWidget(line_edit, 1)
            file_layout.addWidget(file_btn)
            self.config_fields[key] = ("file", line_edit)

            container = QtWidgets.QWidget()
            container.setLayout(file_layout)
            widget = container

        elif setting_type == "choice":
            # 选择类型（下拉框）
            combo = QtWidgets.QComboBox()
            combo.addItems(choices)
            if value is not None and str(value) in choices:
                combo.setCurrentText(str(value))
            self.config_fields[key] = ("choice", combo)
            widget = combo

        return widget

    def _select_directory(self, line_edit):
        """选择目录对话框"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            line_edit.setText(path)

    def _select_file(self, line_edit):
        """选择文件对话框"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            line_edit.setText(path)

    def _select_executable(self, line_edit):
        """选择可执行文件对话框"""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择可执行文件",
            "",
            "可执行文件 (*.exe *.bat *.cmd *.sh);;所有文件 (*.*)"
        )
        if path:
            line_edit.setText(path)

    def _select_path(self, line_edit):
        """选择路径对话框"""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            line_edit.setText(path)

    def _reset_config(self):
        """重置配置到原始值"""
        if not self.original_config:
            return

        reply = QtWidgets.QMessageBox.question(
            self, "确认重置",
            "确定要重置所有配置到原始值吗？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._load_config()

    def _save_config(self):
        """保存配置"""
        if not self.plugin_manager or not self.trigger:
            return

        try:
            # 验证必填字段
            name = self.name_input.text().strip()
            trigger = self.trigger_input.text().strip()
            if not name or not trigger:
                QtWidgets.QMessageBox.warning(self, "验证失败", "插件名称和触发词不能为空")
                return

            # 构建新配置
            new_config = self.original_config.copy()
            new_config["name"] = name
            new_config["trigger"] = trigger
            new_config["type"] = self.type_combo.currentText()

            aliases_text = self.aliases_input.toPlainText().strip()
            aliases = [line.strip() for line in aliases_text.split("\n") if line.strip()]
            new_config["aliases"] = aliases

            new_config["description"] = self.desc_input.toPlainText().strip()
            new_config["example_arg"] = self.example_input.text().strip()
            new_config["timeout_sec"] = self.timeout_input.value()
            new_config["access_control"] = {
                "allow_local": self.allow_local_checkbox.isChecked(),
                "allow_remote_qq": self.allow_remote_qq_checkbox.isChecked(),
                "allow_qq_owner": self.allow_qq_owner_checkbox.isChecked(),
                "allow_qq_others": self.allow_qq_others_checkbox.isChecked(),
            }

            # 保存自定义配置
            settings = {}

            def _store_setting_value(setting_key, raw_value):
                original_setting = self.original_config.get("settings", {}).get(setting_key, {})
                if isinstance(original_setting, dict):
                    settings[setting_key] = {
                        **original_setting,
                        "default": raw_value,
                    }
                else:
                    settings[setting_key] = raw_value

            for key, (field_type, widget) in self.config_fields.items():
                if field_type == "list":
                    value = [line.strip() for line in widget.toPlainText().split("\n") if line.strip()]
                    _store_setting_value(key, value)
                elif field_type == "bool":
                    value = widget.isChecked()
                    _store_setting_value(key, value)
                elif field_type == "number":
                    value = widget.value()
                    _store_setting_value(key, value)
                elif field_type == "choice":
                    value = widget.currentText().strip()
                    _store_setting_value(key, value)
                elif field_type == "path_list":
                    # path_list 类型：widget 是 line_edit 的列表
                    value = []
                    if isinstance(widget, list):
                        for line_edit in widget:
                            path = line_edit.text().strip()
                            if path:
                                value.append(path)
                    _store_setting_value(key, value)
                elif field_type == "app_list":
                    # app_list 类型：widget 是包含 alias 和 path 的字典列表
                    value = []
                    if isinstance(widget, list):
                        for app_input in widget:
                            alias = app_input["alias"].text().strip()
                            path = app_input["path"].text().strip()
                            if alias and path:
                                value.append(f"{alias}|{path}")

                    _store_setting_value(key, value)
                else:
                    # text, path, file 等类型
                    value = widget.text().strip()
                    _store_setting_value(key, value)

            new_config["settings"] = settings

            # 打印调试信息
            print(f"💾 保存配置 [{trigger}]:")
            print(f"  - 自定义配置: {settings}")

            # 保存到文件
            if self.plugin_manager.save_plugin_config(self.trigger, new_config):
                QtWidgets.QMessageBox.information(self, "成功", "插件配置已保存")
                self.accept()
            else:
                QtWidgets.QMessageBox.warning(self, "失败", "保存插件配置失败")

        except Exception as e:
            import traceback
            print(f"❌ 保存配置异常: {e}")
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "错误", f"保存配置失败: {str(e)}")
