from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6 import QtCore, QtWidgets

try:
    from modules.llm import chat_with_ai
except Exception:
    chat_with_ai = None


class ChatRecordImportWizardDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        *,
        main_app=None,
        dirs: Optional[List[dict]] = None,
        default_target: str = "knowledge",
    ):
        super().__init__(parent)
        self.main_app = main_app
        self._dirs = dirs or []
        self._last_rows: List[Dict[str, Any]] = []
        self.setWindowTitle("聊天记录导入向导")
        self.resize(820, 660)
        self.setMinimumSize(720, 560)
        self._setup_ui(default_target)
        self._load_sessions()

    def _setup_ui(self, default_target: str):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("聊天记录导入向导")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #111827;")
        layout.addWidget(title)

        source_box = QtWidgets.QGroupBox("来源")
        source_layout = QtWidgets.QGridLayout(source_box)
        source_layout.setHorizontalSpacing(10)
        source_layout.setVerticalSpacing(8)

        self.cmb_session = QtWidgets.QComboBox()
        self.cmb_session.addItem("全部会话", "")
        self.inp_query = QtWidgets.QLineEdit()
        self.inp_query.setPlaceholderText("关键词过滤，可留空")
        self.cmb_role = QtWidgets.QComboBox()
        self.cmb_role.addItem("全部角色", "")
        self.cmb_role.addItem("只看用户", "user")
        self.cmb_role.addItem("只看助手", "assistant")
        self.cmb_role.addItem("只看系统", "system")
        self.spin_limit = QtWidgets.QSpinBox()
        self.spin_limit.setRange(20, 1000)
        self.spin_limit.setValue(220)
        self.btn_preview = QtWidgets.QPushButton("预览")

        source_layout.addWidget(QtWidgets.QLabel("会话"), 0, 0)
        source_layout.addWidget(self.cmb_session, 0, 1)
        source_layout.addWidget(QtWidgets.QLabel("角色"), 0, 2)
        source_layout.addWidget(self.cmb_role, 0, 3)
        source_layout.addWidget(QtWidgets.QLabel("最多"), 1, 0)
        source_layout.addWidget(self.spin_limit, 1, 1)
        source_layout.addWidget(self.inp_query, 1, 2)
        source_layout.addWidget(self.btn_preview, 1, 3)
        layout.addWidget(source_box)

        target_box = QtWidgets.QGroupBox("目标")
        target_layout = QtWidgets.QGridLayout(target_box)
        target_layout.setHorizontalSpacing(10)
        target_layout.setVerticalSpacing(8)

        self.cmb_target = QtWidgets.QComboBox()
        self.cmb_target.addItem("知识库文档", "knowledge")
        self.cmb_target.addItem("表达学习库", "expression")
        default_idx = self.cmb_target.findData(default_target)
        self.cmb_target.setCurrentIndex(max(0, default_idx))

        self.inp_title = QtWidgets.QLineEdit()
        self.inp_title.setPlaceholderText("例如：聊天记录提炼")
        self.inp_title.setText("聊天记录提炼")
        self.inp_tags = QtWidgets.QLineEdit()
        self.inp_tags.setPlaceholderText("用逗号分隔，例如：聊天记录, 偏好, 设定")

        self.cmb_target_dir = QtWidgets.QComboBox()
        self._load_target_dirs()
        self.btn_browse_dir = QtWidgets.QPushButton("选择目录")
        self.chk_ingest_now = QtWidgets.QCheckBox("生成后立即学习到知识库")
        self.chk_ingest_now.setChecked(True)

        self.inp_character = QtWidgets.QLineEdit()
        self.inp_character.setPlaceholderText("留空表示通用表达")
        self.cmb_scene = QtWidgets.QComboBox()
        self.cmb_scene.addItem("普通聊天", "chat")
        self.cmb_scene.addItem("屏幕吐槽", "sensor")
        self.cmb_scene.addItem("通用", "any")

        self.chk_use_llm = QtWidgets.QCheckBox("使用 LLM 自动提炼")
        self.chk_use_llm.setChecked(chat_with_ai is not None)
        self.chk_use_llm.setEnabled(chat_with_ai is not None)

        target_layout.addWidget(QtWidgets.QLabel("导入到"), 0, 0)
        target_layout.addWidget(self.cmb_target, 0, 1)
        target_layout.addWidget(self.chk_use_llm, 0, 2, 1, 2)
        target_layout.addWidget(QtWidgets.QLabel("标题"), 1, 0)
        target_layout.addWidget(self.inp_title, 1, 1, 1, 3)
        target_layout.addWidget(QtWidgets.QLabel("标签"), 2, 0)
        target_layout.addWidget(self.inp_tags, 2, 1, 1, 3)
        target_layout.addWidget(QtWidgets.QLabel("目录"), 3, 0)
        target_layout.addWidget(self.cmb_target_dir, 3, 1, 1, 2)
        target_layout.addWidget(self.btn_browse_dir, 3, 3)
        target_layout.addWidget(self.chk_ingest_now, 4, 1, 1, 3)
        target_layout.addWidget(QtWidgets.QLabel("角色"), 5, 0)
        target_layout.addWidget(self.inp_character, 5, 1)
        target_layout.addWidget(QtWidgets.QLabel("场景"), 5, 2)
        target_layout.addWidget(self.cmb_scene, 5, 3)
        layout.addWidget(target_box)

        self.preview_box = QtWidgets.QPlainTextEdit()
        self.preview_box.setReadOnly(True)
        layout.addWidget(self.preview_box, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_import = QtWidgets.QPushButton("生成并导入")
        self.btn_close = QtWidgets.QPushButton("关闭")
        btn_row.addStretch()
        btn_row.addWidget(self.btn_import)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.btn_preview.clicked.connect(self._preview_records)
        self.btn_browse_dir.clicked.connect(self._browse_dir)
        self.btn_import.clicked.connect(self._import_records)
        self.btn_close.clicked.connect(self.accept)
        self.cmb_target.currentIndexChanged.connect(lambda *_: self._refresh_target_visibility())
        self._refresh_target_visibility()

    def _get_brain(self):
        return getattr(self.main_app, "brain", None) if self.main_app is not None else None

    def _get_store(self):
        brain = self._get_brain()
        store = getattr(self.main_app, "memory_store", None) if self.main_app is not None else None
        return store or getattr(brain, "sqlite_store", None)

    def _load_target_dirs(self):
        known = set()
        for item in self._dirs:
            path = str((item or {}).get("path") or "").strip()
            if path and path not in known:
                known.add(path)
                self.cmb_target_dir.addItem(path, path)
        default_dir = str(Path.cwd() / "knowledge_docs")
        if default_dir not in known:
            self.cmb_target_dir.addItem(default_dir, default_dir)

    def _load_sessions(self):
        store = self._get_store()
        if store is None:
            return
        sessions: List[Dict[str, Any]] = []
        try:
            if hasattr(store, "list_transcript_sessions"):
                sessions = store.list_transcript_sessions(limit=80)
            else:
                rows = store.list_transcript(limit=1000, offset=0)
                seen = set()
                for row in rows:
                    session_id = str(row.get("session_id") or "").strip()
                    if session_id in seen:
                        continue
                    seen.add(session_id)
                    sessions.append({"session_id": session_id, "label": session_id or "(global)", "message_count": 0})
        except Exception:
            sessions = []
        for item in sessions:
            session_id = str(item.get("session_id") or "").strip()
            label = str(item.get("label") or session_id or "(global)").strip()
            count = int(item.get("message_count") or 0)
            suffix = f" · {count}" if count else ""
            self.cmb_session.addItem(f"{label}{suffix}", session_id)

    def _browse_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择知识文档保存目录")
        if not path:
            return
        idx = self.cmb_target_dir.findData(path)
        if idx < 0:
            self.cmb_target_dir.addItem(path, path)
            idx = self.cmb_target_dir.findData(path)
        self.cmb_target_dir.setCurrentIndex(max(0, idx))

    def _refresh_target_visibility(self):
        target = str(self.cmb_target.currentData() or "knowledge")
        knowledge = target == "knowledge"
        for widget in (
            self.inp_title,
            self.inp_tags,
            self.cmb_target_dir,
            self.btn_browse_dir,
            self.chk_ingest_now,
        ):
            widget.setEnabled(knowledge)
        self.inp_character.setEnabled(not knowledge)
        self.cmb_scene.setEnabled(not knowledge)

    def _fetch_rows(self) -> List[Dict[str, Any]]:
        store = self._get_store()
        if store is None or not hasattr(store, "list_transcript"):
            raise RuntimeError("当前记忆存储不可用，无法读取聊天记录。")
        session_id = str(self.cmb_session.currentData() or "").strip()
        session_scope = "specific" if session_id else "all"
        role = str(self.cmb_role.currentData() or "").strip() or None
        rows = store.list_transcript(
            role=role,
            query=self.inp_query.text().strip(),
            limit=int(self.spin_limit.value()),
            offset=0,
            session_id=session_id or None,
            session_scope=session_scope,
        )
        rows = list(reversed(rows))
        self._last_rows = rows
        return rows

    def _format_rows_for_prompt(self, rows: List[Dict[str, Any]], *, max_chars: int = 12000) -> str:
        parts = []
        total = 0
        for row in rows:
            role = str(row.get("role") or "").strip() or "unknown"
            speaker = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
            content = self._clean_line(str(row.get("content") or ""))
            if not content:
                continue
            line = f"{speaker}: {content}"
            total += len(line)
            if total > max_chars:
                break
            parts.append(line)
        return "\n".join(parts)

    def _clean_line(self, text: str, limit: int = 280) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "..."
        return text

    def _tags(self) -> List[str]:
        return [
            item.strip()
            for item in re.split(r"[,，;；\s]+", self.inp_tags.text().strip())
            if item.strip()
        ]

    def _safe_filename(self, title: str) -> str:
        base = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(title or "").strip())
        base = re.sub(r"_+", "_", base).strip("._")
        return (base or "chat_record_import")[:60]

    def _fallback_knowledge_markdown(self, rows: List[Dict[str, Any]]) -> str:
        title = self.inp_title.text().strip() or "聊天记录提炼"
        tags = self._tags()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = [
            f"# {title}",
            "来源：聊天记录导入",
            f"标签：{', '.join(tags) if tags else '聊天记录'}",
            f"整理时间：{now}",
            "",
            "## 对话知识条目",
        ]
        for row in rows:
            role = str(row.get("role") or "").strip()
            if role == "system":
                continue
            speaker = "用户" if role == "user" else "助手"
            content = self._clean_line(str(row.get("content") or ""), limit=360)
            if content:
                out.append(f"- {speaker}曾说：{content}")
        out.append("")
        return "\n".join(out)

    def _llm_knowledge_markdown(self, rows: List[Dict[str, Any]]) -> str:
        if chat_with_ai is None:
            return ""
        title = self.inp_title.text().strip() or "聊天记录提炼"
        transcript = self._format_rows_for_prompt(rows)
        if not transcript:
            return ""
        prompt = (
            "把下面聊天记录整理成可导入知识库的 Markdown。\n"
            "要求：\n"
            "1. 只提炼稳定事实、设定、偏好、关系、规则和长期有用的信息；\n"
            "2. 不要学习闲聊废话、情绪宣泄、一次性任务；\n"
            "3. 每条知识必须短而完整，适合单独检索；\n"
            "4. 输出 Markdown，包含标题和“## 知识条目”；\n"
            "5. 不要编造聊天里没有的信息。\n\n"
            f"标题：{title}\n\n聊天记录：\n{transcript}"
        )
        messages = [
            {"role": "system", "content": "你是知识库整理器，只输出可导入的 Markdown。"},
            {"role": "user", "content": prompt},
        ]
        try:
            result = asyncio.run(
                asyncio.to_thread(
                    chat_with_ai,
                    messages,
                    task_type="default",
                    caller="chat_record_knowledge_import",
                    timeout_sec=60,
                )
            )
            return str(result or "").strip()
        except Exception:
            return ""

    def _build_knowledge_markdown(self, rows: List[Dict[str, Any]]) -> str:
        if self.chk_use_llm.isChecked():
            md = self._llm_knowledge_markdown(rows)
            if md:
                return md
        return self._fallback_knowledge_markdown(rows)

    def _write_knowledge_doc(self, rows: List[Dict[str, Any]]) -> str:
        title = self.inp_title.text().strip() or "聊天记录提炼"
        target_dir = Path(str(self.cmb_target_dir.currentData() or "")).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = target_dir / f"{self._safe_filename(title)}_{stamp}.md"
        path.write_text(self._build_knowledge_markdown(rows), encoding="utf-8")
        return str(path)

    def _fallback_expression_patterns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []
        current_user = ""
        for row in rows:
            role = str(row.get("role") or "").strip()
            content = self._clean_line(str(row.get("content") or ""), limit=180)
            if not content:
                continue
            if role == "user":
                current_user = content
                continue
            if role != "assistant":
                continue
            situation = current_user[:80] or "日常聊天接话"
            patterns.append(
                {
                    "character_name": self.inp_character.text().strip(),
                    "scene": str(self.cmb_scene.currentData() or "chat"),
                    "situation": situation,
                    "style": "参考历史聊天里的自然回复方式，保持短句和临场感。",
                    "content_list": [content],
                    "source": "chat_record_import",
                    "quality_score": 6.0,
                    "enabled": True,
                }
            )
        return patterns[:80]

    def _llm_expression_patterns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if chat_with_ai is None:
            return []
        transcript = self._format_rows_for_prompt(rows)
        if not transcript:
            return []
        prompt = (
            "从下面聊天记录中提取表达学习库条目。\n"
            "只学习助手说话方式，不学习事实知识。\n"
            "输出 JSON 数组，每项字段固定为 situation, style, content_list。\n"
            "situation 写触发场景；style 写表达风格；content_list 放 1 到 4 个历史助手短句。\n"
            "过滤空话、模板话、长篇说明和明显错误回复。\n\n"
            f"聊天记录：\n{transcript}"
        )
        messages = [
            {"role": "system", "content": "你是表达风格整理器，只输出 JSON 数组。"},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = asyncio.run(
                asyncio.to_thread(
                    chat_with_ai,
                    messages,
                    task_type="default",
                    caller="chat_record_expression_import",
                    timeout_sec=60,
                )
            )
            text = str(raw or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
            data = json.loads(text)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        patterns: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            content_list = item.get("content_list") if isinstance(item.get("content_list"), list) else []
            content_list = [str(x).strip() for x in content_list if str(x).strip()]
            style = str(item.get("style") or "").strip()
            if not style and not content_list:
                continue
            patterns.append(
                {
                    "character_name": self.inp_character.text().strip(),
                    "scene": str(self.cmb_scene.currentData() or "chat"),
                    "situation": str(item.get("situation") or "日常聊天接话").strip(),
                    "style": style,
                    "content_list": content_list[:12],
                    "source": "chat_record_import",
                    "quality_score": 7.0,
                    "enabled": True,
                }
            )
        return patterns[:80]

    def _build_expression_patterns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.chk_use_llm.isChecked():
            patterns = self._llm_expression_patterns(rows)
            if patterns:
                return patterns
        return self._fallback_expression_patterns(rows)

    def _preview_records(self):
        try:
            rows = self._fetch_rows()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "聊天记录导入", str(exc))
            return
        if not rows:
            self.preview_box.setPlainText("没有匹配到聊天记录。")
            return
        preview = self._format_rows_for_prompt(rows[:40], max_chars=6000)
        self.preview_box.setPlainText(f"匹配 {len(rows)} 条记录。\n\n{preview}")

    def _import_knowledge(self, rows: List[Dict[str, Any]]) -> str:
        brain = self._get_brain()
        path = self._write_knowledge_doc(rows)
        result_text = f"已生成知识文档：\n{path}"
        if self.chk_ingest_now.isChecked():
            if brain is None or not hasattr(brain, "import_knowledge_from_file"):
                result_text += "\n\nbrain 未就绪，稍后可在知识库管理里点“一键学习”。"
            else:
                result = brain.import_knowledge_from_file(path)
                result_text += f"\n\n学习结果：{result}"
        return result_text

    def _import_expression(self, rows: List[Dict[str, Any]]) -> str:
        store = self._get_store()
        if store is None or not hasattr(store, "import_expression_patterns"):
            raise RuntimeError("当前记忆存储不支持表达学习库。")
        patterns = self._build_expression_patterns(rows)
        if not patterns:
            return "没有提取到可导入的表达条目。"
        stats = store.import_expression_patterns(patterns, replace=False)
        return f"表达学习库导入完成：新增 {stats.get('inserted', 0)} 条，跳过 {stats.get('skipped', 0)} 条。"

    def _import_records(self):
        try:
            rows = self._fetch_rows()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "聊天记录导入", str(exc))
            return
        if not rows:
            QtWidgets.QMessageBox.information(self, "聊天记录导入", "没有匹配到聊天记录。")
            return
        try:
            if str(self.cmb_target.currentData() or "knowledge") == "knowledge":
                result = self._import_knowledge(rows)
            else:
                result = self._import_expression(rows)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "聊天记录导入", f"导入失败：{exc}")
            return
        self.preview_box.setPlainText(result)
        QtWidgets.QMessageBox.information(self, "聊天记录导入", result)
