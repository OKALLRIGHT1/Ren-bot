import os

import config
from PySide6 import QtCore, QtWidgets

from modules.gui.sedentary_popup import (
    build_sedentary_popup_options,
    show_sedentary_popup_dialog,
)

try:
    from modules.runtime_settings import load_runtime_settings, update_runtime_settings
except Exception:

    def load_runtime_settings():
        return {}

    def update_runtime_settings(patch):
        return patch or {}


try:
    from config import (
        SEDENTARY_REMINDER_MINUTES,
        SEDENTARY_REMINDER_COOLDOWN_MINUTES,
        SEDENTARY_POPUP_ENABLED,
        SEDENTARY_POPUP_TITLE,
        SEDENTARY_POPUP_MESSAGE,
        SEDENTARY_POPUP_IMAGE_PATH,
        SEDENTARY_POPUP_SNOOZE_MINUTES,
        SEDENTARY_POPUP_AUTO_CLOSE_SECONDS,
    )
except ImportError:
    SEDENTARY_REMINDER_MINUTES = 60
    SEDENTARY_REMINDER_COOLDOWN_MINUTES = 30
    SEDENTARY_POPUP_ENABLED = True
    SEDENTARY_POPUP_TITLE = "该起来活动一下了"
    SEDENTARY_POPUP_MESSAGE = "你已经连续使用 {app_name} {active_minutes} 分钟。"
    SEDENTARY_POPUP_IMAGE_PATH = ""
    SEDENTARY_POPUP_SNOOZE_MINUTES = 10
    SEDENTARY_POPUP_AUTO_CLOSE_SECONDS = 20


def select_sedentary_preview_image_path(main_app, app_name: str, active_minutes: int) -> str:
    backend = getattr(main_app, "app", main_app)
    selector = getattr(backend, "select_sedentary_meme_image_path", None)
    if not callable(selector):
        return ""
    try:
        result = selector(app_name, active_minutes)
        if hasattr(result, "result"):
            result = result.result(timeout=8)
        return str(result or "")
    except Exception:
        return ""


def load_sedentary_settings_state() -> dict:
    runtime = load_runtime_settings()
    return {
        "sedentary_reminder_minutes": int(
            runtime.get("sedentary_reminder_minutes", SEDENTARY_REMINDER_MINUTES)
            or SEDENTARY_REMINDER_MINUTES
        ),
        "sedentary_break_minutes": int(
            runtime.get(
                "sedentary_break_minutes",
                getattr(config, "ACTIVITY_AGENT_SEDENTARY_BREAK_MINUTES", 5),
            )
            or 5
        ),
        "sedentary_cooldown_minutes": int(
            runtime.get(
                "sedentary_cooldown_minutes",
                SEDENTARY_REMINDER_COOLDOWN_MINUTES,
            )
            or SEDENTARY_REMINDER_COOLDOWN_MINUTES
        ),
        "sedentary_popup_enabled": bool(
            runtime.get("sedentary_popup_enabled", SEDENTARY_POPUP_ENABLED)
        ),
        "sedentary_status_visible": bool(
            runtime.get("sedentary_status_visible", True)
        ),
        "sedentary_popup_title": str(
            runtime.get("sedentary_popup_title", SEDENTARY_POPUP_TITLE)
            or SEDENTARY_POPUP_TITLE
        ),
        "sedentary_popup_message": str(
            runtime.get("sedentary_popup_message", SEDENTARY_POPUP_MESSAGE)
            or SEDENTARY_POPUP_MESSAGE
        ),
        "sedentary_popup_image_path": str(
            runtime.get("sedentary_popup_image_path", SEDENTARY_POPUP_IMAGE_PATH) or ""
        ),
        "sedentary_popup_snooze_minutes": int(
            runtime.get(
                "sedentary_popup_snooze_minutes",
                SEDENTARY_POPUP_SNOOZE_MINUTES,
            )
            or SEDENTARY_POPUP_SNOOZE_MINUTES
        ),
        "sedentary_popup_auto_close_seconds": int(
            runtime.get(
                "sedentary_popup_auto_close_seconds",
                SEDENTARY_POPUP_AUTO_CLOSE_SECONDS,
            )
            or SEDENTARY_POPUP_AUTO_CLOSE_SECONDS
        ),
    }


