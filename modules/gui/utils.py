import re
from pathlib import Path
from typing import Iterable, Optional

from PySide6 import QtCore, QtGui, QtWidgets


def make_default_icon() -> QtGui.QIcon:
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    rect = QtCore.QRectF(8, 8, 48, 48)
    p.setBrush(QtGui.QColor(37, 99, 235, 230))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, 12, 12)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
    p.setFont(QtGui.QFont("Segoe UI", 16, QtGui.QFont.Weight.Bold))
    p.drawText(pm.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "L2")
    p.end()
    return QtGui.QIcon(pm)


def resolve_icon(path_str) -> QtGui.QIcon:
    if not path_str:
        return make_default_icon()

    p = Path(path_str)

    # 如果不是绝对路径，尝试相对于项目根目录查找
    if not p.is_absolute():
        # 现在的 __file__ 是 modules/gui/utils.py
        # 项目根目录应该是 ../../ (即 modules 的上一级)
        root = Path(__file__).resolve().parent.parent.parent
        p = (root / p).resolve()

    if p.exists():
        icon = QtGui.QIcon(str(p))
        if not icon.isNull():
            return icon

    # 如果还是找不到，打印一下路径方便调试
    print(f"⚠️ 图标未找到: {p}")
    return make_default_icon()


def set_dot_status(label: QtWidgets.QLabel, level: str) -> None:
    if level == "busy":
        color = "#F59E0B"
    elif level == "err":
        color = "#EF4444"
    else:
        color = "#22C55E"
    label.setStyleSheet(
        "QLabel{background:%s; border-radius:6px; min-width:12px; min-height:12px;}" % color
    )


def classify_status(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["error", "fail", "timeout", "异常", "失败", "错误"]):
        return "err"
    if any(
        k in t
        for k in [
            "think",
            "thinking",
            "listen",
            "listening",
            "connect",
            "voice",
            "speaking",
            "处理中",
            "思考",
        ]
    ):
        return "busy"
    return "ok"


class FlowLayout(QtWidgets.QLayout):
    """Simple flow layout that wraps items when the available width shrinks."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        margin: int = 0,
        h_spacing: int = 8,
        v_spacing: int = 8,
    ):
        super().__init__(parent)
        self._items: list[QtWidgets.QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QtWidgets.QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QtWidgets.QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QtWidgets.QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> QtCore.Qt.Orientation:
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QtCore.QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QtCore.QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QtCore.QSize:
        return self.minimumSize()

    def minimumSize(self) -> QtCore.QSize:
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        size += QtCore.QSize(left + right, top + bottom)
        return size

    def _horizontal_spacing(self) -> int:
        if self._h_spacing >= 0:
            return self._h_spacing
        return self._smart_spacing(QtWidgets.QStyle.PixelMetric.PM_LayoutHorizontalSpacing)

    def _vertical_spacing(self) -> int:
        if self._v_spacing >= 0:
            return self._v_spacing
        return self._smart_spacing(QtWidgets.QStyle.PixelMetric.PM_LayoutVerticalSpacing)

    def _smart_spacing(self, metric: QtWidgets.QStyle.PixelMetric) -> int:
        parent = self.parent()
        if parent is None:
            return 8
        if isinstance(parent, QtWidgets.QWidget):
            return parent.style().pixelMetric(metric, None, parent)
        return 8

    def _do_layout(self, rect: QtCore.QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x = effective.x()
        y = effective.y()
        line_height = 0
        space_x = self._horizontal_spacing()
        space_y = self._vertical_spacing()

        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + space_x
            if line_height > 0 and next_x - space_x > effective.right() and x > effective.x():
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + hint.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))

            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + bottom


def wrap_in_scroll_area(
    widget: QtWidgets.QWidget,
    *,
    horizontal: QtCore.Qt.ScrollBarPolicy = QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    vertical: QtCore.Qt.ScrollBarPolicy = QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    frame: bool = False,
) -> QtWidgets.QScrollArea:
    """Wrap content so outer dialogs can shrink while inner content scrolls.

    Outer scroll uses vertical Ignored size policy so QStackedWidget / dialogs
    are not forced tall by nested minimumSizeHint values. Inner widget keeps
    Preferred so its natural height remains available to the scroll area.
    """
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(
        QtWidgets.QFrame.Shape.StyledPanel if frame else QtWidgets.QFrame.Shape.NoFrame
    )
    scroll.setHorizontalScrollBarPolicy(horizontal)
    scroll.setVerticalScrollBarPolicy(vertical)
    widget.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Preferred,
        QtWidgets.QSizePolicy.Policy.Preferred,
    )
    scroll.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Preferred,
        QtWidgets.QSizePolicy.Policy.Ignored,
    )
    scroll.setWidget(widget)
    scroll.setMinimumSize(0, 0)
    return scroll


def apply_embedded_mode(
    widget: QtWidgets.QWidget,
    *,
    clear_minimum: bool = True,
) -> QtWidgets.QWidget:
    """Make a dialog/page safe to host inside Settings or other compact shells."""
    if isinstance(widget, QtWidgets.QDialog):
        widget.setWindowFlags(QtCore.Qt.WindowType.Widget)
    widget.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding,
    )
    if clear_minimum:
        widget.setMinimumSize(0, 0)
    return widget


def clear_minimum_sizes(widgets: Iterable[QtWidgets.QWidget]) -> None:
    for widget in widgets:
        if widget is None:
            continue
        widget.setMinimumSize(0, 0)


def elide_label_text(
    label: QtWidgets.QLabel,
    text: str,
    *,
    max_width: Optional[int] = None,
    mode: QtCore.Qt.TextElideMode = QtCore.Qt.TextElideMode.ElideRight,
) -> None:
    """Set label text with optional eliding for narrow toolbars."""
    plain = text or ""
    label.setToolTip(plain)
    width = max_width if max_width is not None else max(0, label.width() - 4)
    if width <= 0:
        label.setText(plain)
        return
    metrics = label.fontMetrics()
    label.setText(metrics.elidedText(plain, mode, width))
