import asyncio
import functools
import json
import os
import re
import shutil

from PySide6 import QtWidgets, QtCore, QtGui
from modules.gui.styles import get_tool_dialog_styles
from modules.gui.utils import FlowWidget
from modules.gui.dialogs.plugin_market_panel import PluginMarketPanel
from modules.gui.user_ia import (
    CAPABILITY_GROUP_LABELS,
    capability_group,
    capability_group_label,
    is_developer_mode,
)


def setting_ui_hidden(setting_info) -> bool:
    if not isinstance(setting_info, dict):
        return False
    ui = str(setting_info.get("ui") or "").strip().lower()
    if ui in {"hidden", "advanced", "internal"}:
        return True
    return bool(setting_info.get("hidden"))


def is_plausible_url(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    return raw.startswith(("http://", "https://", "ws://", "wss://", "/"))


def _setting_default(raw):
    if isinstance(raw, dict):
        if "default" in raw:
            return raw.get("default")
        return raw.get("value")
    return raw


def webui_url_from_settings(settings) -> str:
    data = settings if isinstance(settings, dict) else {}
    explicit = str(_setting_default(data.get("status_ui_url")) or "").strip()
    port_raw = _setting_default(data.get("status_ui_port"))
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 8765
    if port < 1024 or port > 65535:
        port = 8765
    host_raw = str(_setting_default(data.get("status_ui_host")) or "127.0.0.1").strip()
    host = host_raw or "127.0.0.1"
    composed = f"http://{host}:{port}/"
    if explicit.startswith(("http://", "https://")) and "status_ui_port" not in data:
        return explicit
    return composed


class PluginManagerDialog(QtWidgets.QDialog):
    def __init__(
        self, parent=None, plugin_manager=None, main_app=None, embedded: bool = False
    ):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.main_app = main_app
        self.embedded = bool(embedded)
        if self.embedded:
            self.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
        self.setWindowTitle("能力")
        self.resize(700, 500)

        # 1. 现代化的样式表 (QSS)
        qss = get_tool_dialog_styles()
        if self.embedded:
            qss += "\nQDialog { background: transparent; border: none; }"
        qss += """
            QFrame#pluginCard {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
            QFrame#pluginCard:hover {
                border-color: #93C5FD;
                background: #FAFCFF;
            }
            QFrame#pluginCardDisabled {
                background: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
            QFrame#pluginCardDisabled:hover {
                border-color: #D1D5DB;
                background: #F3F4F6;
            }
        """
        self.setStyleSheet(qss)

        # 2. 布局结构：嵌入设置页时压缩外壳，把空间留给卡片网格。
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(
            0 if self.embedded else 16,
            0 if self.embedded else 16,
            0 if self.embedded else 16,
            0 if self.embedded else 16,
        )

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("dialogShell")
        if self.embedded:
            self.container.setStyleSheet(
                "QFrame#dialogShell { background: transparent; border: none; }"
            )
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(
            0 if self.embedded else 18,
            0 if self.embedded else 16,
            0 if self.embedded else 18,
            0 if self.embedded else 16,
        )
        container_layout.setSpacing(8 if self.embedded else 12)

        if not self.embedded:
            header_row = QtWidgets.QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(8)
            icon_label = QtWidgets.QLabel("🔌")
            icon_label.setStyleSheet("font-size: 18px;")
            header_row.addWidget(icon_label)
            title = QtWidgets.QLabel("能力")
            title.setObjectName("dialogTitle")
            header_row.addWidget(title)
            header_row.addStretch(1)
            container_layout.addLayout(header_row)

            desc_label = QtWidgets.QLabel(
                "她被允许做的事。关掉不需要的能力；开发类能力要打开「显示开发者选项」才出现。"
            )
            desc_label.setObjectName("dialogDesc")
            desc_label.setWordWrap(True)
            container_layout.addWidget(desc_label)

        # --- 本机能力 / 插件市场 ---
        view_row = QtWidgets.QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(8)
        self.local_view_button = QtWidgets.QPushButton("本机能力")
        self.market_view_button = QtWidgets.QPushButton("插件市场")
        for button in (self.local_view_button, self.market_view_button):
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(30)
            button.setCheckable(True)
        self.local_view_button.clicked.connect(lambda: self._set_market_mode(False))
        self.market_view_button.clicked.connect(lambda: self._set_market_mode(True))
        view_row.addWidget(self.local_view_button)
        view_row.addWidget(self.market_view_button)
        view_row.addStretch(1)
        container_layout.addLayout(view_row)

        # --- 单行紧凑控制栏：搜索 + 分组筛选 + 统计 + 导入/刷新 ---
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("搜索能力名称、说明或触发词…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_plugins)
        self.search_input.setFixedHeight(30)
        self.search_input.setStyleSheet("padding: 4px 10px; font-size: 12px;")

        self.group_filter = QtWidgets.QComboBox()
        self.group_filter.setFixedWidth(112)
        self.group_filter.setFixedHeight(30)
        self.group_filter.currentIndexChanged.connect(self._filter_plugins)

        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setStyleSheet("color: #6B7280; font-size: 11px; padding: 0 4px;")

        self.import_btn = QtWidgets.QPushButton("导入插件")
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        for btn in (self.import_btn, self.refresh_btn):
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setStyleSheet(
                """
                QPushButton {
                    background: #FFFFFF;
                    color: #374151;
                    border: 1px solid #D1D5DB;
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    border-color: #93C5FD;
                    color: #1D4ED8;
                    background: #EFF6FF;
                }
                """
            )
        self.import_btn.clicked.connect(self._import_local_plugin)
        self.refresh_btn.clicked.connect(self._refresh_plugins)

        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.group_filter)
        filter_row.addWidget(self.stats_label)
        filter_row.addWidget(self.import_btn)
        filter_row.addWidget(self.refresh_btn)
        container_layout.addLayout(filter_row)

        self._developer_visible = is_developer_mode()
        self._reload_group_filter()

        # --- 小卡片滚动网格区 ---
        self._cards_data: list[dict] = []
        self.card_widgets_by_trigger: dict[str, QtWidgets.QFrame] = {}

        self.cards_scroll = QtWidgets.QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.cards_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.cards_scroll.viewport().setAutoFillBackground(False)

        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setAutoFillBackground(False)
        self.cards_outer_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_outer_layout.setContentsMargins(0, 2, 2, 2)
        self.cards_outer_layout.setSpacing(8)

        self.cards_grid = QtWidgets.QGridLayout()
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(10)
        self.cards_grid.setVerticalSpacing(8)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        self.cards_outer_layout.addLayout(self.cards_grid)

        self.empty_label = QtWidgets.QLabel("没有找到匹配的能力")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #9CA3AF; font-size: 12px; padding: 28px 0;")
        self.empty_label.setVisible(False)
        self.cards_outer_layout.addWidget(self.empty_label)
        self.cards_outer_layout.addStretch(1)

        self.cards_scroll.setWidget(self.cards_container)
        container_layout.addWidget(self.cards_scroll, 1)

        self.market_panel = PluginMarketPanel(plugin_manager, self)
        self.market_panel.setVisible(False)
        self.market_panel.plugins_changed.connect(self._refresh_plugins)
        container_layout.addWidget(self.market_panel, 1)
        self._set_market_mode(False)

        # 保留隐藏的 table 属性以兼容潜在外部引用
        self.table = QtWidgets.QTableWidget(self)
        self.table.setVisible(False)

        if not self.embedded:
            footer_layout = QtWidgets.QHBoxLayout()
            footer_layout.addStretch()

            close_btn = QtWidgets.QPushButton("关闭")
            close_btn.setObjectName("main_btn")
            close_btn.setMinimumWidth(96)
            close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            close_btn.clicked.connect(self.close)
            footer_layout.addWidget(close_btn)

            container_layout.addLayout(footer_layout)
        layout.addWidget(self.container)

        # 初始化数据
        self._refresh_plugins()

    def set_developer_visible(self, visible: bool):
        self._developer_visible = bool(visible)
        self._reload_group_filter()
        self._refresh_plugins()
        if hasattr(self, "market_panel"):
            self.market_panel.set_developer_visible(visible)

    def _set_market_mode(self, enabled: bool):
        market = bool(enabled)
        self.local_view_button.setChecked(not market)
        self.market_view_button.setChecked(market)
        active = (
            "QPushButton { background: #EFF6FF; color: #1D4ED8; border: 1px solid #93C5FD;"
            " border-radius: 8px; padding: 4px 10px; font-size: 12px; font-weight: 700; }"
        )
        idle = (
            "QPushButton { background: #FFFFFF; color: #374151; border: 1px solid #D1D5DB;"
            " border-radius: 8px; padding: 4px 10px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { border-color: #93C5FD; color: #1D4ED8; background: #EFF6FF; }"
        )
        self.local_view_button.setStyleSheet(idle if market else active)
        self.market_view_button.setStyleSheet(active if market else idle)
        for widget in (
            self.search_input,
            self.group_filter,
            self.stats_label,
            self.import_btn,
            self.refresh_btn,
            self.cards_scroll,
        ):
            widget.setVisible(not market)
        self.market_panel.setVisible(market)
        if market:
            self.market_panel.set_developer_visible(self._developer_visible)
            self.market_panel.open_market()

    def _reload_group_filter(self):
        if not hasattr(self, "group_filter"):
            return
        current = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("全部分组", "")
        for key, label in CAPABILITY_GROUP_LABELS.items():
            if key == "developer" and not getattr(self, "_developer_visible", False):
                continue
            self.group_filter.addItem(label, key)
        idx = self.group_filter.findData(current)
        self.group_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_filter.blockSignals(False)

    @staticmethod
    def _group_badge_style(group: str) -> str:
        palette = {
            "companion": ("#EEF2FF", "#4338CA", "#C7D2FE"),
            "desktop": ("#ECFDF5", "#047857", "#A7F3D0"),
            "qq": ("#EFF6FF", "#1D4ED8", "#BFDBFE"),
            "developer": ("#FEF3C7", "#B45309", "#FDE68A"),
        }
        bg, fg, border = palette.get(group, ("#F3F4F6", "#4B5563", "#E5E7EB"))
        return (
            f"background-color: {bg}; color: {fg}; border: 1px solid {border};"
            "padding: 1px 6px; border-radius: 6px; font-size: 10px; font-weight: 600;"
        )

    def _create_plugin_card(self, info: dict, group: str) -> QtWidgets.QFrame:
        trigger = str(info.get("trigger") or "")
        enabled = bool(info.get("enabled"))
        name = str(info.get("name") or trigger)
        summary = str(info.get("description") or "").strip() or f"触发词：{trigger}"
        access_summary = str(info.get("access_summary") or "").strip()
        group_label = capability_group_label(group)

        card = QtWidgets.QFrame(self.cards_container)
        card.setObjectName("pluginCard" if enabled else "pluginCardDisabled")
        card.setFixedHeight(78)
        card.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(3)

        # 第一行：名称 + 分组标签 + 右侧状态开关胶囊 + 配置按钮
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        lbl_name = QtWidgets.QLabel(name)
        name_color = "#111827" if enabled else "#6B7280"
        lbl_name.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {name_color}; background: transparent;"
        )
        top_row.addWidget(lbl_name)

        lbl_group = QtWidgets.QLabel(group_label)
        lbl_group.setStyleSheet(self._group_badge_style(group))
        lbl_group.setFixedHeight(18)
        top_row.addWidget(lbl_group)

        top_row.addStretch(1)

        # 状态与切换合一的胶囊按钮
        toggle_btn = QtWidgets.QPushButton("● 已启用" if enabled else "○ 已禁用")
        toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        toggle_btn.setFixedHeight(22)
        if enabled:
            toggle_btn.setToolTip("当前已启用，点击禁用此能力")
            toggle_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #DCFCE7; color: #15803D;
                    border: 1px solid #86EFAC; border-radius: 11px;
                    padding: 1px 8px; font-size: 11px; font-weight: 600;
                    min-width: 56px;
                }
                QPushButton:hover {
                    background-color: #FEE2E2; color: #B91C1C; border-color: #FCA5A5;
                }
                """
            )
        else:
            toggle_btn.setToolTip("当前已禁用，点击启用此能力")
            toggle_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #F3F4F6; color: #6B7280;
                    border: 1px solid #D1D5DB; border-radius: 11px;
                    padding: 1px 8px; font-size: 11px; font-weight: 600;
                    min-width: 56px;
                }
                QPushButton:hover {
                    background-color: #DCFCE7; color: #15803D; border-color: #86EFAC;
                }
                """
            )
        toggle_btn.clicked.connect(functools.partial(self._toggle_plugin, trigger))

        edit_btn = QtWidgets.QPushButton("配置")
        edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        edit_btn.setFixedHeight(22)
        edit_btn.setToolTip("修改参数、触发权限与元数据")
        edit_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #FFFFFF; color: #2563EB; border: 1px solid #BFDBFE;
                border-radius: 6px; padding: 1px 8px; font-size: 11px; font-weight: 600;
                min-width: 38px;
            }
            QPushButton:hover { background-color: #EFF6FF; border-color: #2563EB; }
            """
        )
        edit_btn.clicked.connect(functools.partial(self._edit_plugin, trigger))

        top_row.addWidget(toggle_btn)
        top_row.addWidget(edit_btn)
        card_layout.addLayout(top_row)

        # 第二行：单行紧凑描述（超出自动省略）
        desc_color = "#4B5563" if enabled else "#9CA3AF"
        lbl_desc = QtWidgets.QLabel()
        lbl_desc.setStyleSheet(f"font-size: 11px; color: {desc_color}; background: transparent;")
        lbl_desc.setWordWrap(False)
        fm = lbl_desc.fontMetrics()
        lbl_desc.setText(fm.elidedText(summary.replace("\n", " "), QtCore.Qt.TextElideMode.ElideRight, 280))
        card_layout.addWidget(lbl_desc)

        # 第三行：底部元信息（触发词 + 权限简述）
        meta_parts = [f"触发词: {trigger}"]
        if access_summary:
            meta_parts.append(access_summary)
        meta_text = "  ·  ".join(meta_parts)
        lbl_meta = QtWidgets.QLabel()
        lbl_meta.setStyleSheet("font-size: 10px; color: #9CA3AF; background: transparent;")
        lbl_meta.setWordWrap(False)
        lbl_meta.setText(
            lbl_meta.fontMetrics().elidedText(meta_text, QtCore.Qt.TextElideMode.ElideRight, 290)
        )
        card_layout.addWidget(lbl_meta)

        tooltip_bits = [f"名称：{name}", f"分组：{group_label}", f"触发词：{trigger}"]
        if summary:
            tooltip_bits.append(f"说明：{summary}")
        if access_summary:
            tooltip_bits.append(f"权限：{access_summary}")
        tooltip = "\n".join(tooltip_bits)
        card.setToolTip(tooltip)
        lbl_name.setToolTip(tooltip)
        lbl_desc.setToolTip(tooltip)
        lbl_meta.setToolTip(tooltip)

        return card

    def _refresh_plugins(self):
        # 清理旧卡片（包括被过滤隐藏、未在 grid 中的卡片）
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)
        for entry in self._cards_data:
            w = entry.get("card")
            if w is not None:
                w.hide()
                w.deleteLater()
        self._cards_data.clear()
        self.card_widgets_by_trigger.clear()

        if not self.plugin_manager:
            self.stats_label.setText("0 项")
            self.empty_label.setVisible(True)
            return

        plugins_info = self.plugin_manager.get_all_plugins_info()
        show_dev = bool(getattr(self, "_developer_visible", False))

        for info in plugins_info:
            trigger = str(info.get("trigger") or "")
            group = capability_group(trigger)
            if group == "developer" and not show_dev:
                continue
            card = self._create_plugin_card(info, group)
            search_blob = " ".join(
                [
                    str(info.get("name") or ""),
                    trigger,
                    str(info.get("description") or ""),
                    capability_group_label(group),
                ]
            ).lower()
            entry = {
                "trigger": trigger,
                "group": group,
                "enabled": bool(info.get("enabled")),
                "search_blob": search_blob,
                "card": card,
            }
            self._cards_data.append(entry)
            self.card_widgets_by_trigger[trigger] = card

        self._filter_plugins()

    def _filter_plugins(self, _text=None):
        text = ""
        if hasattr(self, "search_input"):
            text = self.search_input.text().lower().strip()
        group = ""
        if hasattr(self, "group_filter"):
            group = str(self.group_filter.currentData() or "").strip()

        # 先从网格移除所有项（不销毁 widget），再按可见顺序紧密排列成双列
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)

        visible_entries = []
        for entry in self._cards_data:
            card = entry["card"]
            if group and entry["group"] != group:
                card.setVisible(False)
                continue
            if text and text not in entry["search_blob"]:
                card.setVisible(False)
                continue
            visible_entries.append(entry)

        for idx, entry in enumerate(visible_entries):
            card = entry["card"]
            self.cards_grid.addWidget(card, idx // 2, idx % 2)
            card.setVisible(True)

        total_visible = len(visible_entries)
        enabled_visible = sum(1 for e in visible_entries if e["enabled"])
        if hasattr(self, "stats_label"):
            self.stats_label.setText(f"显示 {total_visible} 项 · 已启用 {enabled_visible}")
        if hasattr(self, "empty_label"):
            self.empty_label.setVisible(total_visible == 0)

    def _toggle_plugin(self, trigger: str):
        if not self.plugin_manager:
            return
        try:
            if self.plugin_manager.is_plugin_enabled(trigger):
                if self.plugin_manager.disable_plugin(trigger):
                    self._show_toast(f"已关闭能力 [{trigger}]", False)
            else:
                if self.plugin_manager.enable_plugin(trigger):
                    self._show_toast(f"已打开能力 [{trigger}]", True)
            self._refresh_plugins()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"操作失败: {str(e)}")

    def _show_toast(self, message, is_success):
        """简单的系统消息反馈"""
        if self.main_app:
            self.main_app.append("system", message)

    def _edit_plugin(self, trigger: str):
        if not self.plugin_manager:
            return
        try:
            dialog = PluginEditorDialog(
                parent=self, plugin_manager=self.plugin_manager, trigger=trigger
            )
            dialog.exec()
            self._refresh_plugins()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "错误", f"打开编辑对话框失败: {str(e)}"
            )

    def _import_local_plugin(self):
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择插件文件夹")
        if not dir_path:
            return

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
            QtWidgets.QMessageBox.information(
                self, "成功", f"插件 {folder_name} 已导入！\n请点击刷新列表。"
            )

            # 自动刷新并重载
            if self.plugin_manager:
                self.plugin_manager.load_plugins()
                self._refresh_plugins()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"导入失败: {e}")


class TagChipEditor(QtWidgets.QWidget):
    """胶囊标签编辑器：用于触发词（aliases）和普通字符串列表，告别手动回车换行。"""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        placeholder: str = "输入内容后按回车或点击 + 添加（支持逗号批量分隔）...",
        empty_text: str = "暂无项目，在下方输入后按回车添加",
        on_change=None,
    ):
        super().__init__(parent)
        self._items: list[str] = []
        self._empty_text = empty_text
        self._on_change = on_change

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.chips_box = QtWidgets.QFrame(self)
        self.chips_box.setObjectName("tagChipBox")
        chips_box_layout = QtWidgets.QVBoxLayout(self.chips_box)
        chips_box_layout.setContentsMargins(8, 6, 8, 6)
        chips_box_layout.setSpacing(4)

        self.empty_label = QtWidgets.QLabel(self._empty_text, self.chips_box)
        self.empty_label.setStyleSheet("color: #94A3B8; font-size: 11px; padding: 2px 0;")
        chips_box_layout.addWidget(self.empty_label)

        self.flow_widget = FlowWidget(self.chips_box, margin=0, h_spacing=6, v_spacing=6)
        chips_box_layout.addWidget(self.flow_widget)
        root.addWidget(self.chips_box)

        add_row = QtWidgets.QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(6)

        self.input_edit = QtWidgets.QLineEdit(self)
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setFixedHeight(28)
        self.input_edit.installEventFilter(self)

        self.add_btn = QtWidgets.QPushButton("+ 添加", self)
        self.add_btn.setObjectName("path_btn")
        self.add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.add_btn.setAutoDefault(False)
        self.add_btn.setDefault(False)
        self.add_btn.clicked.connect(self._commit_input)

        add_row.addWidget(self.input_edit, 1)
        add_row.addWidget(self.add_btn)
        root.addLayout(add_row)

        self._rebuild_chips()

    def eventFilter(self, watched, event):
        if (
            watched is self.input_edit
            and event.type() == QtCore.QEvent.Type.KeyPress
            and event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter)
        ):
            self._commit_input()
            return True
        return super().eventFilter(watched, event)

    def _commit_input(self):
        raw = self.input_edit.text().strip()
        if not raw:
            return
        parts = [p.strip() for p in re.split(r"[,，\n;；]+", raw) if p.strip()]
        added = False
        for part in parts:
            if part not in self._items:
                self._items.append(part)
                added = True
        self.input_edit.clear()
        if added:
            self._rebuild_chips()
            if callable(self._on_change):
                self._on_change(len(self._items))

    def _remove_item(self, text: str):
        if text in self._items:
            self._items.remove(text)
            self._rebuild_chips()
            if callable(self._on_change):
                self._on_change(len(self._items))

    def _rebuild_chips(self):
        fl = self.flow_widget.flow_layout
        while fl.count():
            item = fl.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.hide()
                w.deleteLater()

        has_items = len(self._items) > 0
        self.empty_label.setVisible(not has_items)
        self.flow_widget.setVisible(has_items)

        for token in self._items:
            chip = QtWidgets.QFrame(self.flow_widget)
            chip.setObjectName("tagChip")
            chip_layout = QtWidgets.QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 2, 4, 2)
            chip_layout.setSpacing(4)

            lbl = QtWidgets.QLabel(token, chip)
            lbl.setStyleSheet(
                "font-size: 11px; font-weight: 600; color: #1E293B; background: transparent;"
            )
            rm_btn = QtWidgets.QPushButton("×", chip)
            rm_btn.setObjectName("tagChipRemoveBtn")
            rm_btn.setFixedSize(16, 16)
            rm_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            rm_btn.setAutoDefault(False)
            rm_btn.setDefault(False)
            rm_btn.setToolTip(f"移除「{token}」")
            rm_btn.clicked.connect(functools.partial(self._remove_item, token))

            chip_layout.addWidget(lbl)
            chip_layout.addWidget(rm_btn)
            self.flow_widget.addWidget(chip)

        self.flow_widget.updateGeometry()
        self.chips_box.updateGeometry()

    def set_items(self, items: list[str]):
        cleaned: list[str] = []
        for x in items or []:
            s = str(x or "").strip()
            if s and s not in cleaned:
                cleaned.append(s)
        self._items = cleaned
        self.input_edit.clear()
        self._rebuild_chips()
        if callable(self._on_change):
            self._on_change(len(self._items))

    def items(self) -> list[str]:
        if self.input_edit.text().strip():
            self._commit_input()
        return list(self._items)

    def setPlainText(self, text: str):
        raw = str(text or "")
        lines = [p.strip() for p in re.split(r"[\n]+", raw) if p.strip()]
        self.set_items(lines)

    def toPlainText(self) -> str:
        return "\n".join(self.items())


class PluginEditorDialog(QtWidgets.QDialog):
    _geometry_settings = QtCore.QSettings("Live2D-LLM", "PluginEditorDialog")

    def __init__(self, parent=None, plugin_manager=None, trigger=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.trigger = trigger
        self.original_config = None
        self.config_fields = {}
        self.config_schema = {}
        self.config_schema_fields = {}
        self._initial_geometry_applied = False
        self._restored_geometry = False

        self.setWindowTitle(f"配置能力 - {trigger}")
        self.resize(680, 580)
        self.setMinimumSize(540, 420)

        # 统一接入全局工具弹窗样式 + 小卡片工作台风格
        self.setStyleSheet(
            get_tool_dialog_styles()
            + """
            QDialog {
                background-color: #F1F5F9;
            }
            QLabel {
                background: transparent;
            }
            QCheckBox {
                background: transparent;
            }
            QFrame#editorHeaderCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#editorSectionCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#permSubCard {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QFrame#permSubCard QLabel, QFrame#permSubCard QCheckBox {
                background: transparent;
            }
            QFrame#settingMiniCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 9px;
            }
            QFrame#settingMiniCard:hover {
                border-color: #CBD5E1;
            }
            QFrame#settingMiniCard QLabel, QFrame#settingMiniCard QCheckBox {
                background: transparent;
            }
            QFrame#tagChipBox {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QFrame#tagChip {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 11px;
            }
            QFrame#tagChip:hover {
                border-color: #93C5FD;
                background: #EFF6FF;
            }
            QPushButton#tagChipRemoveBtn {
                background: transparent;
                color: #94A3B8;
                border: none;
                border-radius: 8px;
                padding: 0px;
                min-height: 16px;
                max-height: 16px;
                min-width: 16px;
                max-width: 16px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#tagChipRemoveBtn:hover {
                background: #FEE2E2;
                color: #DC2626;
            }
            QLabel#sectionHeaderTitle {
                font-size: 13px;
                font-weight: 700;
                color: #0F172A;
            }
            QPushButton#metaCollapseBtn {
                background: transparent;
                border: none;
                text-align: left;
                padding: 2px 0px;
                font-size: 12px;
                font-weight: 700;
                color: #334155;
            }
            QPushButton#metaCollapseBtn:hover {
                color: #2563EB;
            }
            QPushButton#path_btn {
                padding: 4px 10px;
                font-size: 11px;
                min-height: 26px;
                max-height: 28px;
                border-radius: 6px;
            }
            QPushButton#dangerSmallBtn {
                background-color: #FEF2F2;
                color: #DC2626;
                border: 1px solid #FECACA;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton#dangerSmallBtn:hover {
                background-color: #DC2626;
                color: #FFFFFF;
            }
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # --- 1. 顶部身份摘要小卡片 ---
        header_card = QtWidgets.QFrame(self)
        header_card.setObjectName("editorHeaderCard")
        header_layout = QtWidgets.QHBoxLayout(header_card)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(10)

        header_left = QtWidgets.QVBoxLayout()
        header_left.setContentsMargins(0, 0, 0, 0)
        header_left.setSpacing(3)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self.header_name_label = QtWidgets.QLabel(str(trigger or "插件"), header_card)
        self.header_name_label.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #0F172A;"
        )
        title_row.addWidget(self.header_name_label)

        group_key = capability_group(str(trigger or ""))
        self.header_group_badge = QtWidgets.QLabel(
            capability_group_label(group_key), header_card
        )
        self.header_group_badge.setStyleSheet(
            PluginManagerDialog._group_badge_style(group_key)
        )
        self.header_group_badge.setFixedHeight(18)
        title_row.addWidget(self.header_group_badge)

        self.header_trigger_badge = QtWidgets.QLabel(f"⚡ {trigger}", header_card)
        self.header_trigger_badge.setStyleSheet(
            "background: #F1F5F9; color: #475569; border: 1px solid #E2E8F0;"
            "padding: 1px 7px; border-radius: 5px; font-size: 10px; font-weight: 600;"
            "font-family: 'Cascadia Mono', 'Consolas', monospace;"
        )
        self.header_trigger_badge.setFixedHeight(18)
        title_row.addWidget(self.header_trigger_badge)
        title_row.addStretch(1)
        header_left.addLayout(title_row)

        self.header_desc_label = QtWidgets.QLabel("", header_card)
        self.header_desc_label.setStyleSheet("color: #64748B; font-size: 11px;")
        self.header_desc_label.setWordWrap(True)
        header_left.addWidget(self.header_desc_label)
        header_layout.addLayout(header_left, 1)

        if str(self.trigger or "").strip() == "knowledge_base":
            ingest_btn = QtWidgets.QPushButton("📚 保存并学习", header_card)
            ingest_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            ingest_btn.setAutoDefault(False)
            ingest_btn.clicked.connect(self._save_and_run_knowledge_ingest)
            header_layout.addWidget(ingest_btn)
        elif str(self.trigger or "").strip() == "search_web":
            check_btn = QtWidgets.QPushButton("🌐 检测搜索接口", header_card)
            check_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            check_btn.setAutoDefault(False)
            check_btn.clicked.connect(self._run_search_endpoint_check)
            header_layout.addWidget(check_btn)

        layout.addWidget(header_card)

        # 保留隐藏的 tab_widget 属性以兼容外部引用/测试，实际采用一屏流式小卡片
        self.tab_widget = QtWidgets.QTabWidget(self)
        self.tab_widget.setVisible(False)
        self.settings_tab = QtWidgets.QWidget()
        self.basic_tab = QtWidgets.QWidget()
        self.tab_widget.addTab(self.settings_tab, "⚙️ 参数与权限")
        self.tab_widget.addTab(self.basic_tab, "🔧 触发词与元数据")

        # --- 2. 单页流式小卡片滚动区 ---
        self.settings_scroll = QtWidgets.QScrollArea(self)
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.settings_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        layout.addWidget(self.settings_scroll, 1)

        self._setup_card_stream()

        # --- 3. 底部按钮区 ---
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        reset_btn = QtWidgets.QPushButton("恢复默认", self)
        reset_btn.setObjectName("reset_btn")
        reset_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        reset_btn.setAutoDefault(False)
        reset_btn.setDefault(False)
        reset_btn.clicked.connect(self._reset_config)
        btn_layout.addWidget(reset_btn)

        btn_layout.addStretch(1)

        cancel_btn = QtWidgets.QPushButton("取消", self)
        cancel_btn.setObjectName("cancel_btn")
        cancel_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QtWidgets.QPushButton("保存配置", self)
        save_btn.setObjectName("primaryAction")
        save_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        save_btn.setAutoDefault(False)
        save_btn.setDefault(False)
        save_btn.clicked.connect(self._save_config)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        # 加载配置
        self._load_config()

    def showEvent(self, arg__1):
        super().showEvent(arg__1)
        self._reposition_within_screen()

    def closeEvent(self, arg__1):
        self._save_window_geometry()
        super().closeEvent(arg__1)

    def reject(self):
        self._save_window_geometry()
        super().reject()

    def _settings_key(self) -> str:
        trigger = str(self.trigger or "default").strip() or "default"
        return f"geometry/{trigger}"

    def _save_window_geometry(self):
        try:
            self._geometry_settings.setValue(self._settings_key(), self.saveGeometry())
        except Exception:
            pass

    def _restore_saved_geometry(self) -> bool:
        if self._restored_geometry:
            return False
        self._restored_geometry = True
        try:
            raw = self._geometry_settings.value(self._settings_key())
            if raw is None:
                return False
            if isinstance(raw, QtCore.QByteArray):
                return bool(self.restoreGeometry(raw))
            if isinstance(raw, (bytes, bytearray)):
                return bool(self.restoreGeometry(QtCore.QByteArray(raw)))
        except Exception:
            return False
        return False

    def _apply_initial_geometry(self, available: QtCore.QRect):
        if self._initial_geometry_applied:
            return

        if self._restore_saved_geometry():
            self._initial_geometry_applied = True
            return

        preferred_width = min(max(680, self.width()), int(available.width() * 0.58))
        preferred_height = min(max(580, self.height()), int(available.height() * 0.78))

        target_width = max(540, min(preferred_width, available.width()))
        target_height = max(420, min(preferred_height, available.height()))

        self.resize(target_width, target_height)
        self._initial_geometry_applied = True

    def _reposition_within_screen(self):
        screen = None
        parent = self.parentWidget()
        if parent is not None and parent.windowHandle() is not None:
            screen = parent.windowHandle().screen()
        if screen is None:
            screen = QtGui.QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        self._apply_initial_geometry(available)
        frame = self.frameGeometry()

        target_width = min(max(self.minimumWidth(), self.width()), available.width())
        target_height = min(
            max(self.minimumHeight(), self.height()), available.height()
        )
        if target_width != self.width() or target_height != self.height():
            self.resize(target_width, target_height)
            frame = self.frameGeometry()

        if parent is not None:
            parent_frame = parent.frameGeometry()
            target_x = parent_frame.center().x() - frame.width() // 2
            target_y = parent_frame.center().y() - frame.height() // 2
        else:
            target_x = available.center().x() - frame.width() // 2
            target_y = available.center().y() - frame.height() // 2

        min_x = available.left()
        max_x = available.right() - frame.width() + 1
        min_y = available.top()
        max_y = available.bottom() - frame.height() + 1

        if max_x < min_x:
            target_x = min_x
        else:
            target_x = max(min_x, min(target_x, max_x))
        if max_y < min_y:
            target_y = min_y
        else:
            target_y = max(min_y, min(target_y, max_y))

        self.move(target_x, target_y)

    def _setup_card_stream(self):
        """构建一屏流式小卡片布局：触发词胶囊卡 + 权限小卡 + 参数小卡片网格 + 折叠基本信息卡"""
        page_body = QtWidgets.QWidget(self.settings_scroll)
        page_layout = QtWidgets.QVBoxLayout(page_body)
        page_layout.setContentsMargins(2, 2, 4, 4)
        page_layout.setSpacing(10)

        # --- 卡片 1：🎯 快捷触发词（胶囊标签流，告别手动回车换行）---
        aliases_card = QtWidgets.QFrame(page_body)
        aliases_card.setObjectName("editorSectionCard")
        aliases_layout = QtWidgets.QVBoxLayout(aliases_card)
        aliases_layout.setContentsMargins(12, 10, 12, 10)
        aliases_layout.setSpacing(6)

        aliases_head = QtWidgets.QHBoxLayout()
        aliases_head.setContentsMargins(0, 0, 0, 0)
        aliases_head.setSpacing(8)
        aliases_title = QtWidgets.QLabel("🎯 快捷触发词", aliases_card)
        aliases_title.setObjectName("sectionHeaderTitle")
        aliases_head.addWidget(aliases_title)

        self.alias_count_badge = QtWidgets.QLabel("0 个", aliases_card)
        self.alias_count_badge.setStyleSheet(
            "background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE;"
            "padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 600;"
        )
        aliases_head.addWidget(self.alias_count_badge)
        aliases_head.addStretch(1)

        aliases_hint = QtWidgets.QLabel("命中任一词即可唤醒此能力", aliases_card)
        aliases_hint.setStyleSheet("color: #94A3B8; font-size: 11px;")
        aliases_head.addWidget(aliases_hint)
        aliases_layout.addLayout(aliases_head)

        self.aliases_input = TagChipEditor(
            aliases_card,
            placeholder="输入新触发词（如 /日报、查天气），按回车或点 + 添加，支持逗号分隔多个…",
            empty_text="暂无额外触发别名（仅使用主触发词），可在下方输入添加",
            on_change=self._update_alias_count_badge,
        )
        aliases_layout.addWidget(self.aliases_input)
        page_layout.addWidget(aliases_card)

        # --- 卡片 2：🛡️ 触发范围与超时（双列内嵌小卡片）---
        access_card = QtWidgets.QFrame(page_body)
        access_card.setObjectName("editorSectionCard")
        access_layout = QtWidgets.QVBoxLayout(access_card)
        access_layout.setContentsMargins(12, 10, 12, 10)
        access_layout.setSpacing(8)

        access_head = QtWidgets.QHBoxLayout()
        access_head.setContentsMargins(0, 0, 0, 0)
        access_title = QtWidgets.QLabel("🛡️ 触发范围与超时", access_card)
        access_title.setObjectName("sectionHeaderTitle")
        access_head.addWidget(access_title)
        access_head.addStretch(1)

        timeout_lbl = QtWidgets.QLabel("超时限制:", access_card)
        timeout_lbl.setStyleSheet("color: #475569; font-size: 11px; font-weight: 600;")
        self.timeout_input = QtWidgets.QSpinBox(access_card)
        self.timeout_input.setRange(1, 300)
        self.timeout_input.setSuffix(" 秒")
        self.timeout_input.setFixedWidth(82)
        self.timeout_input.setFixedHeight(26)
        access_head.addWidget(timeout_lbl)
        access_head.addWidget(self.timeout_input)
        access_layout.addLayout(access_head)

        perm_cols = QtWidgets.QHBoxLayout()
        perm_cols.setContentsMargins(0, 0, 0, 0)
        perm_cols.setSpacing(8)

        # 左小卡：桌面本地
        local_sub = QtWidgets.QFrame(access_card)
        local_sub.setObjectName("permSubCard")
        local_sub_layout = QtWidgets.QVBoxLayout(local_sub)
        local_sub_layout.setContentsMargins(10, 8, 10, 8)
        local_sub_layout.setSpacing(3)
        self.allow_local_checkbox = QtWidgets.QCheckBox("💻 允许桌面本地触发", local_sub)
        self.allow_local_checkbox.setStyleSheet("font-weight: 600; font-size: 12px; color: #1E293B; background: transparent;")
        local_desc = QtWidgets.QLabel("麦克风语音、桌面聊天框与屏幕插话", local_sub)
        local_desc.setStyleSheet("color: #64748B; font-size: 11px; padding-left: 24px; background: transparent;")
        local_sub_layout.addWidget(self.allow_local_checkbox)
        local_sub_layout.addWidget(local_desc)
        perm_cols.addWidget(local_sub, 4)

        # 右小卡：QQ 侧
        qq_sub = QtWidgets.QFrame(access_card)
        qq_sub.setObjectName("permSubCard")
        qq_sub_layout = QtWidgets.QVBoxLayout(qq_sub)
        qq_sub_layout.setContentsMargins(10, 8, 10, 8)
        qq_sub_layout.setSpacing(4)
        self.allow_remote_qq_checkbox = QtWidgets.QCheckBox("🐧 允许 QQ 消息触发", qq_sub)
        self.allow_remote_qq_checkbox.setStyleSheet("font-weight: 600; font-size: 12px; color: #1E293B; background: transparent;")
        qq_sub_layout.addWidget(self.allow_remote_qq_checkbox)

        qq_sub_row = QtWidgets.QHBoxLayout()
        qq_sub_row.setContentsMargins(24, 0, 0, 0)
        qq_sub_row.setSpacing(10)
        self.allow_qq_owner_checkbox = QtWidgets.QCheckBox("主人", qq_sub)
        self.allow_qq_others_checkbox = QtWidgets.QCheckBox("其他联系人", qq_sub)
        self.allow_group_without_at_checkbox = QtWidgets.QCheckBox("群聊免 @", qq_sub)
        for cb in (
            self.allow_qq_owner_checkbox,
            self.allow_qq_others_checkbox,
            self.allow_group_without_at_checkbox,
        ):
            cb.setStyleSheet("font-size: 11px; color: #334155; background: transparent;")
            qq_sub_row.addWidget(cb)
        qq_sub_row.addStretch(1)
        qq_sub_layout.addLayout(qq_sub_row)
        perm_cols.addWidget(qq_sub, 6)

        access_layout.addLayout(perm_cols)
        self.allow_remote_qq_checkbox.toggled.connect(self._sync_access_control_inputs)
        page_layout.addWidget(access_card)

        # --- 区域 3：⚙️ 功能参数（双列/自适应小卡片网格，无参数时自动隐藏）---
        self.custom_settings_card = QtWidgets.QWidget(page_body)
        custom_sec_layout = QtWidgets.QVBoxLayout(self.custom_settings_card)
        custom_sec_layout.setContentsMargins(0, 2, 0, 0)
        custom_sec_layout.setSpacing(6)

        param_head = QtWidgets.QHBoxLayout()
        param_head.setContentsMargins(4, 0, 4, 0)
        param_head.setSpacing(8)
        param_title = QtWidgets.QLabel("⚙️ 功能参数", self.custom_settings_card)
        param_title.setObjectName("sectionHeaderTitle")
        param_head.addWidget(param_title)

        self.param_count_badge = QtWidgets.QLabel("", self.custom_settings_card)
        self.param_count_badge.setStyleSheet(
            "background: #F1F5F9; color: #475569; border: 1px solid #E2E8F0;"
            "padding: 1px 6px; border-radius: 8px; font-size: 10px; font-weight: 600;"
        )
        param_head.addWidget(self.param_count_badge)
        param_head.addStretch(1)
        custom_sec_layout.addLayout(param_head)

        self.settings_container = QtWidgets.QWidget(self.custom_settings_card)
        self.settings_layout = QtWidgets.QVBoxLayout(self.settings_container)
        self.settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_layout.setSpacing(6)

        self.settings_grid = QtWidgets.QGridLayout()
        self.settings_grid.setContentsMargins(0, 0, 0, 0)
        self.settings_grid.setHorizontalSpacing(8)
        self.settings_grid.setVerticalSpacing(8)
        self.settings_grid.setColumnStretch(0, 1)
        self.settings_grid.setColumnStretch(1, 1)
        self.settings_layout.addLayout(self.settings_grid)

        self.hidden_hint_label = QtWidgets.QLabel("", self.settings_container)
        self.hidden_hint_label.setWordWrap(True)
        self.hidden_hint_label.setStyleSheet("color: #64748B; font-size: 11px; padding: 2px 4px;")
        self.hidden_hint_label.setVisible(False)
        self.settings_layout.addWidget(self.hidden_hint_label)

        custom_sec_layout.addWidget(self.settings_container)
        page_layout.addWidget(self.custom_settings_card)

        # --- 卡片 4：🔧 基本信息与底层协议（默认折叠，点击展开）---
        meta_card = QtWidgets.QFrame(page_body)
        meta_card.setObjectName("editorSectionCard")
        meta_layout = QtWidgets.QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(12, 8, 12, 8)
        meta_layout.setSpacing(8)

        self.meta_toggle_btn = QtWidgets.QPushButton(
            "▸  🔧 名称、功能描述与底层标识（低频项，点击展开）", meta_card
        )
        self.meta_toggle_btn.setObjectName("metaCollapseBtn")
        self.meta_toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.meta_toggle_btn.setAutoDefault(False)
        self.meta_toggle_btn.setDefault(False)
        self.meta_toggle_btn.clicked.connect(self._toggle_meta_card)
        meta_layout.addWidget(self.meta_toggle_btn)

        self.meta_body = QtWidgets.QWidget(meta_card)
        meta_body_layout = QtWidgets.QVBoxLayout(self.meta_body)
        meta_body_layout.setContentsMargins(0, 4, 0, 2)
        meta_body_layout.setSpacing(8)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(10)
        name_col = QtWidgets.QVBoxLayout()
        name_col.setSpacing(2)
        name_lbl = QtWidgets.QLabel("显示名称", self.meta_body)
        name_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569;")
        self.name_input = QtWidgets.QLineEdit(self.meta_body)
        self.name_input.setFixedHeight(28)
        self.name_input.textChanged.connect(
            lambda t: self.header_name_label.setText(t.strip() or str(self.trigger or "插件"))
        )
        name_col.addWidget(name_lbl)
        name_col.addWidget(self.name_input)

        ex_col = QtWidgets.QVBoxLayout()
        ex_col.setSpacing(2)
        ex_lbl = QtWidgets.QLabel("示例参数", self.meta_body)
        ex_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569;")
        self.example_input = QtWidgets.QLineEdit(self.meta_body)
        self.example_input.setPlaceholderText("示例调用参数")
        self.example_input.setFixedHeight(28)
        ex_col.addWidget(ex_lbl)
        ex_col.addWidget(self.example_input)

        row1.addLayout(name_col, 1)
        row1.addLayout(ex_col, 1)
        meta_body_layout.addLayout(row1)

        desc_lbl = QtWidgets.QLabel("功能描述（供界面展示与大模型理解何时调用）", self.meta_body)
        desc_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569;")
        self.desc_input = QtWidgets.QTextEdit(self.meta_body)
        self.desc_input.setMaximumHeight(54)
        self.desc_input.setPlaceholderText("供界面展示及大模型理解用途的功能描述")
        meta_body_layout.addWidget(desc_lbl)
        meta_body_layout.addWidget(self.desc_input)

        proto_head = QtWidgets.QHBoxLayout()
        proto_title = QtWidgets.QLabel("底层协议标识（谨慎修改）", self.meta_body)
        proto_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #64748B;")
        proto_head.addWidget(proto_title)
        proto_head.addStretch(1)

        self.unlock_meta_checkbox = QtWidgets.QCheckBox("解锁修改", self.meta_body)
        self.unlock_meta_checkbox.setStyleSheet("font-size: 11px; color: #64748B;")
        self.unlock_meta_checkbox.toggled.connect(self._on_toggle_unlock_meta)
        proto_head.addWidget(self.unlock_meta_checkbox)
        meta_body_layout.addLayout(proto_head)

        proto_row = QtWidgets.QHBoxLayout()
        proto_row.setSpacing(10)
        trig_col = QtWidgets.QVBoxLayout()
        trig_col.setSpacing(2)
        trig_lbl = QtWidgets.QLabel("主触发词 (Trigger)", self.meta_body)
        trig_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        self.trigger_input = QtWidgets.QLineEdit(self.meta_body)
        self.trigger_input.setFixedHeight(28)
        trig_col.addWidget(trig_lbl)
        trig_col.addWidget(self.trigger_input)

        type_col = QtWidgets.QVBoxLayout()
        type_col.setSpacing(2)
        type_lbl = QtWidgets.QLabel("执行模式 (Type)", self.meta_body)
        type_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        self.type_combo = QtWidgets.QComboBox(self.meta_body)
        self.type_combo.addItems(["react", "direct", "observe"])
        self.type_combo.setFixedHeight(28)
        type_col.addWidget(type_lbl)
        type_col.addWidget(self.type_combo)

        proto_row.addLayout(trig_col, 1)
        proto_row.addLayout(type_col, 1)
        meta_body_layout.addLayout(proto_row)

        self.meta_body.setVisible(False)
        meta_layout.addWidget(self.meta_body)
        page_layout.addWidget(meta_card)

        self._on_toggle_unlock_meta(False)
        self._sync_access_control_inputs()
        page_layout.addStretch(1)

        self.settings_scroll.setWidget(page_body)

    def _toggle_meta_card(self):
        expanded = not self.meta_body.isVisible()
        self.meta_body.setVisible(expanded)
        arrow = "▾" if expanded else "▸"
        hint = "点击收起" if expanded else "低频项，点击展开"
        self.meta_toggle_btn.setText(f"{arrow}  🔧 名称、功能描述与底层标识（{hint}）")

    def _update_alias_count_badge(self, count: int):
        if hasattr(self, "alias_count_badge"):
            self.alias_count_badge.setText(f"{count} 个")

    def _on_toggle_unlock_meta(self, unlocked: bool):
        unlocked = bool(unlocked)
        if hasattr(self, "trigger_input"):
            self.trigger_input.setReadOnly(not unlocked)
        if hasattr(self, "type_combo"):
            self.type_combo.setEnabled(unlocked)

    def _normalize_access_control(self, access_control):
        if self.plugin_manager and hasattr(
            self.plugin_manager, "_normalize_access_control"
        ):
            return self.plugin_manager._normalize_access_control(access_control)
        base = {
            "allow_local": True,
            "allow_remote_qq": True,
            "allow_qq_owner": True,
            "allow_qq_others": False,
            "allow_group_without_at": False,
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
        self.allow_group_without_at_checkbox.setEnabled(qq_enabled)

    def _load_config(self):
        """加载插件配置"""
        if not self.plugin_manager or not self.trigger:
            return

        try:
            config = self.plugin_manager.get_plugin_config(self.trigger)
            if not config:
                return

            self.original_config = config.copy()
            self.config_schema = {}
            self.config_schema_fields = {}
            if hasattr(self.plugin_manager, "get_plugin_config_schema"):
                schema = self.plugin_manager.get_plugin_config_schema(self.trigger)
                if isinstance(schema, dict):
                    self.config_schema = schema
                    fields = schema.get("fields") or []
                    self.config_schema_fields = {
                        str(item.get("name") or ""): item
                        for item in fields
                        if isinstance(item, dict) and str(item.get("name") or "")
                    }

            name_text = str(config.get("name") or self.trigger or "")
            desc_text = str(config.get("description") or "")
            self.name_input.setText(name_text)
            self.trigger_input.setText(config.get("trigger", ""))
            self.type_combo.setCurrentText(config.get("type", "react"))

            if hasattr(self, "header_name_label"):
                self.header_name_label.setText(name_text)
            if hasattr(self, "header_desc_label"):
                self.header_desc_label.setText(desc_text or "暂无能力说明")
                self.header_desc_label.setVisible(bool(desc_text))

            aliases = config.get("aliases", [])
            if isinstance(aliases, list):
                self.aliases_input.set_items(aliases)
            else:
                self.aliases_input.set_items([])

            self.desc_input.setPlainText(desc_text)
            self.example_input.setText(config.get("example_arg", ""))
            self.timeout_input.setValue(config.get("timeout_sec", 6))

            access_control = self._normalize_access_control(
                config.get("access_control")
            )
            self.allow_local_checkbox.setChecked(
                access_control.get("allow_local", True)
            )
            self.allow_remote_qq_checkbox.setChecked(
                access_control.get("allow_remote_qq", True)
            )
            self.allow_qq_owner_checkbox.setChecked(
                access_control.get("allow_qq_owner", True)
            )
            self.allow_qq_others_checkbox.setChecked(
                access_control.get("allow_qq_others", False)
            )
            self.allow_group_without_at_checkbox.setChecked(
                access_control.get("allow_group_without_at", False)
            )
            self._sync_access_control_inputs()

            # 加载自定义配置
            self._load_custom_settings(config)

        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"加载配置失败: {str(e)}")

    def _load_custom_settings(self, config):
        """加载自定义配置项：每个配置项独立渲染为精致小卡片，短开关/数字自动排成双列网格"""
        self.config_fields.clear()
        while self.settings_grid.count():
            item = self.settings_grid.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.hide()
                w.deleteLater()

        settings = config.get("settings", {})
        if not settings:
            self.custom_settings_card.setVisible(False)
            return

        hidden_count = 0
        visible_count = 0
        grid_row = 0
        grid_col = 0

        for key, setting_info in settings.items():
            schema_field = self.config_schema_fields.get(str(key), {})
            if setting_ui_hidden(setting_info) or setting_ui_hidden(schema_field):
                hidden_count += 1
                continue

            if isinstance(setting_info, dict) and "type" in setting_info:
                setting_type = setting_info.get("type", "string")
                label = setting_info.get("label", key)
                description = setting_info.get("description", "")
                value = setting_info.get("default")
                min_val = setting_info.get("min", 0)
                max_val = setting_info.get("max", 10000)
                choices = setting_info.get("choices") or setting_info.get("options") or []
            else:
                setting_type = self._infer_type(setting_info)
                label = key
                description = ""
                value = setting_info
                min_val = 0
                max_val = 10000
                choices = []

            if schema_field:
                setting_type = self._setting_type_from_schema(schema_field, setting_type)
                description = description or str(schema_field.get("description") or "")
                choices = choices or schema_field.get("choices") or []
                if not isinstance(setting_info, dict):
                    value = schema_field.get("default", value)

            if str(setting_type).strip().lower() in {"link", "url"}:
                value = webui_url_from_settings(settings)

            choices_source = ""
            if isinstance(setting_info, dict):
                choices_source = str(
                    setting_info.get("choices_source")
                    or setting_info.get("source")
                    or ""
                ).strip().lower()
            if not choices_source and schema_field:
                choices_source = str(
                    schema_field.get("choices_source") or schema_field.get("source") or ""
                ).strip().lower()
            if choices_source in {"characters", "character", "character_names"}:
                setting_type = "choice"
                dynamic_choices = self._character_name_choices()
                if dynamic_choices:
                    choices = dynamic_choices
                current = str(value or "").strip()
                if current and current not in choices:
                    choices = [current] + list(choices)

            validation_error = self._validate_setting_default(
                key, setting_type, value, choices
            )

            widget = self._create_setting_widget(
                key, setting_info, setting_type, value, min_val, max_val, choices
            )
            if not widget:
                continue

            visible_count += 1
            field_type, _ = self.config_fields.get(key, (setting_type, None))
            is_block_widget = field_type in {
                "list",
                "path_list",
                "app_list",
                "model_queue",
            } or isinstance(widget, QtWidgets.QTextEdit)

            is_half_card = (
                not is_block_widget
                and field_type in {"boolean", "bool", "number"}
                and len(str(description or "")) <= 32
                and not validation_error
            )

            mini_card = QtWidgets.QFrame(self.settings_container)
            mini_card.setObjectName("settingMiniCard")

            if is_block_widget:
                card_layout = QtWidgets.QVBoxLayout(mini_card)
                card_layout.setContentsMargins(12, 9, 12, 9)
                card_layout.setSpacing(5)

                lbl_title = QtWidgets.QLabel(str(label), mini_card)
                lbl_title.setStyleSheet(
                    "font-weight: 700; font-size: 12px; color: #1E293B; background: transparent;"
                )
                card_layout.addWidget(lbl_title)

                if description:
                    desc_label = QtWidgets.QLabel(str(description), mini_card)
                    desc_label.setStyleSheet("color: #64748B; font-size: 11px; background: transparent;")
                    desc_label.setWordWrap(True)
                    card_layout.addWidget(desc_label)

                if validation_error:
                    warn_label = QtWidgets.QLabel(f"格式提醒：{validation_error}", mini_card)
                    warn_label.setStyleSheet("color: #B45309; font-size: 11px; background: transparent;")
                    warn_label.setWordWrap(True)
                    card_layout.addWidget(warn_label)

                card_layout.addWidget(widget)
            else:
                card_layout = QtWidgets.QHBoxLayout(mini_card)
                card_layout.setContentsMargins(12, 8, 12, 8)
                card_layout.setSpacing(10)

                left_col = QtWidgets.QVBoxLayout()
                left_col.setContentsMargins(0, 0, 0, 0)
                left_col.setSpacing(2)

                lbl_title = QtWidgets.QLabel(str(label), mini_card)
                lbl_title.setStyleSheet(
                    "font-weight: 700; font-size: 12px; color: #1E293B; background: transparent;"
                )
                left_col.addWidget(lbl_title)

                if description:
                    desc_label = QtWidgets.QLabel(str(description), mini_card)
                    desc_label.setStyleSheet("color: #64748B; font-size: 11px; background: transparent;")
                    desc_label.setWordWrap(True)
                    left_col.addWidget(desc_label)

                if setting_type in {"secret", "password"}:
                    store_label = QtWidgets.QLabel(
                        "安全存储于本机密钥库，config.json 默认留空", mini_card
                    )
                    store_label.setStyleSheet("color: #2563EB; font-size: 10px; background: transparent;")
                    store_label.setWordWrap(True)
                    left_col.addWidget(store_label)

                if validation_error:
                    warn_label = QtWidgets.QLabel(f"格式提醒：{validation_error}", mini_card)
                    warn_label.setStyleSheet("color: #B45309; font-size: 11px; background: transparent;")
                    warn_label.setWordWrap(True)
                    left_col.addWidget(warn_label)

                card_layout.addLayout(left_col, 1)
                if field_type in {"boolean", "bool", "number", "choice"}:
                    card_layout.addWidget(
                        widget,
                        0,
                        QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    )
                else:
                    widget.setMinimumWidth(230)
                    card_layout.addWidget(
                        widget, 1, QtCore.Qt.AlignmentFlag.AlignVCenter
                    )

            if is_half_card:
                self.settings_grid.addWidget(mini_card, grid_row, grid_col)
                if grid_col == 0:
                    grid_col = 1
                else:
                    grid_col = 0
                    grid_row += 1
            else:
                if grid_col == 1:
                    grid_col = 0
                    grid_row += 1
                self.settings_grid.addWidget(mini_card, grid_row, 0, 1, 2)
                grid_row += 1

        self.custom_settings_card.setVisible(visible_count > 0 or hidden_count > 0)
        if hasattr(self, "param_count_badge"):
            self.param_count_badge.setText(f"{visible_count} 项")
            self.param_count_badge.setVisible(visible_count > 0)

        if hidden_count:
            self.hidden_hint_label.setText(
                "注：关注地区和数据源地址等内部项已收起，请用插件本机页修改，避免手改大段 JSON。"
            )
            self.hidden_hint_label.setVisible(True)
        else:
            self.hidden_hint_label.setVisible(False)

    def _validate_setting_default(self, key, setting_type, value, choices):
        key_lower = str(key or "").strip().lower()
        text_value = str(value or "").strip() if value is not None else ""

        if (
            setting_type == "choice"
            and choices
            and text_value
            and text_value not in choices
        ):
            return f"默认值 `{text_value}` 不在可选项中"

        if any(token in key_lower for token in ("base_url", "endpoint", "url")):
            if text_value and not is_plausible_url(text_value):
                return "URL/路径建议以 http://、https://、ws://、wss:// 或 / 开头"

        if any(token in key_lower for token in ("time", "schedule")):
            if text_value and key_lower.endswith("time"):
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text_value):
                    return "时间建议使用 HH:MM 24小时制"

        if "json" in key_lower:
            if text_value:
                try:
                    json.loads(text_value)
                except Exception:
                    return "JSON 格式无效"

        return ""

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
                return (
                    "path"
                    if "." not in value.split("\\")[-1].split("/")[-1]
                    else "file"
                )
            return "string"
        return "string"

    def _setting_type_from_schema(self, schema_field, fallback):
        """把自动 schema 的通用 UI 类型映射到现有编辑器控件类型。"""
        ui_type = str(schema_field.get("ui_type") or "").strip().lower()
        value_type = str(schema_field.get("type") or "").strip().lower()
        if bool(schema_field.get("secret")) or ui_type == "password":
            return "secret"
        if ui_type in {"switch", "checkbox"} or value_type in {"bool", "boolean"}:
            return "boolean"
        if ui_type in {"link", "url", "open_url"} or value_type in {"link", "url"}:
            return "link"
        if ui_type == "select" or schema_field.get("choices"):
            return "choice"
        if ui_type == "json" or value_type in {"object", "dict"}:
            return "text"
        if value_type in {"array", "list"}:
            return "list"
        if value_type in {"integer", "int", "number", "float"}:
            return "number"
        if ui_type in {"path", "file"}:
            return ui_type
        return fallback or "string"

    def _character_name_choices(self):
        """从角色库读取可选角色名，供插件配置下拉框使用。"""
        names = []
        try:
            from modules.character_manager import character_manager

            characters = character_manager.get_all_characters() or {}
            for char in characters.values():
                if not isinstance(char, dict):
                    continue
                name = str(char.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
            active = character_manager.get_active_character()
            if isinstance(active, dict):
                active_name = str(active.get("name") or "").strip()
                if active_name and active_name in names:
                    names.remove(active_name)
                    names.insert(0, active_name)
        except Exception:
            return []
        return names

    def _create_path_item(self, layout, path_value, path_inputs):
        """创建路径列表项（输入框 + 选择按钮 + 删除按钮）"""
        item_widget = QtWidgets.QWidget()
        item_layout = QtWidgets.QHBoxLayout(item_widget)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(6)

        path_input = QtWidgets.QLineEdit()
        path_input.setText(str(path_value) if path_value else "")
        path_input.setPlaceholderText("选择或输入路径...")
        path_input.setFixedHeight(28)

        select_btn = QtWidgets.QPushButton("浏览...")
        select_btn.setObjectName("path_btn")
        select_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        select_btn.setAutoDefault(False)
        select_btn.clicked.connect(lambda: self._select_directory(path_input))

        delete_btn = QtWidgets.QPushButton("删除")
        delete_btn.setObjectName("dangerSmallBtn")
        delete_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        delete_btn.setFixedHeight(26)
        delete_btn.setAutoDefault(False)
        delete_btn.clicked.connect(
            lambda: self._delete_path_item(layout, item_widget, path_input)
        )

        item_layout.addWidget(path_input, 1)
        item_layout.addWidget(select_btn)
        item_layout.addWidget(delete_btn)

        layout.insertWidget(layout.count() - 1, item_widget)
        return path_input

    def _delete_path_item(self, layout, item_widget, path_input):
        """删除路径项"""
        item_widget.deleteLater()
        if path_input in self.config_fields:
            for key, (field_type, widget) in self.config_fields.items():
                if field_type == "path_list" and isinstance(widget, list):
                    if path_input in widget:
                        widget.remove(path_input)
                        break

    def _create_app_list_item(self, layout, app_config, app_inputs):
        """创建别名+路径项（可用于程序或目录）"""
        alias = ""
        path = ""
        if app_config and "|" in str(app_config):
            parts = str(app_config).split("|", 1)
            alias = parts[0].strip()
            path = parts[1].strip() if len(parts) > 1 else ""

        item_widget = QtWidgets.QWidget()
        item_layout = QtWidgets.QHBoxLayout(item_widget)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(6)

        alias_input = QtWidgets.QLineEdit()
        alias_input.setText(alias)
        alias_input.setPlaceholderText("别名")
        alias_input.setFixedWidth(110)
        alias_input.setFixedHeight(28)

        path_input = QtWidgets.QLineEdit()
        path_input.setText(path)
        path_input.setPlaceholderText("本地路径")
        path_input.setFixedHeight(28)

        dir_btn = QtWidgets.QPushButton("目录")
        dir_btn.setObjectName("path_btn")
        dir_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        dir_btn.setAutoDefault(False)
        dir_btn.clicked.connect(lambda: self._select_directory(path_input))

        file_btn = QtWidgets.QPushButton("文件")
        file_btn.setObjectName("path_btn")
        file_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        file_btn.setAutoDefault(False)
        file_btn.clicked.connect(lambda: self._select_executable(path_input))

        delete_btn = QtWidgets.QPushButton("删除")
        delete_btn.setObjectName("dangerSmallBtn")
        delete_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        delete_btn.setFixedHeight(26)
        delete_btn.setAutoDefault(False)
        delete_btn.clicked.connect(
            lambda: self._delete_app_list_item(layout, item_widget, app_inputs)
        )

        item_layout.addWidget(alias_input)
        item_layout.addWidget(path_input, 1)
        item_layout.addWidget(dir_btn)
        item_layout.addWidget(file_btn)
        item_layout.addWidget(delete_btn)

        layout.insertWidget(layout.count() - 1, item_widget)

        app_inputs.append({"alias": alias_input, "path": path_input})
        return app_inputs[-1]

    def _delete_app_list_item(self, layout, item_widget, app_inputs):
        """删除应用列表项"""
        alias_widget = item_widget.layout().itemAt(0).widget()
        path_widget = item_widget.layout().itemAt(1).widget()

        for key, (field_type, widget) in self.config_fields.items():
            if field_type == "app_list" and isinstance(widget, list):
                for i, app_input in enumerate(widget):
                    if (
                        app_input["alias"] is alias_widget
                        and app_input["path"] is path_widget
                    ):
                        widget.pop(i)
                        break
                break

        item_widget.deleteLater()

    def _open_webui(self, url: str):
        target = str(url or "").strip()
        if not target:
            target = webui_url_from_settings(
                (self.original_config or {}).get("settings") or {}
            )
        if not target.startswith(("http://", "https://")):
            QtWidgets.QMessageBox.warning(self, "无法打开", "本机 WebUI 地址无效。")
            return
        opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl(target))
        if not opened:
            QtWidgets.QMessageBox.information(self, "本机 WebUI", target)

    def _create_setting_widget(
        self, key, setting_info, setting_type, value, min_val, max_val, choices
    ):
        """根据类型创建配置控件"""
        widget = None
        key_lower = str(key or "").strip().lower()
        is_sensitive = any(
            token in key_lower
            for token in ("api_key", "token", "secret", "password", "access_key")
        )

        if setting_type in {"link", "url"}:
            url = str(value or "").strip()
            if not url:
                url = webui_url_from_settings(
                    (self.original_config or {}).get("settings") or {}
                )
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            line_edit = QtWidgets.QLineEdit()
            line_edit.setText(url)
            line_edit.setReadOnly(True)
            line_edit.setFixedHeight(28)
            open_btn = QtWidgets.QPushButton("打开 WebUI")
            open_btn.setObjectName("path_btn")
            open_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            open_btn.setAutoDefault(False)
            open_btn.clicked.connect(
                lambda checked=False, le=line_edit: self._open_webui(le.text())
            )
            row_layout.addWidget(line_edit, 1)
            row_layout.addWidget(open_btn)
            self.config_fields[key] = ("link", line_edit)
            widget = row

        elif setting_type in {"string", "text", "secret", "password"}:
            if isinstance(value, list):
                text = "\n".join(str(v) for v in value)
            else:
                text = str(value) if value is not None else ""

            if is_sensitive or setting_type in {"secret", "password"}:
                secret_layout = QtWidgets.QHBoxLayout()
                secret_layout.setContentsMargins(0, 0, 0, 0)
                secret_layout.setSpacing(6)

                line_edit = QtWidgets.QLineEdit()
                line_edit.setText(text)
                line_edit.setPlaceholderText("请输入敏感信息...")
                line_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
                line_edit.setFixedHeight(28)

                toggle_btn = QtWidgets.QPushButton("显示")
                toggle_btn.setObjectName("path_btn")
                toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                toggle_btn.setAutoDefault(False)

                def _toggle_secret_visibility(le=line_edit, btn=toggle_btn):
                    hidden = le.echoMode() == QtWidgets.QLineEdit.EchoMode.Password
                    le.setEchoMode(
                        QtWidgets.QLineEdit.EchoMode.Normal
                        if hidden
                        else QtWidgets.QLineEdit.EchoMode.Password
                    )
                    btn.setText("隐藏" if hidden else "显示")

                toggle_btn.clicked.connect(_toggle_secret_visibility)

                secret_layout.addWidget(line_edit, 1)
                secret_layout.addWidget(toggle_btn)

                container = QtWidgets.QWidget()
                container.setLayout(secret_layout)
                self.config_fields[key] = ("secret", line_edit)
                widget = container
            elif (
                setting_type in {"string", "text"}
                and "\n" not in text
                and len(text) <= 160
                and not any(k in key_lower for k in ("prompt", "json", "template", "textarea"))
            ):
                # 无论是 string 还是 text，只要是单行短文本（URL/路径/名称/模型等），一律使用紧凑单行 QLineEdit
                line_edit = QtWidgets.QLineEdit()
                line_edit.setText(text)
                line_edit.setPlaceholderText("请输入...")
                line_edit.setFixedHeight(28)
                self.config_fields[key] = ("string", line_edit)
                widget = line_edit
            else:
                text_edit = QtWidgets.QTextEdit()
                text_edit.setMaximumHeight(68)
                text_edit.setPlaceholderText("请输入多行文本或 JSON...")
                text_edit.setPlainText(text)
                self.config_fields[key] = ("text", text_edit)
                widget = text_edit

        elif setting_type in {"number", "int", "integer"}:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setRange(min_val, max_val)
            spinbox.setValue(int(value) if value is not None else min_val)
            spinbox.setFixedHeight(28)
            spinbox.setMinimumWidth(88)
            self.config_fields[key] = ("number", spinbox)
            widget = spinbox

        elif setting_type == "boolean" or setting_type == "bool":
            checkbox = QtWidgets.QCheckBox("启用")
            checkbox.setChecked(bool(value) if value is not None else False)
            self.config_fields[key] = ("boolean", checkbox)
            widget = checkbox

        elif setting_type == "list":
            is_path_list = False
            is_app_list = False
            if isinstance(setting_info, dict) and "item_type" in setting_info:
                is_path_list = setting_info["item_type"] == "path"
                is_app_list = setting_info["item_type"] == "app_list"

            if is_path_list:
                scroll = QtWidgets.QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setMaximumHeight(160)
                scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

                container = QtWidgets.QWidget()
                list_layout = QtWidgets.QVBoxLayout(container)
                list_layout.setContentsMargins(0, 0, 0, 0)
                list_layout.setSpacing(6)

                path_inputs = []
                if isinstance(value, list):
                    for path in value:
                        if path:
                            path_inputs.append(
                                self._create_path_item(list_layout, path, path_inputs)
                            )

                add_btn = QtWidgets.QPushButton("+ 添加路径")
                add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                add_btn.setFixedHeight(28)
                add_btn.setAutoDefault(False)
                add_btn.clicked.connect(
                    lambda: path_inputs.append(
                        self._create_path_item(list_layout, "", path_inputs)
                    )
                )
                list_layout.addWidget(add_btn)

                scroll.setWidget(container)
                self.config_fields[key] = ("path_list", path_inputs)
                widget = scroll
            elif is_app_list:
                scroll = QtWidgets.QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setMaximumHeight(180)
                scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

                container = QtWidgets.QWidget()
                list_layout = QtWidgets.QVBoxLayout(container)
                list_layout.setContentsMargins(0, 0, 0, 0)
                list_layout.setSpacing(6)

                app_inputs = []
                if isinstance(value, list):
                    for app in value:
                        if app:
                            self._create_app_list_item(list_layout, app, app_inputs)

                add_btn = QtWidgets.QPushButton("+ 添加应用")
                add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                add_btn.setFixedHeight(28)
                add_btn.setAutoDefault(False)
                add_btn.clicked.connect(
                    lambda checked=False, inputs=app_inputs: self._create_app_list_item(
                        list_layout, "", inputs
                    )
                )
                list_layout.addWidget(add_btn)

                scroll.setWidget(container)
                self.config_fields[key] = ("app_list", app_inputs)
                widget = scroll
            else:
                # 普通字符串列表使用胶囊标签编辑器 TagChipEditor，告别手动回车换行
                tag_editor = TagChipEditor(
                    placeholder="输入项目后按回车或点 + 添加（支持逗号分隔）...",
                    empty_text="暂无列表项，在下方输入后按回车添加",
                )
                if isinstance(value, list):
                    tag_editor.set_items([str(v) for v in value if str(v).strip()])
                self.config_fields[key] = ("list", tag_editor)
                widget = tag_editor

        elif setting_type == "path":
            path_layout = QtWidgets.QHBoxLayout()
            path_layout.setContentsMargins(0, 0, 0, 0)
            path_layout.setSpacing(6)
            line_edit = QtWidgets.QLineEdit()
            line_edit.setText(str(value) if value is not None else "")
            line_edit.setPlaceholderText("选择或输入路径...")
            line_edit.setFixedHeight(28)

            path_btn = QtWidgets.QPushButton("浏览...")
            path_btn.setObjectName("path_btn")
            path_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            path_btn.setAutoDefault(False)
            path_btn.clicked.connect(
                lambda checked, le=line_edit: self._select_directory(le)
            )

            path_layout.addWidget(line_edit, 1)
            path_layout.addWidget(path_btn)
            self.config_fields[key] = ("path", line_edit)

            container = QtWidgets.QWidget()
            container.setLayout(path_layout)
            widget = container

        elif setting_type == "file":
            file_layout = QtWidgets.QHBoxLayout()
            file_layout.setContentsMargins(0, 0, 0, 0)
            file_layout.setSpacing(6)
            line_edit = QtWidgets.QLineEdit()
            line_edit.setText(str(value) if value is not None else "")
            line_edit.setPlaceholderText("选择或输入文件路径...")
            line_edit.setFixedHeight(28)

            file_btn = QtWidgets.QPushButton("浏览...")
            file_btn.setObjectName("path_btn")
            file_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            file_btn.clicked.connect(
                lambda checked, le=line_edit: self._select_file(le)
            )

            file_layout.addWidget(line_edit, 1)
            file_layout.addWidget(file_btn)
            self.config_fields[key] = ("file", line_edit)

            container = QtWidgets.QWidget()
            container.setLayout(file_layout)
            widget = container

        elif setting_type in {"choice", "select"}:
            combo = QtWidgets.QComboBox()
            combo.addItems(choices)
            combo.setFixedHeight(28)
            combo.setMinimumWidth(140)
            if value is not None and str(value) in choices:
                combo.setCurrentText(str(value))
            self.config_fields[key] = ("choice", combo)
            widget = combo

        elif setting_type in {"model_queue", "model_list"}:
            widget = self._create_model_queue_widget(key, setting_info, value)

        return widget

    def _create_model_queue_widget(self, key, setting_info, value):
        """有序模型选择：从模型管理按用途筛选，手动加入执行链。"""
        purpose = []
        max_items = 0
        if isinstance(setting_info, dict):
            purpose = (
                setting_info.get("purpose")
                or setting_info.get("purposes")
                or ["image_gen", "image_edit"]
            )
            max_items = max(0, int(setting_info.get("max_items") or 0))
        try:
            from modules.model_catalog import (
                format_purposes_label,
                list_model_options,
                normalize_model_selection,
                normalize_purposes,
            )
            from config import MODELS
        except Exception:
            format_purposes_label = None
            list_model_options = None
            normalize_model_selection = None
            normalize_purposes = None
            MODELS = {}

        options = []
        if callable(list_model_options):
            try:
                options = list_model_options(MODELS, purposes=purpose)
            except Exception:
                options = []
        option_map = {
            str(item.get("id")): str(item.get("label") or item.get("id"))
            for item in options
            if isinstance(item, dict) and item.get("id")
        }

        if callable(normalize_model_selection):
            selected = normalize_model_selection(value, max_items=max_items)
        else:
            selected = []

        root = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(8)

        if not option_map:
            purpose_label = "声明用途"
            if callable(format_purposes_label) and callable(normalize_purposes):
                purpose_label = format_purposes_label(normalize_purposes(purpose))
            empty = QtWidgets.QLabel(
                f"还没有可选项。请先到「模型与路由」添加模型并勾选用途「{purpose_label}」。"
            )
            empty.setWordWrap(True)
            empty.setStyleSheet("color:#B45309;")
            root_layout.addWidget(empty)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(8)

        left_box = QtWidgets.QVBoxLayout()
        lbl_pool = QtWidgets.QLabel("可选模型")
        lbl_pool.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; background: transparent;")
        left_box.addWidget(lbl_pool)
        list_pool = QtWidgets.QListWidget()
        list_pool.setMinimumHeight(160)
        list_pool.setStyleSheet(
            "QListWidget { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 4px; font-size: 11px; }"
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; color: #1E293B; }"
            "QListWidget::item:hover { background: #EFF6FF; color: #1D4ED8; }"
            "QListWidget::item:selected { background: #2563EB; color: #FFFFFF; }"
        )
        left_box.addWidget(list_pool)
        body.addLayout(left_box, 1)

        mid = QtWidgets.QVBoxLayout()
        mid.addStretch()
        btn_add = QtWidgets.QPushButton("->")
        btn_rm = QtWidgets.QPushButton("<-")
        btn_mid_style = (
            "QPushButton { background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 10px; font-size: 12px; font-weight: 700; color: #334155; }"
            "QPushButton:hover { background: #EFF6FF; border-color: #93C5FD; color: #1D4ED8; }"
            "QPushButton:pressed { background: #DBEAFE; }"
        )
        btn_add.setStyleSheet(btn_mid_style)
        btn_rm.setStyleSheet(btn_mid_style)
        mid.addWidget(btn_add)
        mid.addWidget(btn_rm)
        mid.addStretch()
        body.addLayout(mid)

        right_box = QtWidgets.QVBoxLayout()
        lbl_chain = QtWidgets.QLabel("执行链（上优先）")
        lbl_chain.setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; background: transparent;")
        right_box.addWidget(lbl_chain)
        list_chain = QtWidgets.QListWidget()
        list_chain.setMinimumHeight(160)
        list_chain.setStyleSheet(
            "QListWidget { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 4px; font-size: 11px; }"
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; color: #1E293B; }"
            "QListWidget::item:hover { background: #EFF6FF; color: #1D4ED8; }"
            "QListWidget::item:selected { background: #2563EB; color: #FFFFFF; }"
        )
        right_box.addWidget(list_chain)
        sort_row = QtWidgets.QHBoxLayout()
        btn_up = QtWidgets.QPushButton("上移")
        btn_down = QtWidgets.QPushButton("下移")
        btn_up.setStyleSheet(btn_mid_style)
        btn_down.setStyleSheet(btn_mid_style)
        sort_row.addWidget(btn_up)
        sort_row.addWidget(btn_down)
        right_box.addLayout(sort_row)
        body.addLayout(right_box, 1)
        root_layout.addLayout(body)

        state = {"selected": list(selected)}

        def _label_for(model_id: str) -> str:
            return option_map.get(model_id, model_id)

        def _refresh():
            list_chain.clear()
            for mid in state["selected"]:
                item = QtWidgets.QListWidgetItem(_label_for(mid))
                item.setData(QtCore.Qt.ItemDataRole.UserRole, mid)
                list_chain.addItem(item)
            list_pool.clear()
            for mid, label in option_map.items():
                if mid in state["selected"]:
                    continue
                item = QtWidgets.QListWidgetItem(label)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, mid)
                list_pool.addItem(item)

        def _add():
            item = list_pool.currentItem()
            if not item:
                return
            if max_items and len(state["selected"]) >= max_items:
                return
            mid = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if mid and mid not in state["selected"]:
                state["selected"].append(mid)
                _refresh()

        def _rm():
            row = list_chain.currentRow()
            if row < 0 or row >= len(state["selected"]):
                return
            state["selected"].pop(row)
            _refresh()

        def _up():
            row = list_chain.currentRow()
            if row <= 0:
                return
            state["selected"][row - 1], state["selected"][row] = (
                state["selected"][row],
                state["selected"][row - 1],
            )
            _refresh()
            list_chain.setCurrentRow(row - 1)

        def _down():
            row = list_chain.currentRow()
            if row < 0 or row >= len(state["selected"]) - 1:
                return
            state["selected"][row + 1], state["selected"][row] = (
                state["selected"][row],
                state["selected"][row + 1],
            )
            _refresh()
            list_chain.setCurrentRow(row + 1)

        btn_add.clicked.connect(_add)
        btn_rm.clicked.connect(_rm)
        btn_up.clicked.connect(_up)
        btn_down.clicked.connect(_down)
        list_pool.itemDoubleClicked.connect(lambda *_: _add())
        list_chain.itemDoubleClicked.connect(lambda *_: _rm())
        _refresh()

        self.config_fields[key] = ("model_queue", state)
        return root

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
            "可执行文件 (*.exe *.bat *.cmd *.sh);;所有文件 (*.*)",
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
            self,
            "确认重置",
            "确定要重置所有配置到原始值吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._load_config()

    def _save_config(self):
        """保存配置"""
        if not self.plugin_manager or not self.trigger:
            return

        try:
            original_config = (
                self.original_config if isinstance(self.original_config, dict) else {}
            )
            # 验证必填字段
            name = self.name_input.text().strip()
            trigger = self.trigger_input.text().strip()
            if not name or not trigger:
                QtWidgets.QMessageBox.warning(
                    self, "验证失败", "插件名称和触发词不能为空"
                )
                return

            # 构建新配置
            new_config = dict(original_config)
            new_config["name"] = name
            new_config["trigger"] = trigger
            new_config["type"] = self.type_combo.currentText()

            aliases_text = self.aliases_input.toPlainText().strip()
            aliases = [
                line.strip() for line in aliases_text.split("\n") if line.strip()
            ]
            new_config["aliases"] = aliases

            new_config["description"] = self.desc_input.toPlainText().strip()
            new_config["example_arg"] = self.example_input.text().strip()
            new_config["timeout_sec"] = self.timeout_input.value()
            new_config["access_control"] = {
                "allow_local": self.allow_local_checkbox.isChecked(),
                "allow_remote_qq": self.allow_remote_qq_checkbox.isChecked(),
                "allow_qq_owner": self.allow_qq_owner_checkbox.isChecked(),
                "allow_qq_others": self.allow_qq_others_checkbox.isChecked(),
                "allow_group_without_at": self.allow_group_without_at_checkbox.isChecked(),
            }

            # 保存自定义配置：先保留原 settings，避免隐藏项被整表覆盖丢掉。
            original_settings = original_config.get("settings")
            settings = (
                dict(original_settings) if isinstance(original_settings, dict) else {}
            )
            secret_values = {}

            def _validate_before_store(setting_key, field_type, raw_value):
                setting_meta = original_config.get("settings", {}).get(setting_key, {})
                schema_field = self.config_schema_fields.get(str(setting_key), {})
                setting_type = field_type
                if isinstance(setting_meta, dict):
                    setting_type = (
                        str(setting_meta.get("type") or field_type).strip().lower()
                    )
                    choices = setting_meta.get("choices") or setting_meta.get("options") or []
                else:
                    choices = []
                choices = choices or schema_field.get("choices") or []
                if schema_field:
                    setting_type = self._setting_type_from_schema(schema_field, setting_type)

                if setting_type in {"secret", "password"}:
                    return ""

                if (
                    setting_type == "choice"
                    and choices
                    and raw_value
                    and raw_value not in choices
                ):
                    return f"{setting_key} 取值不在可选项中"

                raw_text = raw_value if isinstance(raw_value, str) else ""
                key_lower = str(setting_key or "").strip().lower()

                if "json" in key_lower and raw_text:
                    try:
                        json.loads(raw_text)
                    except Exception:
                        return f"{setting_key} 不是合法 JSON"

                if (
                    any(token in key_lower for token in ("base_url", "endpoint", "url"))
                    and raw_text
                    and not is_plausible_url(raw_text)
                ):
                    return f"{setting_key} 应以 http://、https://、ws://、wss:// 或 / 开头"

                if key_lower.endswith("time") and raw_text:
                    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw_text):
                        return f"{setting_key} 应为 HH:MM 24小时制"

                return ""

            def _store_setting_value(setting_key, raw_value):
                original_setting = original_config.get("settings", {}).get(
                    setting_key, {}
                )
                if isinstance(original_setting, dict):
                    settings[setting_key] = {
                        **original_setting,
                        "default": raw_value,
                    }
                else:
                    settings[setting_key] = raw_value

            for key, (field_type, widget) in self.config_fields.items():
                if field_type == "list":
                    value = [
                        line.strip()
                        for line in widget.toPlainText().split("\n")
                        if line.strip()
                    ]
                    error = _validate_before_store(key, field_type, value)
                    if error:
                        QtWidgets.QMessageBox.warning(self, "验证失败", error)
                        return
                    _store_setting_value(key, value)
                elif field_type == "link":
                    continue
                elif field_type in {"bool", "boolean"}:
                    value = widget.isChecked()
                    _store_setting_value(key, value)
                elif field_type == "number":
                    value = widget.value()
                    _store_setting_value(key, value)
                elif field_type == "choice":
                    value = widget.currentText().strip()
                    error = _validate_before_store(key, field_type, value)
                    if error:
                        QtWidgets.QMessageBox.warning(self, "验证失败", error)
                        return
                    _store_setting_value(key, value)
                elif field_type == "model_queue":
                    # widget is a state dict holding selected model ids
                    value = []
                    if isinstance(widget, dict):
                        value = [
                            str(x).strip()
                            for x in (widget.get("selected") or [])
                            if str(x).strip()
                        ]
                    _store_setting_value(key, value)
                elif field_type == "secret":
                    value = widget.text().strip()
                    secret_values[key] = value
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
                    if isinstance(widget, QtWidgets.QTextEdit):
                        value = widget.toPlainText().strip()
                    else:
                        value = widget.text().strip()
                    error = _validate_before_store(key, field_type, value)
                    if error:
                        QtWidgets.QMessageBox.warning(self, "验证失败", error)
                        return
                    _store_setting_value(key, value)

            new_config["settings"] = settings

            if secret_values:
                new_config["_secret_values"] = secret_values

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

    def _save_and_run_knowledge_ingest(self):
        self._save_config()
        plugin = self.plugin_manager.plugins.get(self.trigger)
        if plugin is None or not hasattr(plugin, "gui_ingest_configured_dirs"):
            QtWidgets.QMessageBox.information(
                self, "提示", "当前插件不支持 GUI 一键学习。"
            )
            return
        brain = getattr(getattr(self.parent(), "app", None), "brain", None)
        if brain is None:
            try:
                import __main__

                brain = getattr(__main__, "app_instance", None)
                brain = getattr(brain, "brain", None)
            except Exception:
                brain = None
        if brain is None:
            QtWidgets.QMessageBox.warning(
                self, "失败", "未找到 brain 实例，无法执行 GUI 学习。"
            )
            return
        if not hasattr(brain, "import_knowledge_from_file"):
            QtWidgets.QMessageBox.warning(self, "失败", "brain 未就绪，暂时不能学习。")
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
        from pathlib import Path

        from modules.gui.dialogs.knowledge_manager import KnowledgeImportWorker

        progress = QtWidgets.QProgressDialog("准备学习知识目录…", "", 0, 100, self)
        progress.setWindowTitle("一键学习")
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setValue(0)
        progress.show()
        self._knowledge_learn_progress = progress

        def _on_progress(info: dict):
            stage = str(info.get("stage") or "").strip()
            batch = int(info.get("batch") or 0)
            batches = max(1, int(info.get("batches") or 1))
            file_index = int(info.get("file_index") or 1)
            file_count = max(1, int(info.get("file_count") or 1))
            file_name = Path(str(info.get("file_path") or "")).name
            file_base = max(0, file_index - 1) / max(1, file_count)
            file_part = (batch / batches) / max(1, file_count)
            percent = int(max(0, min(100, (file_base + file_part) * 100)))
            stage_text = {
                "prepared": "已解析文件",
                "embedding": "正在生成向量并写入",
                "batch_done": "已完成一批",
            }.get(stage, "正在学习")
            progress.setValue(percent)
            progress.setLabelText(
                f"{stage_text}: {file_name}\n文件 {file_index}/{file_count}，批次 {batch}/{batches}"
            )

        def _on_finished(result: dict):
            progress.close()
            file_count = int(result.get("file_count", 0) or 0)
            added = int(result.get("added", 0) or 0)
            skipped = int(result.get("skipped", 0) or 0)
            failed = int(result.get("failed", 0) or 0)
            details = "\n".join(list(result.get("results") or [])[:80])
            QtWidgets.QMessageBox.information(
                self,
                "学习结果",
                f"已处理 {file_count} 个文件：新增 {added} 条，跳过 {skipped} 条，失败 {failed} 个文件。\n\n{details}",
            )

        self._knowledge_learn_thread = QtCore.QThread(self)
        self._knowledge_learn_worker = KnowledgeImportWorker(brain, paths)
        self._knowledge_learn_worker.moveToThread(self._knowledge_learn_thread)
        self._knowledge_learn_thread.started.connect(self._knowledge_learn_worker.run)
        self._knowledge_learn_worker.progress.connect(_on_progress)
        self._knowledge_learn_worker.finished.connect(_on_finished)
        self._knowledge_learn_worker.finished.connect(self._knowledge_learn_thread.quit)
        self._knowledge_learn_worker.finished.connect(self._knowledge_learn_worker.deleteLater)
        self._knowledge_learn_thread.finished.connect(self._knowledge_learn_thread.deleteLater)
        self._knowledge_learn_thread.start()

    def _run_search_endpoint_check(self):
        plugin = self.plugin_manager.plugins.get(self.trigger)
        if plugin is None or not hasattr(plugin, "gui_check_endpoints"):
            QtWidgets.QMessageBox.information(self, "提示", "当前插件不支持接口检测。")
            return
        try:
            result = asyncio.run(plugin.gui_check_endpoints())
            QtWidgets.QMessageBox.information(self, "接口检测结果", str(result))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"接口检测失败: {e}")
