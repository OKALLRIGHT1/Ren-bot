import asyncio
import functools
import json
import os
import re
import shutil

from pathlib import Path
from PySide6 import QtWidgets, QtCore, QtGui
from modules.gui.styles import get_tool_dialog_styles, get_ui_palette, hex_to_rgba
from modules.gui.utils import FlowWidget, resolve_icon
from modules.gui.dialogs.plugin_market_panel import (
    PluginMarketPanel,
    _ElidedLabel,
    _badge_style,
    _group_icon_spec,
    _is_dark_palette,
)
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


class _ToggleSwitch(QtWidgets.QWidget):
    """Small pill switch. Click flips it and emits toggled(bool)."""

    toggled = QtCore.Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self.setFixedSize(46, 26)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        value = bool(value)
        if value == self._checked:
            self.update()
            return
        self._checked = value
        self.update()
        self.toggled.emit(self._checked)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        palette = get_ui_palette()
        accent = QtGui.QColor(palette.get("accent", "#0A84FF"))
        accent_hover = QtGui.QColor(palette.get("accent_hover", "#0071E3"))
        border = QtGui.QColor(palette.get("border", "#D1D5DB"))
        soft = QtGui.QColor(palette.get("bg_soft", "#F4F5F7"))
        enabled = self.isEnabled()
        track = QtCore.QRectF(1, 3, 44, 20)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        if self._checked:
            gradient = QtGui.QLinearGradient(track.topLeft(), track.bottomRight())
            top = QtGui.QColor(accent).lighter(118)
            bottom = QtGui.QColor(accent_hover)
            if not enabled:
                top.setAlpha(120)
                bottom.setAlpha(120)
            gradient.setColorAt(0.0, top)
            gradient.setColorAt(1.0, bottom)
            painter.setBrush(gradient)
        else:
            track_color = QtGui.QColor(border)
            track_color.setAlpha(210 if enabled else 120)
            painter.setBrush(track_color if enabled else soft)
        painter.drawRoundedRect(track, 10, 10)
        knob_x = 24 if self._checked else 4
        painter.setBrush(QtGui.QColor(15, 23, 42, 36 if enabled else 16))
        painter.drawEllipse(QtCore.QRectF(knob_x + 0.6, 6.2, 16, 16))
        knob = QtGui.QColor("#FFFFFF" if enabled else palette.get("bg_card", "#FFFFFF"))
        painter.setBrush(knob)
        painter.drawEllipse(QtCore.QRectF(knob_x, 5, 16, 16))
        painter.end()


