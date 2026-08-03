"""ASR / microphone input settings page."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from modules.asr_settings import (
    ASR_ACTIVE_WINDOW_SEC_KEY,
    ASR_EXTRA_WAKE_WORDS_KEY,
    ASR_GLOBAL_WAKE_WORDS_KEY,
    ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY,
    ASR_MIN_CHARS_KEY,
    ASR_REQUIRE_WAKE_WORD_KEY,
    ASR_USE_CHARACTER_WAKE_WORDS_KEY,
    default_global_wake_words,
    load_asr_settings,
    resolve_wake_words,
    save_asr_settings,
)


class AsrSettingsPage(QtWidgets.QWidget):
    def __init__(self, parent=None, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self._build_ui()
        self.load_state()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        mode_group = QtWidgets.QGroupBox("监听模式")
        mode_form = QtWidgets.QFormLayout(mode_group)
        mode_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        mode_form.setHorizontalSpacing(12)
        mode_form.setVerticalSpacing(10)

        self.require_wake_word = QtWidgets.QCheckBox("需要唤醒词才进入对话")
        self.require_wake_word.setToolTip(
            "关闭后：ASR 开启期间识别到的句子会直接发给助手（更容易误触发）。"
        )
        mode_form.addRow("唤醒门槛:", self.require_wake_word)

        self.active_window_sec = QtWidgets.QSpinBox()
        self.active_window_sec.setRange(0, 600)
        self.active_window_sec.setSuffix(" 秒")
        self.active_window_sec.setToolTip(
            "唤醒成功后，在此时间内无需再次说唤醒词。0 表示每次都要唤醒。"
        )
        mode_form.addRow("连续对话窗口:", self.active_window_sec)

        self.min_chars = QtWidgets.QSpinBox()
        self.min_chars.setRange(1, 20)
        self.min_chars.setSuffix(" 字")
        mode_form.addRow("最短识别长度:", self.min_chars)

        wake_group = QtWidgets.QGroupBox("唤醒词来源")
        wake_form = QtWidgets.QFormLayout(wake_group)
        wake_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        wake_form.setHorizontalSpacing(12)
        wake_form.setVerticalSpacing(10)

        self.use_character_wake_words = QtWidgets.QCheckBox(
            "使用当前角色名称 / 别名作为唤醒词"
        )
        self.use_character_wake_words.setToolTip(
            "从当前角色的 name、aliases 等字段自动生成唤醒词（如「五十铃怜」→ 五十铃怜/五十铃/怜）。"
        )
        wake_form.addRow("角色唤醒:", self.use_character_wake_words)

        self.include_global_wake_words = QtWidgets.QCheckBox(
            "同时使用下方全局兜底唤醒词"
        )
        self.include_global_wake_words.setToolTip(
            "关闭后仅使用角色名/别名与自定义补充词；开启后合并下方列表。"
        )
        wake_form.addRow("全局兜底:", self.include_global_wake_words)

        self.global_wake_words = QtWidgets.QPlainTextEdit()
        self.global_wake_words.setPlaceholderText(
            "全局兜底唤醒词，每行一个，例如：\n五十铃\n怜\nSuzu\n助手"
        )
        self.global_wake_words.setMinimumHeight(90)
        self.global_wake_words.setToolTip(
            "默认来自 config.WAKE_KEYWORDS，可在此增删改；保存后写入 runtime_settings。"
        )
        wake_form.addRow("全局词列表:", self.global_wake_words)

        self.extra_wake_words = QtWidgets.QPlainTextEdit()
        self.extra_wake_words.setPlaceholderText(
            "自定义唤醒词，每行一个，例如：\n小铃\nSuzu\n助手"
        )
        self.extra_wake_words.setMinimumHeight(100)
        wake_form.addRow("自定义补充:", self.extra_wake_words)

        preview_group = QtWidgets.QGroupBox("当前生效唤醒词预览")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        self.wake_preview = QtWidgets.QLabel("")
        self.wake_preview.setWordWrap(True)
        self.wake_preview.setStyleSheet("color:#374151;")
        preview_layout.addWidget(self.wake_preview)

        hint = QtWidgets.QLabel(
            "说明：\n"
            "1. 托盘/热键的“开语音”仍控制是否启动麦克风 ASR。\n"
            "2. 若开启“需要唤醒词”，请先叫角色名再说话；关闭后即可直接说。\n"
            "3. 保存后立即应用到正在运行的 VoiceSensor。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280;")

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch()
        btn_refresh = QtWidgets.QPushButton("刷新预览")
        btn_refresh.clicked.connect(self.refresh_preview)
        btn_save = QtWidgets.QPushButton("保存并应用")
        btn_save.setObjectName("primaryAction")
        btn_save.clicked.connect(self.save_state)
        footer.addWidget(btn_refresh)
        footer.addWidget(btn_save)

        layout.addWidget(mode_group)
        layout.addWidget(wake_group)
        layout.addWidget(preview_group)
        layout.addWidget(hint)
        layout.addLayout(footer)
        layout.addStretch()

        self.require_wake_word.toggled.connect(self._on_require_wake_toggled)
        self.use_character_wake_words.toggled.connect(self.refresh_preview)
        self.include_global_wake_words.toggled.connect(self._on_include_global_toggled)
        self.global_wake_words.textChanged.connect(self.refresh_preview)
        self.extra_wake_words.textChanged.connect(self.refresh_preview)

    def _on_require_wake_toggled(self, checked: bool) -> None:
        self.active_window_sec.setEnabled(bool(checked))
        self.refresh_preview()

    def _on_include_global_toggled(self, checked: bool) -> None:
        self.global_wake_words.setEnabled(bool(checked))
        self.refresh_preview()

    def load_state(self) -> None:
        state = load_asr_settings()
        self.require_wake_word.setChecked(bool(state[ASR_REQUIRE_WAKE_WORD_KEY]))
        self.use_character_wake_words.setChecked(
            bool(state[ASR_USE_CHARACTER_WAKE_WORDS_KEY])
        )
        self.include_global_wake_words.setChecked(
            bool(state[ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY])
        )
        globals_list = state.get(ASR_GLOBAL_WAKE_WORDS_KEY) or default_global_wake_words()
        self.global_wake_words.setPlainText("\n".join(globals_list))
        extras = state.get(ASR_EXTRA_WAKE_WORDS_KEY) or []
        self.extra_wake_words.setPlainText("\n".join(extras))
        self.active_window_sec.setValue(int(state[ASR_ACTIVE_WINDOW_SEC_KEY]))
        self.min_chars.setValue(int(state[ASR_MIN_CHARS_KEY]))
        self.active_window_sec.setEnabled(self.require_wake_word.isChecked())
        self.global_wake_words.setEnabled(self.include_global_wake_words.isChecked())
        self.refresh_preview()

    def collect_state(self) -> dict:
        return {
            ASR_REQUIRE_WAKE_WORD_KEY: self.require_wake_word.isChecked(),
            ASR_USE_CHARACTER_WAKE_WORDS_KEY: self.use_character_wake_words.isChecked(),
            ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY: self.include_global_wake_words.isChecked(),
            ASR_GLOBAL_WAKE_WORDS_KEY: self.global_wake_words.toPlainText(),
            ASR_EXTRA_WAKE_WORDS_KEY: self.extra_wake_words.toPlainText(),
            ASR_ACTIVE_WINDOW_SEC_KEY: int(self.active_window_sec.value()),
            ASR_MIN_CHARS_KEY: int(self.min_chars.value()),
        }

    def refresh_preview(self) -> None:
        draft = self.collect_state()
        # Preview uses draft values without writing disk.
        words = resolve_wake_words(settings=load_asr_settings(draft))
        if not self.require_wake_word.isChecked():
            self.wake_preview.setText(
                "当前为【免唤醒】模式：识别到的句子会直接进入对话。\n"
                f"仍会识别这些词（用于日志/续杯）：{', '.join(words) if words else '（无）'}"
            )
            return
        if not words:
            self.wake_preview.setText(
                "当前没有生效唤醒词。请开启角色唤醒、全局兜底，或填写自定义词。"
            )
            return
        self.wake_preview.setText("、".join(words))

    def save_state(self) -> None:
        new_settings = save_asr_settings(self.collect_state())
        apply_result = {}
        apply_fn = getattr(self.main_app, "apply_asr_settings", None)
        if not callable(apply_fn) and getattr(self.main_app, "app", None) is not None:
            apply_fn = getattr(self.main_app.app, "apply_asr_settings", None)
        if callable(apply_fn):
            try:
                apply_result = apply_fn(new_settings) or {}
            except Exception as exc:
                apply_result = {"error": str(exc)}
        self.refresh_preview()
        if apply_result.get("error"):
            QtWidgets.QMessageBox.warning(
                self,
                "语音输入",
                f"配置已保存，但应用到 VoiceSensor 失败：{apply_result['error']}",
            )
            return
        mode = (
            "需要唤醒词"
            if new_settings.get(ASR_REQUIRE_WAKE_WORD_KEY)
            else "免唤醒（直接听写）"
        )
        words = apply_result.get("wake_words") or resolve_wake_words(
            settings=new_settings
        )
        QtWidgets.QMessageBox.information(
            self,
            "语音输入",
            f"已保存并应用。\n模式：{mode}\n生效唤醒词：{', '.join(words) if words else '（无）'}",
        )
