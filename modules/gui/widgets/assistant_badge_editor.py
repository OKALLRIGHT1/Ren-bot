from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from modules.character_manager import DATA_FILE
from services.gui_api.characters_service import CharactersService


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_presentation(scale: Any, offset_x: Any, offset_y: Any) -> tuple[float, float, float]:
    return (
        _number(scale, 1.0, 0.5, 3.0),
        _number(offset_x, 0.0, -1.0, 1.0),
        _number(offset_y, 0.0, -1.0, 1.0),
    )


def badge_scope_label(source: str, costume_scope: bool) -> str:
    if costume_scope:
        return "使用服装独立徽章" if source == "costume" else "继承角色默认徽章"
    return "角色默认徽章" if source == "character" else "尚未设置角色默认徽章"


class BadgePreview(QtWidgets.QWidget):
    def __init__(self, parent=None, size: int = 112):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self._pixmap = QtGui.QPixmap()
        self._presentation = (1.0, 0.0, 0.0)

    def set_badge(self, data_url: str, scale: float, offset_x: float, offset_y: float) -> None:
        pixmap = QtGui.QPixmap()
        encoded = str(data_url or "").partition(",")[2]
        try:
            pixmap.loadFromData(base64.b64decode(encoded))
        except Exception:
            pixmap = QtGui.QPixmap()
        self._pixmap = pixmap
        self._presentation = normalize_presentation(scale, offset_x, offset_y)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen_width = 1.5 if self._size < 48 else 3.0
        rect = QtCore.QRectF(pen_width, pen_width, self.width() - pen_width * 2, self.height() - pen_width * 2)
        painter.setBrush(QtGui.QColor("#111827"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#D6B85A"), pen_width))
        painter.drawEllipse(rect)
        if self._pixmap.isNull():
            painter.setPen(QtGui.QColor("#F8FAFC"))
            font = painter.font()
            font.setBold(True)
            if self._size < 48:
                font.setPointSize(max(7, int(self._size * 0.26)))
            painter.setFont(font)
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "L2D")
            return
        path = QtGui.QPainterPath()
        inner_adjust = 1.5 if self._size < 48 else 3.0
        path.addEllipse(rect.adjusted(inner_adjust, inner_adjust, -inner_adjust, -inner_adjust))
        painter.setClipPath(path)
        scale, offset_x, offset_y = self._presentation
        base = max(rect.width() / self._pixmap.width(), rect.height() / self._pixmap.height())
        width = self._pixmap.width() * base * scale
        height = self._pixmap.height() * base * scale
        center = rect.center()
        x = center.x() - width / 2 + offset_x * rect.width() / 2
        y = center.y() - height / 2 + offset_y * rect.height() / 2
        painter.drawPixmap(QtCore.QRectF(x, y, width, height), self._pixmap, QtCore.QRectF(self._pixmap.rect()))


class AssistantBadgeDialog(QtWidgets.QDialog):
    """独立的角色/服装悬浮球徽章配置窗口"""

    def __init__(
        self,
        title: str,
        character_id: str,
        costume_name: str = "",
        *,
        service: Optional[CharactersService] = None,
        on_changed: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.title_text = title
        self.character_id = str(character_id or "")
        self.costume_name = str(costume_name or "")
        self.service = service or CharactersService(Path(DATA_FILE))
        self.on_changed = on_changed
        self._loading = False

        self.setWindowTitle(f"🎨 悬浮球徽章设置 - {title}")
        self.setFixedSize(480, 260)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Dialog
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #F9FAFB;
            }
            QLabel {
                font-family: 'Segoe UI', 'Microsoft YaHei';
                color: #1F2937;
            }
            QDoubleSpinBox {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 4px 8px;
                color: #111827;
                font-size: 12px;
            }
            QDoubleSpinBox:focus {
                border-color: #3B82F6;
            }
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 12px;
                color: #374151;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #F3F4F6;
                border-color: #9CA3AF;
                color: #111827;
            }
            QPushButton#primaryAction {
                background: #2563EB;
                border-color: #1D4ED8;
                color: white;
            }
            QPushButton#primaryAction:hover {
                background: #1D4ED8;
            }
            QPushButton#dangerAction {
                color: #DC2626;
                border-color: #FECACA;
                background: #FEF2F2;
            }
            QPushButton#dangerAction:hover {
                background: #DC2626;
                color: white;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)

        # 左侧头像预览
        preview_wrap = QtWidgets.QVBoxLayout()
        preview_wrap.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview = BadgePreview(size=120)
        preview_wrap.addWidget(self.preview)
        preview_tip = QtWidgets.QLabel("悬浮球微缩展示")
        preview_tip.setStyleSheet("font-size: 11px; color: #6B7280;")
        preview_tip.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        preview_wrap.addWidget(preview_tip)
        body.addLayout(preview_wrap)

        # 右侧表单与操作
        controls = QtWidgets.QVBoxLayout()
        controls.setSpacing(8)

        self.status = QtWidgets.QLabel("尚未选择角色")
        self.status.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151;")
        self.status.setWordWrap(True)
        controls.addWidget(self.status)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.scale = self._spin(0.5, 3.0, 1.0, 0.05)
        self.offset_x = self._spin(-1.0, 1.0, 0.0, 0.05)
        self.offset_y = self._spin(-1.0, 1.0, 0.0, 0.05)
        for control in (self.scale, self.offset_x, self.offset_y):
            control.valueChanged.connect(self._preview_values)

        form.addRow("缩放比例:", self.scale)
        form.addRow("水平偏移:", self.offset_x)
        form.addRow("垂直偏移:", self.offset_y)
        controls.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        import_button = QtWidgets.QPushButton("📁 选择图片")
        import_button.clicked.connect(self._import)
        save_button = QtWidgets.QPushButton("💾 保存构图")
        save_button.setObjectName("primaryAction")
        save_button.clicked.connect(self._save_presentation)
        clear_button = QtWidgets.QPushButton("恢复继承" if self.costume_name else "清除")
        clear_button.setObjectName("dangerAction")
        clear_button.clicked.connect(self._clear)

        btn_row.addWidget(import_button)
        btn_row.addWidget(save_button)
        btn_row.addWidget(clear_button)
        controls.addLayout(btn_row)

        body.addLayout(controls, 1)
        layout.addLayout(body, 1)

        # 底部关闭栏
        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        btn_close = QtWidgets.QPushButton("完成")
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        layout.addLayout(footer)

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float, step: float):
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setSingleStep(step)
        control.setValue(value)
        control.setDecimals(2)
        return control

    def refresh(self) -> None:
        if not self.character_id:
            self.status.setText("尚未选择角色")
            self.preview.set_badge("", 1, 0, 0)
            return
        result = self.service.get_badge(self.character_id, self.costume_name)
        if not result.get("ok"):
            self.status.setText(str(result.get("error") or "徽章读取失败"))
            return
        data = result["data"]
        badge = data.get("badge") or {}
        values = normalize_presentation(
            badge.get("scale", 1), badge.get("offset_x", 0), badge.get("offset_y", 0)
        )
        self._loading = True
        self.scale.setValue(values[0])
        self.offset_x.setValue(values[1])
        self.offset_y.setValue(values[2])
        self._loading = False
        self.preview.set_badge(data.get("image_data_url", ""), *values)
        self.status.setText(badge_scope_label(str(data.get("source") or "none"), bool(self.costume_name)))

    def _preview_values(self) -> None:
        if self._loading:
            return
        result = self.service.get_badge(self.character_id, self.costume_name)
        data = result.get("data") if result.get("ok") else {}
        self.preview.set_badge(
            (data or {}).get("image_data_url", ""),
            self.scale.value(), self.offset_x.value(), self.offset_y.value(),
        )

    def _import(self) -> None:
        source, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择悬浮球徽章", "", "图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if not source:
            return
        result = self.service.import_badge(
            self.character_id, source, costume_name=self.costume_name,
            scale=self.scale.value(), offset_x=self.offset_x.value(), offset_y=self.offset_y.value(),
        )
        self._finish(result)

    def _save_presentation(self) -> None:
        result = self.service.update_badge(
            self.character_id, costume_name=self.costume_name,
            scale=self.scale.value(), offset_x=self.offset_x.value(), offset_y=self.offset_y.value(),
        )
        self._finish(result)

    def _clear(self) -> None:
        self._finish(self.service.clear_badge(self.character_id, costume_name=self.costume_name))

    def _finish(self, result: dict) -> None:
        if not result.get("ok"):
            QtWidgets.QMessageBox.warning(self, "徽章", str(result.get("error") or "操作失败"))
            return
        if self.on_changed:
            self.on_changed()
        self.refresh()