class PluginManagerDialog(QtWidgets.QDialog):
    def __init__(
        self, parent=None, plugin_manager=None, main_app=None, embedded: bool = False
    ):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.main_app = main_app
        self.embedded = bool(embedded)
        self._developer_visible = is_developer_mode()
        self._active_status = ""
        self._group_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._status_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._toast_timer = QtCore.QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)
        if self.embedded:
            self.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
        self.setWindowTitle("能力")
        self.resize(760, 560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(
            0 if self.embedded else 16,
            0 if self.embedded else 16,
            0 if self.embedded else 16,
            0 if self.embedded else 16,
        )

        self.container = QtWidgets.QFrame()
        self.container.setObjectName("dialogShell")
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(
            0 if self.embedded else 18,
            0 if self.embedded else 16,
            0 if self.embedded else 18,
            0 if self.embedded else 16,
        )
        container_layout.setSpacing(10)

        if not self.embedded:
            header_row = QtWidgets.QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(8)
            icon_label = QtWidgets.QLabel("🧩")
            icon_label.setStyleSheet("font-size: 18px; background: transparent;")
            header_row.addWidget(icon_label)
            title = QtWidgets.QLabel("能力")
            title.setObjectName("dialogTitle")
            header_row.addWidget(title)
            header_row.addStretch(1)
            container_layout.addLayout(header_row)

            desc_label = QtWidgets.QLabel(
                "她被允许做的事。点开关即可启用或关闭；双击卡片、或点「配置」改参数。"
            )
            desc_label.setObjectName("dialogDesc")
            desc_label.setWordWrap(True)
            container_layout.addWidget(desc_label)

        view_row = QtWidgets.QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(8)
        self.view_segment = QtWidgets.QFrame()
        self.view_segment.setObjectName("viewSegment")
        segment_layout = QtWidgets.QHBoxLayout(self.view_segment)
        segment_layout.setContentsMargins(3, 3, 3, 3)
        segment_layout.setSpacing(2)
        self.local_view_button = QtWidgets.QPushButton("本机能力")
        self.market_view_button = QtWidgets.QPushButton("插件市场")
        for button in (self.local_view_button, self.market_view_button):
            button.setObjectName("viewSegmentBtn")
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(28)
            button.setCheckable(True)
            segment_layout.addWidget(button)
        self.local_view_button.clicked.connect(lambda: self._set_market_mode(False))
        self.market_view_button.clicked.connect(lambda: self._set_market_mode(True))
        view_row.addWidget(self.view_segment)
        view_row.addStretch(1)
        container_layout.addLayout(view_row)

        self.toast_bar = QtWidgets.QFrame()
        self.toast_bar.setObjectName("pluginToast")
        toast_layout = QtWidgets.QHBoxLayout(self.toast_bar)
        toast_layout.setContentsMargins(12, 6, 8, 6)
        toast_layout.setSpacing(8)
        self.toast_label = QtWidgets.QLabel("")
        self.toast_label.setObjectName("pluginToastText")
        toast_layout.addWidget(self.toast_label, 1)
        self.toast_bar.setVisible(False)
        container_layout.addWidget(self.toast_bar)

        self.local_tools = QtWidgets.QFrame()
        self.local_tools.setObjectName("pluginToolbarCard")
        tools_layout = QtWidgets.QVBoxLayout(self.local_tools)
        tools_layout.setContentsMargins(12, 10, 12, 10)
        tools_layout.setSpacing(8)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setObjectName("pluginSearchInput")
        self.search_input.setPlaceholderText("搜索能力名称、说明或触发词…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_plugins)
        self.search_input.setFixedHeight(34)

        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setObjectName("pluginStatsBadge")
        self.stats_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.import_btn = QtWidgets.QPushButton("导入")
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        for btn in (self.import_btn, self.refresh_btn):
            btn.setObjectName("pluginToolBtn")
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
        self.import_btn.clicked.connect(self._import_local_plugin)
        self.refresh_btn.clicked.connect(self._refresh_plugins)

        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.stats_label)
        filter_row.addWidget(self.import_btn)
        filter_row.addWidget(self.refresh_btn)
        tools_layout.addLayout(filter_row)

        pills_row = QtWidgets.QHBoxLayout()
        pills_row.setContentsMargins(0, 0, 0, 0)
        pills_row.setSpacing(6)
        self.group_pills_host = QtWidgets.QWidget()
        self.group_pills_layout = QtWidgets.QHBoxLayout(self.group_pills_host)
        self.group_pills_layout.setContentsMargins(0, 0, 0, 0)
        self.group_pills_layout.setSpacing(5)
        pills_row.addWidget(self.group_pills_host)

        sep = QtWidgets.QFrame()
        sep.setObjectName("filterPillSep")
        sep.setFixedSize(1, 16)
        pills_row.addWidget(sep)

        for status_key, label in (("", "全部"), ("on", "已启用"), ("off", "已关闭")):
            btn = QtWidgets.QPushButton(label)
            btn.setObjectName("filterPillBtn")
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(functools.partial(self._set_status_filter, status_key))
            self._status_buttons[status_key] = btn
            pills_row.addWidget(btn)
        pills_row.addStretch(1)
        tools_layout.addLayout(pills_row)
        container_layout.addWidget(self.local_tools)

        # 保留下拉框，供分组筛选逻辑和外部读取；界面改用上面的胶囊。
        self.group_filter = QtWidgets.QComboBox(self)
        self.group_filter.setVisible(False)
        self.group_filter.currentIndexChanged.connect(self._on_group_combo_changed)

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
        self.cards_outer_layout.setContentsMargins(0, 2, 2, 4)
        self.cards_outer_layout.setSpacing(8)

        self.cards_grid = QtWidgets.QGridLayout()
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(12)
        self.cards_grid.setVerticalSpacing(10)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        self.cards_outer_layout.addLayout(self.cards_grid)

        self.empty_frame = QtWidgets.QFrame()
        self.empty_frame.setObjectName("pluginEmptyCard")
        empty_layout = QtWidgets.QVBoxLayout(self.empty_frame)
        empty_layout.setContentsMargins(20, 28, 20, 28)
        empty_layout.setSpacing(6)
        self.empty_label = QtWidgets.QLabel("没有找到匹配的能力")
        self.empty_label.setObjectName("pluginEmptyTitle")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_label)
        self.empty_reset_btn = QtWidgets.QPushButton("清除筛选")
        self.empty_reset_btn.setObjectName("pluginToolBtn")
        self.empty_reset_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.empty_reset_btn.setFixedHeight(30)
        self.empty_reset_btn.clicked.connect(self._reset_local_filters)
        reset_row = QtWidgets.QHBoxLayout()
        reset_row.addStretch(1)
        reset_row.addWidget(self.empty_reset_btn)
        reset_row.addStretch(1)
        empty_layout.addLayout(reset_row)
        self.empty_frame.setVisible(False)
        self.cards_outer_layout.addWidget(self.empty_frame)
        self.cards_outer_layout.addStretch(1)

        self.cards_scroll.setWidget(self.cards_container)
        container_layout.addWidget(self.cards_scroll, 1)

        self.market_panel = PluginMarketPanel(plugin_manager, self)
        self.market_panel.setVisible(False)
        self.market_panel.plugins_changed.connect(self._refresh_plugins)
        container_layout.addWidget(self.market_panel, 1)

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

        self._reload_group_filter()
        self.refresh_theme()
        self._set_market_mode(False)

    def refresh_theme(self) -> None:
        palette = get_ui_palette()
        dark = _is_dark_palette(palette)
        card = palette.get("bg_card", "#FFFFFF")
        soft = palette.get("bg_soft", "#F4F5F7")
        border = palette.get("border", "#D8DBE0")
        accent = palette.get("accent", "#0A84FF")
        accent_hover = palette.get("accent_hover", "#0071E3")
        accent_soft = palette.get("accent_soft", "#E8F1FF")
        text = palette.get("text_primary", "#1D1D1F")
        secondary = palette.get("text_secondary", "#6E6E73")
        muted = palette.get("text_muted", "#8E8E93")
        success = palette.get("success", "#30D158")
        danger = palette.get("danger", "#FF453A")
        segment_bg = soft
        checked_fg = accent if dark else accent_hover
        qss = get_tool_dialog_styles()
        if self.embedded:
            qss += "\nQDialog { background: transparent; border: none; }"
            qss += "\nQFrame#dialogShell { background: transparent; border: none; }"
        qss += f"""
            QFrame#pluginToolbarCard {{
                background: {card};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QFrame#viewSegment {{
                background: {segment_bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QPushButton#viewSegmentBtn {{
                background: transparent;
                color: {secondary};
                border: none;
                border-radius: 9px;
                padding: 2px 16px;
                font-size: 12px;
                font-weight: 600;
                min-width: 88px;
            }}
            QPushButton#viewSegmentBtn:hover {{
                color: {text};
            }}
            QPushButton#viewSegmentBtn:checked {{
                background: {card};
                color: {checked_fg};
                font-weight: 700;
            }}
            QLineEdit#pluginSearchInput {{
                background: {soft};
                color: {text};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QLineEdit#pluginSearchInput:focus {{
                background: {card};
                border: 1.5px solid {accent};
            }}
            QLabel#pluginStatsBadge, QLabel#pluginToastText {{
                color: {secondary};
                font-size: 11px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#pluginStatsBadge {{
                background: {soft};
                border: 1px solid {border};
                border-radius: 9px;
                padding: 4px 10px;
            }}
            QFrame#pluginToast {{
                background: {hex_to_rgba(accent_soft, 0.85)};
                border: 1px solid {hex_to_rgba(accent, 0.35)};
                border-radius: 10px;
            }}
            QFrame#filterPillSep {{
                background: {border};
                border: none;
            }}
            QPushButton#filterPillBtn {{
                background: transparent;
                color: {secondary};
                border: 1px solid transparent;
                border-radius: 13px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#filterPillBtn:hover {{
                background: {soft};
                color: {text};
            }}
            QPushButton#filterPillBtn:checked {{
                background: {accent_soft};
                color: {checked_fg};
                border: 1px solid {hex_to_rgba(accent, 0.4)};
                font-weight: 700;
            }}
            QPushButton#pluginToolBtn, QPushButton#pluginConfigBtn {{
                background: {card};
                color: {text};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#pluginConfigBtn {{
                border-radius: 8px;
                padding: 2px 10px;
                font-size: 11px;
            }}
            QPushButton#pluginToolBtn:hover, QPushButton#pluginConfigBtn:hover {{
                background: {accent_soft};
                color: {checked_fg};
                border-color: {hex_to_rgba(accent, 0.45)};
            }}
            QFrame#pluginCard {{
                background: {card};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QFrame#pluginCard:hover {{
                border-color: {hex_to_rgba(accent, 0.55)};
                background: {hex_to_rgba(accent_soft, 0.28 if dark else 0.45)};
            }}
            QFrame#pluginCardDisabled {{
                background: {hex_to_rgba(soft, 0.72)};
                border: 1px solid {hex_to_rgba(border, 0.85)};
                border-radius: 14px;
            }}
            QFrame#pluginCardDisabled:hover {{
                border-color: {border};
                background: {card};
            }}
            QFrame#pluginEmptyCard {{
                background: {hex_to_rgba(soft, 0.5)};
                border: 1px dashed {border};
                border-radius: 14px;
            }}
            QLabel#pluginEmptyTitle {{
                color: {secondary};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#pluginSelfBadge {{
                background: {hex_to_rgba("#7C3AED", 0.16)};
                color: {"#C4B5FD" if dark else "#6D28D9"};
                border: 1px solid {hex_to_rgba("#7C3AED", 0.35)};
                padding: 1px 6px;
                border-radius: 7px;
                font-size: 10px;
                font-weight: 600;
            }}
        """
        self.setStyleSheet(qss)
        self._palette_cache = {
            "text": text,
            "secondary": secondary,
            "muted": muted,
            "success": success,
            "danger": danger,
            "dark": dark,
        }
        if hasattr(self, "market_panel"):
            self.market_panel.refresh_theme()
        self._sync_segment_checks()
        if self._cards_data or self.plugin_manager:
            self._refresh_plugins()
        editor_cls = globals().get("PluginEditorDialog")
        if editor_cls is not None:
            for editor in self.findChildren(editor_cls):
                refresher = getattr(editor, "refresh_theme", None)
                if callable(refresher):
                    refresher()

    def set_developer_visible(self, visible: bool):
        self._developer_visible = bool(visible)
        self._reload_group_filter()
        self._refresh_plugins()
        if hasattr(self, "market_panel"):
            self.market_panel.set_developer_visible(visible)

    def _sync_segment_checks(self):
        market = self.market_panel.isVisible() if hasattr(self, "market_panel") else False
        self.local_view_button.setChecked(not market)
        self.market_view_button.setChecked(market)

    def _set_market_mode(self, enabled: bool):
        market = bool(enabled)
        self.local_view_button.setChecked(not market)
        self.market_view_button.setChecked(market)
        for widget in (
            self.local_tools,
            self.search_input,
            self.group_filter,
            self.stats_label,
            self.import_btn,
            self.refresh_btn,
            self.cards_scroll,
        ):
            widget.setVisible(not market)
        self.group_filter.setVisible(False)
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
        self._rebuild_group_pills()

    def _rebuild_group_pills(self):
        if not hasattr(self, "group_pills_layout"):
            return
        while self.group_pills_layout.count():
            item = self.group_pills_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._group_buttons.clear()
        current = str(self.group_filter.currentData() or "")
        if current == "developer" and not self._developer_visible:
            current = ""
            self.group_filter.blockSignals(True)
            self.group_filter.setCurrentIndex(0)
            self.group_filter.blockSignals(False)
        specs = [("", "全部")]
        for key, label in CAPABILITY_GROUP_LABELS.items():
            if key == "developer" and not self._developer_visible:
                continue
            specs.append((key, label))
        for key, label in specs:
            button = QtWidgets.QPushButton(label, self.group_pills_host)
            button.setObjectName("filterPillBtn")
            button.setCheckable(True)
            button.setChecked(key == current)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(26)
            button.clicked.connect(functools.partial(self._set_group_filter, key))
            self._group_buttons[key] = button
            self.group_pills_layout.addWidget(button)
        self._sync_status_pills()

    def _set_group_filter(self, key: str):
        self.group_filter.blockSignals(True)
        idx = self.group_filter.findData(key)
        self.group_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_filter.blockSignals(False)
        for group_key, button in self._group_buttons.items():
            button.setChecked(group_key == key)
        self._filter_plugins()

    def _on_group_combo_changed(self, _index=None):
        current = str(self.group_filter.currentData() or "")
        for group_key, button in self._group_buttons.items():
            button.setChecked(group_key == current)
        self._filter_plugins()

    def _set_status_filter(self, key: str):
        self._active_status = str(key or "")
        self._sync_status_pills()
        self._filter_plugins()

    def _sync_status_pills(self):
        for key, button in self._status_buttons.items():
            button.setChecked(key == self._active_status)

    def _reset_local_filters(self):
        self._active_status = ""
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.group_filter.blockSignals(True)
        self.group_filter.setCurrentIndex(0)
        self.group_filter.blockSignals(False)
        for key, button in self._group_buttons.items():
            button.setChecked(key == "")
        self._sync_status_pills()
        self._filter_plugins()

    @staticmethod
    def _group_badge_style(group: str) -> str:
        return _badge_style(group)

    def _plugin_icon(self, trigger: str, group: str) -> QtWidgets.QLabel:
        palette = get_ui_palette()
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(38, 38)
        icon_path = None
        manager = self.plugin_manager
        getter = getattr(manager, "get_plugin_icon_path", None)
        if callable(getter):
            try:
                icon_path = getter(trigger)
            except Exception:
                icon_path = None
        pixmap = QtGui.QPixmap()
        if icon_path:
            pixmap = resolve_icon(icon_path).pixmap(28, 28)
        if not pixmap.isNull():
            label.setPixmap(pixmap)
            label.setStyleSheet(
                "background: transparent; border-radius: 10px;"
            )
            return label
        glyph, icon_bg, icon_fg = _group_icon_spec(group, palette)
        label.setText(glyph)
        label.setStyleSheet(
            f"background-color: {icon_bg}; color: {icon_fg};"
            "border-radius: 11px; font-size: 15px; font-weight: 700;"
        )
        return label

    def _create_plugin_card(self, info: dict, group: str) -> tuple[QtWidgets.QFrame, dict]:
        trigger = str(info.get("trigger") or "")
        enabled = bool(info.get("enabled"))
        name = str(info.get("name") or trigger)
        summary = str(info.get("description") or "").strip() or f"触发词：{trigger}"
        card_summary = str(info.get("card_summary") or "").strip()
        visible = card_summary or summary
        access_summary = str(info.get("access_summary") or "").strip()
        group_label = capability_group_label(group)
        colors = getattr(self, "_palette_cache", None) or {}
        text = colors.get("text", "#1D1D1F")
        secondary = colors.get("secondary", "#6E6E73")
        muted = colors.get("muted", "#8E8E93")

        card = QtWidgets.QFrame(self.cards_container)
        card.setObjectName("pluginCard" if enabled else "pluginCardDisabled")
        card.setMinimumHeight(96)
        card.setMaximumHeight(108)
        card.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        card.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        card.installEventFilter(self)

        row = QtWidgets.QHBoxLayout(card)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        icon = self._plugin_icon(trigger, group)
        row.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)

        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        name_label = QtWidgets.QLabel(name)
        name_label.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {text if enabled else muted}; background: transparent;"
        )
        top.addWidget(name_label)

        group_badge = QtWidgets.QLabel(group_label)
        group_badge.setStyleSheet(self._group_badge_style(group))
        group_badge.setFixedHeight(18)
        top.addWidget(group_badge)

        self_badge = None
        if bool(info.get("self_callable")):
            self_badge = QtWidgets.QLabel("可自调用")
            self_badge.setObjectName("pluginSelfBadge")
            self_badge.setFixedHeight(18)
            self_badge.setToolTip("闲聊里她可以自己调用，不必等关键词")
            top.addWidget(self_badge)
        top.addStretch(1)

        edit_btn = QtWidgets.QPushButton("配置")
        edit_btn.setObjectName("pluginConfigBtn")
        edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        edit_btn.setFixedHeight(26)
        edit_btn.setToolTip("修改参数、触发词和权限。双击卡片也可以。")
        edit_btn.clicked.connect(functools.partial(self._edit_plugin, trigger))
        top.addWidget(edit_btn)

        toggle = _ToggleSwitch(enabled)
        toggle.setToolTip("已启用，点击关闭" if enabled else "已关闭，点击启用")
        toggle.toggled.connect(functools.partial(self._on_switch_toggled, trigger))
        top.addWidget(toggle)
        body.addLayout(top)

        desc = _ElidedLabel(visible)
        desc.setStyleSheet(
            f"font-size: 12px; color: {secondary if enabled else muted}; background: transparent;"
        )
        body.addWidget(desc)

        meta_parts = [trigger]
        if access_summary:
            meta_parts.append(access_summary)
        meta = _ElidedLabel("  ·  ".join(meta_parts))
        meta.setStyleSheet(
            f"font-size: 11px; color: {muted}; background: transparent;"
        )
        body.addWidget(meta)
        row.addLayout(body, 1)

        tooltip_bits = [name, f"分组：{group_label}", f"触发词：{trigger}"]
        if card_summary and card_summary != summary:
            tooltip_bits.append(card_summary)
            tooltip_bits.append(summary)
        elif summary:
            tooltip_bits.append(summary)
        if access_summary:
            tooltip_bits.append(access_summary)
        tooltip_bits.append("双击打开配置")
        tooltip = "\n".join(tooltip_bits)
        card.setToolTip(tooltip)
        name_label.setToolTip(tooltip)
        desc.setToolTip(tooltip)
        meta.setToolTip(tooltip)
        card.setProperty("pluginTrigger", trigger)
        for widget in (icon, name_label, group_badge, desc, meta, self_badge):
            if widget is not None:
                widget.setAttribute(
                    QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
        parts = {
            "name": name_label,
            "desc": desc,
            "toggle": toggle,
            "icon": icon,
        }
        return card, parts

    def eventFilter(self, watched, event):
        if (
            isinstance(watched, QtWidgets.QFrame)
            and event.type() == QtCore.QEvent.Type.MouseButtonDblClick
        ):
            trigger = str(watched.property("pluginTrigger") or "")
            if trigger:
                self._edit_plugin(trigger)
                return True
        return super().eventFilter(watched, event)

    def _refresh_plugins(self):
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)
        for entry in self._cards_data:
            widget = entry.get("card")
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._cards_data.clear()
        self.card_widgets_by_trigger.clear()

        if not self.plugin_manager:
            self.stats_label.setText("0 项")
            self.empty_label.setText("当前还不能管理能力")
            self.empty_frame.setVisible(True)
            self.empty_reset_btn.setVisible(False)
            return

        plugins_info = self.plugin_manager.get_all_plugins_info()
        show_dev = bool(getattr(self, "_developer_visible", False))
        for info in plugins_info:
            trigger = str(info.get("trigger") or "")
            group = capability_group(trigger)
            if group == "developer" and not show_dev:
                continue
            card, parts = self._create_plugin_card(info, group)
            search_blob = " ".join(
                [
                    str(info.get("name") or ""),
                    trigger,
                    str(info.get("card_summary") or ""),
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
                "parts": parts,
                "name": str(info.get("name") or trigger),
            }
            self._cards_data.append(entry)
            self.card_widgets_by_trigger[trigger] = card
        self._update_filter_counts()
        self._filter_plugins()

    def _update_filter_counts(self):
        counts = {"": 0}
        on_count = 0
        for entry in self._cards_data:
            counts[""] += 1
            counts[entry["group"]] = counts.get(entry["group"], 0) + 1
            if entry["enabled"]:
                on_count += 1
        for key, button in self._group_buttons.items():
            base = "全部" if key == "" else capability_group_label(key)
            button.setText(f"{base} {counts.get(key, 0)}")
        off_count = counts.get("", 0) - on_count
        labels = {"": "全部", "on": f"已启用 {on_count}", "off": f"已关闭 {off_count}"}
        for key, button in self._status_buttons.items():
            button.setText(labels.get(key, key))

    def _filter_plugins(self, _text=None):
        text = ""
        if hasattr(self, "search_input"):
            text = self.search_input.text().lower().strip()
        group = ""
        if hasattr(self, "group_filter"):
            group = str(self.group_filter.currentData() or "").strip()
        status = str(getattr(self, "_active_status", "") or "")

        while self.cards_grid.count():
            self.cards_grid.takeAt(0)

        visible_entries = []
        for entry in self._cards_data:
            card = entry["card"]
            if group and entry["group"] != group:
                card.setVisible(False)
                continue
            if status == "on" and not entry["enabled"]:
                card.setVisible(False)
                continue
            if status == "off" and entry["enabled"]:
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
        enabled_visible = sum(1 for entry in visible_entries if entry["enabled"])
        if hasattr(self, "stats_label"):
            self.stats_label.setText(f"显示 {total_visible} · 已启用 {enabled_visible}")
        if hasattr(self, "empty_frame"):
            self.empty_frame.setVisible(total_visible == 0)
            filtering = bool(text or group or status)
            self.empty_label.setText(
                "没有找到匹配的能力" if filtering or self._cards_data else "还没有可用的能力"
            )
            self.empty_reset_btn.setVisible(total_visible == 0 and filtering)

    def _on_switch_toggled(self, trigger: str, checked: bool):
        if not self.plugin_manager:
            return
        try:
            current = self.plugin_manager.is_plugin_enabled(trigger)
            if bool(checked) == bool(current):
                return
            ok = (
                self.plugin_manager.enable_plugin(trigger)
                if checked
                else self.plugin_manager.disable_plugin(trigger)
            )
            if not ok:
                self._set_switch_checked(trigger, current)
                self._show_toast(f"没能切换「{trigger}」", False)
                return
            self._apply_enabled_state(trigger, checked)
            name = self._entry_name(trigger)
            self._show_toast(
                f"已打开「{name}」" if checked else f"已关闭「{name}」",
                checked,
            )
            self._filter_plugins()
            self._update_filter_counts()
        except Exception as exc:
            self._set_switch_checked(trigger, not checked)
            QtWidgets.QMessageBox.critical(self, "错误", f"操作失败: {exc}")

    def _entry_name(self, trigger: str) -> str:
        for entry in self._cards_data:
            if entry["trigger"] == trigger:
                return str(entry.get("name") or trigger)
        return trigger

    def _set_switch_checked(self, trigger: str, checked: bool):
        for entry in self._cards_data:
            if entry["trigger"] != trigger:
                continue
            toggle = entry.get("parts", {}).get("toggle")
            if toggle is not None:
                toggle.blockSignals(True)
                toggle.setChecked(checked)
                toggle.blockSignals(False)
            return

    def _apply_enabled_state(self, trigger: str, enabled: bool):
        colors = getattr(self, "_palette_cache", None) or {}
        text = colors.get("text", "#1D1D1F")
        secondary = colors.get("secondary", "#6E6E73")
        muted = colors.get("muted", "#8E8E93")
        for entry in self._cards_data:
            if entry["trigger"] != trigger:
                continue
            entry["enabled"] = bool(enabled)
            card = entry["card"]
            card.setObjectName("pluginCard" if enabled else "pluginCardDisabled")
            card.style().unpolish(card)
            card.style().polish(card)
            parts = entry.get("parts") or {}
            name_label = parts.get("name")
            desc = parts.get("desc")
            toggle = parts.get("toggle")
            if name_label is not None:
                name_label.setStyleSheet(
                    "font-weight: 700; font-size: 13px; background: transparent; "
                    f"color: {text if enabled else muted};"
                )
            if desc is not None:
                desc.setStyleSheet(
                    "font-size: 12px; background: transparent; "
                    f"color: {secondary if enabled else muted};"
                )
            if toggle is not None:
                toggle.setToolTip("已启用，点击关闭" if enabled else "已关闭，点击启用")
            return

    def _show_toast(self, message, is_success=True):
        if hasattr(self, "toast_label"):
            prefix = "已完成" if is_success else "请注意"
            self.toast_label.setText(f"{prefix}  ·  {message}")
            self.toast_bar.setVisible(True)
            self._toast_timer.start(2400)
        if self.main_app and hasattr(self.main_app, "append"):
            self.main_app.append("system", message)

    def _hide_toast(self):
        if hasattr(self, "toast_bar"):
            self.toast_bar.setVisible(False)

    def _toggle_plugin(self, trigger: str):
        if not self.plugin_manager:
            return
        enabled = self.plugin_manager.is_plugin_enabled(trigger)
        self._on_switch_toggled(trigger, not enabled)

    def _edit_plugin(self, trigger: str):
        if not self.plugin_manager:
            return
        try:
            dialog = PluginEditorDialog(
                parent=self, plugin_manager=self.plugin_manager, trigger=trigger
            )
            result = dialog.exec()
            self._refresh_plugins()
            if result == QtWidgets.QDialog.DialogCode.Accepted:
                self._show_toast(f"已保存「{self._entry_name(trigger)}」的配置", True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "错误", f"打开编辑对话框失败: {exc}")

    def _import_local_plugin(self):
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择插件文件夹")
        if not dir_path:
            return
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
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "错误", f"无法删除旧文件: {exc}")
                return
        try:
            shutil.copytree(dir_path, target_path)
            if self.plugin_manager:
                self.plugin_manager.load_plugins()
                self._refresh_plugins()
            self._show_toast(f"已导入「{folder_name}」", True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "错误", f"导入失败: {exc}")


