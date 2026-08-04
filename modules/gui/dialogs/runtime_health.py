from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtWidgets

from modules.gui.runtime_health_view import (
    RUNTIME_HEALTH_REFRESH_INTERVAL_MS,
    component_rows,
    overall_presentation,
)
from modules.gui.styles import get_tool_dialog_styles


class RuntimeHealthDialog(QtWidgets.QDialog):
    def __init__(self, health_center: Any, parent=None):
        super().__init__(parent)
        self.health_center = health_center
        self.setWindowTitle("运行健康中心")
        self.resize(820, 520)
        self.setMinimumSize(680, 420)
        self.setStyleSheet(get_tool_dialog_styles())
        self._build_ui()

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(RUNTIME_HEALTH_REFRESH_INTERVAL_MS)
        self.refresh_timer.timeout.connect(self.refresh_status)
        self.refresh_timer.start()
        self.refresh_status()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QtWidgets.QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(6)

        title = QtWidgets.QLabel("运行健康中心")
        title.setObjectName("dialogTitle")
        self.overall_label = QtWidgets.QLabel("健康状态未知")
        self.overall_label.setObjectName("healthOverallLabel")
        self.summary_label = QtWidgets.QLabel("只读展示各组件最近报告的运行状态。")
        self.summary_label.setObjectName("dialogDesc")
        self.summary_label.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(self.overall_label)
        header_layout.addWidget(self.summary_label)
        root.addWidget(header)

        self.component_table = QtWidgets.QTableWidget(0, 4)
        self.component_table.setHorizontalHeaderLabels(
            ["组件", "状态", "摘要", "更新时间"]
        )
        self.component_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.component_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.component_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.component_table.verticalHeader().setVisible(False)
        self.component_table.setAlternatingRowColors(True)
        header_view = self.component_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.component_table, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        refresh_button = QtWidgets.QPushButton("立即刷新")
        refresh_button.setObjectName("primaryAction")
        refresh_button.clicked.connect(self.refresh_status)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.close)
        footer.addWidget(refresh_button)
        footer.addWidget(close_button)
        root.addLayout(footer)

    def refresh_status(self) -> None:
        try:
            if self.health_center is None:
                snapshot = {}
            else:
                snapshot = self.health_center.snapshot()
                if not isinstance(snapshot, dict):
                    raise TypeError("健康中心返回了无效数据")
        except Exception as exc:
            self._set_overall("状态读取失败", "#EF4444")
            self.summary_label.setText(f"无法读取运行状态：{exc}")
            self.component_table.setRowCount(0)
            return

        presentation = overall_presentation(snapshot)
        self._set_overall(presentation["label"], presentation["color"])
        rows = component_rows(snapshot)
        self.summary_label.setText(
            f"共 {len(rows)} 个组件；状态来自运行时最近一次报告，不会主动探测服务。"
        )
        self.component_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                row["component_label"],
                row["state_label"],
                row["summary"],
                row["updated_at"],
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                self.component_table.setItem(row_index, column, item)

    def _set_overall(self, label: str, color: str) -> None:
        self.overall_label.setText(label)
        self.overall_label.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: 700;"
        )