class AssistantBadgeEntryWidget(QtWidgets.QFrame):
    """紧凑单行条目小部件：嵌入在角色人设或服装属性中，提供微型预览和一键打开配置弹窗入口"""

    def __init__(
        self,
        title: str,
        *,
        service: Optional[CharactersService] = None,
        on_changed: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.title_text = title
        self.service = service or CharactersService(Path(DATA_FILE))
        self.on_changed = on_changed
        self.character_id = ""
        self.costume_name = ""

        self.setObjectName("assistantBadgeEntry")
        self.setStyleSheet("""
            QFrame#assistantBadgeEntry {
                background: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                padding: 2px 4px;
            }
            QFrame#assistantBadgeEntry:hover {
                border-color: #D1D5DB;
                background: #F3F4F6;
            }
            QLabel#badgeTitle {
                font-weight: 600;
                font-size: 12px;
                color: #374151;
            }
            QLabel#badgeStatus {
                font-size: 11px;
                color: #6B7280;
            }
            QPushButton#badgeEditBtn {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
                color: #374151;
            }
            QPushButton#badgeEditBtn:hover {
                background: #EFF6FF;
                border-color: #93C5FD;
                color: #1D4ED8;
            }
        """)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # 32x32 微缩圆形头像
        self.preview = BadgePreview(size=32)
        layout.addWidget(self.preview)

        # 文本描述
        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        self.lbl_title = QtWidgets.QLabel(title)
        self.lbl_title.setObjectName("badgeTitle")
        self.status = QtWidgets.QLabel("尚未选择角色")
        self.status.setObjectName("badgeStatus")
        text_layout.addWidget(self.lbl_title)
        text_layout.addWidget(self.status)
        layout.addLayout(text_layout, 1)

        # 弹窗按钮
        self.btn_edit = QtWidgets.QPushButton("🎨 配置徽章...")
        self.btn_edit.setObjectName("badgeEditBtn")
        self.btn_edit.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.btn_edit.clicked.connect(self.open_dialog)
        layout.addWidget(self.btn_edit)

        self.setEnabled(False)

    def set_context(self, character_id: str, costume_name: str = "") -> None:
        self.character_id = str(character_id or "")
        self.costume_name = str(costume_name or "")
        self.setEnabled(bool(self.character_id) and (not costume_name or bool(self.costume_name)))
        self.refresh()

    def refresh(self) -> None:
        if not self.character_id:
            self.status.setText("尚未选择角色")
            self.preview.set_badge("", 1, 0, 0)
            return
        result = self.service.get_badge(self.character_id, self.costume_name)
        if not result.get("ok"):
            self.status.setText(str(result.get("error") or "徽章读取失败"))
            return
        data = result["data"]
        badge = data.get("badge") or {}
        values = normalize_presentation(
            badge.get("scale", 1), badge.get("offset_x", 0), badge.get("offset_y", 0)
        )
        self.preview.set_badge(data.get("image_data_url", ""), *values)
        self.status.setText(badge_scope_label(str(data.get("source") or "none"), bool(self.costume_name)))

    def open_dialog(self) -> None:
        if not self.character_id:
            return
        dlg = AssistantBadgeDialog(
            title=self.title_text,
            character_id=self.character_id,
            costume_name=self.costume_name,
            service=self.service,
            on_changed=self._on_sub_changed,
            parent=self.window(),
        )
        dlg.exec()

    def _on_sub_changed(self) -> None:
        self.refresh()
        if self.on_changed:
            self.on_changed()


# 向后兼容别名：AssistantBadgeEditor 现在默认采用紧凑单行条目 + 独立弹窗模式
AssistantBadgeEditor = AssistantBadgeEntryWidget
