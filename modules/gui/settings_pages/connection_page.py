"""Connection settings: where this remote control points at.

Two sections:
- 后端大脑：ws/http 地址 + 访问令牌 + 连接测试
- 桌面形象（Live2D）：exe 路径 + 启动/自启开关

Settings persist to the client-local client_settings.json (never the brain's
data dir), so they survive pointing the client at a remote brain.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6 import QtCore, QtWidgets

import client_config


class ConnectionSettingsPage(QtWidgets.QWidget):
    _environment_detected = QtCore.Signal(object)

    def __init__(
        self,
        parent=None,
        *,
        main_app=None,
        on_restart_connection: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.main_app = main_app
        self._on_restart_connection = on_restart_connection
        self._build_ui()
        self._environment_detected.connect(self._apply_environment_status)
        self.refresh()

    # -------------------- UI --------------------
    def _build_ui(self):
        from modules.gui.utils import wrap_in_scroll_area

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        cards_container = QtWidgets.QWidget()
        cards_layout = QtWidgets.QVBoxLayout(cards_container)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)

        # ---------- 后端 ----------
        backend_card = QtWidgets.QFrame()
        backend_card.setObjectName("settingsHeaderCard")
        form = QtWidgets.QFormLayout(backend_card)
        form.setContentsMargins(14, 10, 14, 10)
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        title = QtWidgets.QLabel("后端大脑")
        title.setObjectName("settingsCardTitle")
        form.addRow(title)

        brain_row = QtWidgets.QHBoxLayout()
        brain_row.setSpacing(8)
        self._brain_root_edit = QtWidgets.QLineEdit()
        self._brain_root_edit.setFixedHeight(32)
        self._brain_root_edit.setPlaceholderText("包含 boot.py 和 modules 的外部大脑目录")
        self._btn_browse_brain = QtWidgets.QPushButton("浏览…")
        self._btn_browse_brain.setFixedHeight(30)
        self._btn_browse_brain.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_browse_brain.clicked.connect(self._browse_brain_root)
        self._btn_detect_brain = QtWidgets.QPushButton("自动检测")
        self._btn_detect_brain.setFixedHeight(30)
        self._btn_detect_brain.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_detect_brain.clicked.connect(self._detect_environment)
        brain_row.addWidget(self._brain_root_edit, 1)
        brain_row.addWidget(self._btn_browse_brain)
        brain_row.addWidget(self._btn_detect_brain)
        form.addRow("大脑目录", brain_row)

        python_row = QtWidgets.QHBoxLayout()
        python_row.setSpacing(8)
        self._python_edit = QtWidgets.QLineEdit()
        self._python_edit.setFixedHeight(32)
        self._python_edit.setPlaceholderText("启动外部大脑使用的 python.exe")
        self._btn_browse_python = QtWidgets.QPushButton("浏览…")
        self._btn_browse_python.setFixedHeight(30)
        self._btn_browse_python.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_browse_python.clicked.connect(self._browse_python)
        self._btn_detect_python = QtWidgets.QPushButton("自动检测")
        self._btn_detect_python.setFixedHeight(30)
        self._btn_detect_python.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_detect_python.clicked.connect(self._detect_environment)
        python_row.addWidget(self._python_edit, 1)
        python_row.addWidget(self._btn_browse_python)
        python_row.addWidget(self._btn_detect_python)
        form.addRow("Python 程序", python_row)

        brain_hint = QtWidgets.QLabel("目录修改后需重启客户端。环境变量 SUZU_BRAIN_ROOT、SUZU_PYTHON/PYTHON 始终优先。")
        brain_hint.setObjectName("settingsPageDesc")
        brain_hint.setWordWrap(True)
        form.addRow("", brain_hint)

        self._environment_status = QtWidgets.QLabel("")
        self._environment_status.setObjectName("settingsPageDesc")
        self._environment_status.setWordWrap(True)
        form.addRow("环境状态", self._environment_status)

        self._host_edit = QtWidgets.QLineEdit()
        self._host_edit.setFixedHeight(32)
        self._host_edit.setPlaceholderText("例如 127.0.0.1 或 192.168.1.10:8097")
        form.addRow("后端地址", self._host_edit)

        self._ws_edit = QtWidgets.QLineEdit()
        self._ws_edit.setFixedHeight(32)
        self._ws_edit.setPlaceholderText("ws://127.0.0.1:8096/gui（留空按地址推导）")
        form.addRow("WS 地址", self._ws_edit)

        self._http_edit = QtWidgets.QLineEdit()
        self._http_edit.setFixedHeight(32)
        self._http_edit.setPlaceholderText("http://127.0.0.1:8097/gui（留空按地址推导）")
        form.addRow("HTTP 地址", self._http_edit)

        self._token_edit = QtWidgets.QLineEdit()
        self._token_edit.setFixedHeight(32)
        self._token_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText("大脑 data/runtime_settings.json 里的 gui_access_token")
        form.addRow("访问令牌", self._token_edit)

        self._ws_edit.textEdited.connect(lambda _: self._host_edit.setStyleSheet(""))
        self._host_edit.textEdited.connect(self._on_host_edited)

        test_row = QtWidgets.QHBoxLayout()
        test_row.setSpacing(10)
        self._btn_test = QtWidgets.QPushButton("测试连接")
        self._btn_test.setObjectName("primaryAction")
        self._btn_test.setFixedHeight(30)
        self._btn_test.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_test.clicked.connect(self._test_connection)
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setWordWrap(True)
        test_row.addWidget(self._btn_test)
        test_row.addWidget(self._status_label, 1)
        form.addRow("", test_row)
        cards_layout.addWidget(backend_card)

        # ---------- Live2D ----------
        live2d_card = QtWidgets.QFrame()
        live2d_card.setObjectName("settingsHeaderCard")
        live2d_form = QtWidgets.QFormLayout(live2d_card)
        live2d_form.setContentsMargins(14, 10, 14, 10)
        live2d_form.setSpacing(8)
        live2d_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        l2d_title = QtWidgets.QLabel("桌面形象（Live2D）")
        l2d_title.setObjectName("settingsCardTitle")
        live2d_form.addRow(l2d_title)

        exe_row = QtWidgets.QHBoxLayout()
        exe_row.setSpacing(8)
        self._exe_edit = QtWidgets.QLineEdit()
        self._exe_edit.setFixedHeight(32)
        self._exe_edit.setPlaceholderText("live2d-enhanced.exe 完整路径，留空自动搜索")
        btn_browse = QtWidgets.QPushButton("浏览…")
        btn_browse.setFixedHeight(30)
        btn_browse.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._browse_exe)
        exe_row.addWidget(self._exe_edit, 1)
        exe_row.addWidget(btn_browse)
        live2d_form.addRow("程序路径", exe_row)

        self._auto_l2d_check = QtWidgets.QCheckBox("后端就绪后自动启动桌面形象")
        self._auto_l2d_check.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        live2d_form.addRow("", self._auto_l2d_check)

        self._auto_backend_check = QtWidgets.QCheckBox("客户端启动时自动启动本机大脑")
        self._auto_backend_check.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._auto_backend_check.setToolTip(
            "勾选后客户端启动会自动拉起本机 boot.py（run.bat 默认行为）。"
            "若已配置远程大脑地址，此项自动失效。"
        )
        live2d_form.addRow("", self._auto_backend_check)

        l2d_btn_row = QtWidgets.QHBoxLayout()
        self._btn_start_l2d = QtWidgets.QPushButton("启动桌面形象")
        self._btn_start_l2d.setFixedHeight(30)
        self._btn_start_l2d.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_start_l2d.clicked.connect(self._start_live2d)
        l2d_btn_row.addWidget(self._btn_start_l2d)
        l2d_btn_row.addStretch(1)
        live2d_form.addRow("", l2d_btn_row)
        cards_layout.addWidget(live2d_card)

        cards_layout.addStretch(1)
        scroll = wrap_in_scroll_area(cards_container)
        root.addWidget(scroll, 1)

        # ---------- 底部动作 ----------
        actions_bar = QtWidgets.QFrame()
        actions_bar.setObjectName("settingsActionBar")
        actions = QtWidgets.QHBoxLayout(actions_bar)
        actions.setContentsMargins(14, 10, 14, 10)
        actions.setSpacing(10)

        self._btn_save = QtWidgets.QPushButton("保存")
        self._btn_save.setObjectName("primaryAction")
        self._btn_save.setFixedHeight(32)
        self._btn_save.setMinimumWidth(80)
        self._btn_save.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._save)

        self._btn_reset = QtWidgets.QPushButton("恢复默认（本机）")
        self._btn_reset.setFixedHeight(32)
        self._btn_reset.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_reset.clicked.connect(self._reset_local)

        self._save_hint = QtWidgets.QLabel("")
        self._save_hint.setObjectName("settingsPageDesc")
        self._save_hint.setWordWrap(True)

        actions.addWidget(self._btn_save)
        actions.addWidget(self._btn_reset)
        actions.addWidget(self._save_hint, 1)
        root.addWidget(actions_bar)

    # -------------------- helpers --------------------
    def _on_host_edited(self):
        base = self._host_edit.text().strip()
        if not base:
            return
        derived = client_config.derive_urls(base)
        self._ws_edit.setText(derived["backend_ws_url"])
        self._http_edit.setText(derived["backend_http_url"])

    def _browse_brain_root(self):
        start = (self._brain_root_edit.text() or "").strip()
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择外部大脑目录", start
        )
        if path:
            self._brain_root_edit.setText(path)

    def _browse_python(self):
        start = (self._python_edit.text() or "").strip()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Python 程序", start, "Python 程序 (python.exe python);;所有文件 (*)"
        )
        if path:
            self._python_edit.setText(path)
            self._detect_environment()

    def _browse_exe(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Live2D 程序", "", "可执行文件 (*.exe);;所有文件 (*)"
        )
        if path:
            self._exe_edit.setText(path)

    def _detect_environment(self):
        if not self._btn_detect_brain.isEnabled():
            return
        self._btn_detect_brain.setEnabled(False)
        self._btn_detect_python.setEnabled(False)
        self._environment_status.setText("正在检测大脑和 Python 环境…")
        requested_brain = (self._brain_root_edit.text() or "").strip()
        requested_python = (self._python_edit.text() or "").strip()

        def worker():
            from paths import detect_brain_root, probe_python, resolve_python_exe

            brain = detect_brain_root(configured=requested_brain)
            python_exe = resolve_python_exe(
                brain,
                configured=requested_python,
            )
            status = probe_python(python_exe)
            status["brain"] = str(brain or "")
            status["python"] = str(python_exe or "")
            self._environment_detected.emit(status)

        threading.Thread(
            target=worker,
            name="suzu-environment-detect",
            daemon=True,
        ).start()

    def _apply_environment_status(self, status):
        self._btn_detect_brain.setEnabled(True)
        self._btn_detect_python.setEnabled(True)
        brain = str(status.get("brain") or "")
        python_exe = str(status.get("python") or "")
        if brain:
            self._brain_root_edit.setText(brain)
        if python_exe:
            self._python_edit.setText(python_exe)

        parts = []
        if brain:
            parts.append(f"大脑：已找到 {brain}")
        else:
            parts.append("大脑：未找到包含 boot.py 和 modules 的目录")
        if not python_exe:
            parts.append("Python：未找到")
        else:
            version = str(status.get("version") or "未知版本")
            missing = status.get("missing") or []
            if missing:
                parts.append(
                    f"Python：{version}，缺少关键依赖："
                    + "、".join(str(item) for item in missing)
                )
            elif status.get("ok"):
                parts.append(f"Python：{version}，关键依赖正常")
            else:
                parts.append(
                    f"Python：{version}，检测失败：{status.get('error') or '未知错误'}"
                )
        self._environment_status.setText("\n".join(parts))

    def _test_connection(self):
        http_url = (self._http_edit.text() or "").strip().rstrip("/")
        token = (self._token_edit.text() or "").strip()
        self._status_label.setText("正在测试…")
        QtCore.QTimer.singleShot(10, lambda: self._test_connection_async(http_url, token))

    def _test_connection_async(self, http_url: str, token: str):
        try:
            import urllib.error
            import urllib.request

            request = urllib.request.Request(f"{http_url}/runtime/status")
            if token:
                request.add_header("X-GUI-Token", token)
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = response.status
            self._status_label.setText(f"✅ 连接成功（HTTP {payload}）")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._status_label.setText("⚠️ 服务在线但令牌不对（401）。")
            else:
                self._status_label.setText(f"⚠️ 服务在线但返回 {exc.code}。")
        except Exception as exc:
            self._status_label.setText(f"❌ 连不上：{exc}")

    def _start_live2d(self):
        app = self.main_app
        callback = getattr(app, "on_start_live2d_callback", None)
        if not callable(callback):
            owner = getattr(app, "app", None) if app is not None else None
            if hasattr(owner, "start_live2d_renderer"):
                callback = lambda: owner.start_live2d_renderer(force=True)
            else:
                callback = getattr(owner, "_start_live2d_renderer_when_ready", None)
        if not callable(callback):
            QtWidgets.QMessageBox.information(
                self, "桌面形象", "启动入口还没接上，请先用「启动本机后端」。"
            )
            return
        try:
            callback()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "桌面形象", f"启动失败：{exc}")

    def _save(self):
        patch: Dict[str, Any] = {}
        brain_root = (self._brain_root_edit.text() or "").strip()
        if brain_root:
            boot = os.path.isfile(os.path.join(brain_root, "boot.py"))
            modules = os.path.isdir(os.path.join(brain_root, "modules"))
            if not (boot and modules):
                QtWidgets.QMessageBox.warning(
                    self,
                    "大脑目录无效",
                    "所选目录必须同时包含 boot.py 文件和 modules 目录。",
                )
                return
        patch["brain_root"] = brain_root
        python_exe = (self._python_edit.text() or "").strip()
        if python_exe and not os.path.isfile(python_exe):
            QtWidgets.QMessageBox.warning(
                self,
                "Python 程序无效",
                "所选 Python 程序不存在，请重新选择或使用自动检测。",
            )
            return
        patch["python_exe"] = python_exe
        ws_url = (self._ws_edit.text() or "").strip()
        http_url = (self._http_edit.text() or "").strip()
        patch["backend_ws_url"] = ws_url
        patch["backend_http_url"] = http_url
        patch["backend_token"] = (self._token_edit.text() or "").strip()
        patch["live2d_exe"] = (self._exe_edit.text() or "").strip()
        patch["auto_start_live2d"] = self._auto_l2d_check.isChecked()
        patch["auto_start_backend"] = self._auto_backend_check.isChecked()
        client_config.save(patch)
        self.refresh()
        self._save_hint.setText(
            "已保存到 client_settings.json 并刷新。连接地址会自动重连；大脑和 Python 修改在下次启动本机大脑时生效。"
        )
        if callable(self._on_restart_connection):
            try:
                self._on_restart_connection()
            except Exception:
                pass

    def _reset_local(self):
        from paths import detect_brain_root, resolve_python_exe

        brain = detect_brain_root()
        self._brain_root_edit.setText(str(brain or ""))
        self._python_edit.setText(str(resolve_python_exe(brain) or ""))
        self._host_edit.setText("127.0.0.1")
        self._on_host_edited()
        self._save_hint.setText("点「保存」生效；大脑目录修改后需重启客户端。")

    def refresh(self):
        cfg = client_config.effective()
        stored = client_config.load()
        # env 管理的连接不让编辑误导：显示实际生效值
        ws = cfg.get("backend_ws_url") or "ws://127.0.0.1:8096/gui"
        http = cfg.get("backend_http_url") or "http://127.0.0.1:8097/gui"
        self._ws_edit.setText(str(ws))
        self._http_edit.setText(str(http))
        self._host_edit.setText(self._host_from_urls(str(ws), str(http)))
        self._token_edit.setText(str(stored.get("backend_token") or ""))
        self._brain_root_edit.setText(str(cfg.get("brain_root") or ""))
        self._python_edit.setText(str(cfg.get("python_exe") or ""))
        self._exe_edit.setText(str(stored.get("live2d_exe") or ""))
        self._auto_l2d_check.setChecked(bool(stored.get("auto_start_live2d", True)))
        self._auto_backend_check.setChecked(bool(stored.get("auto_start_backend", False)))

    @staticmethod
    def _host_from_urls(ws_url: str, http_url: str) -> str:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(http_url or ws_url)
            host = parsed.hostname or ""
            port = parsed.port or 0
            if not host:
                return ""
            if port in (0, 8097):
                return host
            return f"{host}:{port}"
        except Exception:
            return ""
