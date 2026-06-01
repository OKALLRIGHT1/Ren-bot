from __future__ import annotations

import asyncio
import html
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import get_tool_dialog_styles, get_ui_palette
from modules.runtime_settings import load_runtime_settings, update_runtime_settings


class _ExternalAgentWorker(QtCore.QObject):
    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, request_data: Dict):
        super().__init__()
        self._request_data = request_data

    @QtCore.Slot()
    def run(self):
        try:
            from modules.code_agent import CodeAgentRequest, run_code_agent

            request = CodeAgentRequest(**self._request_data)
            result = asyncio.run(run_code_agent(request))
            self.finished.emit(asdict(result))
        except Exception as exc:
            self.failed.emit(str(exc))


class CodexAssistantDialog(QtWidgets.QDialog):
    REFRESH_INTERVAL_MS = 1200
    MAX_EVENTS = 80

    def __init__(self, parent=None, on_submit: Optional[Callable[[str, Dict], None]] = None):
        super().__init__(parent)
        self.on_submit = on_submit
        self._runtime = load_runtime_settings()
        self._active_task_id = str(self._runtime.get("codex_last_task_id", "")).strip()
        self._last_history_html = ""
        self._agent_thread: Optional[QtCore.QThread] = None
        self._agent_worker: Optional[_ExternalAgentWorker] = None

        self.setWindowTitle("代码代理")
        self.resize(900, 600)
        self.setMinimumSize(760, 480)
        self.setModal(False)
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, False)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(get_tool_dialog_styles())

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        shell = QtWidgets.QFrame()
        shell.setObjectName("dialogShell")
        outer.addWidget(shell)

        layout = QtWidgets.QVBoxLayout(shell)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("代码代理")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        section = QtWidgets.QFrame()
        section.setObjectName("dialogSection")
        section_layout = QtWidgets.QVBoxLayout(section)
        section_layout.setContentsMargins(12, 10, 12, 10)
        section_layout.setSpacing(8)

        self.chk_mode = QtWidgets.QCheckBox("启用代码助手模式 (Codex)")
        self.chk_mode.setChecked(bool(self._runtime.get("codex_mode_enabled", False)))
        self.chk_mode.toggled.connect(self._on_mode_toggled)
        section_layout.addWidget(self.chk_mode)

        provider_row = QtWidgets.QHBoxLayout()
        provider_row.addWidget(QtWidgets.QLabel("代理类型"))
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItem("内置助手", "internal")
        self.provider_combo.addItem("自定义 CLI", "custom_cli")
        self.provider_combo.addItem("Codex CLI", "codex_cli")
        self.provider_combo.addItem("Claude Code", "claude_code")
        saved_provider = str(self._runtime.get("code_agent_provider", "internal")).strip()
        provider_index = self.provider_combo.findData(saved_provider)
        self.provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo, 1)
        section_layout.addLayout(provider_row)

        path_row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("代码路径 (文件或目录，留空则使用项目根目录)")
        self.path_edit.setText(str(self._runtime.get("codex_last_path", "")))
        path_row.addWidget(self.path_edit, 1)

        btn_file = QtWidgets.QPushButton("选择文件")
        btn_file.clicked.connect(self._pick_file)
        path_row.addWidget(btn_file)

        btn_dir = QtWidgets.QPushButton("选择目录")
        btn_dir.clicked.connect(self._pick_dir)
        path_row.addWidget(btn_dir)
        section_layout.addLayout(path_row)

        perm_row = QtWidgets.QHBoxLayout()
        self.chk_allow_read = QtWidgets.QCheckBox("允许读取")
        self.chk_allow_read.setChecked(True)
        self.chk_allow_read.setEnabled(False)
        perm_row.addWidget(self.chk_allow_read)

        self.chk_allow_write = QtWidgets.QCheckBox("允许写入")
        self.chk_allow_write.setChecked(bool(self._runtime.get("codex_allow_write", False)))
        perm_row.addWidget(self.chk_allow_write)

        self.chk_allow_exec = QtWidgets.QCheckBox("允许执行命令")
        self.chk_allow_exec.setChecked(bool(self._runtime.get("codex_allow_exec", False)))
        perm_row.addWidget(self.chk_allow_exec)

        self.chk_autorun = QtWidgets.QCheckBox("变更后自动验证")
        self.chk_autorun.setChecked(bool(self._runtime.get("codex_autorun", False)))
        perm_row.addWidget(self.chk_autorun)
        perm_row.addStretch()
        section_layout.addLayout(perm_row)

        cli_row = QtWidgets.QHBoxLayout()
        self.command_edit = QtWidgets.QLineEdit()
        self.command_edit.setPlaceholderText("外部 CLI 命令模板，例如: codex exec {prompt}")
        self.command_edit.setText(str(self._runtime.get("code_agent_command_template", "")))
        cli_row.addWidget(self.command_edit, 1)

        self.btn_detect_agent = QtWidgets.QPushButton("自动检测")
        self.btn_detect_agent.clicked.connect(lambda: self._detect_agent_command(show_message=True))
        cli_row.addWidget(self.btn_detect_agent)

        self.btn_pick_agent = QtWidgets.QPushButton("选择程序")
        self.btn_pick_agent.clicked.connect(self._pick_agent_executable)
        cli_row.addWidget(self.btn_pick_agent)

        self.timeout_spin = QtWidgets.QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(int(self._runtime.get("code_agent_timeout_sec", 300) or 300))
        cli_row.addWidget(self.timeout_spin)
        section_layout.addLayout(cli_row)

        self.cli_hint = QtWidgets.QLabel("外部 CLI 不经过 Live2D/TTS；命令模板不使用 shell，{prompt} 会作为单独参数传入。")
        self.cli_hint.setObjectName("dialogHint")
        self.cli_hint.setWordWrap(True)
        section_layout.addWidget(self.cli_hint)
        layout.addWidget(section)

        history_card = QtWidgets.QFrame()
        history_card.setObjectName("dialogSection")
        history_layout = QtWidgets.QVBoxLayout(history_card)
        history_layout.setContentsMargins(12, 10, 12, 10)
        history_layout.setSpacing(6)

        history_head = QtWidgets.QHBoxLayout()
        history_title = QtWidgets.QLabel("对话 / 思考 / 变更确认")
        history_title.setObjectName("dialogHint")
        history_head.addWidget(history_title)
        history_head.addStretch()
        btn_refresh = QtWidgets.QPushButton("刷新")
        btn_refresh.clicked.connect(lambda: self._refresh_history(force=True))
        history_head.addWidget(btn_refresh)
        history_layout.addLayout(history_head)

        self.state_label = QtWidgets.QLabel("任务状态: -")
        self.state_label.setObjectName("dialogHint")
        self.state_label.setWordWrap(True)
        history_layout.addWidget(self.state_label)

        self.history_view = QtWidgets.QTextBrowser()
        self.history_view.setObjectName("consoleView")
        self.history_view.setReadOnly(True)
        self.history_view.setMinimumHeight(180)
        self.history_view.document().setDocumentMargin(10)
        history_layout.addWidget(self.history_view, 1)

        layout.addWidget(history_card, 1)

        hint_card = QtWidgets.QFrame()
        hint_card.setObjectName("dialogSection")
        hint_layout = QtWidgets.QVBoxLayout(hint_card)
        hint_layout.setContentsMargins(12, 10, 12, 10)
        hint_layout.setSpacing(6)

        self.input_edit = QtWidgets.QTextEdit()
        self.input_edit.setAcceptRichText(False)
        self.input_edit.setPlaceholderText(
            "输入代码相关任务...\n"
            "例如：\n"
            "1) 帮我检查这个路径下有哪些 TODO\n"
            "2) 读取并解释某个文件\n"
            "3) 修改某函数并给出 diff 预览"
        )
        self.input_edit.setMinimumHeight(60)
        self.input_edit.setMaximumHeight(100)
        hint_layout.addWidget(self.input_edit, 1)
        layout.addWidget(hint_card, 0)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.btn_send = QtWidgets.QPushButton("发送到代码代理")
        self.btn_send.setObjectName("primaryAction")
        self.btn_send.clicked.connect(self._send)
        btn_row.addWidget(self.btn_send)
        layout.addLayout(btn_row)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._refresh_history)
        self._refresh_timer.start()

        self._on_mode_toggled(self.chk_mode.isChecked())
        self._on_provider_changed()
        self._refresh_history(force=True)

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._ensure_visible_on_screen)
        self._refresh_history(force=True)

    def _ensure_visible_on_screen(self):
        screen = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        margin = 12
        max_width = max(480, available.width() - margin * 2)
        max_height = max(420, available.height() - margin * 2)
        if self.width() > max_width or self.height() > max_height:
            self.resize(min(self.width(), max_width), min(self.height(), max_height))
        frame = self.frameGeometry()
        x = min(max(frame.x(), available.left() + margin), available.right() - frame.width() - margin)
        y = min(max(frame.y(), available.top() + margin), available.bottom() - frame.height() - margin)
        self.move(max(available.left() + margin, x), max(available.top() + margin, y))

    def _on_mode_toggled(self, enabled: bool):
        if self._selected_provider() != "internal":
            self.chk_allow_write.setEnabled(True)
            self.chk_allow_exec.setEnabled(True)
            self.chk_autorun.setEnabled(False)
            return
        self.chk_allow_write.setEnabled(bool(enabled))
        self.chk_allow_exec.setEnabled(bool(enabled))
        self.chk_autorun.setEnabled(bool(enabled))

    def _selected_provider(self) -> str:
        return str(self.provider_combo.currentData() or "internal")

    def _on_provider_changed(self):
        provider = self._selected_provider()
        external = provider != "internal"
        templates = {
            "codex_cli": "codex exec {prompt}",
            "claude_code": "claude -p {prompt}",
        }
        current_template = self.command_edit.text().strip()
        should_refresh_template = (
            not current_template
            or current_template in ("codex {prompt}", "claude {prompt}")
            or current_template.lower().endswith("\\codex.cmd {prompt}")
            or current_template.lower().endswith("\\claude.cmd {prompt}")
        )
        detected = False
        if provider in templates and should_refresh_template:
            detected = self._detect_agent_command(show_message=False)
            if not detected:
                self.command_edit.setText(templates[provider])
        self.chk_mode.setEnabled(not external)
        self.command_edit.setEnabled(external)
        self.btn_detect_agent.setEnabled(provider in ("codex_cli", "claude_code"))
        self.btn_pick_agent.setEnabled(external)
        self.timeout_spin.setEnabled(external)
        self.cli_hint.setVisible(external)
        if not detected:
            self._set_cli_hint()
        self._on_mode_toggled(self.chk_mode.isChecked())

    def _set_cli_hint(self, text: str = ""):
        if text:
            self.cli_hint.setText(text)
            return
        provider = self._selected_provider()
        if provider in ("codex_cli", "claude_code"):
            self.cli_hint.setText("会优先在 PATH 中检测本地 CLI；检测不到时可点“选择程序”手动指定 exe/cmd。")
        else:
            self.cli_hint.setText("外部 CLI 不经过 Live2D/TTS；命令模板不使用 shell，{prompt} 会作为单独参数传入。")

    def _detect_agent_command(self, show_message: bool = False) -> bool:
        provider = self._selected_provider()
        if provider not in ("codex_cli", "claude_code"):
            if show_message:
                QtWidgets.QMessageBox.information(self, "代码代理", "当前代理类型没有可自动检测的默认 CLI。")
            return False
        try:
            from modules.code_agent import discover_agent_command

            command = discover_agent_command(provider)
        except Exception as exc:
            command = ""
            if show_message:
                QtWidgets.QMessageBox.warning(self, "代码代理", f"自动检测失败: {exc}")
        if command:
            self.command_edit.setText(command)
            self._set_cli_hint(f"已检测到本地 CLI: {command}")
            if show_message:
                QtWidgets.QMessageBox.information(self, "代码代理", f"已检测到:\n{command}")
            return True
        self._set_cli_hint("未在 PATH 中检测到对应 CLI。可以手动填写命令模板，或点击“选择程序”。")
        if show_message:
            QtWidgets.QMessageBox.information(self, "代码代理", "未在 PATH 中检测到对应 CLI，请手动选择程序或填写命令模板。")
        return False

    def _pick_agent_executable(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择代码代理程序",
            str(Path.cwd()),
            "Executable (*.exe *.cmd *.bat);;All Files (*)",
        )
        if not path:
            return
        try:
            from modules.code_agent import build_command_template

            self.command_edit.setText(build_command_template(path, self._selected_provider()))
            self._set_cli_hint(f"已选择程序: {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "代码代理", f"选择程序失败: {exc}")

    def _pick_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择代码文件", str(Path.cwd()))
        if path:
            self.path_edit.setText(path)

    def _pick_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择代码目录", str(Path.cwd()))
        if path:
            self.path_edit.setText(path)

    def _send(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        task_id = uuid.uuid4().hex[:8]
        self._active_task_id = task_id
        provider = self._selected_provider()
        if provider != "internal":
            self._send_external(text, task_id, provider)
            return

        codex_mode = bool(self.chk_mode.isChecked())
        payload = {
            "source": "codex_input",
            "codex_mode": codex_mode,
            "codex_task_id": task_id,
            "code_path": self.path_edit.text().strip(),
            "allow_read": True and codex_mode,
            "allow_write": bool(self.chk_allow_write.isChecked()) and codex_mode,
            "allow_exec": bool(self.chk_allow_exec.isChecked()) and codex_mode,
            "codex_autorun": bool(self.chk_autorun.isChecked()) and codex_mode,
        }

        update_runtime_settings(
            {
                "codex_mode_enabled": payload["codex_mode"],
                "codex_last_task_id": payload["codex_task_id"],
                "codex_last_path": payload["code_path"],
                "codex_allow_read": payload["allow_read"],
                "codex_allow_write": payload["allow_write"],
                "codex_allow_exec": payload["allow_exec"],
                "codex_autorun": payload["codex_autorun"],
            }
        )

        if self.on_submit:
            self.on_submit(text, payload)
        self.input_edit.clear()
        self._refresh_history(force=True)

    def _send_external(self, text: str, task_id: str, provider: str):
        if self._agent_thread and self._agent_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "代码代理", "已有外部代理任务正在运行。")
            return
        if not self.chk_allow_exec.isChecked():
            QtWidgets.QMessageBox.warning(self, "代码代理", "外部 CLI 模式需要先勾选“允许执行命令”。")
            return

        code_path = self.path_edit.text().strip()
        command_template = self.command_edit.text().strip()
        request_data = {
            "provider": provider,
            "prompt": text,
            "cwd": code_path,
            "command_template": command_template,
            "timeout_sec": int(self.timeout_spin.value()),
            "allow_write": bool(self.chk_allow_write.isChecked()),
            "allow_exec": bool(self.chk_allow_exec.isChecked()),
            "task_id": task_id,
        }
        update_runtime_settings(
            {
                "code_agent_provider": provider,
                "code_agent_command_template": command_template,
                "code_agent_timeout_sec": int(self.timeout_spin.value()),
                "code_agent_last_path": code_path,
                "code_agent_allow_write": bool(self.chk_allow_write.isChecked()),
                "code_agent_allow_exec": bool(self.chk_allow_exec.isChecked()),
                "codex_last_task_id": task_id,
                "codex_last_path": code_path,
            }
        )

        try:
            from modules.codex_session import add_event
            from modules.codex_task_state import set_task_state

            meta = {"task_id": task_id, "provider": provider}
            add_event("user_task", user_text=text, code_path=code_path, meta=meta)
            add_event("external_agent_start", user_text=f"启动外部代码代理: {provider}", code_path=code_path, meta=meta)
            set_task_state(task_id, "external_running", code_path=code_path, summary=f"外部代码代理运行中: {provider}", meta=meta)
        except Exception:
            pass

        self.input_edit.clear()
        self.btn_send.setEnabled(False)
        self._refresh_history(force=True)

        self._agent_thread = QtCore.QThread(self)
        self._agent_worker = _ExternalAgentWorker(request_data)
        self._agent_worker.moveToThread(self._agent_thread)
        self._agent_thread.started.connect(self._agent_worker.run)
        self._agent_worker.finished.connect(lambda result: self._on_external_finished(task_id, code_path, provider, result))
        self._agent_worker.failed.connect(lambda error: self._on_external_failed(task_id, code_path, provider, error))
        self._agent_worker.finished.connect(self._agent_thread.quit)
        self._agent_worker.failed.connect(self._agent_thread.quit)
        self._agent_thread.finished.connect(self._agent_worker.deleteLater)
        self._agent_thread.finished.connect(self._agent_thread.deleteLater)
        self._agent_thread.finished.connect(self._clear_external_worker)
        self._agent_thread.start()

    def _on_external_finished(self, task_id: str, code_path: str, provider: str, result: Dict):
        try:
            from modules.codex_session import add_event
            from modules.codex_task_state import set_task_state

            meta = {
                "task_id": task_id,
                "provider": provider,
                "exit_code": result.get("exit_code"),
                "duration_sec": result.get("duration_sec"),
                "command_preview": result.get("command_preview"),
            }
            stdout = str(result.get("stdout", "")).strip()
            stderr = str(result.get("stderr", "")).strip()
            if stdout:
                add_event("external_agent_stdout", user_text=stdout, code_path=code_path, meta=meta)
            if stderr:
                add_event("external_agent_stderr", user_text=stderr, code_path=code_path, meta=meta)
            ok = bool(result.get("ok"))
            state = "external_done" if ok else "external_failed"
            summary = f"外部代码代理完成 exit={result.get('exit_code')}"
            add_event("external_agent_done", user_text=summary, code_path=code_path, meta=meta)
            set_task_state(task_id, state, code_path=code_path, summary=summary, meta=meta)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "代码代理", f"记录外部代理结果失败: {exc}")
        self.btn_send.setEnabled(True)
        self._refresh_history(force=True)

    def _on_external_failed(self, task_id: str, code_path: str, provider: str, error: str):
        try:
            from modules.codex_session import add_event
            from modules.codex_task_state import set_task_state

            meta = {"task_id": task_id, "provider": provider}
            add_event("external_agent_error", user_text=error, code_path=code_path, meta=meta)
            set_task_state(task_id, "external_error", code_path=code_path, summary=error, meta=meta)
        except Exception:
            pass
        self.btn_send.setEnabled(True)
        self._refresh_history(force=True)

    def _clear_external_worker(self):
        self._agent_thread = None
        self._agent_worker = None

    def _refresh_history(self, force: bool = False):
        try:
            from modules.codex_session import get_recent as get_recent_events
            from modules.codex_task_state import get_task, get_recent_tasks
        except Exception:
            return

        active_task = None
        if self._active_task_id:
            active_task = get_task(self._active_task_id)

        if not active_task:
            recent_tasks = get_recent_tasks(limit=1)
            if recent_tasks:
                active_task = recent_tasks[0]
                self._active_task_id = str(active_task.get("task_id", "")).strip()

        state_text = "任务状态: -"
        if active_task:
            task_id = str(active_task.get("task_id", "")).strip()
            state = str(active_task.get("state", "unknown")).strip() or "unknown"
            updated_at = str(active_task.get("updated_at", "")).strip()
            state_text = f"任务状态: {state} | task_id={task_id} | 更新时间={updated_at}"
            code_path = str(active_task.get("code_path", "")).strip()
            if code_path:
                state_text += f"\n代码路径: {code_path}"

        try:
            events = get_recent_events(limit=self.MAX_EVENTS)
        except Exception:
            events = []

        relevant_events: List[Dict] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            if self._active_task_id:
                task_id = str((item.get("meta", {}) or {}).get("task_id", "")).strip()
                if task_id and task_id != self._active_task_id:
                    continue
            relevant_events.append(item)
        relevant_events = relevant_events[-28:]

        new_html = self._render_history_html(active_task, relevant_events)
        if force or new_html != self._last_history_html:
            self.history_view.setHtml(new_html)
            self._last_history_html = new_html
            self._scroll_history_to_end()
        self.state_label.setText(state_text)

    def _render_history_html(self, active_task: Optional[Dict], events: List[Dict]) -> str:
        colors = self._console_colors()
        fg = colors["fg"]
        muted = colors["muted"]
        parts = [
            f"<html><body style=\"margin:0; color:{fg}; "
            "font-family:'Cascadia Mono','Consolas','JetBrains Mono', monospace; "
            "font-size:12px; line-height:150%;\">"
        ]
        if active_task:
            parts.append(self._render_task_card(active_task))
        else:
            parts.append(
                f"<div style=\"padding:4px 0 8px 0; color:{muted};\">"
                "等待任务开始。发送需求后，这里会显示控制台风格的对话记录。"
                "</div>"
            )

        if events:
            for item in events:
                event_html = self._render_event_html(item)
                if event_html:
                    parts.append(event_html)
        else:
            parts.append(
                f"<div style=\"padding:6px 0; color:{muted}; font-size:11px;\">暂无对话事件。</div>"
            )

        hint_style = (
            f"margin-top:14px; border-top:1px dashed {colors['border']}; "
            f"padding-top:10px; color:{colors['muted']}; font-size:11px; "
            "font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
        )
        parts.append(
            f"<div style=\"{hint_style}\">"
            "💡 <b>提示</b>：如果需要修改文件，代码助手会先给出计划和 diff 预览；"
            "只有您在主窗口或此处明确确认后，才会真正应用变更。"
            "</div>"
        )
        parts.append("</body></html>")
        return "".join(parts)
    def _render_task_card(self, task: Dict) -> str:
        colors = self._console_colors()
        fg = colors["fg"]
        muted = colors["muted"]
        label = colors["label"]
        border = colors["border"]

        state = html.escape(str(task.get("state", "unknown")).strip() or "unknown")
        summary = self._rich_text(str(task.get("summary", "")).strip(), max_len=220)

        lines = []
        lines.append(
            f"<div style=\"margin:4px 0 6px 0; color:{label};\">"
            f"<span style=\"color:{muted};\">[TASK]</span> <b>{state}</b> {summary or '暂无摘要。'}"
            "</div>"
        )

        history = task.get("history", [])
        if isinstance(history, list) and history:
            for item in history[-6:]:
                h_time = html.escape(str(item.get("time", ""))[-8:])
                h_state = html.escape(str(item.get("state", "")).strip() or "-")
                h_summary = self._rich_text(str(item.get("summary", "")).strip(), max_len=96)
                lines.append(
                    f"<div style=\"margin-top:2px; color:{muted}; font-size:11px;\">"
                    f"[{h_time}] {h_state} {h_summary or '...'}"
                    "</div>"
                )

        meta = task.get("meta", {})
        if isinstance(meta, dict) and str(task.get("state", "")).strip() == "proposed_change":
            change_id = html.escape(str(meta.get("change_id", "")).strip())
            confirm_token = html.escape(str(meta.get("confirm_token", "")).strip())
            file_text = html.escape(str(meta.get("file", "")).strip())
            preview_text = str(meta.get("preview", "")).strip()
            preview = html.escape(self._trim(preview_text, 420)) if preview_text else ""
            if change_id and confirm_token:
                lines.append(f"<div style=\"margin-top:6px; color:{fg}; font-weight:700;\">[PENDING CHANGE]</div>")
                if file_text:
                    lines.append(f"<div style=\"margin-top:2px; color:{muted};\">file: <code>{file_text}</code></div>")
                lines.append(f"<div style=\"margin-top:2px; color:{muted};\">change_id: <code>{change_id}</code></div>")
                lines.append(f"<div style=\"margin-top:2px; color:{muted};\">confirm_token: <code>{confirm_token}</code></div>")
                if preview:
                    lines.append(
                        f"<pre style=\"margin:4px 0 0 0; padding:0; color:{fg}; white-space:pre-wrap;\">"
                        f"{preview}</pre>"
                    )
                lines.append(f"<div style=\"margin-top:4px; color:{muted}; font-size:11px;\">确认后才会写入文件。</div>")

        lines.append(f"<div style=\"border-bottom:1px solid {border}; margin:6px 0;\"></div>")
        return "".join(lines)
    def _render_event_html(self, item: Dict) -> str:
        colors = self._console_colors()
        fg = colors["fg"]
        muted = colors["muted"]

        event_type = str(item.get("type", "")).strip()
        text = str(item.get("user_text", "")).strip()
        ts = str(item.get("time", "")).strip()
        hhmmss = html.escape(ts[-8:] if len(ts) >= 8 else ts)
        files = item.get("files", [])
        if not isinstance(files, list):
            files = []

        role_map = {
            "user_task": "YOU",
            "assistant_reasoning": "AI-THINK",
            "assistant_reply": "AI",
            "proposed_change": "CHANGE",
            "apply_change": "APPLIED",
            "external_agent_start": "AGENT",
            "external_agent_stdout": "STDOUT",
            "external_agent_stderr": "STDERR",
            "external_agent_done": "DONE",
            "external_agent_error": "ERROR",
        }
        role = role_map.get(event_type, "SYS")

        meta = item.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}

        if not text and event_type == "proposed_change":
            change_id = str(meta.get("change_id", "")).strip()
            confirm_token = str(meta.get("confirm_token", "")).strip()
            text = f"待确认变更\nchange_id={change_id}\nconfirm_token={confirm_token}".strip()
        if not text and event_type == "apply_change":
            text = "已应用变更"
        if not text:
            return ""

        body = self._rich_text(text, max_len=520)
        extras = []
        if files:
            joined = html.escape(", ".join(str(x) for x in files[:3]))
            extras.append(
                f"<div style=\"margin-top:2px; color:{muted}; font-size:11px;\">files: <code>{joined}</code></div>"
            )
        if event_type == "proposed_change":
            change_id = html.escape(str(meta.get("change_id", "")).strip())
            confirm_token = html.escape(str(meta.get("confirm_token", "")).strip())
            if change_id and confirm_token:
                extras.append(
                    f"<div style=\"margin-top:2px; color:{muted}; font-size:11px;\">"
                    f"确认时请带 <code>{change_id}</code> 和 <code>{confirm_token}</code>。"
                    "</div>"
                )

        return (
            "<div style=\"margin:0 0 8px 0;\">"
            f"<div style=\"color:{muted}; font-size:11px;\">[{hhmmss}] [{role}]</div>"
            f"<div style=\"margin-top:2px; color:{fg};\">{body}</div>"
            + "".join(extras)
            + "</div>"
        )
    def _console_colors(self) -> Dict[str, str]:
        palette = get_ui_palette()
        console = palette.get("console_codex", {}) if isinstance(palette, dict) else {}
        return {
            "fg": console.get("fg", "#E5E7EB"),
            "muted": console.get("muted", "#94A3B8"),
            "label": console.get("label", "#CBD5E1"),
            "border": console.get("border", "#1F2937"),
        }

    def _scroll_history_to_end(self) -> None:
        cursor = self.history_view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.history_view.setTextCursor(cursor)
        self.history_view.ensureCursorVisible()

    def _rich_text(self, text: str, *, max_len: Optional[int] = None) -> str:
        text = str(text or "").strip()
        if max_len is not None:
            text = self._trim(text, max_len)
        if not text:
            return ""
        return html.escape(text).replace("\n", "<br>")

    @staticmethod
    def _trim(text: str, max_len: int) -> str:
        text = str(text or "").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."
