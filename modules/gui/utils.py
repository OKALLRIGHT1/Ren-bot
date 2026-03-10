import re
from pathlib import Path
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
    if any(k in t for k in ["think", "thinking", "listen", "listening", "connect", "voice", "speaking", "处理中", "思考"]):
        return "busy"
    return "ok"