class SedentarySettingsPage(QtWidgets.QWidget):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self._build_ui()
        self.load_state()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        form_group = QtWidgets.QGroupBox("久坐判定")
        form = QtWidgets.QFormLayout(form_group)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.sedentary_enabled = QtWidgets.QCheckBox("启用久坐弹窗")
        form.addRow("弹窗:", self.sedentary_enabled)

        self.sedentary_status_visible = QtWidgets.QCheckBox("显示顶部久坐时间")
        form.addRow("顶部显示:", self.sedentary_status_visible)

        self.sedentary_reminder_minutes = QtWidgets.QSpinBox()
        self.sedentary_reminder_minutes.setRange(1, 720)
        self.sedentary_reminder_minutes.setSuffix(" 分钟")
        form.addRow("提醒间隔:", self.sedentary_reminder_minutes)

        self.sedentary_break_minutes = QtWidgets.QSpinBox()
        self.sedentary_break_minutes.setRange(1, 120)
        self.sedentary_break_minutes.setSuffix(" 分钟")
        form.addRow("休息重置:", self.sedentary_break_minutes)

        self.sedentary_cooldown_minutes = QtWidgets.QSpinBox()
        self.sedentary_cooldown_minutes.setRange(1, 720)
        self.sedentary_cooldown_minutes.setSuffix(" 分钟")
        form.addRow("提醒冷却:", self.sedentary_cooldown_minutes)

        popup_group = QtWidgets.QGroupBox("弹窗内容")
        popup_form = QtWidgets.QFormLayout(popup_group)
        popup_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        popup_form.setHorizontalSpacing(12)
        popup_form.setVerticalSpacing(10)

        self.sedentary_popup_title = QtWidgets.QLineEdit()
        popup_form.addRow("标题:", self.sedentary_popup_title)

        self.sedentary_popup_message = QtWidgets.QPlainTextEdit()
        self.sedentary_popup_message.setMinimumHeight(90)
        popup_form.addRow("正文模板:", self.sedentary_popup_message)

        self.sedentary_popup_image_path = QtWidgets.QLineEdit()
        btn_pick = QtWidgets.QPushButton("选择")
        btn_pick.clicked.connect(self._pick_sedentary_popup_image)
        image_row = QtWidgets.QWidget()
        image_layout = QtWidgets.QHBoxLayout(image_row)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.addWidget(self.sedentary_popup_image_path, 1)
        image_layout.addWidget(btn_pick)
        popup_form.addRow("默认图片:", image_row)

        self.sedentary_snooze_minutes = QtWidgets.QSpinBox()
        self.sedentary_snooze_minutes.setRange(1, 240)
        self.sedentary_snooze_minutes.setSuffix(" 分钟")
        popup_form.addRow("稍后提醒:", self.sedentary_snooze_minutes)

        self.sedentary_auto_close_seconds = QtWidgets.QSpinBox()
        self.sedentary_auto_close_seconds.setRange(0, 3600)
        self.sedentary_auto_close_seconds.setSuffix(" 秒")
        self.sedentary_auto_close_seconds.setSpecialValueText("不自动关闭")
        popup_form.addRow("自动关闭:", self.sedentary_auto_close_seconds)

        hint = QtWidgets.QLabel(
            "正文模板可使用 {app_name} 和 {active_minutes}。保存后主程序立即生效；Live2D 采集端会自动同步久坐判定参数。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280;")

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        btn_preview = QtWidgets.QPushButton("预览弹窗")
        btn_preview.clicked.connect(self.preview_popup)
        btn_save = QtWidgets.QPushButton("保存并应用")
        btn_save.setObjectName("primaryAction")
        btn_save.clicked.connect(self.save_state)
        footer.addWidget(btn_preview)
        footer.addWidget(btn_save)

        layout.addWidget(form_group)
        layout.addWidget(popup_group)
        layout.addWidget(hint)
        layout.addLayout(footer)
        layout.addStretch()

    def load_state(self) -> None:
        state = load_sedentary_settings_state()
        self.sedentary_enabled.setChecked(bool(state["sedentary_popup_enabled"]))
        self.sedentary_status_visible.setChecked(
            bool(state.get("sedentary_status_visible", True))
        )
        self.sedentary_reminder_minutes.setValue(
            int(state["sedentary_reminder_minutes"])
        )
        self.sedentary_break_minutes.setValue(int(state["sedentary_break_minutes"]))
        self.sedentary_cooldown_minutes.setValue(
            int(state["sedentary_cooldown_minutes"])
        )
        self.sedentary_popup_title.setText(state["sedentary_popup_title"])
        self.sedentary_popup_message.setPlainText(state["sedentary_popup_message"])
        self.sedentary_popup_image_path.setText(state["sedentary_popup_image_path"])
        self.sedentary_snooze_minutes.setValue(
            int(state["sedentary_popup_snooze_minutes"])
        )
        self.sedentary_auto_close_seconds.setValue(
            int(state["sedentary_popup_auto_close_seconds"])
        )

    def collect_state(self) -> dict:
        return {
            "sedentary_reminder_minutes": int(self.sedentary_reminder_minutes.value()),
            "sedentary_break_minutes": int(self.sedentary_break_minutes.value()),
            "sedentary_cooldown_minutes": int(self.sedentary_cooldown_minutes.value()),
            "sedentary_popup_enabled": self.sedentary_enabled.isChecked(),
            "sedentary_status_visible": self.sedentary_status_visible.isChecked(),
            "sedentary_popup_title": self.sedentary_popup_title.text().strip()
            or SEDENTARY_POPUP_TITLE,
            "sedentary_popup_message": self.sedentary_popup_message.toPlainText().strip()
            or SEDENTARY_POPUP_MESSAGE,
            "sedentary_popup_image_path": self.sedentary_popup_image_path.text().strip(),
            "sedentary_popup_snooze_minutes": int(self.sedentary_snooze_minutes.value()),
            "sedentary_popup_auto_close_seconds": int(
                self.sedentary_auto_close_seconds.value()
            ),
        }

    def _pick_sedentary_popup_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择久坐提醒图片",
            self.sedentary_popup_image_path.text().strip() or os.getcwd(),
            "Images (*.png *.jpg *.jpeg *.gif *.webp);;All Files (*)",
        )
        if path:
            self.sedentary_popup_image_path.setText(path)

    def preview_popup(self):
        values = self.collect_state()
        app_name = "电脑"
        active_minutes = int(values["sedentary_reminder_minutes"])
        image_path = select_sedentary_preview_image_path(
            self.main_app, app_name, active_minutes
        )
        cfg = type("SedentaryPreviewConfig", (), {})()
        cfg.SEDENTARY_POPUP_ENABLED = True
        cfg.SEDENTARY_POPUP_TITLE = values["sedentary_popup_title"]
        cfg.SEDENTARY_POPUP_MESSAGE = values["sedentary_popup_message"]
        cfg.SEDENTARY_POPUP_IMAGE_PATH = values["sedentary_popup_image_path"]
        cfg.SEDENTARY_POPUP_SNOOZE_MINUTES = values["sedentary_popup_snooze_minutes"]
        cfg.SEDENTARY_POPUP_AUTO_CLOSE_SECONDS = values[
            "sedentary_popup_auto_close_seconds"
        ]
        options = build_sedentary_popup_options(
            cfg,
            app_name=app_name,
            active_minutes=active_minutes,
            image_path_override=image_path,
        )
        show_sedentary_popup_dialog(self, options)

    def save_state(self) -> None:
        new_settings = self.collect_state()
        update_runtime_settings(new_settings)
        apply_result = {}
        if getattr(self.main_app, "apply_external_settings", None):
            try:
                apply_result = self.main_app.apply_external_settings(new_settings) or {}
            except Exception as exc:
                apply_result = {"error": str(exc)}
        if apply_result.get("error"):
            QtWidgets.QMessageBox.warning(
                self, "久坐提醒", f"配置已保存，但应用失败：{apply_result['error']}"
            )
            return
        QtWidgets.QMessageBox.information(
            self,
            "久坐提醒",
            "配置已保存。Live2D 采集端会自动同步久坐判定参数。",
        )
