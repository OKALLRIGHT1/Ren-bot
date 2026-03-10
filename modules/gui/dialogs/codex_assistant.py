from __future__ import annotations

import html
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import get_tool_dialog_styles, get_ui_palette
from modules.runtime_settings import load_runtime_settings, update_runtime_settings


class CodexAssistantDialog(QtWidgets.QDialog):
    REFRESH_INTERVAL_MS = 1200
    MAX_EVENTS = 80

    def __init__(self, parent=None, on_submit: Optional[Callable[[str, Dict], None]] = None):
        super().__init__(parent)
        self.on_submit = on_submit
        self._runtime = load_runtime_settings()
        self._active_task_id = str(self._runtime.get("codex_last_task_id", "")).strip()
        self._last_history_html = ""

        self.setWindowTitle("代码助手")
        self.resize(940, 680)
        self.setMinimumSize(780, 560)
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
        outer.setContentsMargins(16, 16, 16, 16)

        shell = QtWidgets.QFrame()
        shell.setObjectName("dialogShell")
        outer.addWidget(shell)

        layout = QtWidgets.QVBoxLayout(shell)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QtWidgets.QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(4)
        title = QtWidgets.QLabel("代码助手")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)
        desc = QtWidgets.QLabel("这里会显示你和 AI 的任务对话、思考摘要，以及待你确认的改动预览。")
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        header_layout.addWidget(desc)
        layout.addWidget(header)

        section = QtWidgets.QFrame()
        section.setObjectName("dialogSection")
        section_layout = QtWidgets.QVBoxLayout(section)
        section_layout.setContentsMargins(14, 12, 14, 12)
        section_layout.setSpacing(10)

        self.chk_mode = QtWidgets.QCheckBox("启用代码助手模式 (Codex)")
        self.chk_mode.setChecked(bool(self._runtime.get("codex_mode_enabled", False)))
        self.chk_mode.toggled.connect(self._on_mode_toggled)
        section_layout.addWidget(self.chk_mode)

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
        layout.addWidget(section)

        history_card = QtWidgets.QFrame()
        history_card.setObjectName("dialogSection")
        history_layout = QtWidgets.QVBoxLayout(history_card)
        history_layout.setContentsMargins(14, 12, 14, 12)
        history_layout.setSpacing(8)

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
        self.history_view.setMinimumHeight(220)
        self.history_view.document().setDocumentMargin(10)
        history_layout.addWidget(self.history_view, 1)

        self.confirm_hint = QtWidgets.QLabel(
            "如果需要改文件，代码助手会先给出计划和 diff 预览；只有你明确确认后，才会真正 apply_change。"
        )
        self.confirm_hint.setObjectName("dialogHint")
        self.confirm_hint.setWordWrap(True)
        history_layout.addWidget(self.confirm_hint)

        layout.addWidget(history_card, 1)

        hint_card = QtWidgets.QFrame()
        hint_card.setObjectName("dialogSection")
        hint_layout = QtWidgets.QVBoxLayout(hint_card)
        hint_layout.setContentsMargins(14, 12, 14, 12)
        hint_layout.setSpacing(8)
        tip = QtWidgets.QLabel(
            "可直接输入需求，例如:\n"
            "1) 帮我检查这个路径下有哪些 TODO\n"
            "2) 读取并解释某个文件\n"
            "3) 修改某函数并给出 diff 预览"
        )
        tip.setObjectName("dialogHint")
        tip.setWordWrap(True)
        hint_layout.addWidget(tip)

        self.input_edit = QtWidgets.QTextEdit()
        self.input_edit.setAcceptRichText(False)
        self.input_edit.setPlaceholderText("输入代码相关任务...")
        hint_layout.addWidget(self.input_edit, 1)
        layout.addWidget(hint_card, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_send = QtWidgets.QPushButton("发送到代码助手")
        btn_send.setObjectName("primaryAction")
        btn_send.clicked.connect(self._send)
        btn_row.addWidget(btn_send)
        layout.addLayout(btn_row)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._refresh_history)
        self._refresh_timer.start()

        self._on_mode_toggled(self.chk_mode.isChecked())
        self._refresh_history(force=True)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_history(force=True)

    def _on_mode_toggled(self, enabled: bool):
        self.chk_allow_write.setEnabled(bool(enabled))
        self.chk_allow_exec.setEnabled(bool(enabled))
        self.chk_autorun.setEnabled(bool(enabled))

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
            "font-size:12px; line-height:1.55;\">"
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