class TagChipEditor(QtWidgets.QWidget):
    """胶囊标签编辑器：一体化胶囊输入槽与柔和强调色标签。"""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        placeholder: str = "输入内容后按回车或点右侧添加（支持逗号分隔）",
        empty_text: str = "还没有额外条目，在下方输入后按回车添加",
        on_change=None,
    ):
        super().__init__(parent)
        self._items: list[str] = []
        self._empty_text = empty_text
        self._on_change = on_change

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.chips_box = QtWidgets.QFrame(self)
        self.chips_box.setObjectName("tagChipBox")
        chips_box_layout = QtWidgets.QVBoxLayout(self.chips_box)
        chips_box_layout.setContentsMargins(10, 10, 10, 8)
        chips_box_layout.setSpacing(8)

        self.empty_label = QtWidgets.QLabel(self._empty_text, self.chips_box)
        self.empty_label.setObjectName("tagEmptyLabel")
        chips_box_layout.addWidget(self.empty_label)

        self.flow_widget = FlowWidget(self.chips_box, margin=0, h_spacing=6, v_spacing=6)
        chips_box_layout.addWidget(self.flow_widget)

        self.divider = QtWidgets.QFrame(self.chips_box)
        self.divider.setObjectName("tagInputDivider")
        self.divider.setFixedHeight(1)
        chips_box_layout.addWidget(self.divider)

        add_row = QtWidgets.QHBoxLayout()
        add_row.setContentsMargins(0, 2, 0, 0)
        add_row.setSpacing(8)

        self.input_edit = QtWidgets.QLineEdit(self.chips_box)
        self.input_edit.setObjectName("tagInlineInput")
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setFixedHeight(28)
        self.input_edit.installEventFilter(self)

        self.add_btn = QtWidgets.QPushButton("添加", self.chips_box)
        self.add_btn.setObjectName("tagAddPillBtn")
        self.add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.add_btn.setAutoDefault(False)
        self.add_btn.setDefault(False)
        self.add_btn.clicked.connect(self._commit_input)

        add_row.addWidget(self.input_edit, 1)
        add_row.addWidget(self.add_btn, 0)
        chips_box_layout.addLayout(add_row)
        root.addWidget(self.chips_box)

        self.refresh_theme()
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
            chip_layout.setContentsMargins(10, 3, 5, 3)
            chip_layout.setSpacing(4)

            lbl = QtWidgets.QLabel(token, chip)
            lbl.setObjectName("tagChipText")
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

    def refresh_theme(self) -> None:
        palette = get_ui_palette()
        muted = palette.get("text_muted", "#8E8E93")
        self.empty_label.setStyleSheet(
            f"color: {muted}; font-size: 11px; background: transparent; padding: 2px 2px;"
        )

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


