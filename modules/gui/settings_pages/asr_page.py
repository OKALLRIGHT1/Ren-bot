"""ASR / microphone input settings page."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from modules.asr.settings import (
    ASR_ACTIVE_WINDOW_SEC_KEY,
    ASR_ENGINE_KEY,
    ASR_ENGINE_QWEN3,
    ASR_ENGINE_ZIPFORMER,
    ASR_EXTRA_WAKE_WORDS_KEY,
    ASR_GLOBAL_WAKE_WORDS_KEY,
    ASR_HOTWORDS_FROM_WAKE_KEY,
    ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY,
    ASR_MIN_CHARS_KEY,
    ASR_REQUIRE_WAKE_WORD_KEY,
    ASR_USE_CHARACTER_WAKE_WORDS_KEY,
    default_global_wake_words,
    load_asr_settings,
    resolve_wake_words,
    save_asr_settings,
)


def _resolve_corrector(main_app):
    """Locate the live AsrCorrector on the running VoiceSensor, or build one."""
    app = getattr(main_app, "app", None) or main_app
    sensor = getattr(app, "voice_sensor", None)
    if sensor is not None and getattr(sensor, "corrector", None) is not None:
        return sensor.corrector
    from modules.asr.adapter import build_corrector

    return build_corrector()


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

        self.engine = QtWidgets.QComboBox()
        self.engine.addItem("Qwen3-ASR 0.6B（推荐，说完一句再出字）", ASR_ENGINE_QWEN3)
        self.engine.addItem("Zipformer 流式（旧模型，边说边出字）", ASR_ENGINE_ZIPFORMER)
        self.engine.setToolTip(
            "Qwen3 更准，但需要单独下载模型并无边说边出字。"
            "模型未就绪时会自动回退到 Zipformer。"
        )
        mode_form.addRow("识别引擎:", self.engine)

        self.hotwords_from_wake = QtWidgets.QCheckBox("把唤醒词当作识别热词（压专名错字）")
        self.hotwords_from_wake.setToolTip(
            "开启后把当前生效唤醒词传给 Qwen3，减轻「五十铃→武士林」这类错误。"
            "对旧 Zipformer 无效。"
        )
        mode_form.addRow("专名热词:", self.hotwords_from_wake)

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
        self.global_wake_words.setMinimumHeight(60)
        self.global_wake_words.setToolTip(
            "默认来自 config.WAKE_KEYWORDS，可在此增删改；保存后写入 runtime_settings。"
        )
        wake_form.addRow("全局词列表:", self.global_wake_words)

        self.extra_wake_words = QtWidgets.QPlainTextEdit()
        self.extra_wake_words.setPlaceholderText(
            "自定义唤醒词，每行一个，例如：\n小铃\nSuzu\n助手"
        )
        self.extra_wake_words.setMinimumHeight(60)
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
            "3. Qwen3 默认说完一句再识别；模型或 sherpa-onnx 版本不够时会回退 Zipformer。\n"
            "4. 保存后立即应用到正在运行的 VoiceSensor。\n"
            "5. “识别纠错”在每句话交给模型前修正系统性识别错误；\n"
            "   也可在对话里发送「修正：原词→改成词」让助手记住一次改字。"
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

        # ── 识别纠错 ──────────────────────────────────────────────
        correction_group = QtWidgets.QGroupBox("识别纠错")
        correction_layout = QtWidgets.QVBoxLayout(correction_group)

        correction_intro = QtWidgets.QLabel(
            "识别到文字后、交给模型前，按下表替换系统性识别错误。\n"
            "也可在对话里发送「修正：原词→改成词」自动学习（记两次后晋升为规则）。"
        )
        correction_intro.setWordWrap(True)
        correction_intro.setStyleSheet("color:#6B7280;")
        correction_layout.addWidget(correction_intro)

        self.rules_table = QtWidgets.QTableWidget(0, 3)
        self.rules_table.setHorizontalHeaderLabels(["原词", "改成", "启用"])
        self.rules_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.rules_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.rules_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setMinimumHeight(120)
        correction_layout.addWidget(self.rules_table)

        rule_btn_row = QtWidgets.QHBoxLayout()
        self.rule_pattern_input = QtWidgets.QLineEdit()
        self.rule_pattern_input.setPlaceholderText("原词")
        self.rule_replacement_input = QtWidgets.QLineEdit()
        self.rule_replacement_input.setPlaceholderText("改成")
        btn_add_rule = QtWidgets.QPushButton("添加规则")
        btn_add_rule.clicked.connect(self._add_correction_rule)
        btn_del_rule = QtWidgets.QPushButton("删除选中")
        btn_del_rule.clicked.connect(self._del_correction_rule)
        rule_btn_row.addWidget(self.rule_pattern_input)
        rule_btn_row.addWidget(self.rule_replacement_input)
        rule_btn_row.addWidget(btn_add_rule)
        rule_btn_row.addWidget(btn_del_rule)
        correction_layout.addLayout(rule_btn_row)

        correction_layout.addWidget(
            QtWidgets.QLabel("学习缓冲（手动改字记录，记两次后自动晋升为规则）:")
        )
        self.learned_table = QtWidgets.QTableWidget(0, 4)
        self.learned_table.setHorizontalHeaderLabels(["原词", "改成", "次数", "已晋升"])
        self.learned_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.learned_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.learned_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.learned_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.learned_table.verticalHeader().setVisible(False)
        self.learned_table.setMinimumHeight(80)
        correction_layout.addWidget(self.learned_table)

        correction_btn_row = QtWidgets.QHBoxLayout()
        btn_refresh_correction = QtWidgets.QPushButton("刷新纠错表")
        btn_refresh_correction.clicked.connect(self.load_correction_state)
        btn_promote = QtWidgets.QPushButton("学习一对（原词→改成）")
        btn_promote.clicked.connect(self._learn_pair_dialog)
        btn_clear_learned = QtWidgets.QPushButton("清空学习缓冲")
        btn_clear_learned.clicked.connect(self._clear_learned)
        correction_btn_row.addWidget(btn_refresh_correction)
        correction_btn_row.addWidget(btn_promote)
        correction_btn_row.addWidget(btn_clear_learned)
        correction_btn_row.addStretch()
        correction_layout.addLayout(correction_btn_row)

        layout.addWidget(correction_group)

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
        engine_id = str(state.get(ASR_ENGINE_KEY) or ASR_ENGINE_QWEN3)
        index = self.engine.findData(engine_id)
        self.engine.setCurrentIndex(index if index >= 0 else 0)
        self.hotwords_from_wake.setChecked(bool(state.get(ASR_HOTWORDS_FROM_WAKE_KEY, True)))
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
        self.load_correction_state()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[name-defined]
        super().showEvent(event)
        try:
            self.refresh_preview()
        except Exception:
            pass

    # ── 识别纠错管理 ───────────────────────────────────────────────

    def load_correction_state(self) -> None:
        """Refresh both the rules table and the learned-pairs table."""
        corrector = _resolve_corrector(self.main_app)
        try:
            corrector.reload()
        except Exception:
            pass

        rules = corrector.list_rules()
        self.rules_table.setRowCount(0)
        for rule in rules:
            row = self.rules_table.rowCount()
            self.rules_table.insertRow(row)
            self.rules_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(rule.get("pattern", ""))))
            self.rules_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(rule.get("replacement", ""))))
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(check.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(
                QtCore.Qt.CheckState.Checked if rule.get("enabled", True)
                else QtCore.Qt.CheckState.Unchecked
            )
            self.rules_table.setItem(row, 2, check)

        learned = corrector.list_learned()
        self.learned_table.setRowCount(0)
        for entry in learned:
            row = self.learned_table.rowCount()
            self.learned_table.insertRow(row)
            self.learned_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(entry.get("src", ""))))
            self.learned_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(entry.get("dst", ""))))
            self.learned_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(entry.get("count", 0))))
            self.learned_table.setItem(
                row, 3,
                QtWidgets.QTableWidgetItem("是" if entry.get("promoted") else "否"),
            )

    def _add_correction_rule(self) -> None:
        pattern = self.rule_pattern_input.text().strip()
        replacement = self.rule_replacement_input.text().strip()
        if not pattern or pattern == replacement:
            QtWidgets.QMessageBox.warning(self, "识别纠错", "原词和改成不能为空或相同。")
            return
        corrector = _resolve_corrector(self.main_app)
        try:
            corrector.add_rule(pattern, replacement)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "识别纠错", str(exc))
            return
        self.rule_pattern_input.clear()
        self.rule_replacement_input.clear()
        self.load_correction_state()

    def _del_correction_rule(self) -> None:
        row = self.rules_table.currentRow()
        if row < 0:
            return
        pattern_item = self.rules_table.item(row, 0)
        if pattern_item is None:
            return
        # Match by pattern to find the rule id.
        corrector = _resolve_corrector(self.main_app)
        pattern = pattern_item.text()
        for rule in corrector.list_rules():
            if rule.get("pattern") == pattern:
                corrector.remove_rule(rule["id"])
                break
        self.load_correction_state()

    def _learn_pair_dialog(self) -> None:
        src, ok = QtWidgets.QInputDialog.getText(
            self, "学习一对", "原词（识别错的）:", text=self.rule_pattern_input.text()
        )
        if not ok or not src.strip():
            return
        dst, ok = QtWidgets.QInputDialog.getText(self, "学习一对", "改成（正确的）:")
        if not ok or not dst.strip() or src.strip() == dst.strip():
            return
        corrector = _resolve_corrector(self.main_app)
        entry = corrector.learn(src.strip(), dst.strip())
        self.load_correction_state()
        promoted = entry.get("promoted")
        count = entry.get("count", 0)
        if promoted:
            QtWidgets.QMessageBox.information(
                self, "识别纠错", f"已晋升为规则：{src} → {dst}（第 {count} 次）"
            )

    def _clear_learned(self) -> None:
        button = QtWidgets.QMessageBox.question(
            self,
            "识别纠错",
            "确定清空全部学习缓冲？已晋升的规则不受影响。",
        )
        if button != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        corrector = _resolve_corrector(self.main_app)
        for entry in corrector.list_learned():
            corrector.learned.remove(entry.get("src", ""))
        self.load_correction_state()

    def collect_state(self) -> dict:
        return {
            ASR_REQUIRE_WAKE_WORD_KEY: self.require_wake_word.isChecked(),
            ASR_USE_CHARACTER_WAKE_WORDS_KEY: self.use_character_wake_words.isChecked(),
            ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY: self.include_global_wake_words.isChecked(),
            ASR_GLOBAL_WAKE_WORDS_KEY: self.global_wake_words.toPlainText(),
            ASR_EXTRA_WAKE_WORDS_KEY: self.extra_wake_words.toPlainText(),
            ASR_ACTIVE_WINDOW_SEC_KEY: int(self.active_window_sec.value()),
            ASR_MIN_CHARS_KEY: int(self.min_chars.value()),
            ASR_ENGINE_KEY: str(self.engine.currentData() or ASR_ENGINE_QWEN3),
            ASR_HOTWORDS_FROM_WAKE_KEY: self.hotwords_from_wake.isChecked(),
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
