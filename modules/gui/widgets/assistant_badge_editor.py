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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(112, 112)
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
        rect = QtCore.QRectF(4, 4, self.width() - 8, self.height() - 8)
        painter.setBrush(QtGui.QColor("#111827"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#D6B85A"), 3))
        painter.drawEllipse(rect)
        if self._pixmap.isNull():
            painter.setPen(QtGui.QColor("#F8FAFC"))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "L2D")
            return
        path = QtGui.QPainterPath()
        path.addEllipse(rect.adjusted(3, 3, -3, -3))
        painter.setClipPath(path)
        scale, offset_x, offset_y = self._presentation
        base = max(rect.width() / self._pixmap.width(), rect.height() / self._pixmap.height())
        width = self._pixmap.width() * base * scale
        height = self._pixmap.height() * base * scale
        center = rect.center()
        x = center.x() - width / 2 + offset_x * rect.width() / 2
        y = center.y() - height / 2 + offset_y * rect.height() / 2
        painter.drawPixmap(QtCore.QRectF(x, y, width, height), self._pixmap, QtCore.QRectF(self._pixmap.rect()))


class AssistantBadgeEditor(QtWidgets.QGroupBox):
    def __init__(
        self,
        title: str,
        *,
        service: Optional[CharactersService] = None,
        on_changed: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(title, parent)
        self.service = service or CharactersService(Path(DATA_FILE))
        self.on_changed = on_changed
        self.character_id = ""
        self.costume_name = ""
        self._loading = False

        layout = QtWidgets.QHBoxLayout(self)
        self.preview = BadgePreview()
        layout.addWidget(self.preview, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        controls = QtWidgets.QVBoxLayout()
        self.status = QtWidgets.QLabel("尚未选择角色")
        self.status.setWordWrap(True)
        controls.addWidget(self.status)

        form = QtWidgets.QFormLayout()
        self.scale = self._spin(0.5, 3.0, 1.0, 0.05)
        self.offset_x = self._spin(-1.0, 1.0, 0.0, 0.05)
        self.offset_y = self._spin(-1.0, 1.0, 0.0, 0.05)
        for control in (self.scale, self.offset_x, self.offset_y):
            control.valueChanged.connect(self._preview_values)
        form.addRow("缩放", self.scale)
        form.addRow("水平", self.offset_x)
        form.addRow("垂直", self.offset_y)
        controls.addLayout(form)

        buttons = QtWidgets.QHBoxLayout()
        import_button = QtWidgets.QPushButton("选择图片")
        import_button.clicked.connect(self._import)
        save_button = QtWidgets.QPushButton("保存构图")
        save_button.clicked.connect(self._save_presentation)
        clear_button = QtWidgets.QPushButton("恢复继承" if title.startswith("服装") else "清除")
        clear_button.clicked.connect(self._clear)
        buttons.addWidget(import_button)
        buttons.addWidget(save_button)
        buttons.addWidget(clear_button)
        controls.addLayout(buttons)
        layout.addLayout(controls, 1)
        self.setEnabled(False)

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float, step: float):
        control = QtWidgets.QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setSingleStep(step)
        control.setValue(value)
        control.setDecimals(2)
        return control

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
