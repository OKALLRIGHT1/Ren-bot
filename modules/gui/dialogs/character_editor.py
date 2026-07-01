import json
import os
import re
import requests

from PySide6 import QtWidgets, QtCore, QtGui
from modules.character_manager import character_manager, DEFAULT_EMOTION_KEYS
from modules.live2d import MODEL_DEFAULT_MOTION, STOP_MOTION

try:
    from modules.gui.styles import get_ui_palette
except Exception:

    def get_ui_palette():
        return {
            "accent": "#6366F1",
            "accent_hover": "#4F46E5",
            "accent_soft": "#EEF2FF",
            "bg_app": "#F5F7FB",
            "bg_card": "#FFFFFF",
            "bg_soft": "#F3F4F6",
            "bg_console": "#111827",
            "border": "#E5E7EB",
            "border_strong": "#D1D5DB",
            "text_primary": "#111827",
            "text_secondary": "#6B7280",
            "text_muted": "#9CA3AF",
            "success": "#10B981",
            "success_soft": "#D1FAE5",
            "warning": "#F59E0B",
            "danger": "#EF4444",
        }


try:
    from config import EMO_TO_LIVE2D
except Exception:
    EMO_TO_LIVE2D = {}


def get_character_editor_styles_v2() -> str:
    p = get_ui_palette()
    return f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei';
            color: {p["text_primary"]};
        }}
        QFrame#charLeftCard, QFrame#charRightCard {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 16px;
        }}
        QLabel#charSectionTitle {{
            color: {p["text_primary"]};
            font-size: 16px;
            font-weight: 700;
        }}
        QLabel#charHint {{
            color: {p["text_secondary"]};
            font-size: 12px;
        }}
        QListWidget, QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QTableWidget {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 10px;
        }}
        QListWidget::item {{
            padding: 8px 10px;
            border-radius: 8px;
            margin: 2px 0;
            color: {p["text_secondary"]};
        }}
        QListWidget::item:selected {{
            background: {p["accent_soft"]};
            color: {p["accent_hover"]};
            font-weight: 700;
        }}
        QTabWidget::pane {{
            border: 1px solid {p["border"]};
            border-radius: 12px;
            background: {p["bg_card"]};
        }}
        QTabBar::tab {{
            background: {p["bg_soft"]};
            color: {p["text_secondary"]};
            border: 1px solid {p["border"]};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p["accent_soft"]};
            color: {p["accent_hover"]};
            font-weight: 700;
        }}
        QTableWidget {{
            gridline-color: {p["border"]};
        }}
        QHeaderView::section {{
            background: {p["bg_card"]};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p["border"]};
        }}
        QPushButton {{
            background: {p["bg_card"]};
            color: {p["text_primary"]};
            border: 1px solid {p["border_strong"]};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p["accent"]};
            color: {p["accent_hover"]};
        }}
        QPushButton#charPrimary {{
            background: {p["accent"]};
            color: white;
            border: none;
        }}
        QPushButton#charPrimary:hover {{
            background: {p["accent_hover"]};
            color: white;
        }}
        QPushButton#charDanger {{
            color: #DC2626;
            background: #FEF2F2;
            border: 1px solid #FECACA;
        }}
        QPushButton#charDanger:hover {{
            color: #B91C1C;
            background: #FEE2E2;
            border-color: #FCA5A5;
        }}
        QGroupBox {{
            border: 1px solid {p["border"]};
            border-radius: 12px;
            margin-top: 10px;
            padding: 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            color: {p["text_secondary"]};
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        QScrollArea > QWidget > QWidget {{
            background: transparent;
        }}
        QScrollBar:vertical {{
            border: none;
            background: transparent;
            width: 8px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {p["border_strong"]};
            min-height: 20px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {p["accent"]};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            border: none;
            background: none;
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}
    """


class CharacterEditorWidget(QtWidgets.QWidget):
    """嵌入在 SettingsDialog 中的角色管理页"""

    def __init__(self, main_app):
        super().__init__()
        self.main_app = main_app
        self.mgr = character_manager
        self.current_char_id = None
        self.current_costume_name = None
        self._current_motion_options = []
        self._current_expression_options = []
        self._selected_motion_candidates = []

        self.setStyleSheet(get_character_editor_styles_v2())
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        left_panel = QtWidgets.QFrame()
        left_panel.setObjectName("charLeftCard")
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        left_title = QtWidgets.QLabel("角色列表")
        left_title.setObjectName("charSectionTitle")
        left_layout.addWidget(left_title)

        left_hint = QtWidgets.QLabel(
            "这里放你当前角色与已创建角色，激活角色会带 ⭐ 标识。"
        )
        left_hint.setObjectName("charHint")
        left_hint.setWordWrap(True)
        left_layout.addWidget(left_hint)

        self.char_list = QtWidgets.QListWidget()
        self.char_list.currentRowChanged.connect(self._on_char_selected)

        btn_add_char = QtWidgets.QPushButton("+ 新建角色")
        btn_add_char.setObjectName("charPrimary")
        btn_add_char.clicked.connect(self._add_character)

        # 允许列表缩小，避免其 minimumSizeHint 顶住外层对话框高度
        self.char_list.setMinimumHeight(0)
        left_layout.addWidget(self.char_list, 1)
        left_layout.addWidget(btn_add_char)

        self.right_shell = QtWidgets.QFrame()
        self.right_shell.setObjectName("charRightCard")
        right_shell_layout = QtWidgets.QVBoxLayout(self.right_shell)
        right_shell_layout.setContentsMargins(14, 14, 14, 14)
        right_shell_layout.setSpacing(10)

        right_title = QtWidgets.QLabel("角色详情")
        right_title.setObjectName("charSectionTitle")
        right_shell_layout.addWidget(right_title)

        right_hint = QtWidgets.QLabel(
            "编辑人设、服装和情绪映射。左侧选择角色后，这里会显示详细配置。"
        )
        right_hint.setObjectName("charHint")
        right_hint.setWordWrap(True)
        right_shell_layout.addWidget(right_hint)

        self.right_panel = QtWidgets.QTabWidget()
        self.right_panel.setVisible(False)

        self.tab_persona = QtWidgets.QWidget()
        self._init_tab_persona()
        self.right_panel.addTab(self.tab_persona, "人设与提示词")

        self.tab_tts = QtWidgets.QWidget()
        self._init_tab_tts()
        self.right_panel.addTab(self.tab_tts, "角色 TTS")

        self.tab_costume = QtWidgets.QWidget()
        self._init_tab_costume()
        self.right_panel.addTab(self.tab_costume, "服装管理")

        right_shell_layout.addWidget(self.right_panel, 1)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.right_shell)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

    def _init_tab_persona(self):
        # 创建主容器布局
        main_layout = QtWidgets.QVBoxLayout(self.tab_persona)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 创建滚动区域
        scroll = QtWidgets.QScrollArea(self.tab_persona)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        
        # 创建滚动区域的容器 widget
        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setObjectName("personaScrollWidget")
        
        # 将原 layout 绑定到 scroll_widget 上
        layout = QtWidgets.QVBoxLayout(scroll_widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()
        self.edit_name = QtWidgets.QLineEdit()
        self.edit_name.textChanged.connect(self._save_current_char)
        form.addRow("角色名称:", self.edit_name)
        self.edit_aliases = QtWidgets.QPlainTextEdit()
        self.edit_aliases.setMaximumHeight(72)
        self.edit_aliases.setPlaceholderText("每行一个别名，例如：祥子、小祥")
        self.edit_aliases.textChanged.connect(self._save_current_char)
        form.addRow("别名:", self.edit_aliases)
        layout.addLayout(form)

        layout.addWidget(QtWidgets.QLabel("人设提示词 (System Prompt):"))
        self.edit_prompt = QtWidgets.QTextEdit()
        self.edit_prompt.setMinimumHeight(150)  # 保证输入框有合适高度
        self.edit_prompt.textChanged.connect(self._save_current_char)
        layout.addWidget(self.edit_prompt)

        catchphrase_group = QtWidgets.QGroupBox("固定口癖")
        catchphrase_layout = QtWidgets.QFormLayout(catchphrase_group)
        self.catchphrase_enabled = QtWidgets.QCheckBox(
            "启用发送层概率追加（不会交给大模型自由发挥）"
        )
        self.catchphrase_enabled.stateChanged.connect(self._save_current_char)
        catchphrase_layout.addRow("开关:", self.catchphrase_enabled)

        self.catchphrase_text = QtWidgets.QLineEdit()
        self.catchphrase_text.setPlaceholderText("例如：……はい。")
        self.catchphrase_text.textChanged.connect(self._save_current_char)
        catchphrase_layout.addRow("文本:", self.catchphrase_text)

        self.catchphrase_probability = QtWidgets.QSpinBox()
        self.catchphrase_probability.setRange(0, 100)
        self.catchphrase_probability.setSuffix("%")
        self.catchphrase_probability.setSingleStep(1)
        self.catchphrase_probability.valueChanged.connect(self._save_current_char)
        catchphrase_layout.addRow("出现概率:", self.catchphrase_probability)

        catchphrase_hint = QtWidgets.QLabel(
            "角色没有配置口癖时不会追加；启用后会贴到最后一句末尾，不会单独分行。"
        )
        catchphrase_hint.setObjectName("charHint")
        catchphrase_hint.setWordWrap(True)
        catchphrase_layout.addRow("", catchphrase_hint)
        layout.addWidget(catchphrase_group)

        qq_group = QtWidgets.QGroupBox("QQ 资料同步")
        qq_layout = QtWidgets.QFormLayout(qq_group)
        self.qq_profile_enabled = QtWidgets.QCheckBox(
            "切换到此角色时同步 QQ 昵称/头像"
        )
        self.qq_profile_enabled.stateChanged.connect(self._save_current_char)
        qq_layout.addRow("开关:", self.qq_profile_enabled)

        self.qq_profile_nickname = QtWidgets.QLineEdit()
        self.qq_profile_nickname.setPlaceholderText("例如：五十铃")
        self.qq_profile_nickname.textChanged.connect(self._save_current_char)
        qq_layout.addRow("QQ 昵称:", self.qq_profile_nickname)

        self.qq_profile_avatar = QtWidgets.QLineEdit()
        self.qq_profile_avatar.setPlaceholderText(
            "本地头像路径或 URL，例如 assets/avatars/isuzu.png"
        )
        self.qq_profile_avatar.textChanged.connect(self._save_current_char)
        avatar_row = QtWidgets.QHBoxLayout()
        avatar_row.addWidget(self.qq_profile_avatar, 1)
        btn_pick_avatar = QtWidgets.QPushButton("选择头像")
        btn_pick_avatar.clicked.connect(self._pick_qq_avatar_file)
        avatar_row.addWidget(btn_pick_avatar)
        avatar_wrap = QtWidgets.QWidget()
        avatar_wrap.setLayout(avatar_row)
        qq_layout.addRow("QQ 头像:", avatar_wrap)

        qq_hint = QtWidgets.QLabel(
            "保存后，激活该角色会先检查当前 QQ 昵称；昵称相同则跳过修改。头像仅在填写时同步。"
        )
        qq_hint.setObjectName("charHint")
        qq_hint.setWordWrap(True)
        qq_layout.addRow("", qq_hint)
        layout.addWidget(qq_group)

        self.btn_activate = QtWidgets.QPushButton("🚀 切换为此角色")
        self.btn_activate.setObjectName("charPrimary")
        self.btn_activate.clicked.connect(self._activate_character)
        layout.addWidget(self.btn_activate)

        btn_del = QtWidgets.QPushButton("🗑️ 删除此角色")
        btn_del.setObjectName("charDanger")
        btn_del.clicked.connect(self._delete_current_char)
        layout.addWidget(btn_del, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        # 关联滚动部件
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

    def _init_tab_tts(self):
        # 创建主容器布局
        main_layout = QtWidgets.QVBoxLayout(self.tab_tts)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 创建滚动区域
        scroll = QtWidgets.QScrollArea(self.tab_tts)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        
        # 创建滚动区域的容器 widget
        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setObjectName("ttsScrollWidget")
        
        # 将原 layout 绑定到 scroll_widget 上
        layout = QtWidgets.QVBoxLayout(scroll_widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()
        self.tts_enabled = QtWidgets.QCheckBox("启用角色专属 GPT-SoVITS")
        self.tts_enabled.stateChanged.connect(self._save_current_char)
        form.addRow("开关:", self.tts_enabled)

        base_hint = QtWidgets.QLabel(
            "GPTSOVITS_BASE 使用全局配置；这里仅设置角色自己的 GPT/SoVITS 权重、参考音频和提示词。"
        )
        base_hint.setObjectName("charHint")
        base_hint.setWordWrap(True)
        layout.addWidget(base_hint)

        self.tts_gpt_w = QtWidgets.QLineEdit()
        self.tts_gpt_w.textChanged.connect(self._save_current_char)
        gpt_row = QtWidgets.QHBoxLayout()
        gpt_row.addWidget(self.tts_gpt_w, 1)
        btn_pick_gpt = QtWidgets.QPushButton("选择文件")
        btn_pick_gpt.clicked.connect(
            lambda: self._pick_tts_file(
                self.tts_gpt_w,
                "选择 GPT 权重",
                "模型文件 (*.ckpt *.pth);;所有文件 (*.*)",
            )
        )
        gpt_row.addWidget(btn_pick_gpt)
        gpt_wrap = QtWidgets.QWidget()
        gpt_wrap.setLayout(gpt_row)
        form.addRow("GPT_W:", gpt_wrap)

        self.tts_sov_w = QtWidgets.QLineEdit()
        self.tts_sov_w.textChanged.connect(self._save_current_char)
        sov_row = QtWidgets.QHBoxLayout()
        sov_row.addWidget(self.tts_sov_w, 1)
        btn_pick_sov = QtWidgets.QPushButton("选择文件")
        btn_pick_sov.clicked.connect(
            lambda: self._pick_tts_file(
                self.tts_sov_w,
                "选择 SoVITS 权重",
                "模型文件 (*.pth *.ckpt);;所有文件 (*.*)",
            )
        )
        sov_row.addWidget(btn_pick_sov)
        sov_wrap = QtWidgets.QWidget()
        sov_wrap.setLayout(sov_row)
        form.addRow("SOV_W:", sov_wrap)

        self.tts_ref_wav = QtWidgets.QLineEdit()
        self.tts_ref_wav.textChanged.connect(self._save_current_char)
        ref_row = QtWidgets.QHBoxLayout()
        ref_row.addWidget(self.tts_ref_wav, 1)
        btn_pick_ref = QtWidgets.QPushButton("选择音频")
        btn_pick_ref.clicked.connect(
            lambda: self._pick_tts_file(
                self.tts_ref_wav,
                "选择参考音频",
                "音频文件 (*.wav *.mp3 *.flac *.ogg);;所有文件 (*.*)",
            )
        )
        ref_row.addWidget(btn_pick_ref)
        ref_wrap = QtWidgets.QWidget()
        ref_wrap.setLayout(ref_row)
        form.addRow("REF_WAV:", ref_wrap)

        self.tts_prompt_lang = QtWidgets.QLineEdit()
        self.tts_prompt_lang.textChanged.connect(self._save_current_char)
        form.addRow("PROMPT_LANG:", self.tts_prompt_lang)

        self.tts_prompt_text = QtWidgets.QTextEdit()
        self.tts_prompt_text.setMinimumHeight(80)
        self.tts_prompt_text.textChanged.connect(self._save_current_char)
        form.addRow("PROMPT_TEXT:", self.tts_prompt_text)
        layout.addLayout(form)

        self.tts_status = QtWidgets.QPlainTextEdit()
        self.tts_status.setReadOnly(True)
        self.tts_status.setMaximumHeight(120)
        layout.addWidget(self.tts_status)

        test_row = QtWidgets.QHBoxLayout()
        self.tts_test_text = QtWidgets.QLineEdit()
        self.tts_test_text.setPlaceholderText(
            "输入一段测试文本，例如：こんにちは、五十铃怜です。"
        )
        btn_check_tts = QtWidgets.QPushButton("检测配置")
        btn_check_tts.clicked.connect(self._check_current_tts)
        btn_test_tts = QtWidgets.QPushButton("测试发音")
        btn_test_tts.setObjectName("charPrimary")
        btn_test_tts.clicked.connect(self._test_current_tts)
        test_row.addWidget(self.tts_test_text, 1)
        test_row.addWidget(btn_check_tts)
        test_row.addWidget(btn_test_tts)
        layout.addLayout(test_row)

        # 关联滚动部件
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

    def _init_tab_costume(self):
        # Costume Management Tab is redesigned as a side-by-side layout
        # Left Panel: Costume list and basic controls
        left_group = QtWidgets.QGroupBox("服装模型")
        left_layout = QtWidgets.QVBoxLayout(left_group)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        self.costume_list = QtWidgets.QListWidget()
        self.costume_list.itemDoubleClicked.connect(self._wear_selected_costume)
        self.costume_list.currentItemChanged.connect(self._on_costume_changed)
        # 允许列表缩小，避免其 minimumSizeHint 顶住外层对话框高度
        self.costume_list.setMinimumHeight(0)
        left_layout.addWidget(self.costume_list, 1)

        btn_wear = QtWidgets.QPushButton("👕 立即换穿")
        btn_wear.setObjectName("charPrimary")
        btn_wear.clicked.connect(self._wear_selected_costume)
        left_layout.addWidget(btn_wear)

        btn_action_row = QtWidgets.QHBoxLayout()
        btn_import = QtWidgets.QPushButton("📂 导入模型")
        btn_import.clicked.connect(self._import_costume)
        btn_import.setToolTip("选择 .model3.json 或 model.json 模型文件")
        btn_del_cos = QtWidgets.QPushButton("🗑️ 删除")
        btn_del_cos.setObjectName("charDanger")
        btn_del_cos.clicked.connect(self._delete_costume)
        btn_action_row.addWidget(btn_import, 1)
        btn_action_row.addWidget(btn_del_cos, 1)
        left_layout.addLayout(btn_action_row)

        btn_generate_role_default = QtWidgets.QPushButton("设为角色默认来源")
        btn_generate_role_default.clicked.connect(
            self._generate_character_default_from_current_costume
        )
        left_layout.addWidget(btn_generate_role_default)

        self.lbl_costume_summary = QtWidgets.QLabel("未加载模型")
        self.lbl_costume_summary.setObjectName("charHint")
        self.lbl_costume_summary.setWordWrap(True)
        left_layout.addWidget(self.lbl_costume_summary)

        # Right Panel: Emotion mapping table and edit details form
        right_group = QtWidgets.QGroupBox("情绪与动作映射")
        right_layout = QtWidgets.QVBoxLayout(right_group)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        self.emo_table = QtWidgets.QTableWidget()
        self.emo_table.setColumnCount(4)
        self.emo_table.setHorizontalHeaderLabels(
            ["情绪", "动作(mtn)", "表情(exp)", "来源"]
        )
        self.emo_table.horizontalHeader().setStretchLastSection(True)
        self.emo_table.verticalHeader().setVisible(False)
        self.emo_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.emo_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.emo_table.itemSelectionChanged.connect(self._on_emotion_selection_changed)
        # 允许表格缩小，避免其 minimumSizeHint 顶住外层对话框高度
        self.emo_table.setMinimumHeight(0)
        right_layout.addWidget(self.emo_table, 1)

        self.edit_mapping_group = QtWidgets.QGroupBox("编辑选中情绪映射")
        edit_layout = QtWidgets.QVBoxLayout(self.edit_mapping_group)
        edit_layout.setContentsMargins(10, 10, 10, 10)
        edit_layout.setSpacing(8)

        self.lbl_selected_emo = QtWidgets.QLabel("请在上方列表中选择一个情绪")
        self.lbl_selected_emo.setStyleSheet("font-weight: bold; color: #4F46E5;")
        edit_layout.addWidget(self.lbl_selected_emo)

        form_grid = QtWidgets.QGridLayout()
        form_grid.setSpacing(6)
        # 让动作/表情下拉框所在列可伸缩，窄宽时让位于右侧固定宽度按钮，避免重叠
        form_grid.setColumnStretch(1, 1)

        form_grid.addWidget(QtWidgets.QLabel("动作 (Motion):"), 0, 0)
        self.combo_motion = QtWidgets.QComboBox()
        form_grid.addWidget(self.combo_motion, 0, 1)

        btn_preview_motion = QtWidgets.QPushButton("▶ 预览")
        btn_preview_motion.clicked.connect(self._preview_selected_motion)
        btn_preview_motion.setMinimumWidth(64)
        btn_preview_motion.setMaximumWidth(64)
        form_grid.addWidget(btn_preview_motion, 0, 2)

        btn_add_motion_candidate = QtWidgets.QPushButton("+ 候选")
        btn_add_motion_candidate.clicked.connect(self._add_motion_candidate)
        btn_add_motion_candidate.setMinimumWidth(72)
        btn_add_motion_candidate.setMaximumWidth(72)
        form_grid.addWidget(btn_add_motion_candidate, 0, 3)

        form_grid.addWidget(QtWidgets.QLabel("动作类型:"), 1, 0)
        self.combo_motion_type = QtWidgets.QComboBox()
        self.combo_motion_type.addItem("普通 (0)", 0)
        self.combo_motion_type.addItem("闲置 (1)", 1)
        form_grid.addWidget(self.combo_motion_type, 1, 1, 1, 3)

        form_grid.addWidget(QtWidgets.QLabel("候选动作:"), 4, 0)
        self.motion_candidates_list = QtWidgets.QListWidget()
        self.motion_candidates_list.setMaximumHeight(72)
        form_grid.addWidget(self.motion_candidates_list, 4, 1, 1, 2)

        btn_remove_motion_candidate = QtWidgets.QPushButton("移除")
        btn_remove_motion_candidate.clicked.connect(self._remove_motion_candidate)
        btn_remove_motion_candidate.setMinimumWidth(72)
        btn_remove_motion_candidate.setMaximumWidth(72)
        form_grid.addWidget(btn_remove_motion_candidate, 4, 3)

        form_grid.addWidget(QtWidgets.QLabel("表情 (Expr):"), 2, 0)
        self.combo_expression = QtWidgets.QComboBox()
        form_grid.addWidget(self.combo_expression, 2, 1)

        btn_preview_expr = QtWidgets.QPushButton("▶ 预览")
        btn_preview_expr.clicked.connect(self._preview_selected_expression)
        btn_preview_expr.setMinimumWidth(64)
        btn_preview_expr.setMaximumWidth(64)
        form_grid.addWidget(btn_preview_expr, 2, 2)

        edit_layout.addLayout(form_grid)

        btn_save_row = QtWidgets.QHBoxLayout()
        self.btn_save_character_map = QtWidgets.QPushButton("保存到角色默认")
        self.btn_save_character_map.setObjectName("charPrimary")
        self.btn_save_character_map.clicked.connect(
            self._apply_dropdown_to_character_default
        )
        self.btn_save_map = QtWidgets.QPushButton("✅ 保存映射")
        self.btn_save_map.setObjectName("charPrimary")
        self.btn_save_map.clicked.connect(self._apply_dropdown_to_selected_emotion)

        self.btn_clear_map = QtWidgets.QPushButton("🧹 清除映射")
        self.btn_clear_map.setObjectName("charDanger")
        self.btn_clear_map.clicked.connect(self._clear_selected_emotion_override)

        btn_save_row.addWidget(self.btn_save_character_map, 1)
        btn_save_row.addWidget(self.btn_save_map, 1)
        btn_save_row.addWidget(self.btn_clear_map, 1)
        edit_layout.addLayout(btn_save_row)

        right_layout.addWidget(self.edit_mapping_group)

        # Assemble layout
        main_costume_layout = QtWidgets.QHBoxLayout(self.tab_costume)
        main_costume_layout.setContentsMargins(6, 6, 6, 6)
        main_costume_layout.setSpacing(10)
        main_costume_layout.addWidget(left_group, 2)
        main_costume_layout.addWidget(right_group, 3)

    # --- 逻辑 ---

    def _refresh_list(self):
        chars = self.mgr.get_all_characters()
        self.char_list.clear()
        active_id = self.mgr.data.get("active_id")

        for cid, data in chars.items():
            name = data.get("name", cid)
            prefix = "⭐ " if cid == active_id else ""
            item = QtWidgets.QListWidgetItem(f"{prefix}{name}")
            item.setData(QtCore.Qt.UserRole, cid)
            self.char_list.addItem(item)

    def _on_char_selected(self, row):
        item = self.char_list.currentItem()
        if not item:
            return
        cid = item.data(QtCore.Qt.UserRole)
        self.current_char_id = cid
        self._load_char_to_ui(cid)
        self.right_panel.setVisible(True)

    def _load_char_to_ui(self, cid):
        data = self.mgr.get_character(cid)
        if not data:
            return

        self.edit_name.blockSignals(True)
        self.edit_aliases.blockSignals(True)
        self.edit_prompt.blockSignals(True)
        self.catchphrase_enabled.blockSignals(True)
        self.catchphrase_text.blockSignals(True)
        self.catchphrase_probability.blockSignals(True)
        self.qq_profile_enabled.blockSignals(True)
        self.qq_profile_nickname.blockSignals(True)
        self.qq_profile_avatar.blockSignals(True)

        self.edit_name.setText(data.get("name", ""))
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases_text = aliases
        elif isinstance(aliases, list):
            aliases_text = "\n".join(str(item) for item in aliases if str(item).strip())
        else:
            aliases_text = ""
        self.edit_aliases.setPlainText(aliases_text)
        self.edit_prompt.setPlainText(data.get("prompt", ""))
        catchphrase_cfg = data.get("catchphrase") or {}
        self.catchphrase_enabled.setChecked(bool(catchphrase_cfg.get("enabled", False)))
        self.catchphrase_text.setText(str(catchphrase_cfg.get("text", "") or ""))
        try:
            catchphrase_probability = int(catchphrase_cfg.get("probability", 0))
        except Exception:
            catchphrase_probability = 0
        self.catchphrase_probability.setValue(max(0, min(100, catchphrase_probability)))
        qq_profile = data.get("qq_profile") or {}
        self.qq_profile_enabled.setChecked(bool(qq_profile.get("enabled", False)))
        self.qq_profile_nickname.setText(str(qq_profile.get("nickname", "") or ""))
        self.qq_profile_avatar.setText(
            str(
                qq_profile.get("avatar_path")
                or qq_profile.get("avatar")
                or qq_profile.get("avatar_url")
                or qq_profile.get("avatar_file")
                or ""
            )
        )
        tts_cfg = data.get("tts_config") or {}
        self.tts_enabled.blockSignals(True)
        self.tts_gpt_w.blockSignals(True)
        self.tts_sov_w.blockSignals(True)
        self.tts_ref_wav.blockSignals(True)
        self.tts_prompt_lang.blockSignals(True)
        self.tts_prompt_text.blockSignals(True)
        self.tts_enabled.setChecked(bool(tts_cfg.get("enabled", False)))
        self.tts_gpt_w.setText(str(tts_cfg.get("gpt_w", "")))
        self.tts_sov_w.setText(str(tts_cfg.get("sov_w", "")))
        self.tts_ref_wav.setText(str(tts_cfg.get("ref_wav", "")))
        self.tts_prompt_lang.setText(str(tts_cfg.get("prompt_lang", "ja")))
        self.tts_prompt_text.setPlainText(str(tts_cfg.get("prompt_text", "")))
        self.tts_status.setPlainText("")

        self.edit_name.blockSignals(False)
        self.edit_aliases.blockSignals(False)
        self.edit_prompt.blockSignals(False)
        self.catchphrase_enabled.blockSignals(False)
        self.catchphrase_text.blockSignals(False)
        self.catchphrase_probability.blockSignals(False)
        self.qq_profile_enabled.blockSignals(False)
        self.qq_profile_nickname.blockSignals(False)
        self.qq_profile_avatar.blockSignals(False)
        self.tts_enabled.blockSignals(False)
        self.tts_gpt_w.blockSignals(False)
        self.tts_sov_w.blockSignals(False)
        self.tts_ref_wav.blockSignals(False)
        self.tts_prompt_lang.blockSignals(False)
        self.tts_prompt_text.blockSignals(False)

        self.costume_list.clear()
        costumes = data.get("costumes", {})
        for cname, cdata in costumes.items():
            item = QtWidgets.QListWidgetItem(cname)
            item.setData(QtCore.Qt.UserRole, cdata.get("path"))
            self.costume_list.addItem(item)

        preferred_costume = self.mgr.get_current_costume_name(cid)
        target_row = 0
        if preferred_costume:
            for index in range(self.costume_list.count()):
                if self.costume_list.item(index).text() == preferred_costume:
                    target_row = index
                    break
        if self.costume_list.count() > 0:
            self.costume_list.setCurrentRow(target_row)
        else:
            self.current_costume_name = None
            self._refresh_costume_detail_ui()

        is_active = cid == self.mgr.data.get("active_id")
        self.btn_activate.setEnabled(not is_active)
        self.btn_activate.setText("当前已激活" if is_active else "🚀 切换为此角色")

    def _save_current_char(self):
        if not self.current_char_id:
            return
        data = self.mgr.get_character(self.current_char_id)
        data["name"] = self.edit_name.text()
        data["aliases"] = [
            line.strip()
            for line in self.edit_aliases.toPlainText().splitlines()
            if line.strip()
        ]
        data["prompt"] = self.edit_prompt.toPlainText()
        data["catchphrase"] = {
            "enabled": bool(self.catchphrase_enabled.isChecked()),
            "text": self.catchphrase_text.text().strip(),
            "probability": int(self.catchphrase_probability.value()),
        }
        data["qq_profile"] = {
            "enabled": bool(self.qq_profile_enabled.isChecked()),
            "nickname": self.qq_profile_nickname.text().strip(),
            "avatar_path": self.qq_profile_avatar.text().strip(),
        }
        data["tts_config"] = {
            "enabled": bool(self.tts_enabled.isChecked()),
            "gpt_w": self.tts_gpt_w.text().strip(),
            "sov_w": self.tts_sov_w.text().strip(),
            "ref_wav": self.tts_ref_wav.text().strip(),
            "prompt_lang": self.tts_prompt_lang.text().strip() or "ja",
            "prompt_text": self.tts_prompt_text.toPlainText().strip(),
        }
        self.mgr.save()
        item = self.char_list.currentItem()
        if item:
            item.setText(data["name"])

    def _pick_tts_file(self, line_edit, title: str, file_filter: str):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, "", file_filter)
        if path:
            line_edit.setText(path.replace("\\", "/"))

    def _pick_qq_avatar_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 QQ 头像",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*.*)",
        )
        if path:
            self.qq_profile_avatar.setText(path.replace("\\", "/"))

    def _test_current_tts(self):
        if not self.current_char_id:
            return
        app_ref = self.main_app
        tts = getattr(app_ref, "tts", None)
        if tts is None:
            nested_app = getattr(app_ref, "app", None)
            tts = getattr(nested_app, "tts", None)
        if tts is None:
            QtWidgets.QMessageBox.warning(
                self,
                "失败",
                "未找到运行中的 TTSRouter 实例，请先确认主程序已完整启动。",
            )
            return
        text = self.tts_test_text.text().strip() or "こんにちは、五十铃怜です。"
        cfg = self.mgr.get_tts_config(self.current_char_id)
        try:
            tts.apply_role_tts_config(cfg)
            import asyncio

            loop = getattr(app_ref, "loop", None) or getattr(
                getattr(app_ref, "app", None), "loop", None
            )
            if loop is None:
                raise RuntimeError("未找到主事件循环")
            asyncio.run_coroutine_threadsafe(
                tts.say(text, emotion="neutral", interrupt=True, show_bubble=True),
                loop,
            )
            QtWidgets.QMessageBox.information(self, "成功", "已发送测试发音请求。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "错误", f"测试发音失败: {e}")

    def _check_current_tts(self):
        if not self.current_char_id:
            return
        cfg = self.mgr.get_tts_config(self.current_char_id)
        lines = []
        enabled = bool(cfg.get("enabled", False))
        lines.append(f"开关: {'开启' if enabled else '关闭'}")

        from config import GPTSOVITS_BASE

        base = str(GPTSOVITS_BASE or "").strip()
        lines.append(f"GPTSOVITS_BASE(全局): {base or '(未填写)'}")

        for key in ("gpt_w", "sov_w", "ref_wav"):
            path = str(cfg.get(key, "") or "").strip()
            exists = os.path.isfile(path) if path else False
            lines.append(f"{key}: {'OK' if exists else '缺失'} | {path or '(未填写)'}")

        prompt_lang = str(cfg.get("prompt_lang", "") or "").strip()
        prompt_text = str(cfg.get("prompt_text", "") or "").strip()
        lines.append(f"PROMPT_LANG: {prompt_lang or '(未填写)'}")
        lines.append(f"PROMPT_TEXT: {'已填写' if prompt_text else '未填写'}")

        if base:
            try:
                requests.get(base.rstrip("/") + "/", timeout=2)
                lines.append("服务连通: OK")
            except Exception as e:
                lines.append(f"服务连通: 失败 ({e})")
        else:
            lines.append("服务连通: 未检测（未填写 BASE）")

        self.tts_status.setPlainText("\n".join(lines))

    def _add_character(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "新建角色", "请输入角色名称:")
        if ok and name:
            import uuid

            cid = f"char_{uuid.uuid4().hex[:6]}"
            self.mgr.add_character(cid, name, "你是一个AI助手。")
            self._refresh_list()

    def _delete_current_char(self):
        if not self.current_char_id:
            return
        ret = QtWidgets.QMessageBox.question(self, "确认", "确定要删除这个角色吗？")
        if ret == QtWidgets.QMessageBox.StandardButton.Yes:
            self.mgr.delete_character(self.current_char_id)
            self._refresh_list()
            self.right_panel.setVisible(False)

    def _activate_character(self):
        if not self.current_char_id:
            return
        app_ref = getattr(self.main_app, "app", None) or self.main_app
        try:
            import __main__

            app_ref = getattr(__main__, "app_instance", app_ref)
        except Exception:
            pass
        if app_ref and hasattr(app_ref, "switch_character_runtime"):
            app_ref.switch_character_runtime(self.current_char_id)
        else:
            self.mgr.set_active_character(self.current_char_id)

        if hasattr(self.main_app, "plugin_manager"):
            pass

        self._refresh_list()
        self._load_char_to_ui(self.current_char_id)
        QtWidgets.QMessageBox.information(
            self,
            "成功",
            "角色已切换！\n提示词、TTS 与默认服装已同步。",
        )

    def _extract_expression_id(self, name: str, file_name: str):
        for text in [name or "", file_name or ""]:
            nums = re.findall(r"\d+", text)
            if nums:
                try:
                    return int(nums[-1])
                except Exception:
                    pass
        return None

    def _normalize_motion_name(self, raw_motion_name: str):
        name = str(raw_motion_name or "").strip()
        if not name:
            return ""
        if ":" in name:
            return name
        return f"Motion:{name}"

    def _motion_name_from_file(self, file_name: str):
        raw = str(file_name or "").replace("\\", "/").strip()
        if not raw:
            return ""
        name = raw.rsplit("/", 1)[-1]
        name = re.sub(r"\.motion3\.json$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\.mtn$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\.[a-z0-9]+$", "", name, flags=re.IGNORECASE)
        return name.strip()

    def _is_generic_motion_group(self, group_name: str):
        group = str(group_name or "").strip().lower()
        return group in {"", "motion", "motions", "idle", "tapbody"}

    def _iter_motion_groups(self, raw_motion_refs):
        if isinstance(raw_motion_refs, dict):
            for group_name, items in raw_motion_refs.items():
                if isinstance(items, list):
                    yield str(group_name), items
            return
        if isinstance(raw_motion_refs, list):
            yield "Motion", raw_motion_refs

    def _parse_model_meta(self, model_path: str):
        motions, expressions = [], []
        if not model_path:
            return motions, expressions

        path = str(model_path).replace("\\", "/")
        abs_path = os.path.abspath(path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            refs = data.get("FileReferences", {})

            motion_refs = refs.get("Motions", {}) if isinstance(refs, dict) else {}
            for group_name, motion_items in self._iter_motion_groups(motion_refs):
                for idx, item in enumerate(motion_items):
                    if not isinstance(item, dict):
                        continue
                    file_name = str(item.get("File") or item.get("file") or "").strip()
                    raw_name = (
                        item.get("Name")
                        or item.get("name")
                        or item.get("mtn")
                        or self._motion_name_from_file(file_name)
                    )
                    motion_name = (
                        str(raw_name).strip() if raw_name else f"{group_name}:{idx}"
                    )
                    motion_name = self._normalize_motion_name(motion_name)
                    motions.append(
                        {
                            "name": motion_name,
                            "raw_name": str(raw_name).strip()
                            if raw_name
                            else motion_name,
                            "preview_mtn": motion_name,
                            "group": group_name,
                            "index": int(idx),
                        }
                    )

            expr_items = refs.get("Expressions", []) if isinstance(refs, dict) else []
            if isinstance(expr_items, list):
                for idx, item in enumerate(expr_items):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name") or item.get("name") or "").strip()
                    file_name = str(item.get("File") or item.get("file") or "").strip()
                    exp_id = int(idx)
                    label = name or file_name or f"exp_{idx}"
                    expressions.append(
                        {
                            "label": label,
                            "name": name,
                            "file": file_name,
                            "exp_id": exp_id,
                            "preview_exp": name or file_name,
                        }
                    )

            if not motions:
                legacy_motions = data.get("motions", {})
                for group_name, motion_items in self._iter_motion_groups(
                    legacy_motions
                ):
                    for idx, item in enumerate(motion_items):
                        if isinstance(item, dict):
                            file_name = str(item.get("file") or item.get("File") or "").strip()
                            raw_name = item.get("name") or item.get("Name") or item.get("mtn")
                            if not raw_name and not self._is_generic_motion_group(group_name):
                                raw_name = group_name
                            if not raw_name:
                                raw_name = self._motion_name_from_file(file_name)
                        else:
                            raw_name = (
                                group_name
                                if not self._is_generic_motion_group(group_name)
                                else self._motion_name_from_file(str(item)) or str(item)
                            )
                        motion_name = (
                            str(raw_name).strip() if raw_name else f"{group_name}:{idx}"
                        )
                        preview_mtn = motion_name
                        group_text = str(group_name or "").strip()
                        if group_text and ":" not in preview_mtn:
                            preview_mtn = f"{group_text}:{preview_mtn}"
                        motions.append(
                            {
                                "name": motion_name,
                                "raw_name": str(raw_name).strip()
                                if raw_name
                                else motion_name,
                                "preview_mtn": preview_mtn,
                                "group": group_name,
                                "index": int(idx),
                            }
                        )

            if not expressions:
                legacy_expr = data.get("expressions", [])
                if isinstance(legacy_expr, list):
                    for idx, item in enumerate(legacy_expr):
                        if isinstance(item, dict):
                            name = str(
                                item.get("name") or item.get("Name") or ""
                            ).strip()
                            file_name = str(
                                item.get("file") or item.get("File") or ""
                            ).strip()
                        else:
                            name = ""
                            file_name = str(item)
                        exp_id = int(idx)
                        label = name or file_name or f"exp_{idx}"
                        expressions.append(
                            {
                                "label": label,
                                "name": name,
                                "file": file_name,
                                "exp_id": exp_id,
                                "preview_exp": name or file_name,
                            }
                        )
        except Exception:
            pass
        return motions, expressions

    def _refresh_preview_options(self, motions, expressions):
        self._current_motion_options = motions if isinstance(motions, list) else []
        self._current_expression_options = (
            expressions if isinstance(expressions, list) else []
        )

        self.combo_motion.clear()
        self.combo_motion.addItem("模型默认姿态 / 刚打开状态", MODEL_DEFAULT_MOTION)
        self.combo_motion.addItem("停止动作 / 不播放动作", STOP_MOTION)
        if self._current_motion_options:
            for item in self._current_motion_options:
                name = str(item.get("name") or "").strip()
                raw_name = str(item.get("raw_name") or "").strip()
                preview_mtn = str(item.get("preview_mtn") or "").strip()
                group = str(item.get("group") or "").strip()
                preview_name = raw_name or name
                label = f"{preview_name} [{group}]" if group else preview_name
                self.combo_motion.addItem(label, preview_mtn or name)
        self.combo_expression.clear()
        if self._current_expression_options:
            for item in self._current_expression_options:
                label = str(item.get("label") or "").strip() or "(未命名表情)"
                exp_id = item.get("exp_id")
                suffix = f" (ID={exp_id})" if exp_id is not None else " (按名预览)"
                self.combo_expression.addItem(f"{label}{suffix}", exp_id)
        else:
            self.combo_expression.addItem("(未解析到表情)", None)

    def _resolve_emotion_row(
        self,
        emotion: str,
        derived_map: dict,
        character_map: dict,
        costume_map: dict,
    ):
        default_cfg = (
            EMO_TO_LIVE2D.get(emotion, {}) if isinstance(EMO_TO_LIVE2D, dict) else {}
        )
        cfg = default_cfg if isinstance(default_cfg, dict) else {}
        source = "global"
        derived_cfg = derived_map.get(emotion, {}) if isinstance(derived_map, dict) else {}
        if isinstance(derived_cfg, dict) and derived_cfg.get("mtn"):
            cfg = derived_cfg
            source = "derived"
        character_cfg = (
            character_map.get(emotion, {}) if isinstance(character_map, dict) else {}
        )
        if isinstance(character_cfg, dict) and (
            character_cfg.get("mtn") or character_cfg.get("motions")
        ):
            cfg = character_cfg
            source = "character"
        costume_cfg = costume_map.get(emotion, {}) if isinstance(costume_map, dict) else {}
        if isinstance(costume_cfg, dict) and (
            costume_cfg.get("mtn") or costume_cfg.get("motions")
        ):
            cfg = costume_cfg
            source = "costume"

        if isinstance(cfg, dict) and cfg.get("motions"):
            mtn = self._format_motion_candidates(self._normalize_motion_candidates(cfg))
        else:
            mtn = str(cfg.get("mtn", "")) if isinstance(cfg, dict) else ""
        exp = cfg.get("exp", "") if isinstance(cfg, dict) else ""
        return mtn, "" if exp is None else str(exp), source

    def _normalize_motion_candidates(self, cfg: dict):
        if not isinstance(cfg, dict):
            return []
        candidates = []
        raw = cfg.get("motions")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    mtn = str(item.get("mtn") or "").strip()
                    type_val = item.get("type", cfg.get("type", 0))
                else:
                    mtn = str(item or "").strip()
                    type_val = cfg.get("type", 0)
                if not mtn:
                    continue
                try:
                    type_val = int(type_val or 0)
                except Exception:
                    type_val = 0
                candidate = {"mtn": mtn, "type": type_val}
                if candidate not in candidates:
                    candidates.append(candidate)
        if not candidates:
            mtn = str(cfg.get("mtn") or "").strip()
            if mtn:
                try:
                    type_val = int(cfg.get("type", 0) or 0)
                except Exception:
                    type_val = 0
                candidates.append({"mtn": mtn, "type": type_val})
        return candidates

    def _format_motion_candidates(self, candidates):
        if not candidates:
            return ""
        return "\n".join(
            f"{item.get('mtn', '')} (type={int(item.get('type', 0) or 0)})"
            for item in candidates
            if item.get("mtn")
        )

    def _refresh_motion_candidates_list(self):
        if not hasattr(self, "motion_candidates_list"):
            return
        self.motion_candidates_list.clear()
        for item in self._selected_motion_candidates:
            self.motion_candidates_list.addItem(
                f"{item.get('mtn', '')} (type={int(item.get('type', 0) or 0)})"
            )

    def _add_motion_candidate(self):
        mtn = str(self.combo_motion.currentData() or "").strip()
        if not mtn:
            return
        candidate = {
            "mtn": mtn,
            "type": int(self.combo_motion_type.currentData() or 0),
        }
        if candidate not in self._selected_motion_candidates:
            self._selected_motion_candidates.append(candidate)
        self._refresh_motion_candidates_list()

    def _remove_motion_candidate(self):
        row = self.motion_candidates_list.currentRow()
        if row < 0 or row >= len(self._selected_motion_candidates):
            return
        self._selected_motion_candidates.pop(row)
        self._refresh_motion_candidates_list()

    def _refresh_costume_detail_ui(self):
        if not self.current_char_id or not self.current_costume_name:
            self.lbl_costume_summary.setText("未加载模型")
            self.emo_table.setRowCount(0)
            self._refresh_preview_options([], [])
            self.edit_mapping_group.setEnabled(False)
            self.lbl_selected_emo.setText("无可用服装")
            return

        self.edit_mapping_group.setEnabled(True)
        char = self.mgr.get_character(self.current_char_id) or {}
        costume = (char.get("costumes") or {}).get(self.current_costume_name) or {}
        model_path = costume.get("path", "")
        costume_map = (
            costume.get("emotion_map", {})
            if isinstance(costume.get("emotion_map", {}), dict)
            else {}
        )
        character_map = (
            char.get("default_emotion_map", {})
            if isinstance(char.get("default_emotion_map", {}), dict)
            else {}
        )

        motions, expressions = self._parse_model_meta(model_path)
        motion_names = [
            str(item.get("name", "")) for item in motions if isinstance(item, dict)
        ]
        expr_labels = [
            str(item.get("label", "")) for item in expressions if isinstance(item, dict)
        ]

        self.lbl_costume_summary.setText(
            f"已载入服装模型配置:\n"
            f"• 动作数量: {len(motion_names)} 个\n"
            f"• 表情数量: {len(expr_labels)} 个"
        )
        self._refresh_preview_options(motions, expressions)
        runtime_cfg = self.mgr.get_costume_runtime_config(
            self.current_char_id, self.current_costume_name
        )
        runtime_map = (
            runtime_cfg.get("emotion_map", {})
            if isinstance(runtime_cfg.get("emotion_map", {}), dict)
            else {}
        )
        derived_keys = set(runtime_cfg.get("derived_emotion_keys") or [])
        character_keys = set(runtime_cfg.get("character_default_emotion_keys") or [])
        costume_keys = set(runtime_cfg.get("costume_override_emotion_keys") or [])
        derived_map = {
            key: value
            for key, value in runtime_map.items()
            if key in derived_keys and key not in character_keys and key not in costume_keys
        }

        rows = list(DEFAULT_EMOTION_KEYS)
        self.emo_table.setRowCount(len(rows))
        for row, emo in enumerate(rows):
            mtn, exp, source = self._resolve_emotion_row(
                emo, derived_map, character_map, costume_map
            )
            self.emo_table.setItem(row, 0, QtWidgets.QTableWidgetItem(emo))
            self.emo_table.setItem(row, 1, QtWidgets.QTableWidgetItem(mtn))
            self.emo_table.setItem(row, 2, QtWidgets.QTableWidgetItem(exp))
            self.emo_table.setItem(row, 3, QtWidgets.QTableWidgetItem(source))

        # Restore row selection or default to first row (neutral)
        current_row = self.emo_table.currentRow()
        if current_row >= 0 and current_row < len(rows):
            self.emo_table.setCurrentCell(current_row, 0)
        elif len(rows) > 0:
            self.emo_table.setCurrentCell(0, 0)
        else:
            self._on_emotion_selection_changed()

    def _on_costume_changed(self, current, previous):
        if not current:
            self.current_costume_name = None
            self._refresh_costume_detail_ui()
            return

        self.current_costume_name = current.text()
        if self.current_char_id and self.current_costume_name:
            self.mgr.set_current_costume_name(
                self.current_char_id, self.current_costume_name
            )
            if self.main_app and hasattr(self.main_app, "_refresh_character_status"):
                self.main_app._refresh_character_status()
        self._refresh_costume_detail_ui()

    def _on_emotion_selection_changed(self):
        emo = self._selected_emotion()
        if not emo:
            self.lbl_selected_emo.setText("请在上方列表中选择一个情绪")
            self.btn_save_character_map.setEnabled(False)
            self.btn_save_map.setEnabled(False)
            self.btn_clear_map.setEnabled(False)
            self._selected_motion_candidates = []
            self._refresh_motion_candidates_list()
            return

        self.btn_save_character_map.setEnabled(True)
        self.btn_save_map.setEnabled(True)
        self.btn_clear_map.setEnabled(True)
        self.lbl_selected_emo.setText(f"当前选中情绪: {emo.upper()}")

        row = self.emo_table.currentRow()
        if row < 0:
            return

        mtn_cell = self.emo_table.item(row, 1)
        exp_cell = self.emo_table.item(row, 2)

        mtn_str = mtn_cell.text().strip() if mtn_cell else ""
        exp_str = exp_cell.text().strip() if exp_cell else ""

        selected_mtn = mtn_str
        selected_type = 0
        if self.current_char_id and self.current_costume_name:
            char = self.mgr.get_character(self.current_char_id) or {}
            costume = (char.get("costumes") or {}).get(self.current_costume_name) or {}
            costume_map = costume.get("emotion_map", {})
            character_map = char.get("default_emotion_map", {})
            costume_cfg = costume_map.get(emo, {}) if isinstance(costume_map, dict) else {}
            character_cfg = (
                character_map.get(emo, {}) if isinstance(character_map, dict) else {}
            )
            override_cfg = (
                costume_cfg
                if isinstance(costume_cfg, dict)
                and (costume_cfg.get("mtn") or costume_cfg.get("motions"))
                else character_cfg
            )
            self._selected_motion_candidates = self._normalize_motion_candidates(
                override_cfg if isinstance(override_cfg, dict) else {}
            )
            self._refresh_motion_candidates_list()
            if self._selected_motion_candidates:
                selected_mtn = str(self._selected_motion_candidates[0].get("mtn") or "")
                selected_type = int(self._selected_motion_candidates[0].get("type", 0) or 0)
            elif isinstance(override_cfg, dict):
                selected_type = int(override_cfg.get("type", 0) or 0)

        if selected_mtn:
            idx = -1
            for i in range(self.combo_motion.count()):
                if self.combo_motion.itemData(i) == selected_mtn:
                    idx = i
                    break
            if idx < 0:
                idx = self.combo_motion.findText(selected_mtn)
            if idx >= 0:
                self.combo_motion.setCurrentIndex(idx)
        else:
            self.combo_motion.setCurrentIndex(0)

        idx = self.combo_motion_type.findData(selected_type)
        if idx >= 0:
            self.combo_motion_type.setCurrentIndex(idx)
        else:
            self.combo_motion_type.setCurrentIndex(0)

        # Update expression selection
        if exp_str:
            idx = -1
            if exp_str.isdigit():
                val_to_find = int(exp_str)
            else:
                val_to_find = exp_str

            for i in range(self.combo_expression.count()):
                data_val = self.combo_expression.itemData(i)
                if str(data_val) == str(val_to_find):
                    idx = i
                    break
            if idx < 0:
                idx = self.combo_expression.findText(exp_str)
            if idx >= 0:
                self.combo_expression.setCurrentIndex(idx)
        else:
            self.combo_expression.setCurrentIndex(0)

    def _selected_emotion(self):
        row = self.emo_table.currentRow()
        if row < 0:
            return None
        item = self.emo_table.item(row, 0)
        return item.text().strip().lower() if item else None

    def _edit_selected_emotion_override(self):
        # Retain for compatibility or fallback, but logic now centers on _apply_dropdown_to_selected_emotion
        if not self.current_char_id or not self.current_costume_name:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(
                self, "提示", "请先在表格中选中一个情绪。"
            )
            return
        self._apply_dropdown_to_selected_emotion()

    def _preview_selected_motion(self):
        if not self.main_app or not hasattr(self.main_app, "preview_motion"):
            return
        motion_name = str(self.combo_motion.currentData() or "").strip()
        if not motion_name:
            QtWidgets.QMessageBox.information(
                self, "提示", "当前服装没有可预览的动作。"
            )
            return
        motion_type = int(self.combo_motion_type.currentData() or 0)
        reloaded = self._load_selected_costume_for_preview()
        for delay_ms in self._preview_retry_delays(reloaded):
            QtCore.QTimer.singleShot(
                delay_ms,
                lambda name=motion_name, type_=motion_type: self.main_app.preview_motion(
                    name, type_
                ),
            )

    def _preview_selected_expression(self):
        if not self.main_app or not hasattr(self.main_app, "preview_expression"):
            return
        exp_value = self.combo_expression.currentData()
        if exp_value is None:
            QtWidgets.QMessageBox.information(
                self, "提示", "该表情未识别到 exp ID，无法直接预览。"
            )
            return
        reloaded = self._load_selected_costume_for_preview()
        for delay_ms in self._preview_retry_delays(reloaded):
            QtCore.QTimer.singleShot(
                delay_ms,
                lambda value=exp_value: self.main_app.preview_expression(value),
            )

    def _preview_retry_delays(self, reloaded: bool):
        if reloaded:
            return (700, 1400, 2200)
        return (0,)

    def _normalize_preview_model_path(self, path: str) -> str:
        raw = str(path or "").strip().replace("\\", "/")
        if not raw:
            return ""
        try:
            return os.path.normcase(os.path.abspath(raw)).replace("\\", "/")
        except Exception:
            return raw.lower()

    def _load_selected_costume_for_preview(self) -> bool:
        if (
            not self.current_char_id
            or not self.current_costume_name
            or not self.main_app
            or not getattr(self.main_app, "on_costume_callback", None)
        ):
            return False
        char = self.mgr.get_character(self.current_char_id) or {}
        costume = (char.get("costumes") or {}).get(self.current_costume_name) or {}
        path = str(costume.get("path") or "").strip()
        if not path:
            return False
        current_path = getattr(self.main_app, "_current_costume_path", "")
        if self._normalize_preview_model_path(current_path) == self._normalize_preview_model_path(
            path
        ):
            return False
        cfg = self.mgr.get_costume_runtime_config(
            self.current_char_id, self.current_costume_name
        )
        cfg = dict(cfg)
        cfg["preview_mode"] = True
        cfg["suppress_auto_idle"] = True
        self.main_app.on_costume_callback(path, cfg)
        return True

    def _apply_dropdown_to_selected_emotion(self):
        if not self.current_char_id or not self.current_costume_name:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(
                self, "提示", "请先在表格中选中一个情绪。"
            )
            return

        mtn = str(self.combo_motion.currentData() or "").strip()
        if not mtn:
            QtWidgets.QMessageBox.warning(self, "无效输入", "当前动作为空，无法应用。")
            return

        motion_type = int(self.combo_motion_type.currentData() or 0)
        payload = {
            "mtn": mtn,
            "type": motion_type,
        }
        if len(self._selected_motion_candidates) > 1:
            payload["motions"] = [dict(item) for item in self._selected_motion_candidates]
        exp_value = self.combo_expression.currentData()
        if exp_value is not None:
            if isinstance(exp_value, str) and exp_value.strip():
                payload["exp"] = exp_value.strip()
            else:
                payload["exp"] = int(exp_value)

        self.mgr.set_costume_emotion_override(
            self.current_char_id, self.current_costume_name, emo, payload
        )
        current_row = self.emo_table.currentRow()
        self._refresh_costume_detail_ui()
        if current_row >= 0:
            self.emo_table.setCurrentCell(current_row, 0)

    def _apply_dropdown_to_character_default(self):
        if not self.current_char_id:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(
                self, "Info", "Select an emotion first."
            )
            return

        mtn = str(self.combo_motion.currentData() or "").strip()
        if not mtn:
            QtWidgets.QMessageBox.warning(self, "Invalid", "Motion is empty.")
            return

        payload = {
            "mtn": mtn,
            "type": int(self.combo_motion_type.currentData() or 0),
        }
        if len(self._selected_motion_candidates) > 1:
            payload["motions"] = [dict(item) for item in self._selected_motion_candidates]
        exp_value = self.combo_expression.currentData()
        if exp_value is not None:
            if isinstance(exp_value, str) and exp_value.strip():
                payload["exp"] = exp_value.strip()
            else:
                payload["exp"] = int(exp_value)

        self.mgr.set_character_emotion_default(self.current_char_id, emo, payload)
        current_row = self.emo_table.currentRow()
        self._refresh_costume_detail_ui()
        if current_row >= 0:
            self.emo_table.setCurrentCell(current_row, 0)

    def _clear_selected_emotion_override(self):
        if not self.current_char_id or not self.current_costume_name:
            return
        emo = self._selected_emotion()
        if not emo:
            QtWidgets.QMessageBox.information(
                self, "提示", "请先在表格中选中一个情绪。"
            )
            return

        char = self.mgr.get_character(self.current_char_id) or {}
        costume = (char.get("costumes") or {}).get(self.current_costume_name) or {}
        costume_map = costume.get("emotion_map", {})
        if isinstance(costume_map, dict) and emo in costume_map:
            self.mgr.set_costume_emotion_override(
                self.current_char_id, self.current_costume_name, emo, None
            )
        else:
            self.mgr.set_character_emotion_default(self.current_char_id, emo, None)
        self._selected_motion_candidates = []
        self._refresh_motion_candidates_list()
        current_row = self.emo_table.currentRow()
        self._refresh_costume_detail_ui()
        if current_row >= 0:
            self.emo_table.setCurrentCell(current_row, 0)

    def _generate_character_default_from_current_costume(self):
        if not self.current_char_id or not self.current_costume_name:
            return
        ok = self.mgr.generate_character_default_emotion_map(
            self.current_char_id, self.current_costume_name
        )
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Invalid", "Cannot generate defaults from this costume."
            )
            return
        current_row = self.emo_table.currentRow()
        self._refresh_costume_detail_ui()
        if current_row >= 0:
            self.emo_table.setCurrentCell(current_row, 0)

    def _import_costume(self):
        if not self.current_char_id:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 Live2D 模型定义文件",
            "",
            "Live2D JSON (*.model3.json *.json);;Model3 JSON (*.model3.json);;Model JSON (*.json)",
        )
        if path:
            base = os.path.basename(path).lower()
            if base not in {"model.json"} and not base.endswith(".model3.json"):
                QtWidgets.QMessageBox.warning(
                    self,
                    "提示",
                    "请选择标准的 .model3.json 或名为 model.json 的 Live2D 模型文件。",
                )
                return
            name, ok = QtWidgets.QInputDialog.getText(
                self, "服装名称", "给这件衣服起个名字:"
            )
            if ok and name:
                self.mgr.add_costume(self.current_char_id, name, path)
                self._load_char_to_ui(self.current_char_id)

    def _delete_costume(self):
        item = self.costume_list.currentItem()
        if not item:
            return
        name = item.text()
        self.mgr.delete_costume(self.current_char_id, name)
        self._load_char_to_ui(self.current_char_id)

    def _wear_selected_costume(self):
        item = self.costume_list.currentItem()
        if not item:
            return
        path = item.data(QtCore.Qt.UserRole)
        name = item.text()
        cfg = self.mgr.get_costume_runtime_config(self.current_char_id, name)
        self.mgr.set_current_costume_name(self.current_char_id, name)

        if self.main_app and self.main_app.on_costume_callback:
            self.main_app.on_costume_callback(path, cfg)
            if hasattr(self.main_app, "_refresh_character_status"):
                self.main_app._refresh_character_status()
            QtWidgets.QMessageBox.information(self, "换装", f"已发送换装指令: {name}")
