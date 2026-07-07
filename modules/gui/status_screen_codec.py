from __future__ import annotations

from PySide6 import QtCore, QtGui


def image_to_icon_payload(image: QtGui.QImage) -> tuple[str, tuple[int, int], str]:
    scaled = image.scaled(
        32,
        32,
        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.FastTransformation,
    )
    canvas = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32)
    canvas.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(canvas)
    x = (32 - scaled.width()) // 2
    y = (32 - scaled.height()) // 2
    painter.drawImage(x, y, scaled)
    painter.end()

    bits = []
    for yy in range(32):
        byte_val = 0
        bit_count = 0
        for xx in range(32):
            color = QtGui.QColor(canvas.pixel(xx, yy))
            gray = (color.red() + color.green() + color.blue()) // 3
            bit = 1 if gray < 190 else 0
            byte_val = (byte_val << 1) | bit
            bit_count += 1
            if bit_count == 8:
                bits.append(f"{byte_val:02x}")
                byte_val = 0
                bit_count = 0

    rgb565 = []
    for yy in range(32):
        for xx in range(32):
            color = QtGui.QColor(canvas.pixel(xx, yy))
            r = color.red() >> 3
            g = color.green() >> 2
            b = color.blue() >> 3
            value = (r << 11) | (g << 5) | b
            rgb565.append(f"{value:04x}")
    return "".join(bits), (32, 32), "".join(rgb565)


def text_to_bitmap_hex(
    text: str,
    width: int,
    height: int,
    *,
    point_size: int = 14,
    bold: bool = False,
) -> tuple[str, int, int]:
    canvas = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    canvas.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(canvas)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, False)
    font = QtGui.QFont("Microsoft YaHei UI", point_size)
    font.setBold(bold)
    painter.setFont(font)
    painter.setPen(QtGui.QColor("black"))
    rect = QtCore.QRect(0, 0, width, height)
    painter.drawText(
        rect,
        QtCore.Qt.AlignmentFlag.AlignLeft
        | QtCore.Qt.AlignmentFlag.AlignVCenter
        | QtCore.Qt.TextFlag.TextWordWrap,
        text,
    )
    painter.end()

    bits = []
    for yy in range(height):
        byte_val = 0
        bit_count = 0
        for xx in range(width):
            color = QtGui.QColor(canvas.pixel(xx, yy))
            gray = (color.red() + color.green() + color.blue()) // 3
            bit = 1 if gray < 180 else 0
            byte_val = (byte_val << 1) | bit
            bit_count += 1
            if bit_count == 8:
                bits.append(f"{byte_val:02x}")
                byte_val = 0
                bit_count = 0
        if bit_count > 0:
            byte_val <<= 8 - bit_count
            bits.append(f"{byte_val:02x}")
    return "".join(bits), width, height
