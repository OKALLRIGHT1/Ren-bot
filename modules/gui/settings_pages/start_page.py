"""First-run checklist: character, chat model, optional Live2D window."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets

from modules.gui.user_ia import chat_setup_status

try:
    import client_config
except Exception:
    client_config = None


class StartSettingsPage(QtWidgets.QWidget):
    def __init__(
        self,
        parent=None,
        *,
        main_app=None,
        on_open_page: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.main_app = main_app
        self._on_open_page = on_open_page
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(
            "桌面上的角色是她本人；这个窗口是遥控器。先让她能回一句话，再用到的能力再开。"
        )
        intro.setObjectName("settingsPageDesc")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("settingsPageTitle")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        card = QtWidgets.QFrame()
        card.setObjectName("settingsHeaderCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        self._character_row = self._make_row("角色")
        self._model_row = self._make_row("聊天模型")
        self._live2d_row = self._make_row("桌面形象")
        card_layout.addLayout(self._character_row)
        card_layout.addLayout(self._model_row)
        card_layout.addLayout(self._live2d_row)
        layout.addWidget(card)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        self._btn_character = QtWidgets.QPushButton("去设置角色")
        self._btn_model = QtWidgets.QPushButton("去填聊天模型")
        self._btn_talk = QtWidgets.QPushButton("说「你好」试一下")
        self._btn_talk.setObjectName("primaryAction")
        self._btn_health = QtWidgets.QPushButton("运行健康")
        self._btn_health.setToolTip("查看各组件运行健康状态")
        for btn in (self._btn_character, self._btn_model, self._btn_talk, self._btn_health):
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
        self._btn_character.clicked.connect(lambda: self._open("character"))
        self._btn_model.clicked.connect(lambda: self._open("llm"))
        self._btn_talk.clicked.connect(self._try_hello)
        self._btn_health.clicked.connect(self._open_runtime_health)
        actions.addWidget(self._btn_character)
        actions.addWidget(self._btn_model)
        actions.addWidget(self._btn_talk)
        actions.addWidget(self._btn_health)
        actions.addStretch(1)
        layout.addLayout(actions)

        hint = QtWidgets.QLabel(
            "桌面形象是另一个窗口。没连上也能打字聊天，但气泡和动作不会出现。"
        )
        hint.setObjectName("settingsPageDesc")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        live2d_card = QtWidgets.QFrame()
        live2d_card.setObjectName("settingsHeaderCard")
        live2d_form = QtWidgets.QFormLayout(live2d_card)
        live2d_form.setContentsMargins(14, 10, 14, 10)
        live2d_form.setSpacing(8)
        live2d_title = QtWidgets.QLabel("桌面形象程序")
        live2d_title.setObjectName("settingsCardTitle")
        live2d_form.addRow(live2d_title)
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
        self._auto_l2d_check = QtWidgets.QCheckBox("启动后自动打开桌面形象")
        self._auto_l2d_check.setTristate(False)
        self._auto_l2d_check.setCheckState(QtCore.Qt.CheckState.Checked)
        self._auto_l2d_check.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        live2d_form.addRow("", self._auto_l2d_check)
        btn_row = QtWidgets.QHBoxLayout()
        self._btn_save_l2d = QtWidgets.QPushButton("保存桌面形象设置")
        self._btn_save_l2d.setFixedHeight(30)
        self._btn_save_l2d.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_save_l2d.clicked.connect(self._save_live2d)
        self._btn_start_l2d = QtWidgets.QPushButton("立即启动")
        self._btn_start_l2d.setFixedHeight(30)
        self._btn_start_l2d.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_start_l2d.clicked.connect(self._start_live2d)
        btn_row.addWidget(self._btn_save_l2d)
        btn_row.addWidget(self._btn_start_l2d)
        btn_row.addStretch(1)
        live2d_form.addRow("", btn_row)
        self._l2d_hint = QtWidgets.QLabel("路径改完点保存。下次启动或点「立即启动」才会用新程序。")
        self._l2d_hint.setObjectName("settingsPageDesc")
        self._l2d_hint.setWordWrap(True)
        live2d_form.addRow(self._l2d_hint)
        layout.addWidget(live2d_card)
        layout.addStretch(1)

    def _make_row(self, title: str) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        mark = QtWidgets.QLabel("○")
        mark.setFixedWidth(22)
        label = QtWidgets.QLabel(title)
        label.setWordWrap(True)
        row.addWidget(mark, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        row._mark = mark  # type: ignore[attr-defined]
        row._label = label  # type: ignore[attr-defined]
        return row

    def _set_row(self, row: QtWidgets.QHBoxLayout, *, ok: bool, text: str, warn: bool = False):
        mark = row._mark  # type: ignore[attr-defined]
        label = row._label  # type: ignore[attr-defined]
        if ok and not warn:
            mark.setText("●")
            mark.setStyleSheet("color:#16A34A; font-size:16px;")
        elif warn:
            mark.setText("●")
            mark.setStyleSheet("color:#D97706; font-size:16px;")
        else:
            mark.setText("○")
            mark.setStyleSheet("color:#9CA3AF; font-size:16px;")
        label.setText(text)

    def _open(self, page_id: str):
        if callable(self._on_open_page):
            self._on_open_page(page_id)

    def _open_runtime_health(self):
        app = self.main_app
        if app is not None and hasattr(app, "_on_runtime_health_clicked"):
            app._on_runtime_health_clicked()
            return
        if callable(self._on_open_page):
            self._on_open_page("runtime_health")

    def _browse_exe(self):
        start = (self._exe_edit.text() or "").strip()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Live2D 程序", start, "可执行文件 (*.exe);;所有文件 (*)"
        )
        if path:
            self._exe_edit.setText(path)

    def _save_live2d(self):
        if client_config is None:
            QtWidgets.QMessageBox.warning(self, "桌面形象", "客户端配置模块不可用。")
            return
        patch = {
            "live2d_exe": (self._exe_edit.text() or "").strip(),
            "auto_start_live2d": self._auto_l2d_check.isChecked(),
        }
        client_config.save(patch)
        self._l2d_hint.setText("已保存。下次启动会用这个程序；也可以点「立即启动」。")

    def _start_live2d(self):
        self._save_live2d()
        app = self.main_app
        callback = getattr(app, "on_start_live2d_callback", None)
        if not callable(callback):
            owner = getattr(app, "app", None) if app is not None else None
            callback = getattr(owner, "_start_live2d_renderer_when_ready", None)
        if not callable(callback):
            QtWidgets.QMessageBox.information(self, "桌面形象", "启动入口还没接上。")
            return
        try:
            callback()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "桌面形象", f"启动失败：{exc}")

    def _start_backend(self):
        app = self.main_app
        callback = getattr(app, "on_start_backend_callback", None)
        if not callable(callback):
            QtWidgets.QMessageBox.information(
                self, "启动本机后端", "这个入口还没接上（独立模式外启动的窗口）。"
            )
            return
        try:
            callback()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "启动本机后端", f"没能发起启动：{exc}")
            return

    def _backend_alive(self) -> bool:
        app = self.main_app
        if app is not None and getattr(app, "brain", None) is not None:
            return True
        try:
            from runtime import backend_alive

            return backend_alive(timeout=0.2)
        except Exception:
            return False

    def _live2d_state(self, fallback: str) -> str:
        app = self.main_app
        owner = getattr(app, "app", None) if app is not None else None
        if owner is not None and hasattr(owner, "_has_live2d_protocol_client"):
            try:
                return "connected" if owner._has_live2d_protocol_client() else "offline"
            except Exception:
                return fallback
        return fallback

    def _try_hello(self):
        app = self.main_app
        if app is None:
            QtWidgets.QMessageBox.information(
                self, "开始使用", "主程序还没接上，稍后再试。"
            )
            return
        try:
            if hasattr(app, "_switch_to_panel"):
                app._switch_to_panel()
            if hasattr(app, "ensure_history_visible"):
                app.ensure_history_visible()
            if hasattr(app, "append"):
                app.append("system", "正在试着打个招呼…")
            if hasattr(app, "_dispatch_send"):
                app._dispatch_send("你好")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "开始使用", f"没能发出去：{exc}")

    def refresh(self):
        health = getattr(self.main_app, "runtime_health", None) if self.main_app else None
        status = chat_setup_status(runtime_health=health)
        if status["ready"]:
            self._status.setText("可以开始聊天了")
        else:
            self._status.setText("还不能聊天")

        if status["character_ok"]:
            name = status["character_name"] or "已选择角色"
            self._set_row(self._character_row, ok=True, text=f"角色：{name}")
        else:
            self._set_row(
                self._character_row,
                ok=False,
                text="还没有角色。先到「形象与人设」选一个或新建。",
            )

        if status["model_ok"] and status["model_local_only"]:
            self._set_row(
                self._model_row,
                ok=True,
                warn=True,
                text=f"聊天模型：{status['model_label']}（本机）。请确认本机服务已打开。",
            )
        elif status["model_ok"]:
            self._set_row(
                self._model_row,
                ok=True,
                text=f"聊天模型：{status['model_label']}",
            )
        else:
            self._set_row(
                self._model_row,
                ok=False,
                text="还没有可用的聊天模型。到「聊天模型」添加一个并填入 Key。",
            )

        enabled = True
        stored = {}
        if client_config is not None:
            stored = client_config.load()
            if not self._exe_edit.hasFocus():
                self._exe_edit.setText(str(stored.get("live2d_exe") or ""))
            value = stored.get("auto_start_live2d", True)
            if isinstance(value, bool):
                enabled = value
            elif str(value).strip().lower() in {"0", "false", "no", "off"}:
                enabled = False
        self._auto_l2d_check.blockSignals(True)
        self._auto_l2d_check.setCheckState(
            QtCore.Qt.CheckState.Checked if enabled else QtCore.Qt.CheckState.Unchecked
        )
        self._auto_l2d_check.blockSignals(False)

        live2d = self._live2d_state(status["live2d_state"])
        if live2d == "connected":
            self._set_row(self._live2d_row, ok=True, text="桌面形象已连接。气泡和动作会走那边。")
        elif live2d == "offline":
            self._set_row(
                self._live2d_row,
                ok=False,
                warn=True,
                text="桌面形象还没连上。可以先打字；要看到她，请先打开 Live2D 桌面端。",
            )
        else:
            self._set_row(
                self._live2d_row,
                ok=False,
                text="桌面形象状态还不知道。主程序起来并打开桌面端后会显示在这里。",
            )

        self._btn_talk.setEnabled(bool(self.main_app) and bool(status["ready"]))
        if not status["ready"]:
            self._btn_talk.setToolTip("角色和聊天模型都就绪后才能试聊。")
        else:
            self._btn_talk.setToolTip("向她发送「你好」，并展开遥控窗对话记录。")
