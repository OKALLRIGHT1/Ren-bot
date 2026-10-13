"""UI palette / theme settings page."""

from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import (
    THEMES,
    get_current_theme_name,
    get_settings_styles,
    get_ui_palette,
)

try:
    from modules.runtime_settings import update_runtime_settings
except Exception:

    def update_runtime_settings(patch):
        return patch or {}


class ColorSettingsPage(QtWidgets.QWidget):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self._ui_color_inputs = {}
        self._ui_color_labels = {}
        self._ui_color_previews = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        tip = QtWidgets.QLabel(
            "支持输入十六进制颜色（如 #1F2937）。保存后会自动应用到主界面与对话窗口。"
        )
        tip.setStyleSheet("color:#6B7280;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        def add_group(title: str):
            group = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout(group)
            form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(10)
            body_layout.addWidget(group)
            return form

        def add_color_row(form: QtWidgets.QFormLayout, label: str, key: str):
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)

            inp = QtWidgets.QLineEdit()
            inp.setPlaceholderText("#RRGGBB")
            preview = QtWidgets.QLabel()
            preview.setFixedSize(22, 22)
            preview.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 4px;")

            btn = QtWidgets.QPushButton("选色")
            btn.setFixedWidth(64)
            btn.clicked.connect(
                lambda checked=False, w=inp, p=preview: self._pick_color(w, p)
            )
            inp.textChanged.connect(
                lambda text, p=preview: self._refresh_color_preview(p, text)
            )

            h.addWidget(inp, 1)
            h.addWidget(preview)
            h.addWidget(btn)
            form.addRow(label + ":", row)

            self._ui_color_inputs[key] = inp
            self._ui_color_labels[key] = label
            self._ui_color_previews[key] = preview

        base_form = add_group("基础配色")
        add_color_row(base_form, "主色", "accent")
        add_color_row(base_form, "主色悬停", "accent_hover")
        add_color_row(base_form, "主色浅背景", "accent_soft")
        add_color_row(base_form, "应用背景", "bg_app")
        add_color_row(base_form, "卡片背景", "bg_card")
        add_color_row(base_form, "柔和背景", "bg_soft")
        add_color_row(base_form, "边框", "border")
        add_color_row(base_form, "强边框", "border_strong")
        add_color_row(base_form, "主文本", "text_primary")
        add_color_row(base_form, "次文本", "text_secondary")
        add_color_row(base_form, "弱文本", "text_muted")
        add_color_row(base_form, "成功", "success")
        add_color_row(base_form, "成功浅色", "success_soft")
        add_color_row(base_form, "警告", "warning")
        add_color_row(base_form, "危险", "danger")

        main_console_form = add_group("主控制台")
        add_color_row(main_console_form, "背景", "console_main.bg")
        add_color_row(main_console_form, "文字", "console_main.fg")
        add_color_row(main_console_form, "边框", "console_main.border")
        add_color_row(main_console_form, "选中背景", "console_main.selection_bg")
        add_color_row(main_console_form, "选中文字", "console_main.selection_fg")
        add_color_row(main_console_form, "弱文本", "console_main.muted")
        add_color_row(main_console_form, "标签", "console_main.label")

        body_layout.addStretch()
        layout.addWidget(body, 1)

        theme_action_card = QtWidgets.QGroupBox("快速主题选择")
        theme_action_layout = QtWidgets.QHBoxLayout(theme_action_card)
        self.combo_theme = QtWidgets.QComboBox()
        try:
            for theme_name in THEMES.keys():
                self.combo_theme.addItem(theme_name)
            self.combo_theme.setCurrentText(get_current_theme_name())
        except Exception:
            pass
        btn_apply_theme = QtWidgets.QPushButton("使用此主题覆盖所有自定义配色")
        btn_apply_theme.setObjectName("primaryAction")
        btn_apply_theme.clicked.connect(self._on_theme_apply_clicked)
        theme_action_layout.addWidget(QtWidgets.QLabel("预设风格:"))
        theme_action_layout.addWidget(self.combo_theme, 1)
        theme_action_layout.addWidget(btn_apply_theme)
        layout.addWidget(theme_action_card)
        layout.addSpacing(8)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        btn_reset = QtWidgets.QPushButton("恢复默认")
        btn_reset.clicked.connect(self.reset_settings)
        btn_save = QtWidgets.QPushButton("保存并应用")
        btn_save.setObjectName("primaryAction")
        btn_save.clicked.connect(self.save_settings)
        footer.addWidget(btn_reset)
        footer.addWidget(btn_save)
        layout.addLayout(footer)

    def refresh(self):
        self._refresh_color_inputs()

    def _on_theme_apply_clicked(self):
        theme_name = self.combo_theme.currentText()
        update_runtime_settings(
            {
                "theme_name": theme_name,
                "ui_palette": None,
            }
        )
        self._refresh_color_inputs()
        self._apply_palette_now()

    def _default_ui_palette(self):
        try:
            theme_name = get_current_theme_name()
            if theme_name not in THEMES:
                theme_name = list(THEMES.keys())[0]
            base = dict(THEMES[theme_name])
            palette = dict(base)
            palette["console_main"] = {
                "bg": base.get("console_bg", ""),
                "fg": base.get("console_fg", ""),
                "border": base.get("console_border", ""),
                "selection_bg": base.get("console_selection_bg", ""),
                "selection_fg": base.get("console_selection_fg", ""),
            }
            palette["console_codex"] = dict(palette["console_main"])
            return palette
        except Exception:
            return {}

    def _refresh_color_inputs(self):
        palette = get_ui_palette()
        for key, inp in self._ui_color_inputs.items():
            value = self._get_palette_value(palette, key)
            if value:
                inp.setText(str(value))

    @staticmethod
    def _get_palette_value(palette: dict, key: str) -> str:
        if "." not in key:
            return str(palette.get(key, ""))
        group, sub = key.split(".", 1)
        group_map = palette.get(group, {})
        if isinstance(group_map, dict):
            return str(group_map.get(sub, ""))
        return ""

    @staticmethod
    def _is_valid_color(value: str) -> bool:
        return bool(re.match(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", value or ""))

    def _refresh_color_preview(self, preview: QtWidgets.QLabel, value: str):
        color = value.strip()
        if self._is_valid_color(color):
            preview.setStyleSheet(
                f"background: {color}; border: 1px solid #D1D5DB; border-radius: 4px;"
            )
        else:
            preview.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 4px;")

    def _pick_color(self, inp: QtWidgets.QLineEdit, preview: QtWidgets.QLabel):
        current = inp.text().strip()
        if not self._is_valid_color(current):
            current = "#FFFFFF"
        color = QtGui.QColor(current)
        chosen = QtWidgets.QColorDialog.getColor(color, self, "选择颜色")
        if chosen.isValid():
            inp.setText(chosen.name())
            self._refresh_color_preview(preview, chosen.name())

    def collect_settings(self):
        palette = {}
        console_main = {}
        console_codex = {}
        for key, inp in self._ui_color_inputs.items():
            value = inp.text().strip()
            label = self._ui_color_labels.get(key, key)
            if not self._is_valid_color(value):
                QtWidgets.QMessageBox.warning(
                    self, "颜色设置", f"{label} 的颜色值无效：{value}"
                )
                return None
            if "." in key:
                group, sub = key.split(".", 1)
                if group == "console_main":
                    console_main[sub] = value
                elif group == "console_codex":
                    console_codex[sub] = value
            else:
                palette[key] = value
        if console_main:
            palette["console_main"] = console_main
        if console_codex:
            palette["console_codex"] = console_codex
        return palette

    def _apply_palette_now(self):
        if self.main_app is not None and hasattr(self.main_app, "apply_ui_palette"):
            self.main_app.apply_ui_palette()
        else:
            host = self.window()
            if host is not None and hasattr(host, "setStyleSheet"):
                host.setStyleSheet(get_settings_styles())
            else:
                self.setStyleSheet(get_settings_styles())

    def save_settings(self):
        palette = self.collect_settings()
        if not palette:
            return
        update_runtime_settings({"ui_palette": palette})
        self._apply_palette_now()

    def reset_settings(self):
        update_runtime_settings({"ui_palette": None})
        self._refresh_color_inputs()
        self._apply_palette_now()
