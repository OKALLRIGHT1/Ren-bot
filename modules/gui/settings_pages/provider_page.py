"""Provider catalog settings page."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from modules.gui.settings_pages.catalog_io import retarget_provider_name, save_model_catalog

try:
    from config import PROVIDERS
except ImportError:
    PROVIDERS = {}


class ProviderSettingsPage(QtWidgets.QWidget):
    def __init__(self, parent=None, on_changed=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._build_ui()
        self.refresh()

    def _notify(self):
        if callable(self._on_changed):
            self._on_changed()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("提供商配置 (Providers)", objectName="header"))
        btn_add = QtWidgets.QPushButton("+ 添加提供商")
        btn_add.clicked.connect(self._on_add_provider)
        top.addWidget(btn_add)
        layout.addLayout(top)

        self.prov_table = QtWidgets.QTableWidget()
        self.prov_table.setColumnCount(3)
        self.prov_table.setHorizontalHeaderLabels(["名称", "Base URL", "操作"])
        self.prov_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.prov_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.prov_table.verticalHeader().setVisible(False)
        self.prov_table.verticalHeader().setDefaultSectionSize(44)
        layout.addWidget(self.prov_table, 1)

    def reload_from_disk(self):
        self.refresh()

    def refresh(self):
        self.prov_table.setRowCount(0)
        for name, data in PROVIDERS.items():
            row = self.prov_table.rowCount()
            self.prov_table.insertRow(row)
            self.prov_table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.prov_table.setItem(
                row,
                1,
                QtWidgets.QTableWidgetItem(str((data or {}).get("base_url", ""))),
            )

            action_widget = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(action_widget)
            h.setContentsMargins(4, 4, 4, 4)
            h.setSpacing(8)
            btn_edit = QtWidgets.QPushButton("编辑")
            btn_edit.clicked.connect(lambda c=False, n=name: self._on_edit_provider(n))
            btn_del = QtWidgets.QPushButton("删除")
            btn_edit.setObjectName("tableActionBtn")
            btn_del.setObjectName("tableDangerBtn")
            btn_del.clicked.connect(lambda c=False, n=name: self._on_del_provider(n))
            h.addWidget(btn_edit)
            h.addWidget(btn_del)
            h.addStretch()
            self.prov_table.setCellWidget(row, 2, action_widget)

    def _edit_dialog(self, *args, **kwargs):
        from modules.gui.dialogs.settings import ProviderEditDialog

        return ProviderEditDialog(*args, **kwargs)

    def _on_add_provider(self):
        dlg = self._edit_dialog(self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dlg.result_data
            PROVIDERS[data["name"]] = data["config"]
            save_model_catalog(parent=self)
            self.refresh()
            self._notify()

    def _on_edit_provider(self, name):
        dlg = self._edit_dialog(self, name, PROVIDERS.get(name, {}))
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dlg.result_data or {}
            new_name = str(data.get("name") or "").strip()
            cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
            if not new_name:
                QtWidgets.QMessageBox.warning(self, "错误", "提供商名称不能为空")
                return
            if new_name != name:
                result = retarget_provider_name(name, new_name)
                if not result.get("ok"):
                    if result.get("error") == "id_exists":
                        QtWidgets.QMessageBox.warning(
                            self, "错误", f"提供商 {new_name} 已存在"
                        )
                    else:
                        QtWidgets.QMessageBox.warning(
                            self, "错误", "无法重命名提供商"
                        )
                    return
            PROVIDERS[new_name] = cfg
            save_model_catalog(parent=self)
            self.refresh()
            self._notify()

    def _on_del_provider(self, name):
        reply = QtWidgets.QMessageBox.question(
            self,
            "删除",
            f"确定删除提供商 {name} 吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            PROVIDERS.pop(name, None)
            save_model_catalog(parent=self)
            self.refresh()
            self._notify()
