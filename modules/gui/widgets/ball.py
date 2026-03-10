import time
from PySide6 import QtCore, QtWidgets, QtGui

class DraggableBall(QtWidgets.QPushButton):
    """
    自定义悬浮球按钮：
    - 支持拖拽移动窗口
    - 支持短按点击触发功能
    """
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._drag_start_pos = None
        self._window_start_pos = None
        self._is_dragging = False
        self._press_time = 0

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            if self.main_window:
                self._window_start_pos = self.main_window.pos()
            self._is_dragging = False
            self._press_time = time.time()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        self.setDown(True)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos and self.main_window:
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            if not self._is_dragging and delta.manhattanLength() > 5:
                self._is_dragging = True

            if self._is_dragging:
                self.main_window.move(self._window_start_pos + delta)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.setDown(False)
        is_long_press = (time.time() - self._press_time) > 0.5

        if self._is_dragging or is_long_press:
            self._is_dragging = False
            event.ignore()
        else:
            self.clicked.emit()
