from __future__ import annotations

import os
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import get_tool_dialog_styles


class ConsoleLogDialog(QtWidgets.QDialog):
    """Live tail view for stdout/stderr and application logs."""

    def __init__(self, parent=None, *, log_dir: str = "./logs"):
        super().__init__(parent)
        self.log_dir = Path(log_dir).resolve()
        self.console_log = self.log_dir / "console.log"
        self.agent_log = self.log_dir / "agent.log"
        self._tail_bytes = 240_000

        self.setWindowTitle("控制台输出")
        self.resize(940, 620)
        self.setMinimumSize(720, 420)
        self.setStyleSheet(get_tool_dialog_styles())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QtWidgets.QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)

        title = QtWidgets.QLabel("控制台输出")
        title.setObjectName("dialogTitle")
        desc = QtWidgets.QLabel(
            "实时查看程序 print/stdout/stderr 输出；应用日志在第二个标签页。"
        )
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        root.addWidget(header)

        self.tabs = QtWidgets.QTabWidget()
        self.console_view = self._make_view()
        self.agent_view = self._make_view()
        self.tabs.addTab(self.console_view, "控制台 console.log")
        self.tabs.addTab(self.agent_view, "应用日志 agent.log")
        root.addWidget(self.tabs, 1)

        toolbar = QtWidgets.QHBoxLayout()
        self.auto_scroll = QtWidgets.QCheckBox("自动滚动")
        self.auto_scroll.setChecked(True)
        self.pause_refresh = QtWidgets.QCheckBox("暂停刷新")

        btn_refresh = QtWidgets.QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_now)
        btn_copy = QtWidgets.QPushButton("复制当前页")
        btn_copy.clicked.connect(self._copy_current)
        btn_open_dir = QtWidgets.QPushButton("打开日志目录")
        btn_open_dir.clicked.connect(self._open_log_dir)
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(self.close)

        toolbar.addWidget(self.auto_scroll)
        toolbar.addWidget(self.pause_refresh)
        toolbar.addStretch()
        toolbar.addWidget(btn_refresh)
        toolbar.addWidget(btn_copy)
        toolbar.addWidget(btn_open_dir)
        toolbar.addWidget(btn_close)
        root.addLayout(toolbar)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(900)
        self.timer.timeout.connect(self.refresh_now)
        self.refresh_now()

    def _make_view(self):
        view = QtWidgets.QPlainTextEdit()
        view.setObjectName("consoleView")
        view.setReadOnly(True)
        view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        font = QtGui.QFont("Cascadia Mono")
        if not font.exactMatch():
            font = QtGui.QFont("Consolas")
        font.setPointSize(10)
        view.setFont(font)
        return view

    def _read_tail(self, path: Path) -> str:
        if not path.exists():
            return f"{path} 不存在。程序启动后的输出会写到这里。"
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                if size > self._tail_bytes:
                    fh.seek(-self._tail_bytes, os.SEEK_END)
                    prefix = f"... 仅显示最近 {self._tail_bytes // 1024} KB ...\n"
                else:
                    prefix = ""
                return prefix + fh.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return f"读取失败: {exc}"

    def refresh_now(self):
        if self.pause_refresh.isChecked():
            return
        self._set_text_if_changed(self.console_view, self._read_tail(self.console_log))
        self._set_text_if_changed(self.agent_view, self._read_tail(self.agent_log))

    def _set_text_if_changed(self, view, text: str):
        if view.toPlainText() == text:
            return
        scrollbar = view.verticalScrollBar()
        old_value = scrollbar.value()
        was_at_end = old_value >= scrollbar.maximum() - 2
        view.setPlainText(text)
        if self.auto_scroll.isChecked() or was_at_end:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(old_value, scrollbar.maximum()))

    def _copy_current(self):
        widget = self.tabs.currentWidget()
        if isinstance(widget, QtWidgets.QPlainTextEdit):
            QtWidgets.QApplication.clipboard().setText(widget.toPlainText())

    def _open_log_dir(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.log_dir))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "打开失败", str(exc))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_now()
        self.timer.start()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
