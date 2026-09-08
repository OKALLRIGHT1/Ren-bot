"""Plugin dependency scan / install settings page."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

try:
    from modules.dependency_check import (
        build_install_command,
        install_missing as run_install_missing,
        scan_missing_dependencies,
    )
except Exception:

    def scan_missing_dependencies(*args, **kwargs):
        return []

    def build_install_command(rows):
        return ""

    def run_install_missing(rows, timeout=600):
        return {"ok": "0", "message": "dependency_check unavailable"}


class DependencySettingsPage(QtWidgets.QWidget):
    def __init__(self, parent=None, plugins_dir: str = "./plugins"):
        super().__init__(parent)
        self.plugins_dir = plugins_dir
        self.dep_rows = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top = QtWidgets.QHBoxLayout()
        self.dep_title = QtWidgets.QLabel("依赖体检", objectName="header")
        top.addWidget(self.dep_title)
        top.addStretch()

        btn_refresh = QtWidgets.QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        top.addWidget(btn_refresh)

        btn_copy = QtWidgets.QPushButton("复制安装命令")
        btn_copy.clicked.connect(self.copy_install_cmd)
        top.addWidget(btn_copy)

        btn_install = QtWidgets.QPushButton("一键安装")
        btn_install.clicked.connect(self.install_missing)
        top.addWidget(btn_install)
        layout.addLayout(top)

        tip = QtWidgets.QLabel("扫描 plugins 目录中缺失的第三方依赖。")
        tip.setStyleSheet("color:#6B7280;")
        layout.addWidget(tip)

        self.dep_table = QtWidgets.QTableWidget()
        self.dep_table.setColumnCount(4)
        self.dep_table.setHorizontalHeaderLabels(["模块", "pip 包", "来源插件", "状态"])
        self.dep_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.dep_table.verticalHeader().setVisible(False)
        layout.addWidget(self.dep_table, 1)

        self.dep_result = QtWidgets.QPlainTextEdit()
        self.dep_result.setReadOnly(True)
        self.dep_result.setPlaceholderText("安装结果会显示在这里")
        self.dep_result.setMaximumHeight(180)
        layout.addWidget(self.dep_result)

    def refresh(self):
        self.dep_rows = scan_missing_dependencies(self.plugins_dir)
        rows = self.dep_rows or []
        self.dep_table.setRowCount(0)

        if not rows:
            self.dep_title.setText("依赖体检（无缺失）")
            self.dep_table.setRowCount(1)
            self.dep_table.setItem(0, 0, QtWidgets.QTableWidgetItem("无"))
            self.dep_table.setItem(0, 1, QtWidgets.QTableWidgetItem("-"))
            self.dep_table.setItem(0, 2, QtWidgets.QTableWidgetItem("-"))
            self.dep_table.setItem(0, 3, QtWidgets.QTableWidgetItem("OK"))
            return

        self.dep_title.setText(f"依赖体检（缺失 {len(rows)} 项）")
        for row in rows:
            index = self.dep_table.rowCount()
            self.dep_table.insertRow(index)
            self.dep_table.setItem(
                index, 0, QtWidgets.QTableWidgetItem(str(row.get("module", "")))
            )
            self.dep_table.setItem(
                index, 1, QtWidgets.QTableWidgetItem(str(row.get("package", "")))
            )
            self.dep_table.setItem(
                index, 2, QtWidgets.QTableWidgetItem(str(row.get("plugins", "")))
            )
            self.dep_table.setItem(index, 3, QtWidgets.QTableWidgetItem("缺失"))

    def copy_install_cmd(self):
        cmd = build_install_command(self.dep_rows)
        if not cmd:
            QtWidgets.QMessageBox.information(
                self, "依赖体检", "当前没有需要安装的依赖。"
            )
            return
        QtWidgets.QApplication.clipboard().setText(cmd)
        self.dep_result.setPlainText(cmd)
        QtWidgets.QMessageBox.information(self, "依赖体检", "安装命令已复制到剪贴板。")

    def install_missing(self):
        if not self.dep_rows:
            QtWidgets.QMessageBox.information(
                self, "依赖体检", "当前没有需要安装的依赖。"
            )
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "依赖体检",
            "将执行 pip 安装缺失依赖，是否继续？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            result = run_install_missing(self.dep_rows, timeout=900)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        ok = str(result.get("ok", "0")) == "1"
        msg = str(result.get("message", ""))
        self.dep_result.setPlainText(msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "依赖体检", "依赖安装完成。")
        else:
            QtWidgets.QMessageBox.warning(
                self, "依赖体检", "依赖安装失败，请查看输出日志。"
            )
        self.refresh()
