"""External services management page (Ollama / GPT-SoVITS / NapCat).

计划 §4（docs/部分重构.md）：薄适配页 —— 状态展示与启动/停止/重启操作
全部走 Brain 的 /external-services* API；客户端不直接扫描或启动本机服务。
远程模式下这些服务由远程 Brain 所在机器管理，页面对远程同样适用。

线程→UI 传递用 Qt Signal(object)（后台线程 emit，主线程槽处理）。
不要用 QMetaObject.invokeMethod + Q_ARG(list, ...)：Python 对象经元对象
系统跨线程传递在部分 PySide6 版本不可靠，会让页面永远停在「正在加载…」。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PySide6 import QtCore, QtWidgets

try:
    from modules.gui.remote_external_services import RemoteExternalServicesStore
except Exception:
    RemoteExternalServicesStore = None


class LocalExternalServicesStore:
    """In-process store for the brain GUI. Avoids HTTP + client_config."""

    def __init__(self) -> None:
        self.last_error = ""
        self._rows: List[Dict[str, Any]] = []
        self._service = None

    def _gui_service(self):
        if self._service is None:
            from services.gui_api.external_services_service import ExternalServicesGuiService

            self._service = ExternalServicesGuiService()
        return self._service

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def refresh(self) -> List[Dict[str, Any]]:
        self.last_error = ""
        try:
            payload = self._gui_service().list_services()
            data = payload.get("data") if isinstance(payload, dict) else {}
            services = data.get("services") if isinstance(data, dict) else None
            if not isinstance(services, list):
                services = []
            self._rows = [row for row in services if isinstance(row, dict)]
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._rows = []
        return list(self._rows)

    def start(self, service_id: str) -> Dict[str, Any]:
        return self._gui_service().ensure({"id": service_id})

    def stop(self, service_id: str) -> Dict[str, Any]:
        return self._gui_service().stop({"id": service_id})

    def restart(self, service_id: str) -> Dict[str, Any]:
        self.stop(service_id)
        return self.start(service_id)

    def save(self, service_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        body = {"id": str(service_id or "").strip()}
        body.update(patch or {})
        return self._gui_service().save_services(body)


STATUS_TEXT = {
    "running": "运行中",
    "stopped": "未运行",
}


class ExternalServicesPage(QtWidgets.QWidget):
    _rows_ready = QtCore.Signal(object)
    _result_ready = QtCore.Signal(str)

    def __init__(self, parent=None, *, store: Optional[Any] = None):
        super().__init__(parent)
        self._store = store or LocalExternalServicesStore()
        self._rows: List[Dict[str, Any]] = []
        self._busy = threading.Lock()
        self._rows_ready.connect(self._render_rows)
        self._result_ready.connect(self._show_result)
        self._build_ui()
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._rows:
            self.refresh()

    # -------------------- UI --------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(
            "大脑依赖的本机服务在这里查看、编辑和开关（Ollama、GPT-SoVITS、NapCat）。"
            "启动位置和「随大脑启动」写在大脑配置里；连远程大脑时，路径是远程机器上的路径。"
        )
        intro.setObjectName("settingsPageDesc")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["服务", "状态", "实际地址", "说明"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_refresh = QtWidgets.QPushButton("刷新")
        self._btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(self._btn_refresh)

        self._btn_start = QtWidgets.QPushButton("启动")
        self._btn_start.clicked.connect(lambda: self._act("start"))
        btn_row.addWidget(self._btn_start)

        self._btn_stop = QtWidgets.QPushButton("停止")
        self._btn_stop.clicked.connect(lambda: self._act("stop"))
        btn_row.addWidget(self._btn_stop)

        self._btn_restart = QtWidgets.QPushButton("重启")
        self._btn_restart.clicked.connect(lambda: self._act("restart"))
        btn_row.addWidget(self._btn_restart)

        self._btn_edit = QtWidgets.QPushButton("编辑")
        self._btn_edit.clicked.connect(self._edit_selected)
        btn_row.addWidget(self._btn_edit)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._detail = QtWidgets.QLabel("")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

    # -------------------- 数据 --------------------

    def refresh(self):
        if self._store is None:
            self._status_label.setText("本页不可用：缺少远程服务适配模块。")
            return
        self._status_label.setText("正在加载…")

        def worker():
            rows: List[Dict[str, Any]] = []
            error = ""
            try:
                rows = self._store.refresh()
                error = str(getattr(self._store, "last_error", "") or "")
            except Exception as exc:  # 兜底：loading 一定要结束
                error = f"{type(exc).__name__}: {exc}"
            self._rows_ready.emit({"rows": rows, "error": error})

        threading.Thread(target=worker, name="svc-refresh", daemon=True).start()

    @QtCore.Slot(object)
    def _render_rows(self, payload):
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if rows is None:
            rows = payload if isinstance(payload, list) else []
        error = str(payload.get("error") or "") if isinstance(payload, dict) else ""

        if error:
            self._rows = list(rows) if rows else []
            self._table.setRowCount(0)
            self._status_label.setText(
                f"加载外部服务失败：{error}"
                "（检查大脑是否已连接、访问令牌是否正确）"
            )
            return

        self._rows = list(rows)
        if not self._rows:
            self._status_label.setText("没有拿到服务列表：请确认大脑已连接。")
            self._table.setRowCount(0)
            return
        self._status_label.setText("")
        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            sid = str(row.get("id") or "")
            label = str(row.get("label") or sid)
            running = bool(row.get("running"))
            configured = bool(str(row.get("command") or "").strip())
            if not configured:
                status = "未配置"
            else:
                status = STATUS_TEXT["running"] if running else STATUS_TEXT["stopped"]
            if bool(row.get("autostart_enabled")):
                status += " · 随大脑启动"
            addr = ""
            live_host = str(row.get("live_host") or "")
            live_port = row.get("live_port")
            if live_host and live_port:
                addr = f"{live_host}:{live_port}"
                if (
                    running
                    and str(row.get("port"))
                    and int(row.get("port") or 0) != int(live_port)
                ):
                    addr += "（配置端口为 %s）" % row.get("port")

            self._table.setItem(i, 0, QtWidgets.QTableWidgetItem(label))
            self._table.setItem(i, 1, QtWidgets.QTableWidgetItem(status))
            self._table.setItem(i, 2, QtWidgets.QTableWidgetItem(addr))
            self._table.setItem(i, 3, QtWidgets.QTableWidgetItem(str(row.get("message") or "")))

    def _selected_service(self) -> Optional[Dict[str, Any]]:
        index = self._table.currentRow()
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def _act(self, action: str):
        service = self._selected_service()
        if service is None:
            self._detail.setText("先在表格里选中一个服务。")
            return
        if self._store is None:
            self._detail.setText("本页不可用。")
            return
        sid = str(service.get("id") or "")
        if not self._busy.acquire(blocking=False):
            self._detail.setText("上一个操作还在执行，请稍候…")
            return

        def worker():
            try:
                if action == "start":
                    result = self._store.start(sid)
                elif action == "stop":
                    result = self._store.stop(sid)
                else:
                    result = self._store.restart(sid)
            except Exception as exc:  # result 必有定义，线程不得静默死掉
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._busy.release()
            if not isinstance(result, dict):
                result = {"ok": False, "error": str(result)}
            msg = str(
                result.get("message")
                or result.get("error")
                or ("已执行" if result.get("ok") else "失败")
            )
            self._result_ready.emit(f"{service.get('label') or sid}：{msg}")

        threading.Thread(target=worker, name=f"svc-{action}", daemon=True).start()

    @QtCore.Slot(str)
    def _show_result(self, text: str):
        self._detail.setText(text)
        self.refresh()

    def _edit_selected(self):
        service = self._selected_service()
        if service is None:
            self._detail.setText("先在表格里选中一个服务。")
            return
        if self._store is None or not hasattr(self._store, "save"):
            self._detail.setText("本页不可用。")
            return
        dialog = _ServiceEditDialog(self, service)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        patch = dialog.patch()
        sid = str(service.get("id") or "")
        if not self._busy.acquire(blocking=False):
            self._detail.setText("上一个操作还在执行，请稍候…")
            return

        def worker():
            try:
                result = self._store.save(sid, patch)
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._busy.release()
            if not isinstance(result, dict):
                result = {"ok": False, "error": str(result)}
            msg = str(
                result.get("error")
                or result.get("message")
                or ("已保存" if result.get("ok") else "保存失败")
            )
            self._result_ready.emit(f"{service.get('label') or sid}：{msg}")

        threading.Thread(target=worker, name="svc-save", daemon=True).start()


class _ServiceEditDialog(QtWidgets.QDialog):
    def __init__(self, parent, service: Dict[str, Any]):
        super().__init__(parent)
        self.setWindowTitle(f"编辑 {service.get('label') or service.get('id') or '服务'}")
        self.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        path_row = QtWidgets.QHBoxLayout()
        self._command = QtWidgets.QLineEdit(str(service.get("command") or ""))
        self._command.setPlaceholderText("可执行文件路径（大脑所在机器）")
        browse = QtWidgets.QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self._command, 1)
        path_row.addWidget(browse)
        form.addRow("启动位置", path_row)

        self._autostart = QtWidgets.QCheckBox("大脑启动时自动拉起")
        self._autostart.setChecked(bool(service.get("autostart_enabled")))
        form.addRow("自启", self._autostart)

        self._autostop = QtWidgets.QCheckBox("大脑退出时自动停止（仅停本会话拉起的进程）")
        self._autostop.setChecked(bool(service.get("autostop_enabled")))
        form.addRow("自停", self._autostop)

        layout.addLayout(form)
        hint = QtWidgets.QLabel("留空启动位置时，大脑会按常见安装路径自动发现。")
        hint.setObjectName("settingsPageDesc")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择可执行文件", self._command.text().strip() or "", "程序 (*.exe);;所有文件 (*)"
        )
        if path:
            self._command.setText(path)

    def patch(self) -> Dict[str, Any]:
        return {
            "command": self._command.text().strip(),
            "autostart_enabled": self._autostart.isChecked(),
            "autostop_enabled": self._autostop.isChecked(),
        }