class _EditorNavButton(QtWidgets.QPushButton):
    """左侧栏分区导航按钮：圆角填充、左侧强调色竖条、副标题与计数胶囊。"""

    def __init__(
        self,
        glyph: str,
        title: str,
        subtitle: str,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self._glyph = glyph
        self._title = title
        self._subtitle = subtitle
        self._badge = ""
        self._hovered = False
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFixedHeight(56)
        self.setMinimumWidth(164)
        self.setText(title)

    def set_badge(self, text: str) -> None:
        self._badge = str(text or "").strip()
        self.update()

    def set_subtitle(self, text: str) -> None:
        self._subtitle = str(text or "").strip()
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        palette = get_ui_palette()
        dark = _is_dark_palette(palette)
        accent = QtGui.QColor(palette.get("accent", "#0A84FF"))
        accent_hover = QtGui.QColor(palette.get("accent_hover", "#0071E3"))
        accent_soft = QtGui.QColor(palette.get("accent_soft", "#E8F1FF"))
        text_primary = QtGui.QColor(palette.get("text_primary", "#1D1D1F"))
        text_secondary = QtGui.QColor(palette.get("text_secondary", "#6E6E73"))
        text_muted = QtGui.QColor(palette.get("text_muted", "#8E8E93"))
        soft_bg = QtGui.QColor(palette.get("bg_soft", "#F4F5F7"))

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        active_fg = accent if dark else accent_hover
        if self.isChecked():
            gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
            wash = QtGui.QColor(accent_soft)
            wash.setAlpha(245 if not dark else 200)
            glow = QtGui.QColor(accent)
            glow.setAlpha(58 if not dark else 86)
            gradient.setColorAt(0.0, wash)
            gradient.setColorAt(1.0, glow)
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(accent.red(), accent.green(), accent.blue(), 78), 1.0
                )
            )
            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, 13.0, 13.0)

            bar = QtCore.QRectF(
                rect.left() + 5.0, rect.top() + 13.0, 3.0, rect.height() - 26.0
            )
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(bar, 1.5, 1.5)
        elif self._hovered:
            hover_fill = QtGui.QColor(soft_bg)
            hover_fill.setAlpha(220 if not dark else 150)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(hover_fill)
            painter.drawRoundedRect(rect, 13.0, 13.0)

        icon_rect = QtCore.QRectF(
            rect.left() + 14.0, rect.center().y() - 14.0, 28.0, 28.0
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        if self.isChecked():
            tile = QtGui.QColor("#FFFFFF" if not dark else "#1C2430")
            painter.setBrush(tile)
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(accent.red(), accent.green(), accent.blue(), 55), 1.0
                )
            )
        else:
            icon_bg = QtGui.QColor(soft_bg)
            icon_bg.setAlpha(210 if not dark else 140)
            painter.setBrush(icon_bg)
        painter.drawRoundedRect(icon_rect, 9.0, 9.0)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)

        glyph_font = QtGui.QFont(self.font())
        glyph_font.setPixelSize(13)
        glyph_font.setWeight(QtGui.QFont.Weight.Bold)
        painter.setFont(glyph_font)
        painter.setPen(active_fg if self.isChecked() else text_secondary)
        painter.drawText(icon_rect, QtCore.Qt.AlignmentFlag.AlignCenter, self._glyph)

        badge_width = 0.0
        if self._badge:
            badge_font = QtGui.QFont(self.font())
            badge_font.setPixelSize(10)
            badge_font.setWeight(QtGui.QFont.Weight.DemiBold)
            fm_badge = QtGui.QFontMetrics(badge_font)
            bw = max(20, fm_badge.horizontalAdvance(self._badge) + 12)
            badge_width = float(bw) + 8.0
            badge_rect = QtCore.QRectF(
                rect.right() - bw - 10.0,
                rect.top() + 10.0,
                float(bw),
                18.0,
            )
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            if self.isChecked():
                painter.setBrush(accent)
                painter.drawRoundedRect(badge_rect, 9.0, 9.0)
                painter.setPen(QtGui.QColor("#FFFFFF"))
            else:
                pill_bg = QtGui.QColor(soft_bg)
                pill_bg.setAlpha(230 if not dark else 160)
                painter.setBrush(pill_bg)
                painter.drawRoundedRect(badge_rect, 9.0, 9.0)
                painter.setPen(text_muted)
            painter.setFont(badge_font)
            painter.drawText(
                badge_rect, QtCore.Qt.AlignmentFlag.AlignCenter, self._badge
            )

        text_left = icon_rect.right() + 9.0
        text_right = rect.right() - 10.0 - badge_width
        title_font = QtGui.QFont(self.font())
        title_font.setPixelSize(12)
        title_font.setWeight(
            QtGui.QFont.Weight.Bold if self.isChecked() else QtGui.QFont.Weight.DemiBold
        )
        painter.setFont(title_font)
        painter.setPen(active_fg if self.isChecked() else text_primary)
        title_rect = QtCore.QRectF(
            text_left, rect.top() + 8.0, max(30.0, text_right - text_left), 18.0
        )
        painter.drawText(
            title_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

        sub_font = QtGui.QFont(self.font())
        sub_font.setPixelSize(10)
        sub_font.setWeight(QtGui.QFont.Weight.Normal)
        painter.setFont(sub_font)
        painter.setPen(text_secondary if self.isChecked() else text_muted)
        sub_rect = QtCore.QRectF(
            text_left,
            rect.top() + 26.0,
            max(30.0, rect.right() - 10.0 - text_left),
            16.0,
        )
        elided_sub = QtGui.QFontMetrics(sub_font).elidedText(
            self._subtitle, QtCore.Qt.TextElideMode.ElideRight, int(sub_rect.width())
        )
        painter.drawText(
            sub_rect,
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            elided_sub,
        )
        painter.end()


def _wrap_spin_stepper(
    spinbox: QtWidgets.QSpinBox | QtWidgets.QDoubleSpinBox,
    parent: QtWidgets.QWidget | None = None,
) -> QtWidgets.QFrame:
    """把原生 QSpinBox 包成胶囊式 [ − | 数值 | + ] 步进器，消除简陋的小上下箭头。"""
    spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    spinbox.setObjectName("stepperSpin")
    spinbox.setFixedHeight(30)

    shell = QtWidgets.QFrame(parent)
    shell.setObjectName("stepperShell")
    shell.setFixedHeight(36)
    row = QtWidgets.QHBoxLayout(shell)
    row.setContentsMargins(4, 3, 4, 3)
    row.setSpacing(4)

    minus_btn = QtWidgets.QPushButton("−", shell)
    minus_btn.setObjectName("stepperBtn")
    minus_btn.setFixedSize(28, 28)
    minus_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    minus_btn.setAutoDefault(False)
    minus_btn.setDefault(False)
    minus_btn.clicked.connect(spinbox.stepDown)

    plus_btn = QtWidgets.QPushButton("+", shell)
    plus_btn.setObjectName("stepperBtn")
    plus_btn.setFixedSize(28, 28)
    plus_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    plus_btn.setAutoDefault(False)
    plus_btn.setDefault(False)
    plus_btn.clicked.connect(spinbox.stepUp)

    row.addWidget(minus_btn, 0)
    row.addWidget(spinbox, 1)
    row.addWidget(plus_btn, 0)
    return shell


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

        self._param_entries = []
        self._visible_setting_count = 0
        self._section_chosen = False
        self._active_section = "params"
        self._suspend_dirty = False
        self._baseline = None
        self._header_group = capability_group(str(trigger or ""))
        self._header_icon_is_glyph = True

        self.setWindowTitle(f"配置能力 - {trigger}")
        self.resize(900, 660)
        self.setMinimumSize(760, 540)

        self._build_editor_chrome()
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
        return f"geometry_v2/{trigger}"

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

        preferred_width = min(max(900, self.width()), int(available.width() * 0.72))
        preferred_height = min(max(660, self.height()), int(available.height() * 0.84))

        target_width = max(760, min(preferred_width, available.width()))
        target_height = max(540, min(preferred_height, available.height()))

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

    _SECTION_COPY = {
        "params": ("功能参数", "开关、数值、密钥和这个能力自己的配置"),
        "access": ("触发与调用", "额外触发词、超时，以及谁可以调用它"),
        "meta": ("能力信息", "展示名称、说明，以及锁定的协议标识"),
    }

    def _build_editor_chrome(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(14)

        layout.addWidget(self._build_notice_bar())

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.addWidget(self._build_sidebar(), 0)
        body.addWidget(self._build_stage(), 1)
        layout.addLayout(body, 1)
        layout.addWidget(self._build_footer())

        self.tab_widget = QtWidgets.QTabWidget(self)
        self.tab_widget.setVisible(False)
        self.settings_tab = QtWidgets.QWidget()
        self.basic_tab = QtWidgets.QWidget()
        self.tab_widget.addTab(self.settings_tab, "功能参数")
        self.tab_widget.addTab(self.basic_tab, "触发与能力信息")
        layout.addWidget(self.tab_widget)

        self._wire_form_watchers()
        self._on_toggle_unlock_meta(False)
        self._sync_access_control_inputs()
        self._apply_editor_theme()

    def _build_sidebar(self) -> QtWidgets.QFrame:
        sidebar = QtWidgets.QFrame(self)
        sidebar.setObjectName("editorSidebarCard")
        sidebar.setFixedWidth(248)
        self.section_bar = sidebar
        column = QtWidgets.QVBoxLayout(sidebar)
        column.setContentsMargins(14, 16, 14, 14)
        column.setSpacing(12)

        identity = QtWidgets.QHBoxLayout()
        identity.setContentsMargins(2, 0, 2, 0)
        identity.setSpacing(10)
        self.header_icon = self._build_header_icon()
        identity.addWidget(self.header_icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        name_col = QtWidgets.QVBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(4)
        self.header_name_label = QtWidgets.QLabel(str(self.trigger or "能力"), sidebar)
        self.header_name_label.setWordWrap(True)
        name_col.addWidget(self.header_name_label)
        badge_row = QtWidgets.QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(6)
        self.header_group_badge = QtWidgets.QLabel(
            capability_group_label(self._header_group), sidebar
        )
        self.header_group_badge.setStyleSheet(
            PluginManagerDialog._group_badge_style(self._header_group)
        )
        self.header_group_badge.setFixedHeight(18)
        self.header_trigger_badge = QtWidgets.QLabel(f"⚡ {self.trigger}", sidebar)
        self.header_trigger_badge.setObjectName("editorTriggerBadge")
        self.header_trigger_badge.setFixedHeight(18)
        badge_row.addWidget(self.header_group_badge, 0)
        badge_row.addWidget(self.header_trigger_badge, 0)
        badge_row.addStretch(1)
        name_col.addLayout(badge_row)
        identity.addLayout(name_col, 1)
        column.addLayout(identity)

        self.header_desc_label = QtWidgets.QLabel("暂无能力说明", sidebar)
        self.header_desc_label.setObjectName("editorHeaderDesc")
        self.header_desc_label.setWordWrap(True)
        column.addWidget(self.header_desc_label)

        enable_row = QtWidgets.QFrame(sidebar)
        enable_row.setObjectName("editorEnableRow")
        enable_layout = QtWidgets.QHBoxLayout(enable_row)
        enable_layout.setContentsMargins(12, 8, 12, 8)
        enable_layout.setSpacing(8)
        enable_copy = QtWidgets.QVBoxLayout()
        enable_copy.setSpacing(0)
        enable_title = QtWidgets.QLabel("插件开关", enable_row)
        enable_title.setObjectName("settingFieldTitle")
        self.enable_state_label = QtWidgets.QLabel("启用中", enable_row)
        self.enable_state_label.setObjectName("settingFieldDesc")
        enable_copy.addWidget(enable_title)
        enable_copy.addWidget(self.enable_state_label)
        self.plugin_enable_switch = _ToggleSwitch(True)
        self.plugin_enable_switch.toggled.connect(self._on_plugin_enable_toggled)
        enable_layout.addLayout(enable_copy, 1)
        enable_layout.addWidget(self.plugin_enable_switch, 0)
        column.addWidget(enable_row)

        self._section_buttons = {}
        nav_specs = (
            ("params", "参", "功能参数", "开关、数值、密钥"),
            ("access", "触", "触发与调用", "触发词和权限"),
            ("meta", "信", "能力信息", "名称与协议"),
        )
        for key, glyph, title, subtitle in nav_specs:
            button = _EditorNavButton(glyph, title, subtitle, sidebar)
            button.setObjectName("editorNavBtn")
            button.setAutoExclusive(True)
            button.clicked.connect(
                lambda _checked=False, section=key: self._select_section(section)
            )
            column.addWidget(button)
            self._section_buttons[key] = button

        column.addStretch(1)

        self.sidebar_actions = QtWidgets.QFrame(sidebar)
        self.sidebar_actions.setObjectName("editorActionWell")
        actions = QtWidgets.QVBoxLayout(self.sidebar_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.readme_btn = QtWidgets.QPushButton("使用说明", self.sidebar_actions)
        self.readme_btn.setObjectName("ghostPillBtn")
        self.readme_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.readme_btn.setAutoDefault(False)
        self.readme_btn.clicked.connect(self._open_readme)
        readme_path = ""
        getter = getattr(self.plugin_manager, "get_plugin_readme_path", None)
        if callable(getter):
            try:
                readme_path = str(getter(self.trigger) or "")
            except Exception:
                readme_path = ""
        self.readme_btn.setVisible(bool(readme_path))
        actions.addWidget(self.readme_btn)

        trigger = str(self.trigger or "").strip()
        if trigger == "knowledge_base":
            ingest_btn = QtWidgets.QPushButton("保存并学习", self.sidebar_actions)
            ingest_btn.setObjectName("ghostPillBtn")
            ingest_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            ingest_btn.setAutoDefault(False)
            ingest_btn.clicked.connect(self._save_and_run_knowledge_ingest)
            actions.addWidget(ingest_btn)
        elif trigger == "search_web":
            check_btn = QtWidgets.QPushButton("检测搜索接口", self.sidebar_actions)
            check_btn.setObjectName("ghostPillBtn")
            check_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            check_btn.setAutoDefault(False)
            check_btn.clicked.connect(self._run_search_endpoint_check)
            actions.addWidget(check_btn)
        self.sidebar_actions.setVisible(actions.count() > 0 and any(
            actions.itemAt(index).widget() and not actions.itemAt(index).widget().isHidden()
            for index in range(actions.count())
        ))
        column.addWidget(self.sidebar_actions)

        self.sidebar_mode_label = QtWidgets.QLabel("", sidebar)
        self.sidebar_mode_label.setObjectName("editorModeLine")
        self.sidebar_mode_label.setWordWrap(True)
        column.addWidget(self.sidebar_mode_label)
        return sidebar

    def _build_stage(self) -> QtWidgets.QFrame:
        stage = QtWidgets.QFrame(self)
        stage.setObjectName("editorStageCard")
        self.stage_card = stage
        column = QtWidgets.QVBoxLayout(stage)
        column.setContentsMargins(16, 14, 16, 12)
        column.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        header.setSpacing(12)
        titles = QtWidgets.QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        self.stage_kicker = QtWidgets.QLabel("配置", stage)
        self.stage_kicker.setObjectName("stageKicker")
        self.stage_title = QtWidgets.QLabel("功能参数", stage)
        self.stage_title.setObjectName("stageTitle")
        self.stage_subtitle = QtWidgets.QLabel("", stage)
        self.stage_subtitle.setObjectName("stageSubtitle")
        self.stage_subtitle.setWordWrap(True)
        titles.addWidget(self.stage_kicker)
        titles.addWidget(self.stage_title)
        titles.addWidget(self.stage_subtitle)
        header.addLayout(titles, 1)

        self.param_search = QtWidgets.QLineEdit(stage)
        self.param_search.setObjectName("pluginSearchInput")
        self.param_search.setPlaceholderText("搜索参数")
        self.param_search.setClearButtonEnabled(True)
        self.param_search.setFixedHeight(34)
        self.param_search.setMinimumWidth(180)
        self.param_search.setMaximumWidth(240)
        self.param_search.textChanged.connect(self._reflow_settings)
        self.param_search.setVisible(False)
        self._search_action = self.param_search.addAction(
            self._make_search_icon(),
            QtWidgets.QLineEdit.ActionPosition.TrailingPosition,
        )
        header.addWidget(self.param_search, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        column.addLayout(header)

        self.section_stack = QtWidgets.QStackedWidget(stage)
        self.section_stack.addWidget(self._build_params_page())
        self.section_stack.addWidget(self._build_access_page())
        self.section_stack.addWidget(self._build_meta_page())
        column.addWidget(self.section_stack, 1)
        return stage

    def _build_footer(self) -> QtWidgets.QFrame:
        footer_card = QtWidgets.QFrame(self)
        footer_card.setObjectName("editorFooterCard")
        self.footer_card = footer_card
        footer = QtWidgets.QHBoxLayout(footer_card)
        footer.setContentsMargins(12, 10, 12, 10)
        footer.setSpacing(8)
        self.reset_btn = QtWidgets.QPushButton("恢复默认", footer_card)
        self.reset_btn.setObjectName("ghostPillBtn")
        self.reset_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setAutoDefault(False)
        self.reset_btn.clicked.connect(self._reset_config)
        footer.addWidget(self.reset_btn)
        footer.addStretch(1)

        self.dirty_label = QtWidgets.QLabel("✓  已与磁盘同步", footer_card)
        self.dirty_label.setObjectName("editorDirtyOff")
        self.dirty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.dirty_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Maximum,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        footer.addWidget(self.dirty_label, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        footer.addStretch(1)

        self.cancel_btn = QtWidgets.QPushButton("取消", footer_card)
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self.cancel_btn)

        self.save_btn = QtWidgets.QPushButton("保存更改", footer_card)
        self.save_btn.setObjectName("primaryAction")
        self.save_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.save_btn.setAutoDefault(False)
        self.save_btn.clicked.connect(self._save_config)
        footer.addWidget(self.save_btn)
        return footer_card

    def _build_notice_bar(self) -> QtWidgets.QFrame:
        self.notice_bar = QtWidgets.QFrame(self)
        self.notice_bar.setObjectName("editorNoticeWarn")
        notice_layout = QtWidgets.QHBoxLayout(self.notice_bar)
        notice_layout.setContentsMargins(12, 8, 12, 8)
        notice_layout.setSpacing(8)
        self.notice_label = QtWidgets.QLabel("", self.notice_bar)
        self.notice_label.setObjectName("editorNoticeText")
        self.notice_label.setWordWrap(True)
        notice_layout.addWidget(self.notice_label, 1)
        self.notice_bar.setVisible(False)
        return self.notice_bar

    def _plugin_enabled_now(self) -> bool:
        manager = self.plugin_manager
        checker = getattr(manager, "is_plugin_enabled", None)
        if not callable(checker):
            return True
        try:
            return bool(checker(self.trigger))
        except Exception:
            return True

    def _sync_enable_switch(self):
        if not hasattr(self, "plugin_enable_switch"):
            return
        enabled = self._plugin_enabled_now()
        switch = self.plugin_enable_switch
        switch.blockSignals(True)
        switch.setChecked(enabled)
        switch.blockSignals(False)
        manager = self.plugin_manager
        can_toggle = callable(getattr(manager, "enable_plugin", None)) and callable(
            getattr(manager, "disable_plugin", None)
        )
        switch.setEnabled(can_toggle)
        if hasattr(self, "enable_state_label"):
            self.enable_state_label.setText("启用中" if enabled else "已停用")

    def _on_plugin_enable_toggled(self, checked: bool):
        manager = self.plugin_manager
        enable = getattr(manager, "enable_plugin", None)
        disable = getattr(manager, "disable_plugin", None)
        if not callable(enable) or not callable(disable):
            self._sync_enable_switch()
            return
        ok = False
        try:
            ok = bool(enable(self.trigger) if checked else disable(self.trigger))
        except Exception:
            ok = False
        if not ok:
            self._sync_enable_switch()
            self._show_notice("插件开关没有改成功", "warn")
            return
        self._sync_enable_switch()

    def _refresh_sidebar_summary(self):
        if not hasattr(self, "sidebar_mode_label"):
            return
        mode = ""
        if hasattr(self, "type_combo"):
            mode = self.type_combo.currentText().strip()
        timeout = self.timeout_input.value() if hasattr(self, "timeout_input") else 0
        alias_count = len(self.aliases_input._items) if hasattr(self, "aliases_input") else 0
        self.sidebar_mode_label.setText(
            f"{mode or 'react'}  ·  {timeout} 秒  ·  {alias_count} 个别名"
        )

    def _build_header_icon(self) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(52, 52)
        icon_path = None
        getter = getattr(self.plugin_manager, "get_plugin_icon_path", None)
        if callable(getter):
            try:
                icon_path = getter(self.trigger)
            except Exception:
                icon_path = None
        pixmap = QtGui.QPixmap()
        if icon_path:
            pixmap = resolve_icon(icon_path).pixmap(34, 34)
        if not pixmap.isNull():
            label.setPixmap(pixmap)
            label.setStyleSheet("background: transparent; border-radius: 16px;")
            self._header_icon_is_glyph = False
            return label
        self._header_icon_is_glyph = True
        self._paint_header_glyph(label)
        return label

    def _paint_header_glyph(self, label: QtWidgets.QLabel):
        glyph, icon_bg, icon_fg = _group_icon_spec(self._header_group, get_ui_palette())
        label.setText(glyph)
        tint = QtGui.QColor(icon_bg)
        if not tint.isValid():
            tint = QtGui.QColor("#EEF2FF")
        top = tint.lighter(112).name()
        label.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {top}, stop:1 {icon_bg});"
            f"color: {icon_fg}; border-radius: 16px; font-size: 20px; font-weight: 700;"
            f"border: 1px solid {hex_to_rgba('#FFFFFF', 0.72)};"
        )

    def _make_scroll(self) -> QtWidgets.QScrollArea:
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        return scroll

    def _build_params_page(self) -> QtWidgets.QScrollArea:
        self.settings_scroll = self._make_scroll()
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(2, 4, 8, 8)
        page_layout.setSpacing(10)

        self.params_empty = QtWidgets.QFrame(page)
        self.params_empty.setObjectName("editorEmptyCard")
        empty_layout = QtWidgets.QVBoxLayout(self.params_empty)
        empty_layout.setContentsMargins(20, 28, 20, 28)
        empty_layout.setSpacing(6)
        empty_title = QtWidgets.QLabel("这个能力没有单独的功能参数", self.params_empty)
        empty_title.setObjectName("sectionHeaderTitle")
        empty_title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_body = QtWidgets.QLabel("触发词、超时和谁可以调用它，在「触发与权限」。", self.params_empty)
        empty_body.setObjectName("settingFieldDesc")
        empty_body.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_body.setWordWrap(True)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_body)
        self.params_empty.setVisible(False)
        page_layout.addWidget(self.params_empty)

        self.custom_settings_card = QtWidgets.QWidget(page)
        custom_layout = QtWidgets.QVBoxLayout(self.custom_settings_card)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(8)
        self.settings_container = QtWidgets.QWidget(self.custom_settings_card)
        self.settings_layout = QtWidgets.QVBoxLayout(self.settings_container)
        self.settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_layout.setSpacing(10)
        self._param_groups = {}

        self.param_filter_empty = QtWidgets.QFrame(self.settings_container)
        self.param_filter_empty.setObjectName("editorEmptyCard")
        filter_layout = QtWidgets.QVBoxLayout(self.param_filter_empty)
        filter_layout.setContentsMargins(16, 18, 16, 18)
        filter_layout.setSpacing(8)
        filter_label = QtWidgets.QLabel("没有匹配的参数", self.param_filter_empty)
        filter_label.setObjectName("sectionHeaderTitle")
        filter_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        clear_btn = QtWidgets.QPushButton("清空筛选", self.param_filter_empty)
        clear_btn.setObjectName("path_btn")
        clear_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        clear_btn.setAutoDefault(False)
        clear_btn.clicked.connect(self.param_search.clear)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(clear_btn, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.param_filter_empty.setVisible(False)
        self.settings_layout.addWidget(self.param_filter_empty)

        self.hidden_hint_label = QtWidgets.QLabel("", self.settings_container)
        self.hidden_hint_label.setObjectName("settingFieldDesc")
        self.hidden_hint_label.setWordWrap(True)
        self.hidden_hint_label.setVisible(False)
        self.settings_layout.addWidget(self.hidden_hint_label)
        custom_layout.addWidget(self.settings_container)
        page_layout.addWidget(self.custom_settings_card)
        page_layout.addStretch(1)
        self.settings_scroll.setWidget(page)
        return self.settings_scroll

    def _build_access_page(self) -> QtWidgets.QScrollArea:
        scroll = self._make_scroll()
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(2, 4, 8, 8)
        page_layout.setSpacing(12)

        aliases_card = QtWidgets.QFrame(page)
        aliases_card.setObjectName("editorGroupCard")
        aliases_layout = QtWidgets.QVBoxLayout(aliases_card)
        aliases_layout.setContentsMargins(14, 12, 14, 14)
        aliases_layout.setSpacing(10)
        aliases_head = QtWidgets.QHBoxLayout()
        aliases_head.setSpacing(8)
        aliases_title = QtWidgets.QLabel("快捷触发词", aliases_card)
        aliases_title.setObjectName("sectionHeaderTitle")
        aliases_head.addWidget(aliases_title)
        self.alias_count_badge = QtWidgets.QLabel("0 个", aliases_card)
        self.alias_count_badge.setObjectName("countBadge")
        aliases_head.addWidget(self.alias_count_badge)
        aliases_head.addStretch(1)
        aliases_layout.addLayout(aliases_head)
        aliases_hint = QtWidgets.QLabel("命中其中任意一个，都会唤起这个能力", aliases_card)
        aliases_hint.setObjectName("settingFieldDesc")
        aliases_layout.addWidget(aliases_hint)
        self.aliases_input = TagChipEditor(
            aliases_card,
            placeholder="输入触发词，回车添加。逗号可以一次加多个",
            empty_text="还没有额外触发词，目前只认主触发词",
            on_change=self._on_alias_change,
        )
        aliases_layout.addWidget(self.aliases_input)
        page_layout.addWidget(aliases_card)

        limits = QtWidgets.QFrame(page)
        limits.setObjectName("editorGroupCard")
        limits_layout = QtWidgets.QVBoxLayout(limits)
        limits_layout.setContentsMargins(6, 8, 6, 8)
        limits_layout.setSpacing(0)
        limits_title = QtWidgets.QLabel("调用限制", limits)
        limits_title.setObjectName("groupHeader")
        limits_layout.addWidget(limits_title)

        self.timeout_input = QtWidgets.QSpinBox(limits)
        self.timeout_input.setRange(1, 300)
        self.timeout_input.setSuffix(" 秒")
        timeout_stepper = _wrap_spin_stepper(self.timeout_input, limits)
        timeout_stepper.setFixedWidth(168)
        limits_layout.addWidget(
            self._inset_row(
                limits,
                "超时限制",
                "单次调用超过这个时间会停下来",
                timeout_stepper,
            )
        )
        self.allow_local_checkbox = _ToggleSwitch(True)
        limits_layout.addWidget(
            self._inset_row(
                limits,
                "允许桌面本地触发",
                "麦克风、桌面聊天框和屏幕插话都可以用它",
                self.allow_local_checkbox,
            )
        )
        page_layout.addWidget(limits)

        qq_card = QtWidgets.QFrame(page)
        qq_card.setObjectName("editorGroupCard")
        qq_layout = QtWidgets.QVBoxLayout(qq_card)
        qq_layout.setContentsMargins(6, 8, 6, 10)
        qq_layout.setSpacing(0)
        qq_title = QtWidgets.QLabel("QQ 通道", qq_card)
        qq_title.setObjectName("groupHeader")
        qq_layout.addWidget(qq_title)
        self.allow_remote_qq_checkbox = _ToggleSwitch(True)
        qq_layout.addWidget(
            self._inset_row(
                qq_card,
                "允许 QQ 消息触发",
                "关掉之后，下面三项一起停用",
                self.allow_remote_qq_checkbox,
            )
        )
        self.qq_perm_row = QtWidgets.QFrame(qq_card)
        self.qq_perm_row.setObjectName("permSubCard")
        pills = QtWidgets.QHBoxLayout(self.qq_perm_row)
        pills.setContentsMargins(12, 10, 12, 10)
        pills.setSpacing(16)
        self.allow_qq_owner_checkbox = _ToggleSwitch(True)
        self.allow_qq_others_checkbox = _ToggleSwitch(False)
        self.allow_group_without_at_checkbox = _ToggleSwitch(False)
        for label, switch in (
            ("主人", self.allow_qq_owner_checkbox),
            ("其他联系人", self.allow_qq_others_checkbox),
            ("群聊免 @", self.allow_group_without_at_checkbox),
        ):
            pills.addWidget(self._mini_switch(self.qq_perm_row, label, switch))
        pills.addStretch(1)
        qq_layout.addWidget(self.qq_perm_row)
        self.allow_remote_qq_checkbox.toggled.connect(self._sync_access_control_inputs)
        page_layout.addWidget(qq_card)
        page_layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _build_meta_page(self) -> QtWidgets.QScrollArea:
        scroll = self._make_scroll()
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(2, 4, 8, 8)
        page_layout.setSpacing(12)

        card = QtWidgets.QFrame(page)
        card.setObjectName("editorGroupCard")
        body = QtWidgets.QVBoxLayout(card)
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(10)

        intro = QtWidgets.QLabel("展示给人和模型看的信息", card)
        intro.setObjectName("groupHeader")
        body.addWidget(intro)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        self.name_input = QtWidgets.QLineEdit(card)
        self.name_input.setFixedHeight(34)
        self.name_input.textChanged.connect(self._sync_header_name)
        row.addLayout(self._field_block(card, "显示名称", self.name_input), 1)
        self.example_input = QtWidgets.QLineEdit(card)
        self.example_input.setPlaceholderText("示例调用参数")
        self.example_input.setFixedHeight(34)
        row.addLayout(self._field_block(card, "示例参数", self.example_input), 1)
        body.addLayout(row)

        desc_label = QtWidgets.QLabel("功能描述", card)
        desc_label.setObjectName("settingFieldTitle")
        desc_hint = QtWidgets.QLabel("写在能力卡片上，也供大模型判断什么时候该用它", card)
        desc_hint.setObjectName("settingFieldDesc")
        self.desc_input = QtWidgets.QTextEdit(card)
        self.desc_input.setMaximumHeight(96)
        self.desc_input.setPlaceholderText("这段话会写在能力卡片上")
        self.desc_input.textChanged.connect(
            lambda: self._sync_header_desc(self.desc_input.toPlainText())
        )
        body.addWidget(desc_label)
        body.addWidget(desc_hint)
        body.addWidget(self.desc_input)
        page_layout.addWidget(card)

        proto = QtWidgets.QFrame(page)
        proto.setObjectName("editorGroupCard")
        proto_layout = QtWidgets.QVBoxLayout(proto)
        proto_layout.setContentsMargins(14, 12, 14, 14)
        proto_layout.setSpacing(8)
        proto_head = QtWidgets.QHBoxLayout()
        proto_title = QtWidgets.QLabel("底层标识", proto)
        proto_title.setObjectName("sectionHeaderTitle")
        proto_head.addWidget(proto_title)
        proto_head.addStretch(1)
        self.unlock_meta_checkbox = _ToggleSwitch(False)
        proto_head.addWidget(self._mini_switch(proto, "解锁修改", self.unlock_meta_checkbox))
        proto_layout.addLayout(proto_head)
        proto_note = QtWidgets.QLabel(
            "主触发词和执行模式平时锁定。改错会导致这个能力对不上。", proto
        )
        proto_note.setObjectName("settingFieldDesc")
        proto_note.setWordWrap(True)
        proto_layout.addWidget(proto_note)
        proto_row = QtWidgets.QHBoxLayout()
        proto_row.setSpacing(12)
        self.trigger_input = QtWidgets.QLineEdit(proto)
        self.trigger_input.setFixedHeight(34)
        proto_row.addLayout(self._field_block(proto, "主触发词", self.trigger_input), 1)
        self.type_combo = QtWidgets.QComboBox(proto)
        self.type_combo.addItems(["react", "direct", "observe", "delegate"])
        self.type_combo.setFixedHeight(34)
        proto_row.addLayout(self._field_block(proto, "执行模式", self.type_combo), 1)
        proto_layout.addLayout(proto_row)
        self.unlock_meta_checkbox.toggled.connect(self._on_toggle_unlock_meta)
        page_layout.addWidget(proto)
        page_layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _inset_row(self, parent, title, detail, control):
        row = QtWidgets.QFrame(parent)
        row.setObjectName("settingMiniCard")
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        layout.addLayout(self._switch_copy(row, title, detail), 1)
        layout.addWidget(
            control,
            0,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
        )
        return row

    def _field_block(self, parent, title, widget):
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(4)
        label = QtWidgets.QLabel(title, parent)
        label.setObjectName("settingFieldTitle")
        col.addWidget(label)
        col.addWidget(widget)
        return col

    def _labeled_switch_card(self, parent, title, detail, switch):
        card = QtWidgets.QFrame(parent)
        card.setObjectName("editorSectionCard")
        layout = QtWidgets.QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        layout.addLayout(self._switch_copy(card, title, detail), 1)
        layout.addWidget(
            switch,
            0,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
        )
        return card

    def _inline_switch_row(self, parent, title, detail, switch):
        row = QtWidgets.QWidget(parent)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(self._switch_copy(row, title, detail), 1)
        layout.addWidget(
            switch,
            0,
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
        )
        return row

    def _switch_copy(self, parent, title, detail):
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(2)
        title_label = QtWidgets.QLabel(title, parent)
        title_label.setObjectName("settingFieldTitle")
        detail_label = QtWidgets.QLabel(detail, parent)
        detail_label.setObjectName("settingFieldDesc")
        detail_label.setWordWrap(True)
        col.addWidget(title_label)
        col.addWidget(detail_label)
        return col

    def _mini_switch(self, parent, text, switch):
        wrap = QtWidgets.QWidget(parent)
        row = QtWidgets.QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QtWidgets.QLabel(text, wrap)
        label.setObjectName("settingFieldDesc")
        row.addWidget(label)
        row.addWidget(switch)
        return wrap

    def _wire_form_watchers(self):
        for widget in (
            self.name_input,
            self.trigger_input,
            self.type_combo,
            self.example_input,
            self.desc_input,
            self.timeout_input,
            self.allow_local_checkbox,
            self.allow_remote_qq_checkbox,
            self.allow_qq_owner_checkbox,
            self.allow_qq_others_checkbox,
            self.allow_group_without_at_checkbox,
            self.aliases_input,
        ):
            self._watch_editor(widget)

    def _select_section(self, section: str):
        order = {"params": 0, "access": 1, "meta": 2}
        section = section if section in order else "params"
        self._active_section = section
        self._section_chosen = True
        if hasattr(self, "section_stack"):
            self.section_stack.setCurrentIndex(order[section])
        for key, button in self._section_buttons.items():
            if key != section:
                button.setChecked(False)
        chosen = self._section_buttons.get(section)
        if chosen is not None:
            chosen.setChecked(True)
        for button in self._section_buttons.values():
            button.update()
        title, subtitle = self._SECTION_COPY.get(section, ("配置", ""))
        if hasattr(self, "stage_title"):
            self.stage_title.setText(title)
            self.stage_subtitle.setText(subtitle)
        show_search = section == "params" and self._visible_setting_count >= 5
        if hasattr(self, "param_search"):
            self.param_search.setVisible(show_search)
        if hasattr(self, "tab_widget"):
            self.tab_widget.setCurrentIndex(0 if section == "params" else 1)

    def _sync_header_name(self, text: str):
        self.header_name_label.setText(str(text or "").strip() or str(self.trigger or "能力"))

    def _sync_header_desc(self, text: str):
        cleaned = " ".join(str(text or "").split())
        self.header_desc_label.setText(cleaned or "暂无能力说明")

    def _on_alias_change(self, count: int):
        self._update_alias_count_badge(count)
        access_button = self._section_buttons.get("access")
        if isinstance(access_button, _EditorNavButton):
            access_button.set_badge(str(count) if count else "")
            access_button.set_subtitle(f"{count} 个触发词" if count else "触发词和权限")
        self._refresh_sidebar_summary()
        self._on_form_edited()

    def _sync_param_chrome(self):
        count = int(getattr(self, "_visible_setting_count", 0) or 0)
        button = self._section_buttons.get("params")
        if isinstance(button, _EditorNavButton):
            button.set_badge(str(count) if count else "")
            button.set_subtitle(f"{count} 项可配" if count else "没有单独参数")
        elif button is not None:
            button.setText(f"功能参数  {count}" if count else "功能参数")
        if hasattr(self, "params_empty"):
            self.params_empty.setVisible(count == 0)
        if hasattr(self, "custom_settings_card"):
            self.custom_settings_card.setVisible(count > 0)
        if hasattr(self, "param_search"):
            self.param_search.setVisible(
                count >= 5 and getattr(self, "_active_section", "params") == "params"
            )
        self._reflow_settings()

    _PARAM_GROUP_ORDER = ("switch", "value", "secret", "text")
    _PARAM_GROUP_TITLES = {
        "switch": "开关",
        "value": "数值与选项",
        "secret": "密钥",
        "text": "文本与地址",
    }

    def _ensure_param_group(self, name: str) -> QtWidgets.QFrame:
        card = self._param_groups.get(name)
        if card is not None:
            return card
        card = QtWidgets.QFrame(self.settings_container)
        card.setObjectName("editorGroupCard")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(0)
        title = QtWidgets.QLabel(self._PARAM_GROUP_TITLES.get(name, name), card)
        title.setObjectName("groupHeader")
        lay.addWidget(title)
        if name == "secret":
            note = QtWidgets.QLabel("只保存在这台电脑的密钥库里，配置文件保持为空", card)
            note.setObjectName("settingFieldNote")
            note.setWordWrap(True)
            lay.addWidget(note)
        card.hide()
        self._param_groups[name] = card
        return card

    def _reflow_settings(self, *_args):
        if not hasattr(self, "settings_layout"):
            return
        query = ""
        if hasattr(self, "param_search"):
            query = self.param_search.text().strip().lower()
        layout = self.settings_layout
        while layout.count():
            layout.takeAt(0)
        grouped = {name: [] for name in self._PARAM_GROUP_ORDER}
        blocks = []
        for entry in self._param_entries:
            matched = (not query) or query in entry["search"]
            entry["card"].setVisible(matched)
            if not matched:
                continue
            if entry.get("group") == "block":
                blocks.append(entry)
            else:
                grouped.setdefault(entry.get("group") or "text", []).append(entry)
        visible_any = False
        for name in self._PARAM_GROUP_ORDER:
            rows = grouped.get(name) or []
            card = self._ensure_param_group(name)
            group_layout = card.layout()
            keep = 2 if name == "secret" else 1
            while group_layout.count() > keep:
                group_layout.takeAt(keep)
            if not rows:
                card.hide()
                continue
            for entry in rows:
                group_layout.addWidget(entry["card"])
            card.show()
            layout.addWidget(card)
            visible_any = True
        for entry in blocks:
            layout.addWidget(entry["card"])
            visible_any = True
        if hasattr(self, "param_filter_empty"):
            layout.addWidget(self.param_filter_empty)
            filtering = bool(query) and bool(self._param_entries)
            self.param_filter_empty.setVisible(filtering and not visible_any)
        if hasattr(self, "hidden_hint_label"):
            layout.addWidget(self.hidden_hint_label)

    def _show_notice(self, text: str, kind: str = "warn"):
        self.notice_label.setText(str(text or ""))
        self.notice_bar.setObjectName(
            "editorNoticeInfo" if kind == "info" else "editorNoticeWarn"
        )
        self.notice_bar.style().unpolish(self.notice_bar)
        self.notice_bar.style().polish(self.notice_bar)
        self.notice_bar.setVisible(True)

    def _hide_notice(self):
        if hasattr(self, "notice_bar"):
            self.notice_bar.setVisible(False)

    def _reject_form(self, message: str, section: str = "params", key: str | None = None):
        self._select_section(section)
        if key:
            self._highlight_setting(key)
        self._show_notice(message, "warn")

    def _highlight_setting(self, key: str):
        target = None
        for entry in self._param_entries:
            warn = entry["key"] == key
            entry["card"].setObjectName("settingMiniCardWarn" if warn else "settingMiniCard")
            entry["card"].style().unpolish(entry["card"])
            entry["card"].style().polish(entry["card"])
            if warn:
                target = entry["card"]
        if target is not None and hasattr(self, "settings_scroll"):
            self.settings_scroll.ensureWidgetVisible(target)

    def _on_form_edited(self, *_args):
        if getattr(self, "_suspend_dirty", False):
            return
        self._hide_notice()
        self._refresh_sidebar_summary()
        self._refresh_dirty_label()

    def _watch_editor(self, widget):
        if widget is None or getattr(widget, "_dirty_watched", False):
            return
        try:
            widget._dirty_watched = True
        except Exception:
            return
        if isinstance(widget, TagChipEditor):
            widget.input_edit.textChanged.connect(self._on_form_edited)
            return
        if isinstance(widget, _ToggleSwitch):
            widget.toggled.connect(self._on_form_edited)
        elif isinstance(widget, QtWidgets.QLineEdit):
            widget.textChanged.connect(self._on_form_edited)
        elif isinstance(widget, QtWidgets.QTextEdit):
            widget.textChanged.connect(self._on_form_edited)
        elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            widget.valueChanged.connect(self._on_form_edited)
        elif isinstance(widget, QtWidgets.QComboBox):
            widget.currentTextChanged.connect(self._on_form_edited)

    def _watch_field(self, field_type, widget):
        del field_type
        if isinstance(widget, list):
            for item in widget:
                if isinstance(item, dict):
                    self._watch_editor(item.get("alias"))
                    self._watch_editor(item.get("path"))
                else:
                    self._watch_editor(item)
            return
        if isinstance(widget, TagChipEditor):
            widget._on_change = lambda _count: self._on_form_edited()
            self._watch_editor(widget)
            return
        self._watch_editor(widget)

    def _snapshot_value(self, field_type, widget):
        if field_type == "list" and isinstance(widget, TagChipEditor):
            return (tuple(widget._items), widget.input_edit.text())
        if field_type in {"bool", "boolean"}:
            return bool(widget.isChecked())
        if field_type == "number":
            return widget.value()
        if field_type == "choice":
            return widget.currentText()
        if field_type == "model_queue" and isinstance(widget, dict):
            return tuple(widget.get("selected") or [])
        if field_type == "path_list" and isinstance(widget, list):
            return tuple(item.text() for item in widget)
        if field_type == "app_list" and isinstance(widget, list):
            return tuple(
                (item["alias"].text(), item["path"].text())
                for item in widget
                if isinstance(item, dict)
            )
        if isinstance(widget, QtWidgets.QTextEdit):
            return widget.toPlainText()
        if hasattr(widget, "text"):
            return widget.text()
        return None

    def _form_fingerprint(self):
        settings = tuple(
            (key, field_type, self._snapshot_value(field_type, widget))
            for key, (field_type, widget) in self.config_fields.items()
        )
        alias_pending = ""
        alias_items = ()
        if hasattr(self, "aliases_input"):
            alias_items = tuple(self.aliases_input._items)
            alias_pending = self.aliases_input.input_edit.text()
        return (
            self.name_input.text(),
            self.trigger_input.text(),
            self.type_combo.currentText(),
            alias_items,
            alias_pending,
            self.desc_input.toPlainText(),
            self.example_input.text(),
            self.timeout_input.value(),
            self.allow_local_checkbox.isChecked(),
            self.allow_remote_qq_checkbox.isChecked(),
            self.allow_qq_owner_checkbox.isChecked(),
            self.allow_qq_others_checkbox.isChecked(),
            self.allow_group_without_at_checkbox.isChecked(),
            settings,
        )

    def _refresh_dirty_label(self):
        if not hasattr(self, "dirty_label"):
            return
        dirty = self._form_fingerprint() != self._baseline
        self.dirty_label.setText("有未保存的更改" if dirty else "✓  已与磁盘同步")
        self.dirty_label.setObjectName("editorDirtyOn" if dirty else "editorDirtyOff")
        self.dirty_label.style().unpolish(self.dirty_label)
        self.dirty_label.style().polish(self.dirty_label)

    def _finish_load_state(self):
        self._suspend_dirty = False
        self._baseline = self._form_fingerprint()
        self._refresh_dirty_label()
        self._sync_param_chrome()
        self._sync_enable_switch()
        self._refresh_sidebar_summary()
        if not self._section_chosen:
            self._select_section("params" if self._visible_setting_count else "access")
        else:
            self._select_section(self._active_section)

    def refresh_theme(self) -> None:
        self._apply_editor_theme()
        if getattr(self, "_header_icon_is_glyph", False) and hasattr(self, "header_icon"):
            self._paint_header_glyph(self.header_icon)
        if hasattr(self, "header_group_badge"):
            self.header_group_badge.setStyleSheet(
                PluginManagerDialog._group_badge_style(self._header_group)
            )
        self._restyle_queue_lists()
        for chip in self.findChildren(TagChipEditor):
            chip.refresh_theme()
        for switch in self.findChildren(_ToggleSwitch):
            switch.update()
        for button in getattr(self, "_section_buttons", {}).values():
            button.update()

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

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        palette = get_ui_palette()
        dark = _is_dark_palette(palette)
        app_bg = QtGui.QColor(palette.get("bg_app", "#E7EDF5"))
        accent = QtGui.QColor(palette.get("accent", "#0A84FF"))
        rect = QtCore.QRectF(self.rect())
        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        if dark:
            top = QtGui.QColor(app_bg).lighter(118)
            bottom = QtGui.QColor(app_bg).darker(112)
        else:
            top = QtGui.QColor(app_bg).lighter(108)
            bottom = QtGui.QColor(app_bg).darker(103)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillRect(rect, gradient)
        glow = QtGui.QRadialGradient(QtCore.QPointF(rect.width() * 0.12, 24), 340)
        glow_color = QtGui.QColor(accent)
        glow_color.setAlpha(26 if not dark else 34)
        glow.setColorAt(0.0, glow_color)
        glow.setColorAt(1.0, QtGui.QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QtCore.QRectF(-120, -160, 520, 380))
        painter.end()
        del event

    def _make_search_icon(self) -> QtGui.QIcon:
        palette = get_ui_palette()
        color = QtGui.QColor(palette.get("text_muted", "#8E8E93"))
        pixmap = QtGui.QPixmap(16, 16)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtGui.QPen(color, 1.6))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QtCore.QRectF(1.4, 1.4, 8.6, 8.6))
        painter.drawLine(QtCore.QPointF(8.8, 8.8), QtCore.QPointF(13.4, 13.4))
        painter.end()
        return QtGui.QIcon(pixmap)

    def _polish_surface(self, widget, blur=26, y=7, alpha=34):
        if widget is None:
            return
        palette = get_ui_palette()
        dark = _is_dark_palette(palette)
        effect = QtWidgets.QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur + (8 if dark else 0))
        effect.setOffset(0, y)
        if dark:
            effect.setColor(QtGui.QColor(0, 0, 0, 110))
        else:
            effect.setColor(QtGui.QColor(28, 52, 96, alpha))
        widget.setGraphicsEffect(effect)

    def _apply_editor_theme(self):
        palette = get_ui_palette()
        dark = _is_dark_palette(palette)
        card = palette.get("bg_card", "#FFFFFF")
        soft = palette.get("bg_soft", "#F4F5F7")
        border = palette.get("border", "#D8DBE0")
        accent = palette.get("accent", "#0A84FF")
        accent_hover = palette.get("accent_hover", "#0071E3")
        accent_soft = palette.get("accent_soft", "#E8F1FF")
        text = palette.get("text_primary", "#1D1D1F")
        secondary = palette.get("text_secondary", "#6E6E73")
        muted = palette.get("text_muted", "#8E8E93")
        danger = palette.get("danger", "#FF453A")
        warning = palette.get("warning", "#F59E0B")
        app_bg = palette.get("bg_app", "#EBECEF")
        checked = accent if dark else accent_hover
        self.setStyleSheet(
            get_tool_dialog_styles()
            + f"""
            QDialog {{
                background: transparent;
            }}
            QLabel, QCheckBox {{
                background: transparent;
                color: {text};
            }}
            QFrame#editorSidebarCard, QFrame#editorStageCard, QFrame#editorFooterCard {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {hex_to_rgba(card, 0.97)},
                    stop:1 {hex_to_rgba(card, 0.90)});
                border: 1px solid {hex_to_rgba("#FFFFFF" if not dark else border, 0.72 if not dark else 0.38)};
                border-radius: 20px;
            }}
            QFrame#editorGroupCard, QFrame#editorSectionCard {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {hex_to_rgba(card, 0.99)},
                    stop:1 {hex_to_rgba(soft, 0.55)});
                border: 1px solid {hex_to_rgba(border, 0.55)};
                border-radius: 16px;
            }}
            QFrame#editorEnableRow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {hex_to_rgba(card, 0.96)},
                    stop:1 {hex_to_rgba(soft, 0.92)});
                border: 1px solid {hex_to_rgba(border, 0.35)};
                border-radius: 14px;
            }}
            QFrame#editorActionWell {{
                background: transparent;
                border: none;
            }}
            QFrame#settingMiniCard {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {hex_to_rgba(border, 0.9)};
                border-radius: 0px;
            }}
            QFrame#settingMiniCard:hover {{
                background: {hex_to_rgba(accent, 0.05)};
            }}
            QFrame#settingMiniCardWarn {{
                background: {hex_to_rgba(warning, 0.12)};
                border: 1px solid {warning};
                border-radius: 10px;
            }}
            QLineEdit, QComboBox, QTextEdit {{
                background: {card};
                border: 1px solid {hex_to_rgba(border, 0.75)};
                border-radius: 12px;
                min-height: 32px;
                padding: 4px 10px;
                color: {text};
            }}
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
                border: 1.5px solid {accent};
                background: {card};
            }}
            QLineEdit#pluginSearchInput {{
                background: {card};
                color: {text};
                border: 1px solid {hex_to_rgba(border, 0.7)};
                border-radius: 14px;
                padding: 4px 12px;
                font-size: 12px;
                min-height: 36px;
            }}
            QLineEdit#pluginSearchInput:focus {{
                border: 1.5px solid {accent};
            }}
            QFrame#editorEmptyCard {{
                background: {hex_to_rgba(soft, 0.72)};
                border: 1px dashed {border};
                border-radius: 12px;
            }}
            QFrame#editorNoticeWarn {{
                background: {hex_to_rgba(warning, 0.14)};
                border: 1px solid {hex_to_rgba(warning, 0.45)};
                border-radius: 10px;
            }}
            QFrame#editorNoticeInfo {{
                background: {accent_soft};
                border: 1px solid {hex_to_rgba(accent, 0.4)};
                border-radius: 10px;
            }}
            QLabel#editorNoticeText {{
                color: {text};
                font-size: 12px;
                background: transparent;
            }}
            QLabel#editorDirtyOn {{
                color: {warning};
                background: {hex_to_rgba(warning, 0.13)};
                border: 1px solid {hex_to_rgba(warning, 0.32)};
                border-radius: 12px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#editorDirtyOff {{
                color: {muted};
                background: transparent;
                border: none;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#stageKicker, QLabel#groupHeader {{
                color: {muted};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.6px;
                background: transparent;
            }}
            QLabel#groupHeader {{
                padding: 8px 10px 2px 10px;
            }}
            QLabel#stageTitle {{
                color: {text};
                font-size: 20px;
                font-weight: 800;
                background: transparent;
            }}
            QLabel#stageSubtitle, QLabel#editorModeLine {{
                color: {muted};
                font-size: 12px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#countBadge {{
                background: {accent_soft};
                color: {checked};
                border: 1px solid {hex_to_rgba(accent, 0.35)};
                padding: 1px 6px;
                border-radius: 8px;
                font-size: 10px;
                font-weight: 600;
            }}
            QLabel#settingFieldTitle, QLabel#sectionHeaderTitle {{
                color: {text};
                font-weight: 700;
                background: transparent;
            }}
            QLabel#settingFieldTitle {{
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#sectionHeaderTitle {{
                font-size: 13px;
            }}
            QLabel#settingFieldDesc, QLabel#tagEmptyLabel {{
                color: {muted};
                font-size: 11px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#settingFieldWarn {{
                color: {warning};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#settingFieldNote {{
                color: {checked};
                font-size: 11px;
                background: transparent;
                padding: 0px 8px 6px 8px;
            }}
            QFrame#permSubCard {{
                background: {soft};
                border: none;
                border-radius: 12px;
                margin: 4px 8px 6px 8px;
            }}
            QFrame#tagChipBox {{
                background: {soft};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QFrame#tagInputDivider {{
                background: {border};
                border: none;
                min-height: 1px;
                max-height: 1px;
            }}
            QLineEdit#tagInlineInput {{
                background: transparent;
                border: none;
                padding: 0px 4px;
                min-height: 26px;
                font-size: 12px;
            }}
            QLineEdit#tagInlineInput:focus {{
                border: none;
                background: transparent;
            }}
            QPushButton#tagAddPillBtn {{
                background: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 9px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: 700;
                min-height: 28px;
            }}
            QPushButton#tagAddPillBtn:hover {{
                background: {accent_hover};
            }}
            QFrame#tagChip {{
                background: {accent_soft};
                border: none;
                border-radius: 11px;
            }}
            QLabel#tagChipText {{
                color: {checked};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            QFrame#stepperShell {{
                background: {card};
                border: 1px solid {hex_to_rgba(border, 0.65)};
                border-radius: 12px;
            }}
            QPushButton#stepperBtn {{
                background: {soft};
                color: {secondary};
                border: none;
                border-radius: 8px;
                padding: 0px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                font-size: 16px;
                font-weight: 700;
            }}
            QPushButton#stepperBtn:hover {{
                background: {accent_soft};
                color: {checked};
            }}
            QSpinBox#stepperSpin, QDoubleSpinBox#stepperSpin {{
                background: transparent;
                border: none;
                padding: 0px 2px;
                min-height: 26px;
                font-size: 13px;
                font-weight: 700;
                color: {text};
            }}
            QLabel#editorTriggerBadge {{
                background: {soft};
                color: {secondary};
                border: 1px solid {border};
                padding: 1px 7px;
                border-radius: 6px;
                font-size: 10px;
                font-weight: 600;
            }}
            QLabel#editorHeaderDesc {{
                color: {secondary};
                font-size: 12px;
            }}
            QPushButton#tagChipRemoveBtn {{
                background: transparent;
                color: {muted};
                border: none;
                border-radius: 8px;
                padding: 0px;
                min-height: 16px;
                max-height: 16px;
                min-width: 16px;
                max-width: 16px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#tagChipRemoveBtn:hover {{
                background: {hex_to_rgba(danger, 0.16)};
                color: {danger};
            }}
            QLabel#sectionHeaderTitle {{
                font-size: 13px;
                font-weight: 700;
                color: {text};
            }}
            QPushButton#metaCollapseBtn {{
                background: transparent;
                border: none;
                text-align: left;
                padding: 2px 0px;
                font-size: 12px;
                font-weight: 700;
                color: {secondary};
            }}
            QPushButton#metaCollapseBtn:hover {{
                color: {checked};
            }}
            QPushButton#ghostPillBtn, QPushButton#path_btn, QPushButton#cancel_btn {{
                background: {card};
                color: {text};
                border: 1px solid {hex_to_rgba(border, 0.8)};
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
                min-height: 36px;
            }}
            QPushButton#ghostPillBtn:hover, QPushButton#path_btn:hover, QPushButton#cancel_btn:hover {{
                background: {accent_soft};
                color: {checked};
                border-color: {hex_to_rgba(accent, 0.4)};
            }}
            QPushButton#primaryAction {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {QtGui.QColor(accent).lighter(112).name()},
                    stop:1 {accent});
                color: #FFFFFF;
                border: none;
                min-height: 38px;
                padding: 6px 20px;
                border-radius: 12px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton#primaryAction:hover {{
                background: {accent_hover};
                color: #FFFFFF;
            }}
            QPushButton#dangerSmallBtn {{
                background-color: {hex_to_rgba(danger, 0.12)};
                color: {danger};
                border: 1px solid {hex_to_rgba(danger, 0.35)};
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#dangerSmallBtn:hover {{
                background-color: {danger};
                color: #FFFFFF;
            }}
            """
        )
        if hasattr(self, "header_name_label"):
            self.header_name_label.setStyleSheet(
                f"font-size: 16px; font-weight: 800; color: {text}; background: transparent;"
            )
            self.header_desc_label.setStyleSheet(
                f"color: {muted}; font-size: 12px; font-weight: 500; background: transparent;"
            )
            self.header_desc_label.setMaximumHeight(52)
        if hasattr(self, "_search_action"):
            self._search_action.setIcon(self._make_search_icon())
        self._polish_surface(getattr(self, "section_bar", None), blur=28, y=8, alpha=32)
        self._polish_surface(getattr(self, "stage_card", None), blur=28, y=8, alpha=30)
        self._polish_surface(getattr(self, "footer_card", None), blur=22, y=6, alpha=26)
        if hasattr(self, "save_btn"):
            accent_shadow = QtGui.QColor(accent)
            accent_shadow.setAlpha(88 if not dark else 70)
            glow = QtWidgets.QGraphicsDropShadowEffect(self.save_btn)
            glow.setBlurRadius(16)
            glow.setOffset(0, 4)
            glow.setColor(accent_shadow)
            self.save_btn.setGraphicsEffect(glow)
        if hasattr(self, "header_icon"):
            icon_shadow = QtWidgets.QGraphicsDropShadowEffect(self.header_icon)
            icon_shadow.setBlurRadius(16)
            icon_shadow.setOffset(0, 4)
            icon_shadow.setColor(QtGui.QColor(15, 23, 42, 40 if not dark else 90))
            self.header_icon.setGraphicsEffect(icon_shadow)
        self._restyle_queue_lists()
        for chip in self.findChildren(TagChipEditor):
            chip.refresh_theme()

    def _queue_list_qss(self) -> str:
        palette = get_ui_palette()
        card = palette.get("bg_card", "#FFFFFF")
        soft = palette.get("bg_soft", "#F4F5F7")
        border = palette.get("border", "#D8DBE0")
        text = palette.get("text_primary", "#1D1D1F")
        accent = palette.get("accent", "#0A84FF")
        accent_soft = palette.get("accent_soft", "#E8F1FF")
        return (
            f"QListWidget {{ background: {soft}; color: {text}; border: 1px solid {border};"
            " border-radius: 8px; padding: 4px; font-size: 12px; }}"
            "QListWidget::item { padding: 6px 8px; border-radius: 6px; }"
            f"QListWidget::item:hover {{ background: {accent_soft}; }}"
            f"QListWidget::item:selected {{ background: {accent}; color: #FFFFFF; }}"
            f"QListWidget {{ alternate-background-color: {card}; }}"
        )

    def _restyle_queue_lists(self):
        style = self._queue_list_qss()
        for widget in self.findChildren(QtWidgets.QListWidget):
            if widget.objectName() == "queueList":
                widget.setStyleSheet(style)

    def _open_readme(self):
        getter = getattr(self.plugin_manager, "get_plugin_readme_path", None)
        path = ""
        if callable(getter):
            try:
                path = str(getter(self.trigger) or "")
            except Exception:
                path = ""
        if not path or not os.path.exists(path):
            QtWidgets.QMessageBox.information(self, "使用说明", "这个能力还没有使用说明。")
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "使用说明", f"读不了说明文件：{exc}")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"使用说明 · {self.trigger}")
        dialog.resize(640, 520)
        dialog.setStyleSheet(get_tool_dialog_styles())
        layout = QtWidgets.QVBoxLayout(dialog)
        browser = QtWidgets.QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        if hasattr(browser, "setMarkdown"):
            browser.setMarkdown(text)
        else:
            browser.setPlainText(text)
        layout.addWidget(browser, 1)
        close_btn = QtWidgets.QPushButton("关闭", dialog)
        close_btn.clicked.connect(dialog.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
        dialog.exec()

    def _load_config(self):
        """加载插件配置"""
        self._suspend_dirty = True
        try:
            self._load_config_inner()
        finally:
            self._finish_load_state()

    def _load_config_inner(self):
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
            plugin_type = str(config.get("type") or "react")
            if self.type_combo.findText(plugin_type) < 0:
                self.type_combo.addItem(plugin_type)
            self.type_combo.setCurrentText(plugin_type)
            self.setWindowTitle(f"配置 · {name_text}")
            self._sync_header_name(name_text)
            self._sync_header_desc(desc_text)

            aliases = config.get("aliases", [])
            if isinstance(aliases, list):
                self.aliases_input.set_items(aliases)
            else:
                self.aliases_input.set_items([])

            self.desc_input.setPlainText(desc_text)
            self.example_input.setText(config.get("example_arg", ""))
            self.timeout_input.setValue(config.get("timeout_sec", 30))

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

    def _runtime_setting_value(self, key):
        manager = self.plugin_manager
        plugins = getattr(manager, "plugins", None)
        if not isinstance(plugins, dict):
            return None
        plugin = plugins.get(self.trigger)
        getter = getattr(plugin, "runtime_status", None)
        if not callable(getter):
            return None
        try:
            data = getter() or {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return data.get(key)

    def _load_custom_settings(self, config):
        """加载自定义配置项：每个配置项独立渲染为精致小卡片，短开关/数字自动排成双列网格"""
        self.config_fields.clear()
        for entry in list(self._param_entries):
            card = entry.get("card")
            if card is not None:
                card.hide()
                card.deleteLater()
        self._param_entries = []
        while self.settings_layout.count():
            self.settings_layout.takeAt(0)

        settings = config.get("settings", {})
        if not settings:
            self._visible_setting_count = 0
            self.hidden_hint_label.clear()
            self.hidden_hint_label.setVisible(False)
            self._sync_param_chrome()
            return

        hidden_count = 0
        visible_count = 0

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

            if str(setting_type).strip().lower() in {"status", "readonly"}:
                live = self._runtime_setting_value(key)
                if live not in (None, ""):
                    value = live

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
            stored = self.config_fields.get(key)
            if stored:
                self._watch_field(stored[0], stored[1])

            visible_count += 1
            field_type, _ = self.config_fields.get(key, (setting_type, None))
            is_block_widget = field_type in {
                "list",
                "path_list",
                "app_list",
                "model_queue",
            } or isinstance(widget, QtWidgets.QTextEdit)
            if is_block_widget:
                group_name = "block"
            elif field_type in {"boolean", "bool"}:
                group_name = "switch"
            elif field_type in {"number", "choice"}:
                group_name = "value"
            elif field_type == "secret":
                group_name = "secret"
            else:
                group_name = "text"

            mini_card = QtWidgets.QFrame(self.settings_container)
            mini_card.setObjectName(
                "editorGroupCard" if is_block_widget else "settingMiniCard"
            )

            if is_block_widget:
                card_layout = QtWidgets.QVBoxLayout(mini_card)
                card_layout.setContentsMargins(14, 12, 14, 12)
                card_layout.setSpacing(6)

                lbl_title = QtWidgets.QLabel(str(label), mini_card)
                lbl_title.setObjectName("settingFieldTitle")
                lbl_title.setWordWrap(True)
                card_layout.addWidget(lbl_title)

                if description:
                    desc_label = QtWidgets.QLabel(str(description), mini_card)
                    desc_label.setObjectName("settingFieldDesc")
                    desc_label.setWordWrap(True)
                    card_layout.addWidget(desc_label)

                if validation_error:
                    warn_label = QtWidgets.QLabel(f"格式提醒：{validation_error}", mini_card)
                    warn_label.setObjectName("settingFieldWarn")
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
                lbl_title.setObjectName("settingFieldTitle")
                lbl_title.setWordWrap(True)
                left_col.addWidget(lbl_title)

                if description:
                    desc_label = QtWidgets.QLabel(str(description), mini_card)
                    desc_label.setObjectName("settingFieldDesc")
                    desc_label.setWordWrap(True)
                    left_col.addWidget(desc_label)

                if validation_error:
                    warn_label = QtWidgets.QLabel(f"格式提醒：{validation_error}", mini_card)
                    warn_label.setObjectName("settingFieldWarn")
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

            mini_card.setVisible(False)
            self._param_entries.append(
                {
                    "key": str(key),
                    "label": str(label),
                    "description": str(description or ""),
                    "card": mini_card,
                    "half": False,
                    "group": group_name,
                    "search": " ".join(
                        [str(key), str(label or ""), str(description or "")]
                    ).lower(),
                }
            )

        self._visible_setting_count = visible_count
        if hidden_count:
            self.hidden_hint_label.setText(
                "有些内部参数已收起，保存时会原样保留。请用这个能力自己的页面修改。"
            )
            self.hidden_hint_label.setVisible(True)
        else:
            self.hidden_hint_label.clear()
            self.hidden_hint_label.setVisible(False)
        self._sync_param_chrome()

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
        self._watch_editor(path_input)
        self._on_form_edited()
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
        self._watch_editor(alias_input)
        self._watch_editor(path_input)
        self._on_form_edited()
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

        elif setting_type in {"status", "readonly"}:
            line_edit = QtWidgets.QLineEdit()
            line_edit.setReadOnly(True)
            line_edit.setText(str(value) if value is not None else "")
            line_edit.setPlaceholderText("还没有")
            line_edit.setFixedHeight(28)
            self.config_fields[key] = ("status", line_edit)
            widget = line_edit

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
                placeholder = ""
                if isinstance(setting_info, dict):
                    placeholder = str(setting_info.get("placeholder") or "").strip()
                line_edit.setPlaceholderText(placeholder or "请输入...")
                line_edit.setFixedHeight(28)
                self.config_fields[key] = ("string", line_edit)
                widget = line_edit
            else:
                text_edit = QtWidgets.QTextEdit()
                text_edit.setMaximumHeight(68)
                placeholder = ""
                if isinstance(setting_info, dict):
                    placeholder = str(setting_info.get("placeholder") or "").strip()
                text_edit.setPlaceholderText(placeholder or "请输入多行文本或 JSON...")
                text_edit.setPlainText(text)
                self.config_fields[key] = ("text", text_edit)
                widget = text_edit

        elif setting_type in {"number", "int", "integer", "float"}:
            step = setting_info.get("step") if isinstance(setting_info, dict) else None
            use_float = setting_type == "float" or any(
                isinstance(item, float) for item in (value, min_val, max_val, step)
            )
            if use_float:
                spinbox = QtWidgets.QDoubleSpinBox()
                decimals = 1
                for item in (value, min_val, max_val, step):
                    if isinstance(item, float):
                        text = f"{item:.6f}".rstrip("0")
                        if "." in text:
                            decimals = max(decimals, len(text.split(".", 1)[1]))
                decimals = min(decimals, 6)
                spinbox.setDecimals(decimals)
                spinbox.setRange(float(min_val), float(max_val))
                if isinstance(step, (int, float)) and not isinstance(step, bool):
                    spinbox.setSingleStep(float(step))
                else:
                    spinbox.setSingleStep(10 ** (-decimals))
                try:
                    numeric = float(value) if value is not None else float(min_val)
                except (TypeError, ValueError):
                    numeric = float(min_val)
                spinbox.setValue(numeric)
            else:
                spinbox = QtWidgets.QSpinBox()
                spinbox.setRange(int(min_val), int(max_val))
                try:
                    numeric = int(value) if value is not None else int(min_val)
                except (TypeError, ValueError):
                    numeric = int(min_val)
                spinbox.setValue(numeric)
            self.config_fields[key] = ("number", spinbox)
            stepper = _wrap_spin_stepper(spinbox)
            stepper.setMinimumWidth(156 if use_float else 132)
            widget = stepper

        elif setting_type == "boolean" or setting_type == "bool":
            switch = _ToggleSwitch(bool(value) if value is not None else False)
            self.config_fields[key] = ("boolean", switch)
            widget = switch

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
                or ["image_gen"]
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
            empty.setObjectName("settingFieldWarn")
            root_layout.addWidget(empty)

        chain_hint = QtWidgets.QLabel("靠前的模型先尝试。双击一行也能加入或移出。")
        chain_hint.setObjectName("settingFieldDesc")
        chain_hint.setWordWrap(True)
        root_layout.addWidget(chain_hint)

        body = QtWidgets.QGridLayout()
        body.setHorizontalSpacing(8)
        body.setVerticalSpacing(6)

        lbl_pool = QtWidgets.QLabel("可选模型")
        lbl_pool.setObjectName("settingFieldTitle")
        lbl_chain = QtWidgets.QLabel("执行顺序")
        lbl_chain.setObjectName("settingFieldTitle")
        body.addWidget(lbl_pool, 0, 0)
        body.addWidget(lbl_chain, 0, 2)

        list_pool = QtWidgets.QListWidget()
        list_chain = QtWidgets.QListWidget()
        for widget in (list_pool, list_chain):
            widget.setObjectName("queueList")
            widget.setMinimumHeight(180)
            widget.setStyleSheet(self._queue_list_qss())
        body.addWidget(list_pool, 1, 0)
        body.addWidget(list_chain, 1, 2)

        mid = QtWidgets.QVBoxLayout()
        mid.setSpacing(6)
        mid.addStretch()
        btn_add = QtWidgets.QPushButton("加入 →")
        btn_rm = QtWidgets.QPushButton("← 移除")
        btn_up = QtWidgets.QPushButton("↑ 上移")
        btn_down = QtWidgets.QPushButton("↓ 下移")
        for button in (btn_add, btn_rm, btn_up, btn_down):
            button.setObjectName("path_btn")
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(False)
            button.setMinimumWidth(88)
            mid.addWidget(button)
        mid.addStretch()
        body.addLayout(mid, 1, 1)
        body.setColumnStretch(0, 1)
        body.setColumnStretch(2, 1)
        body.setRowStretch(1, 1)
        root_layout.addLayout(body)

        state = {"selected": list(selected)}

        def _label_for(model_id: str) -> str:
            return option_map.get(model_id, model_id)

        def _refresh():
            list_chain.clear()
            for index, mid in enumerate(state["selected"], start=1):
                item = QtWidgets.QListWidgetItem(f"{index}. {_label_for(mid)}")
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
                self._on_form_edited()

        def _rm():
            row = list_chain.currentRow()
            if row < 0 or row >= len(state["selected"]):
                return
            state["selected"].pop(row)
            _refresh()
            self._on_form_edited()

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
            self._on_form_edited()

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
            self._on_form_edited()

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
                self._reject_form("显示名称和主触发词不能为空", section="meta")
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
                        self._reject_form(error, section="params", key=key)
                        return
                    _store_setting_value(key, value)
                elif field_type == "link":
                    continue
                elif field_type == "status":
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
                        self._reject_form(error, section="params", key=key)
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
                        self._reject_form(error, section="params", key=key)
                        return
                    _store_setting_value(key, value)

            new_config["settings"] = settings

            if secret_values:
                new_config["_secret_values"] = secret_values

            # 保存到文件
            if self.plugin_manager.save_plugin_config(self.trigger, new_config):
                self._baseline = self._form_fingerprint()
                self._refresh_dirty_label()
                self.accept()
            else:
                self._show_notice("保存插件配置失败", "warn")
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
