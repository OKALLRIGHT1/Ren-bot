from __future__ import annotations
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Any

from PySide6 import QtCore, QtGui, QtWidgets

# 引入我们的新模块
from modules.gui.config import QtGuiConfig, DEFAULT_BALL_CONFIG
from modules.gui.window_geom import clamp_rect, panel_pos_from_ball
from modules.gui.widgets.chat_input import ChatInput
from modules.gui.widgets.chat_transcript import ChatTranscript
from modules.gui.styles import (
    get_main_styles,
    get_panel_styles,
    get_ui_palette,
    get_settings_styles,
    get_tool_dialog_styles,
    get_memory_dialog_styles,
)
from modules.gui.utils import resolve_icon, set_dot_status, classify_status, elide_label_text
from modules.gui.runtime_health_view import (
    RUNTIME_HEALTH_REFRESH_INTERVAL_MS,
    component_rows,
    overall_presentation,
)
from modules.gui.widgets.ball import DraggableBall
from modules.gui.sedentary_popup import (
    build_sedentary_popup_options,
    show_sedentary_popup_dialog,
)
try:
    from modules.character_manager import character_manager
except ImportError:
    # External-brain packaging: the brain-side modules.character_manager is
    # not bundled. Provide a no-op stub so the client still boots and shows
    # offline state — character/costume features simply report "未激活角色".
    class _OfflineCharacterManager:
        data = {}

        @staticmethod
        def _empty(*_args, **_kwargs):
            return {}

        def __getattr__(self, name):
            return self._empty

    character_manager = _OfflineCharacterManager()  # type: ignore[assignment]
from modules.gui.user_ia import (
    MODE_PRESETS,
    TRAY_HIDE_HINT_KEY,
    chat_setup_status,
    is_developer_mode,
)
try:
    from modules.runtime_settings import load_runtime_settings, update_runtime_settings
except ImportError:
    # External-brain packaging: runtime_settings lives in the brain.
    # Provide passthrough stubs so imports and feature calls don't crash.
    def load_runtime_settings(*_args, **_kwargs):
        return {}

    def update_runtime_settings(*_args, **_kwargs):
        return {}

# 引入配置
try:
    from config import COSTUME_MAP
except ImportError:
    COSTUME_MAP = {}

try:
    from config import BALL_CONFIG
except ImportError:
    BALL_CONFIG = DEFAULT_BALL_CONFIG

# External-brain packaging: the brain's top-level config.py is not bundled.
# Insert a benign placeholder into sys.modules so bare `import config`
# statements later in this file (and in modules it pulls in) resolve to an
# object with safe attribute defaults instead of raising ImportError.
import sys as _sys
import types as _types

if "config" not in _sys.modules:
    try:
        import config  # noqa: F401  — last attempt via real sys.path
    except ImportError:
        _stub = _types.ModuleType("config")
        _stub.TTS_ENABLED = False
        _stub.VOICE_SENSOR_ENABLED = False
        _stub.VOICE_PTT_ENABLED = False
        _stub.DND_MODE = False
        _stub.HOTKEY_TOGGLE_GUI = "<ctrl>+<alt>+<space>"
        _stub.COSTUME_MAP = {}
        _stub.BALL_CONFIG = BALL_CONFIG
        _sys.modules["config"] = _stub


WORK_SESSION_EMPTY_LABEL = "工作了 --"
WORK_SESSION_RESTING_LABEL = "休息中"
WORK_SESSION_LABEL_MIN_WIDTH = 88
WORK_SESSION_LABEL_MAX_WIDTH = 168
PANEL_MIN_WIDTH = 360
PANEL_COMPACT_HEIGHT = 156
PANEL_EXPANDED_MIN_HEIGHT = 280
# 30s：会话分钟数更新粒度本来就粗，10s 刷新看不到差别。
WORK_SESSION_REFRESH_INTERVAL_MS = 30_000


def format_work_session_label(session: Optional[dict]) -> str:
    if not isinstance(session, dict):
        return ""
    try:
        active_minutes = int(session.get("active_minutes") or 0)
    except Exception:
        active_minutes = 0
    try:
        active_seconds = int(session.get("active_seconds") or 0)
    except Exception:
        active_seconds = 0
    if active_minutes <= 0 and active_seconds <= 0:
        if str(session.get("state") or "").strip().lower() == "resting":
            return WORK_SESSION_RESTING_LABEL
        # No countable sit time yet (first minute, missing payload, or disconnected).
        # Top bar is a clock, not a sensor debug line.
        return ""

    if active_minutes >= 60:
        hours = active_minutes // 60
        minutes = active_minutes % 60
        duration = f"{hours} 小时" if minutes == 0 else f"{hours} 小时 {minutes} 分钟"
    elif active_minutes <= 0:
        duration = "<1 分钟"
    else:
        duration = f"{active_minutes} 分钟"

    return f"工作了 {duration}"


class _Bridge(QtCore.QObject):
    sig_append = QtCore.Signal(str, str)
    sig_status = QtCore.Signal(str)
    sig_refresh_character = QtCore.Signal()
    sig_apply_character_switch = QtCore.Signal(str, object, str, str)
    sig_sync_active_character_visual = QtCore.Signal()
    sig_trigger_costume_name = QtCore.Signal(str)
    sig_focus_input = QtCore.Signal()
    sig_set_wake = QtCore.Signal(bool)
    sig_set_tts = QtCore.Signal(bool)
    sig_toggle_gui = QtCore.Signal()
    sig_send_text = QtCore.Signal(str)
    sig_sedentary_popup = QtCore.Signal(str, int, str, object)
    sig_action_confirm = QtCore.Signal(str, str, object)
    sig_refresh_work_session = QtCore.Signal()
    sig_ptt_start = QtCore.Signal()
    sig_ptt_stop = QtCore.Signal()
    sig_set_frontend_suppressed = QtCore.Signal(bool)
    sig_show_hud = QtCore.Signal()
    sig_toggle_show_hide = QtCore.Signal()
    sig_quit = QtCore.Signal()
    sig_connection_state = QtCore.Signal(str, str)


class QtChatTrayApp(QtCore.QObject):
    def __init__(
        self,
        on_send_callback: Callable[..., None],
        *,
        on_tts_toggle_callback: Optional[Callable[[bool], None]] = None,
        on_voice_toggle_callback: Optional[
            Callable[[bool], None]
        ] = None,  # 🟢 新增语音回调
        on_ptt_start_callback: Optional[Callable[[], None]] = None,
        on_ptt_stop_callback: Optional[Callable[[], None]] = None,
        on_costume_callback: Optional[Callable[[str, dict], None]] = None,
        on_preview_motion_callback: Optional[Callable[[str, int], None]] = None,
        on_preview_expression_callback: Optional[Callable[[int], None]] = None,
        on_quit_callback: Optional[Callable[[], None]] = None,
        on_restart_callback: Optional[Callable[[], None]] = None,
        on_apply_external_settings_callback: Optional[Callable[[dict], dict]] = None,
        on_display_state_callback: Optional[Callable[[dict], None]] = None,
        on_cancel_callback: Optional[Callable[[], None]] = None,
        on_new_topic_callback: Optional[Callable[[], None]] = None,
        on_start_backend_callback: Optional[Callable[[], None]] = None,
        on_stop_backend_callback: Optional[Callable[[], None]] = None,
        on_stop_all_callback: Optional[Callable[[], None]] = None,
        plugin_manager: Optional[Any] = None,
        runtime_health: Optional[Any] = None,
        cfg: Optional[QtGuiConfig] = None,
    ):
        super().__init__()
        self.cfg = cfg or QtGuiConfig()
        self.on_send_callback = on_send_callback
        self.on_tts_toggle_callback = on_tts_toggle_callback
        self.on_voice_toggle_callback = on_voice_toggle_callback  # 🟢 保存回调
        self.on_ptt_start_callback = on_ptt_start_callback
        self.on_ptt_stop_callback = on_ptt_stop_callback
        self._external_costume_callback = on_costume_callback
        self.on_costume_callback = self._apply_costume_change
        self.on_preview_motion_callback = on_preview_motion_callback
        self.on_preview_expression_callback = on_preview_expression_callback
        self.on_quit_callback = on_quit_callback
        self.on_restart_callback = on_restart_callback
        self.on_apply_external_settings_callback = on_apply_external_settings_callback
        self.on_display_state_callback = on_display_state_callback
        self.on_cancel_callback = on_cancel_callback
        self.on_new_topic_callback = on_new_topic_callback
        self.on_start_backend_callback = on_start_backend_callback
        self.on_stop_backend_callback = on_stop_backend_callback
        self.on_stop_all_callback = on_stop_all_callback
        self.plugin_manager = plugin_manager
        self.runtime_health = runtime_health
        self.screen_sensor = None
        self._remote_work_session = None
        self.on_request_work_session_callback = None
        self._runtime_health_dialog = None
        self._last_health_rows = None  # 首次快照只记录基线，不补发历史故障

        # --- 内部状态 ---
        self._is_ball_mode = True
        self._last_panel_size = QtCore.QSize(604, 156)
        self._current_mode_name = "陪伴"
        self._wake_enabled = False
        self._current_costume_name = "未设定"
        self._ball_geom = None
        self._chat_busy = False
        self._setup_hint_shown = False

        # --- ASR 占位符 ---
        self._mic_lock = threading.Lock()
        self._asr_recognizer = None
        self._is_wake_listening = False

        # --- Qt 初始化 ---
        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
            sys.argv
        )
        self._app.setQuitOnLastWindowClosed(False)
        self._app.applicationStateChanged.connect(self._on_app_state_changed)
        self._icon = resolve_icon(self.cfg.icon_path)
        self._app.setWindowIcon(self._icon)
        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "Live2D.Suzu.Brain"
                )
            except Exception:
                pass

        # --- 信号桥接 ---
        self._bridge = _Bridge()
        self._bridge.sig_append.connect(self._append_ui)
        self._bridge.sig_status.connect(self._set_status_ui)
        self._bridge.sig_refresh_character.connect(self._refresh_character_status)
        self._bridge.sig_apply_character_switch.connect(self._apply_character_switch)
        self._bridge.sig_sync_active_character_visual.connect(
            self._sync_active_character_visual
        )
        self._bridge.sig_trigger_costume_name.connect(self._on_costume_triggered)
        self._bridge.sig_toggle_gui.connect(self.toggle_hotkey)
        self._bridge.sig_send_text.connect(self._send_text_from_asr)
        self._bridge.sig_sedentary_popup.connect(self._show_sedentary_popup_ui)
        self._bridge.sig_action_confirm.connect(self._show_action_confirm_ui)
        self._bridge.sig_refresh_work_session.connect(self.refresh_work_session_status)
        self._bridge.sig_ptt_start.connect(self._on_ptt_start)
        self._bridge.sig_ptt_stop.connect(self._on_ptt_stop)
        self._bridge.sig_set_frontend_suppressed.connect(
            self._apply_frontend_suppressed
        )
        self._bridge.sig_quit.connect(self._quit)
        self._bridge.sig_show_hud.connect(self._show_hud_ui)
        self._bridge.sig_toggle_show_hide.connect(self.toggle_show_hide)
        self._bridge.sig_connection_state.connect(self._apply_connection_state_ui)
        self._frontend_suppressed = False
        self._connection_state = "connecting"
        self._connection_detail = ""

        # --- 构建 UI ---
        self._win = self._build_window()
        self._tray = self._build_tray()
        self._work_session_timer = QtCore.QTimer(self)
        self._work_session_timer.setInterval(WORK_SESSION_REFRESH_INTERVAL_MS)
        self._work_session_timer.timeout.connect(self.refresh_work_session_status)
        self._work_session_timer.start()
        self._runtime_health_timer = QtCore.QTimer(self)
        self._runtime_health_timer.setInterval(RUNTIME_HEALTH_REFRESH_INTERVAL_MS)
        self._runtime_health_timer.timeout.connect(self.refresh_runtime_health_status)
        self._runtime_health_timer.start()
        self._last_health_announce_at: dict[str, float] = {}

        self._settings_dialog = None
        self._knowledge_dialog = None
        self._status_screen_dialog = None
        self._plugin_dialog = None
        self._memory_dialog = None
        self._console_dialog = None
        self._expression_library_dialog = None
        self._meme_dialog = None

        # 启动逻辑
        if self.cfg.start_minimized_to_tray:
            self.hide()
        else:
            self._switch_to_ball()
            self._center_window()
            self._win.show()

        # --- 全局热键 + Push-to-talk 监听 ---
        self._hotkey_listener = None
        self._ptt_active = False
        self._start_global_listeners()

        self.set_status("就绪")
        # 显示初始连接状态：连接中（WS 尚未拨通）/ 未连接 / 已连接，与
        # set_status 的"就绪"分开维护，避免 WS 未连通时状态栏还显示"就绪"。
        try:
            self._apply_connection_state_ui(self._connection_state, self._connection_detail)
        except Exception:
            pass
        self.refresh_work_session_status()
        self.refresh_runtime_health_status()
        self._maybe_hint_setup()

    def apply_external_settings(self, settings: Optional[dict] = None):
        res = {}
        if callable(self.on_apply_external_settings_callback):
            res = self.on_apply_external_settings_callback(settings or {}) or {}
        self._sync_transcript_identity()
        return res

    def apply_asr_settings(self, settings: Optional[dict] = None) -> dict:
        app_obj = getattr(self, "app", None)
        if app_obj is not None and callable(getattr(app_obj, "apply_asr_settings", None)):
            return app_obj.apply_asr_settings(settings) or {}
        from modules.asr.settings import (
            load_asr_settings,
            resolve_wake_words,
            save_asr_settings,
        )

        active = (
            save_asr_settings(settings)
            if isinstance(settings, dict)
            else load_asr_settings()
        )
        return {
            "ok": True,
            "wake_words": resolve_wake_words(settings=active),
            "sensor_reloaded": False,
        }

    # --- 核心 UI 构建 ---

    def apply_ui_palette(self):
        try:
            self._win.setStyleSheet(get_panel_styles())
            if self._settings_dialog:
                self._settings_dialog.setStyleSheet(get_settings_styles())
            if self._console_dialog:
                self._console_dialog.setStyleSheet(get_tool_dialog_styles())
            if self._memory_dialog:
                self._memory_dialog.setStyleSheet(get_memory_dialog_styles())
            if self._plugin_dialog:
                self._plugin_dialog.setStyleSheet(get_tool_dialog_styles())
            if hasattr(self, "_expression_library_dialog") and self._expression_library_dialog:
                self._expression_library_dialog.setStyleSheet(get_tool_dialog_styles())
            if hasattr(self, "_meme_dialog") and self._meme_dialog:
                self._meme_dialog.setStyleSheet(get_tool_dialog_styles())
            if hasattr(self, "_knowledge_dialog") and self._knowledge_dialog:
                self._knowledge_dialog.setStyleSheet(get_tool_dialog_styles())
            if hasattr(self, "_status_screen_dialog") and self._status_screen_dialog:
                self._status_screen_dialog.setStyleSheet(get_tool_dialog_styles())
        except Exception:
            pass


    def _build_window(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setWindowTitle(self.cfg.title)
        w.setWindowIcon(self._icon)
        w.setWindowFlags(
            QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
        )
        w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        w.setStyleSheet(get_panel_styles())

        self._stack = QtWidgets.QStackedLayout(w)

        self._ball_widget = QtWidgets.QWidget()
        self._ball_widget.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self._ball_widget.setStyleSheet("background: transparent;")

        ball_layout = QtWidgets.QVBoxLayout(self._ball_widget)
        ball_layout.setContentsMargins(5, 5, 5, 5)

        self._ball_btn = DraggableBall(main_window=w)
        self._ball_btn.setObjectName("ball_btn")
        s = BALL_CONFIG.get("size", 60)
        self._ball_btn.setFixedSize(s, s)
        self._ball_btn.clicked.connect(self._switch_to_panel)
        self._ball_btn.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._ball_btn.customContextMenuRequested.connect(
            self._show_costume_menu_from_ball
        )

        img_path = BALL_CONFIG.get("image_path", "")
        enable_image = BALL_CONFIG.get("enable_image", False)

        valid_icon = False
        if enable_image and img_path:
            p = Path(img_path)
            if not p.is_absolute():
                # Relative paths are resolved against the client root, the
                # install root, and the brain root (where assets/ lives in
                # dev mode) — never against Path.cwd(), which for a packaged
                # build may be unrelated to the EXE directory.
                p = self._resolve_resource_path(p)
            if p is not None and p.exists():
                icon = QtGui.QIcon(str(p))
                if not icon.isNull():
                    self._ball_btn.setIcon(icon)
                    self._ball_btn.setIconSize(QtCore.QSize(s, s))
                    self._ball_btn.setText("")
                    self._ball_btn.setStyleSheet(f"""
                        QPushButton {{ background-color: transparent; border: none; border-radius: {s // 2}px; }}
                        QPushButton:hover {{ margin-top: -2px; }}
                        QPushButton:pressed {{ margin-top: 0px; }}
                    """)
                    valid_icon = True

        if not valid_icon:
            # Fallback must always be a visible solid circle, even if the
            # configured image is missing or failed to load. Using a non-empty
            # text ensures the button never collapses into a hollow ring.
            text = BALL_CONFIG.get("text") or "L2D"
            try:
                self._ball_btn.setText(text)
            except Exception:
                pass
            safe_cfg = dict(BALL_CONFIG)
            safe_cfg["text"] = text
            self._ball_btn.setStyleSheet(get_main_styles(safe_cfg))

        shadow = QtWidgets.QGraphicsDropShadowEffect(self._ball_btn)
        shadow.setBlurRadius(15)
        shadow.setColor(QtGui.QColor(0, 0, 0, 80))
        self._ball_btn.setGraphicsEffect(shadow)

        ball_layout.addWidget(self._ball_btn)
        self._stack.addWidget(self._ball_widget)

        self._panel_widget = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(self._panel_widget)
        panel_layout.setContentsMargins(7, 7, 7, 7)

        self._container = QtWidgets.QFrame()
        self._container.setObjectName("container")

        panel_shadow = QtWidgets.QGraphicsDropShadowEffect(self._container)
        panel_shadow.setBlurRadius(22)
        panel_shadow.setColor(QtGui.QColor(15, 23, 42, 35))
        self._container.setGraphicsEffect(panel_shadow)
        panel_layout.addWidget(self._container)

        main_vbox = QtWidgets.QVBoxLayout(self._container)
        main_vbox.setContentsMargins(12, 10, 12, 10)
        main_vbox.setSpacing(8)

        # 顶栏：状态信息 + 紧凑窗口控制胶囊
        title_bar = QtWidgets.QFrame()
        title_bar.setObjectName("titleBar")
        top_bar = QtWidgets.QHBoxLayout(title_bar)
        top_bar.setContentsMargins(8, 4, 6, 4)
        top_bar.setSpacing(6)

        self._dot = QtWidgets.QPushButton("")
        self._dot.setObjectName("statusDot")
        self._dot.setFixedSize(12, 12)
        self._dot.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._dot.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._dot.setToolTip("打开运行健康中心")
        self._dot.clicked.connect(self._on_runtime_health_clicked)
        set_dot_status(self._dot, "ok")

        self._lbl_status = QtWidgets.QLabel("就绪")
        self._lbl_status.setObjectName("statusLabel")
        self._lbl_status.setMinimumWidth(48)
        self._lbl_status.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self._lbl_character = QtWidgets.QLabel("")
        self._lbl_character.setObjectName("characterLabel")
        self._lbl_character.setMinimumWidth(0)
        self._lbl_character.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._character_full_text = ""
        self._current_costume_name = self._resolve_initial_costume_name()
        self._refresh_character_status()

        self._lbl_work_session = QtWidgets.QLabel("")
        self._lbl_work_session.setObjectName("workSessionLabel")
        self._lbl_work_session.setMinimumWidth(WORK_SESSION_LABEL_MIN_WIDTH)
        self._lbl_work_session.setMaximumWidth(WORK_SESSION_LABEL_MAX_WIDTH)
        self._lbl_work_session.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._lbl_work_session.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._lbl_work_session.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self._work_session_full_text = WORK_SESSION_EMPTY_LABEL

        self._btn_runtime_health = QtWidgets.QPushButton("●")
        self._btn_runtime_health.setObjectName("runtimeHealthButton")
        self._btn_runtime_health.setFixedSize(22, 22)
        self._btn_runtime_health.setCursor(
            QtCore.Qt.CursorShape.PointingHandCursor
        )
        self._btn_runtime_health.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._btn_runtime_health.setToolTip("打开运行健康中心")
        self._btn_runtime_health.clicked.connect(self._on_runtime_health_clicked)

        window_ctl = QtWidgets.QFrame()
        window_ctl.setObjectName("windowCtlGroup")
        window_ctl_layout = QtWidgets.QHBoxLayout(window_ctl)
        window_ctl_layout.setContentsMargins(3, 2, 3, 2)
        window_ctl_layout.setSpacing(2)

        btn_shrink = QtWidgets.QPushButton("–")
        btn_shrink.setObjectName("windowCtl")
        btn_shrink.setToolTip("最小化到悬浮球")
        btn_shrink.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_shrink.setFixedSize(24, 22)
        btn_shrink.clicked.connect(self._switch_to_ball)

        btn_close = QtWidgets.QPushButton("×")
        btn_close.setObjectName("windowCtlClose")
        btn_close.setToolTip("藏到托盘")
        btn_close.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_close.setFixedSize(24, 22)
        btn_close.clicked.connect(self._hide_to_tray)

        window_ctl_layout.addWidget(btn_shrink)
        window_ctl_layout.addWidget(btn_close)

        top_bar.addWidget(self._dot)
        top_bar.addWidget(self._lbl_status)
        top_bar.addWidget(self._lbl_character, 1)
        top_bar.addWidget(self._lbl_work_session)
        top_bar.addWidget(self._btn_runtime_health)
        top_bar.addWidget(window_ctl)
        main_vbox.addWidget(title_bar)

        self._history = ChatTranscript()
        self._history.setVisible(False)
        self._history.regenerateRequested.connect(self._on_regenerate)
        self._history.newTopicRequested.connect(self._on_new_topic)
        self._sync_transcript_identity()
        main_vbox.addWidget(self._history, 1)

        input_box = QtWidgets.QHBoxLayout()
        self._input = ChatInput()
        self._input.setPlaceholderText("和我聊聊，或直接输入任务 / 指令…")
        self._input.sendRequested.connect(self._on_send_clicked)

        btn_send = QtWidgets.QPushButton("➤")
        btn_send.setObjectName("sendButton")
        btn_send.setFixedSize(30, 30)
        btn_send.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_send.clicked.connect(self._on_send_or_stop)
        self._btn_send = btn_send

        input_box.setContentsMargins(10, 2, 4, 2)
        input_box.setSpacing(6)
        input_box.addWidget(self._input, 1)
        input_box.addWidget(btn_send)

        input_container = QtWidgets.QFrame()
        input_container.setObjectName("inputShell")
        input_container.setLayout(input_box)
        main_vbox.addWidget(input_container)

        # 底栏：左侧快捷开关，右侧设置/工具
        tools_bar = QtWidgets.QFrame()
        tools_bar.setObjectName("toolsBar")
        tools_layout = QtWidgets.QHBoxLayout(tools_bar)
        tools_layout.setContentsMargins(2, 1, 2, 1)
        tools_layout.setSpacing(1)

        def mk_btn(text, cb, tip=""):
            b = QtWidgets.QPushButton(text)
            b.setObjectName("toolbarBtn")
            b.setToolTip(tip)
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(28, 26)
            b.clicked.connect(cb)
            return b

        self._btn_expand = mk_btn("⌄", self._toggle_full, "展开或收起对话记录")

        import config

        init_tts_state = getattr(config, "TTS_ENABLED", True)
        tts_icon = "🔊" if init_tts_state else "🔈"
        tts_tip = "语音播报 (当前: 开启)" if init_tts_state else "语音播报 (当前: 关闭)"
        self._btn_tts = mk_btn(tts_icon, self._toggle_tts, tts_tip)

        init_voice_state = getattr(config, "VOICE_SENSOR_ENABLED", False)
        voice_icon = "🎙️" if init_voice_state else "🔇"
        voice_tip = (
            "语音唤醒 (当前: 开启)" if init_voice_state else "语音唤醒 (当前: 关闭)"
        )
        self._btn_voice = mk_btn(voice_icon, self._toggle_voice, voice_tip)

        init_dnd_state = getattr(config, "DND_MODE", False)
        dnd_icon = "🔕" if init_dnd_state else "🔔"
        dnd_tip = "免打扰 (当前: 开启)" if init_dnd_state else "免打扰 (当前: 关闭)"
        self._btn_dnd = mk_btn(dnd_icon, self._toggle_dnd, dnd_tip)
        self._btn_costume = mk_btn("👗", self._on_quick_costume_clicked, "换装")
        tools_layout.setSpacing(4)

        self._btn_mode = QtWidgets.QPushButton("🌸 陪伴 ▾")
        if not init_tts_state and init_dnd_state:
            self._btn_mode.setText("🌙 安静 ▾")
            self._current_mode_name = "安静"
        self._btn_mode.setObjectName("modePillBtn")
        self._btn_mode.setToolTip("切换工作模式")
        self._btn_mode.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_mode.setFixedHeight(24)
        self._btn_mode.clicked.connect(self._on_mode_menu_clicked)
        self._btn_settings = mk_btn("⚙️", self._on_settings_clicked, "设置")

        self._btn_more = mk_btn("⋯", self._show_more_menu, "更多功能")

        # 左：状态快捷开关
        tools_layout.addWidget(self._btn_tts)
        tools_layout.addWidget(self._btn_voice)
        tools_layout.addWidget(self._btn_dnd)
        tools_layout.addWidget(self._btn_costume)
        tools_layout.addWidget(self._btn_mode)
        tools_layout.addStretch(1)
        tools_layout.addWidget(self._btn_settings)
        tools_layout.addWidget(self._btn_more)
        tools_layout.addWidget(self._btn_expand)

        main_vbox.addWidget(tools_bar)

        self._size_grip = QtWidgets.QSizeGrip(self._container)
        self._size_grip.setFixedSize(16, 16)
        self._size_grip.setStyleSheet(
            "background: transparent; width: 16px; height: 16px;"
        )
        self._stack.addWidget(self._panel_widget)
        # 默认启动为悬浮球；面板最小尺寸在切入 panel 时再施加。
        w.setMinimumSize(0, 0)
        self._enable_drag(w)
        self._refresh_companion_summary()
        self._install_panel_resize_guard(w)

        return w

    def _install_panel_resize_guard(self, window: QtWidgets.QWidget) -> None:
        original_resize = window.resizeEvent
        self._last_adapted_width = -1

        def _on_resize(event):
            if callable(original_resize):
                original_resize(event)
            if hasattr(self, "_size_grip") and hasattr(self, "_container"):
                cw = self._container.width()
                ch = self._container.height()
                gw = self._size_grip.width()
                gh = self._size_grip.height()
                self._size_grip.move(max(0, cw - gw - 2), max(0, ch - gh - 2))
            width = int(window.width())
            if abs(width - int(getattr(self, "_last_adapted_width", -1))) < 8:
                return
            self._last_adapted_width = width
            self._adapt_panel_for_width(width)

        window.resizeEvent = _on_resize  # type: ignore[method-assign]

    @staticmethod
    def _resolve_resource_path(p: Path) -> Optional[Path]:
        """Resolve a relative resource path against stable roots, never cwd.

        Order: PyInstaller data root (sys._MEIPASS, where bundled assets/
        live in onedir) → client repo root → install root (EXE dir) →
        external brain root. Returns the first existing absolute path, else
        the client-root join (so callers can report a clear missing-file path).
        """
        try:
            from paths import CLIENT_ROOT, resolve_install_root

            candidates: list[Path] = []
            # Frozen onedir: datas land under _MEIPASS (= _internal/).
            meipass = getattr(_sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass))
            candidates.append(CLIENT_ROOT)
            candidates.append(resolve_install_root())
            try:
                from paths import BRAIN_ROOT

                candidates.append(BRAIN_ROOT)
            except Exception:
                pass
            for base in candidates:
                if base is None:
                    continue
                full = (base / p)
                if full.exists():
                    return full
            return (CLIENT_ROOT / p)
        except Exception:
            return p

    def _adapt_panel_for_width(self, width: int) -> None:
        if getattr(self, "_is_ball_mode", False):
            return
        # 窄宽时优先保住输入区和窗口控制，次要信息做省略/隐藏。
        narrow = width < 420
        very_narrow = width < 380
        if hasattr(self, "_lbl_character"):
            self._lbl_character.setVisible(not very_narrow)
            if self._lbl_character.isVisible():
                elide_label_text(
                    self._lbl_character,
                    getattr(self, "_character_full_text", "") or self._lbl_character.text(),
                    max_width=max(48, self._lbl_character.width() - 4),
                )
        if hasattr(self, "_lbl_work_session"):
            self._lbl_work_session.setVisible(not narrow)
            if self._lbl_work_session.isVisible():
                full = getattr(self, "_work_session_full_text", "") or self._lbl_work_session.text()
                elide_label_text(
                    self._lbl_work_session,
                    full,
                    max_width=max(48, self._lbl_work_session.width() - 8),
                )

    def _refresh_companion_summary(self, status_text: Optional[str] = None):
        try:
            import config
        except Exception:
            config = object()

        tts_enabled = bool(getattr(config, "TTS_ENABLED", True))
        voice_enabled = bool(getattr(config, "VOICE_SENSOR_ENABLED", False))
        dnd_enabled = bool(getattr(config, "DND_MODE", False))
        mode_name = getattr(self, "_current_mode_name", "Companion")
        status_value = status_text or (
            self._lbl_status.text() if hasattr(self, "_lbl_status") else "就绪"
        )

        if hasattr(self, "_pill_mode"):
            self._pill_mode.setText(f"模式 · {mode_name}")
            self._pill_tts.setText(f"播报 · {'开' if tts_enabled else '关'}")
            self._pill_voice.setText(f"唤醒 · {'开' if voice_enabled else '关'}")
            self._pill_focus.setText(
                "免打扰 · 开" if dnd_enabled else f"状态 · {status_value}"
            )

        if hasattr(self, "_input"):
            if dnd_enabled:
                self._input.setPlaceholderText("安静模式中，也可以继续输入任务或聊天…")
            elif voice_enabled:
                self._input.setPlaceholderText("可以直接说话，也可以继续输入…")
            else:
                self._input.setPlaceholderText("和我聊聊，或直接输入任务 / 指令…")

    def set_screen_sensor(self, screen_sensor: Any) -> None:
        self.screen_sensor = screen_sensor
        self.refresh_work_session_status()

    def set_runtime_health(self, runtime_health: Any) -> None:
        self.runtime_health = runtime_health
        dialog = self._alive_runtime_health_dialog()
        if dialog is not None:
            dialog.health_center = runtime_health
        self.refresh_runtime_health_status()

    def refresh_runtime_health_status(self) -> None:
        if not hasattr(self, "_btn_runtime_health"):
            return
        rows = []
        try:
            if self.runtime_health is None:
                snapshot = {}
            else:
                snapshot = self.runtime_health.snapshot()
                if not isinstance(snapshot, dict):
                    raise TypeError("健康中心返回了无效数据")
            presentation = overall_presentation(snapshot)
            label = presentation["label"]
            color = presentation["color"]
            rows = component_rows(snapshot)
            tooltip = f"{label}，点击查看组件详情"
            # tooltip 里直接给出最严重的异常组件和原因，用户不点开也能看到。
            issue = next(
                (
                    row
                    for row in rows
                    if row["state"] in {"offline", "degraded"}
                ),
                None,
            )
            if issue:
                summary = issue["summary"] or issue["state_label"]
                tooltip = f"{label} · {issue['component_label']}：{summary}\n点击查看组件详情"
        except Exception as exc:
            label = "状态读取失败"
            color = "#EF4444"
            tooltip = f"运行健康状态读取失败：{exc}"

        self._btn_runtime_health.setText("●")
        self._btn_runtime_health.setToolTip(tooltip)
        self._btn_runtime_health.setStyleSheet(
            "QPushButton#runtimeHealthButton {"
            f"color: {color}; background: transparent; border: none;"
            "padding: 0px; font-size: 13px; font-weight: 700;"
            "}"
            "QPushButton#runtimeHealthButton:hover {"
            "background: rgba(148, 163, 184, 0.14);"
            "}"
        )
        self._notify_health_transitions(rows)

    def _notify_health_transitions(self, rows: list) -> None:
        """对比上次快照，只在组件进入/脱离异常态时各提示一次。"""
        fail_states = {"offline", "degraded"}
        current = {row["component"]: row for row in rows if row["state"] != "disabled"}
        last = self._last_health_rows
        self._last_health_rows = current
        if last is None:
            return
        if last == current:
            return
        now = time.time()
        for key, row in current.items():
            was_failing = last.get(key, {}).get("state") in fail_states
            if row["state"] in fail_states and not was_failing:
                self._announce_health(row, recovered=False, now=now)
            elif row["state"] == "healthy" and was_failing:
                self._announce_health(row, recovered=True, now=now)
        # 上次异常、这次消失的组件视为已恢复（比如被 clear 掉）
        for key, prev in last.items():
            if prev.get("state") in fail_states and key not in current:
                self._announce_health(prev, recovered=True, now=now)

    def _announce_health(self, row: dict, *, recovered: bool, now: float | None = None) -> None:
        component = str(row.get("component") or "")
        component_label = row.get("component_label") or component or "组件"
        now_ts = time.time() if now is None else float(now)
        last_at = float((self._last_health_announce_at or {}).get(component) or 0.0)
        # rust 活动抖动很常见：同一组件 15 分钟内最多弹一次；闪断恢复不弹。
        min_gap = 15 * 60 if component == "rust_activity" else 3 * 60
        if recovered and component == "rust_activity":
            return
        if last_at and now_ts - last_at < min_gap:
            return
        self._last_health_announce_at[component] = now_ts
        if recovered:
            text = f"{component_label}已恢复"
            icon = QtWidgets.QSystemTrayIcon.MessageIcon.Information
            prefix = "✅"
        else:
            summary = row.get("summary") or "状态异常"
            text = f"{component_label}异常：{summary}"
            icon = QtWidgets.QSystemTrayIcon.MessageIcon.Warning
            prefix = "⚠️"
        try:
            self._tray.showMessage("Live2D-Suzu 运行提醒", text, icon, 6000)
        except Exception:
            pass
        try:
            self.append("system", f"{prefix} {text}")
        except Exception:
            pass

    def _alive_runtime_health_dialog(self):
        dialog = getattr(self, "_runtime_health_dialog", None)
        if dialog is None:
            return None
        try:
            dialog.objectName()
        except RuntimeError:
            self._runtime_health_dialog = None
            return None
        return dialog

    def _on_runtime_health_dialog_destroyed(self, *_args) -> None:
        self._runtime_health_dialog = None

    def _on_runtime_health_clicked(self) -> None:
        try:
            if self.runtime_health is None:
                try:
                    from services.runtime_health import get_runtime_health

                    self.runtime_health = get_runtime_health()
                except Exception:
                    pass
            dialog = self._alive_runtime_health_dialog()
            if dialog is None:
                from modules.gui.dialogs.runtime_health import RuntimeHealthDialog

                dialog = RuntimeHealthDialog(self.runtime_health, parent=None)
                dialog.destroyed.connect(self._on_runtime_health_dialog_destroyed)
                self._runtime_health_dialog = dialog
            else:
                dialog.health_center = self.runtime_health
                dialog.refresh_status()
            # 托盘菜单和「更多」菜单会在这个槽返回后关掉，并把槽里刚 show
            # 出来的窗口一起藏掉。等菜单结束再显示，第二次点击才不会还是看不见。
            QtCore.QTimer.singleShot(
                0, lambda d=dialog: self._present_runtime_health_dialog(d)
            )
            QtCore.QTimer.singleShot(
                120, lambda d=dialog: self._present_runtime_health_dialog(d)
            )
        except Exception as exc:
            print(f"[RuntimeHealth] open failed: {exc}")
            self.append("system", f"运行健康中心没能打开：{exc}")

    def _present_runtime_health_dialog(self, dialog) -> None:
        if dialog is None:
            return
        try:
            dialog.objectName()
        except RuntimeError:
            return
        try:
            if not dialog.isVisible():
                screen = (
                    QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
                    or QtWidgets.QApplication.primaryScreen()
                )
                if screen is not None:
                    area = screen.availableGeometry()
                    w = max(dialog.width(), 680)
                    h = max(dialog.height(), 420)
                    dialog.move(
                        area.center().x() - w // 2,
                        area.center().y() - h // 2,
                    )
            if dialog.isMinimized():
                dialog.setWindowState(
                    dialog.windowState() & ~QtCore.Qt.WindowState.WindowMinimized
                )
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            self._show_as_normal_window(dialog)
        except RuntimeError:
            return
        except Exception as exc:
            print(f"[RuntimeHealth] open failed: {exc}")
            self.append("system", f"运行健康中心没能打开：{exc}")

    @staticmethod
    def _show_as_normal_window(widget) -> None:
        """Show a regular window. Do not make it topmost."""
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            hwnd = int(widget.winId())
            user32.ShowWindow(hwnd, 1)
            user32.SetWindowPos(
                hwnd,
                ctypes.c_void_p(-2),
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0040,
            )
        except Exception:
            pass

    def request_work_session_status_refresh(self) -> None:
        self._bridge.sig_refresh_work_session.emit()

    def refresh_work_session_status(self) -> None:
        label = WORK_SESSION_EMPTY_LABEL
        tooltip = ""
        try:
            sensor = getattr(self, "screen_sensor", None)
            if sensor and hasattr(sensor, "get_current_work_session"):
                session = sensor.get_current_work_session()
                label, tooltip = self._work_session_presentation(session)
            else:
                # 遥控窗远程模式：数据在大脑内存里。用最近一次 WS 推送渲染，
                # 并顺手向大脑再拉一次（apply_remote_work_session 回来更新）。
                callback = getattr(self, "on_request_work_session_callback", None)
                if callable(callback):
                    try:
                        callback()
                    except Exception:
                        pass
                label, tooltip = self._work_session_presentation(
                    getattr(self, "_remote_work_session", None)
                )
                if not getattr(self, "_remote_work_session", None):
                    tooltip = "还没收到大脑的工作时长数据（已发起拉取）"
        except Exception as exc:
            label = WORK_SESSION_EMPTY_LABEL
            tooltip = f"工作时长数据读取失败: {exc}"

        self._render_work_session(label, tooltip)

    @staticmethod
    def _work_session_presentation(session):
        label = format_work_session_label(session) or WORK_SESSION_EMPTY_LABEL
        if label == WORK_SESSION_RESTING_LABEL:
            return label, "当前在休息，工作计时已重置"
        if (
            label == WORK_SESSION_EMPTY_LABEL
            and isinstance(session, dict)
            and session.get("source")
        ):
            return label, "工作满 1 分钟后显示工作时长"
        return label, label

    def apply_remote_work_session(self, session) -> None:
        self._remote_work_session = session if isinstance(session, dict) else None
        label, tooltip = self._work_session_presentation(self._remote_work_session)
        self._render_work_session(label, tooltip)

    def _render_work_session(self, label: str, tooltip: str) -> None:
        if not hasattr(self, "_lbl_work_session"):
            return
        self._work_session_full_text = label
        self._lbl_work_session.setToolTip(tooltip or label)
        win = getattr(self, "_win", None)
        if win is not None and not getattr(self, "_is_ball_mode", False):
            self._adapt_panel_for_width(win.width())
        else:
            elide_label_text(
                self._lbl_work_session,
                label,
                max_width=max(48, self._lbl_work_session.width() - 8),
            )
            self._lbl_work_session.setVisible(True)

    def _show_more_menu(self):
        menu = QtWidgets.QMenu(self._btn_more)
        menu.addAction("记忆").triggered.connect(self._on_memory_clicked)
        menu.addAction("日记").triggered.connect(self._on_diary_clicked)
        menu.addAction("新话题").triggered.connect(self._on_new_topic)
        menu.addAction("能力").triggered.connect(self._on_plugin_clicked)
        menu.addSeparator()
        menu.addAction("语音").triggered.connect(self._on_asr_clicked)
        menu.addAction("QQ").triggered.connect(self._on_qq_clicked)
        menu.addAction("运行健康").triggered.connect(self._on_runtime_health_clicked)
        if callable(getattr(self, "on_start_backend_callback", None)):
            menu.addAction("启动本机后端").triggered.connect(self._on_start_backend)
        if is_developer_mode():
            menu.addSeparator()
            dev_menu = menu.addMenu("开发者")
            dev_menu.addAction("控制台").triggered.connect(self._on_console_clicked)
            dev_menu.addAction("模型监控").triggered.connect(self._on_monitor_clicked)
            dev_menu.addAction("运行健康").triggered.connect(
                self._on_runtime_health_clicked
            )
            dev_menu.addAction("状态屏").triggered.connect(self._on_status_screen_clicked)
        if self.on_restart_callback:
            menu.addSeparator()
            menu.addAction("重启程序").triggered.connect(self._handle_restart)
        popup_pos = self._btn_more.mapToGlobal(
            QtCore.QPoint(0, self._btn_more.height())
        )
        menu.exec(popup_pos)

    def _on_toggle_live2d(self):
        callback = getattr(self, "on_toggle_live2d_callback", None)
        if callable(callback):
            callback()

    def _on_start_backend(self):
        callback = getattr(self, "on_start_backend_callback", None)
        if not callable(callback):
            return
        self.append("system", "正在启动本机后端…")
        try:
            callback()
        except Exception as exc:
            self.append("system", f"启动本机后端失败：{exc}")

    def _toggle_tts(self):
        import config

        if not hasattr(config, "TTS_ENABLED"):
            config.TTS_ENABLED = True

        config.TTS_ENABLED = not bool(config.TTS_ENABLED)
        enabled = bool(config.TTS_ENABLED)

        if enabled:
            self._btn_tts.setText("🔊")
            self._btn_tts.setToolTip("语音播报 (当前: 开启)")
            self.append("system", "🔊 已开启语音播报。")
        else:
            self._btn_tts.setText("🔈")
            self._btn_tts.setToolTip("语音播报 (当前: 关闭)")
            self.append("system", "🔈 已关闭语音播报。")

        # 通知后端切换 Presenter 的 TTS 状态
        self._refresh_companion_summary()

        if self.on_tts_toggle_callback:
            self.on_tts_toggle_callback(enabled)

    # 🟢 新增：语音监听切换逻辑 (将此函数粘贴到 _toggle_dnd 函数的上方或下方)
    def _toggle_voice(self):
        import config

        if not hasattr(config, "VOICE_SENSOR_ENABLED"):
            config.VOICE_SENSOR_ENABLED = False

        config.VOICE_SENSOR_ENABLED = not config.VOICE_SENSOR_ENABLED

        if config.VOICE_SENSOR_ENABLED:
            self._btn_voice.setText("🎙️")
            self._btn_voice.setToolTip("语音唤醒 (当前: 开启 - 监听中)")
            self.append("system", "🎙️ 已开启语音监听：现在你可以通过声音唤醒我了。")
        else:
            self._btn_voice.setText("🔇")
            self._btn_voice.setToolTip("语音唤醒 (当前: 关闭 - 停止监听)")
            self.append("system", "🔇 已关闭语音监听：麦克风已释放。")

        # 通知后端执行具体启停操作
        self._refresh_companion_summary()

        if self.on_voice_toggle_callback:
            self.on_voice_toggle_callback(config.VOICE_SENSOR_ENABLED)

    # --- 逻辑方法 ---
    def run(self):
        return self._app.exec()

    # --- 全局热键 + Push-to-talk ---

    def _stop_global_listeners(self):
        listener = self._hotkey_listener
        self._hotkey_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def _start_global_listeners(self):
        """启动 pynput 监听线程：处理全局热键（Ctrl+Alt+Space 打开/收回面板）和右 Ctrl push-to-talk。"""
        if self._hotkey_listener is not None:
            return
        try:
            from pynput import keyboard as pk
        except Exception:
            print("[Hotkey] 未安装 pynput，全局热键和 PTT 不可用。")
            return

        import config as _cfg

        combo = getattr(_cfg, "HOTKEY_TOGGLE_GUI", "") or "<ctrl>+<alt>+<space>"
        ptt_enabled = bool(getattr(_cfg, "VOICE_PTT_ENABLED", True))
        ptt_key_name = str(getattr(_cfg, "VOICE_PTT_KEY", "ctrl_r")).strip().lower()
        combo_spec = self._normalize_hotkey_combo(combo)

        try:
            combo_keys = pk.HotKey.parse(combo_spec)
        except Exception as exc:
            print(f"[Hotkey] 组合键解析失败 ({combo_spec}): {exc}")
            combo_keys = []

        state = {"listener": None}

        def _emit_toggle():
            print(f"[Hotkey] 触发面板切换: {combo_spec}")
            self._bridge.sig_toggle_gui.emit()

        hotkey = pk.HotKey(combo_keys, _emit_toggle) if combo_keys else None

        def _canonical(key):
            listener = state["listener"]
            if listener is None:
                return key
            try:
                return listener.canonical(key)
            except Exception:
                return key

        def _on_press(key):
            try:
                if hotkey is not None:
                    hotkey.press(_canonical(key))
                name = self._key_name(key)
                if ptt_enabled and name == ptt_key_name:
                    combo_active = bool(getattr(hotkey, "_state", set())) if hotkey else False
                    if not combo_active and not self._ptt_active:
                        self._ptt_active = True
                        self._bridge.sig_ptt_start.emit()
            except Exception as exc:
                print(f"[Hotkey] press 处理失败: {exc}")

        def _on_release(key):
            try:
                if hotkey is not None:
                    hotkey.release(_canonical(key))
                name = self._key_name(key)
                if ptt_enabled and name == ptt_key_name and self._ptt_active:
                    self._ptt_active = False
                    self._bridge.sig_ptt_stop.emit()
            except Exception as exc:
                print(f"[Hotkey] release 处理失败: {exc}")

        def _run():
            try:
                listener = pk.Listener(on_press=_on_press, on_release=_on_release)
                state["listener"] = listener
                self._hotkey_listener = listener
                listener.start()
                listener.join()
            except Exception as e:
                print(f"[Hotkey] 监听线程异常: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        print(
            f"[Hotkey] 全局热键已启用: {combo_spec}"
            + (f" | PTT: 按住 {ptt_key_name} 说话" if ptt_enabled else "")
        )

    @staticmethod
    def _normalize_hotkey_combo(combo: str) -> str:
        """把 config 里的 <ctrl>+<alt>+space 转成 pynput HotKey 能解析的格式。"""
        parts = []
        for raw in str(combo or "").split("+"):
            token = raw.strip().lower()
            if not token:
                continue
            if token.startswith("<") and token.endswith(">"):
                parts.append(token)
            else:
                parts.append(f"<{token}>")
        return "+".join(parts) or "<ctrl>+<alt>+<space>"

    @staticmethod
    def _key_name(key) -> str:
        """把 pynput key 转成可比较的小写字符串名（保留左右修饰键原始名）。"""
        name = ""
        try:
            name = getattr(key, "name", "") or ""
        except Exception:
            name = ""
        if not name:
            try:
                name = str(key.char).lower()
            except Exception:
                name = str(key).lower().strip("'")
        return name.lower()

    @QtCore.Slot()
    def _on_ptt_start(self):
        if callable(self.on_ptt_start_callback):
            try:
                self.on_ptt_start_callback()
            except Exception as e:
                print(f"[PTT] start 回调失败: {e}")

    @QtCore.Slot()
    def _on_ptt_stop(self):
        if callable(self.on_ptt_stop_callback):
            try:
                self.on_ptt_stop_callback()
            except Exception as e:
                print(f"[PTT] stop 回调失败: {e}")

    def _center_window(self):
        try:
            geo = self._app.primaryScreen().geometry()
            win = self._win.geometry()
            self._win.move(
                (geo.width() - win.width()) // 2, (geo.height() - win.height()) // 2
            )
        except Exception:
            pass
        self._remember_ball_geom()

    def _screen_avail(self):
        screen = (
            QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
            or self._app.primaryScreen()
        )
        if not screen:
            return None
        geo = screen.availableGeometry()
        return geo.left(), geo.top(), geo.right(), geo.bottom()

    def _remember_ball_geom(self):
        if not getattr(self, "_is_ball_mode", False):
            return
        g = self._win.geometry()
        self._ball_geom = (g.x(), g.y(), g.width(), g.height())

    def _on_ball_drag_finished(self):
        self._remember_ball_geom()

    def _ensure_on_screen(self):
        avail = self._screen_avail()
        if not avail:
            return
        g = self._win.geometry()
        x, y, w, h = clamp_rect((g.x(), g.y(), g.width(), g.height()), avail)
        if (x, y) != (g.x(), g.y()):
            self._win.move(x, y)

    def _apply_panel_size_constraints(self, *, expanded: Optional[bool] = None) -> None:
        """Panel mode keeps a safe floor; ball mode must not inherit panel mins."""
        if expanded is None:
            expanded = bool(getattr(self._win, "_is_full_mode", False))
        min_h = PANEL_EXPANDED_MIN_HEIGHT if expanded else PANEL_COMPACT_HEIGHT
        # 解除 setFixedSize 留下的 max 限制，允许用户继续拖大。
        self._win.setMaximumSize(16777215, 16777215)
        self._win.setMinimumSize(PANEL_MIN_WIDTH, min_h)

    def _switch_to_ball(self):
        curr = self._win.geometry()
        was_panel = not self._is_ball_mode
        if was_panel:
            self._last_panel_size = curr.size()
        self._is_ball_mode = True
        self._stack.setCurrentIndex(0)
        s = BALL_CONFIG.get("size", 60)
        bw, bh = s + 10, s + 10
        self._win.setFixedSize(bw, bh)
        avail = self._screen_avail()
        stored = getattr(self, "_ball_geom", None)
        if stored:
            x, y, w, h = stored
            if avail:
                x, y, w, h = clamp_rect((x, y, bw, bh), avail)
            self._win.move(x, y)
        elif avail:
            x, y = clamp_rect((curr.x(), curr.y(), bw, bh), avail)[:2]
            self._win.move(x, y)
        self._remember_ball_geom()

    def _switch_to_panel(self):
        if not self._is_ball_mode:
            return
        self._remember_ball_geom()
        curr = self._win.geometry()
        self._is_ball_mode = False
        self._stack.setCurrentIndex(1)
        self._collapse_history_view()
        self._apply_panel_size_constraints(expanded=False)
        target = QtCore.QSize(
            max(PANEL_MIN_WIDTH, int(self._last_panel_size.width())),
            PANEL_COMPACT_HEIGHT,
        )
        self._win.resize(target)
        avail = self._screen_avail()
        if avail:
            x, y = panel_pos_from_ball(
                (curr.x(), curr.y(), curr.width(), curr.height()),
                (target.width(), target.height()),
                avail,
            )
            self._win.move(x, y)
        self._adapt_panel_for_width(self._win.width())

    def _collapse_history_view(self) -> None:
        if hasattr(self, "_win"):
            self._win._is_full_mode = False
        if hasattr(self, "_history"):
            self._history.setVisible(False)
        if hasattr(self, "_btn_expand"):
            self._btn_expand.setText("⌄")
            self._btn_expand.setToolTip("展开对话记录")

    def ensure_history_visible(self):
        if getattr(self, "_is_ball_mode", False):
            self._switch_to_panel()
        if not getattr(self._win, "_is_full_mode", False):
            self._toggle_full()

    def _hide_to_tray(self):
        self.hide()
        self._maybe_show_tray_hint()

    def _maybe_show_tray_hint(self):
        if getattr(self, "_frontend_suppressed", False):
            return
        settings = load_runtime_settings()
        if settings.get(TRAY_HIDE_HINT_KEY):
            return
        try:
            self._tray.showMessage(
                "Live2D-Suzu",
                "遥控窗已藏到托盘。点右下角图标可以再打开；要退出请用托盘菜单里的「退出」。",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                8000,
            )
        except Exception:
            pass
        update_runtime_settings({TRAY_HIDE_HINT_KEY: True})

    def _maybe_hint_setup(self):
        if getattr(self, "_setup_hint_shown", False):
            return
        try:
            status = chat_setup_status(runtime_health=self.runtime_health)
        except Exception:
            return
        if status.get("ready"):
            return
        self._setup_hint_shown = True
        self.append(
            "system",
            "还不能聊天：打开设置 → 开始使用，先选角色并填一个聊天模型。",
        )

    def _toggle_full(self):
        is_full = getattr(self._win, "_is_full_mode", False)
        width = max(PANEL_MIN_WIDTH, self._win.width())
        if is_full:
            self._collapse_history_view()
            self._apply_panel_size_constraints(expanded=False)
            self._win.resize(width, PANEL_COMPACT_HEIGHT)
        else:
            self._win._is_full_mode = True
            self._history.setVisible(True)
            self._btn_expand.setText("⌃")
            self._btn_expand.setToolTip("收起对话记录")
            self._apply_panel_size_constraints(expanded=True)
            expanded_h = max(
                PANEL_EXPANDED_MIN_HEIGHT, self._last_panel_size.height(), 360
            )
            self._win.resize(width, expanded_h)
            self._scroll_history_to_bottom()
        self._last_panel_size = self._win.size()
        self._adapt_panel_for_width(width)

    def _scroll_history_to_bottom(self) -> None:
        history = getattr(self, "_history", None)
        if history is None or not history.isVisible():
            return
        if hasattr(history, "scroll_to_bottom"):
            history.scroll_to_bottom()
            QtCore.QTimer.singleShot(80, history.scroll_to_bottom)
            return
        bar = history.verticalScrollBar()
        QtCore.QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    # 🟢 新增：免打扰切换逻辑
    def _toggle_dnd(self):
        import config

        # 如果 config 里没有定义 DND_MODE，赋个初值
        if not hasattr(config, "DND_MODE"):
            config.DND_MODE = False

        enabled = not bool(config.DND_MODE)
        app = getattr(self, "app", None)
        setter = getattr(app, "set_dnd_mode", None)
        if callable(setter):
            setter(enabled)
        else:
            config.DND_MODE = enabled
            try:
                update_runtime_settings({"dnd_mode": enabled})
            except Exception:
                pass

        # 更新 UI 和提示
        if config.DND_MODE:
            self._btn_dnd.setText("🔕")
            self._btn_dnd.setToolTip("免打扰 (当前: 开启 - 静默观察不说话)")
            self.append(
                "system", "🔕 已开启手动免打扰：助手将只默默记录，不再主动发声打断。"
            )
        else:
            self._btn_dnd.setText("🔔")
            self._btn_dnd.setToolTip("免打扰 (当前: 关闭 - 允许主动吐槽)")
            self.append("system", "🔔 已关闭手动免打扰：助手恢复吐槽与主动关怀。")

        self._refresh_companion_summary()

    def _on_settings_clicked(self):
        try:
            if self._settings_dialog:
                self._settings_dialog.close()
                self._settings_dialog.deleteLater()
                self._settings_dialog = None
            from modules.gui.dialogs.settings import SettingsDialog

            dialog = SettingsDialog(parent=None, main_app=self)
            self._settings_dialog = dialog
            self._refresh_character_status()
            screen = (
                QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
                or QtWidgets.QApplication.primaryScreen()
            )
            if screen is not None:
                area = screen.availableGeometry()
                dialog.resize(
                    min(1040, int(area.width() * 0.86)),
                    min(680, int(area.height() * 0.82)),
                )
                frame = dialog.frameGeometry()
                dialog.move(
                    area.center().x() - frame.width() // 2,
                    area.center().y() - frame.height() // 2,
                )
            dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except Exception as exc:
            import traceback

            print(f"[Settings] open failed: {exc}")
            traceback.print_exc()
            self.append("system", f"设置窗口打开失败：{exc}")

    def _on_console_clicked(self):
        try:
            if not self._console_dialog:
                from modules.gui.dialogs.console_log import ConsoleLogDialog

                self._console_dialog = ConsoleLogDialog(parent=None)
            self._console_dialog.showNormal()
            self._console_dialog.raise_()
            self._console_dialog.activateWindow()
        except Exception as exc:
            import traceback

            print(f"[Console] open failed: {exc}")
            traceback.print_exc()
            self.append("system", f"控制台打开失败：{exc}")

    def _open_settings_page(self, page_id: str):
        self._on_settings_clicked()
        if self._settings_dialog and hasattr(self._settings_dialog, "open_page"):
            self._settings_dialog.open_page(page_id)

    def _on_plugin_clicked(self):
        self._open_settings_page("plugins")

    def _on_memory_clicked(self):
        try:
            self._open_settings_page("memory")
        except Exception as e:
            self.append("system", f"❌ 记忆编辑器加载失败: {e}")

    def _on_diary_clicked(self):
        try:
            self._open_settings_page("diary")
        except Exception as e:
            self.append("system", f"❌ 日记打开失败: {e}")

    def _on_asr_clicked(self):
        self._open_settings_page("asr")

    def _on_qq_clicked(self):
        self._open_settings_page("qq")

    def _on_knowledge_clicked(self):
        try:
            self._open_settings_page("knowledge")
        except Exception as e:
            self.append("system", f"❌ 知识库管理器加载失败: {e}")

    def _on_status_screen_clicked(self):
        try:
            self._open_settings_page("status_screen")
        except Exception as e:
            self.append("system", f"❌ 状态屏管理器加载失败: {e}")

    def _on_expression_library_clicked(self):
        try:
            self._open_settings_page("expression")
        except Exception as e:
            self.append("system", f"❌ 表达学习库加载失败: {e}")

    def _on_meme_clicked(self):
        try:
            self._open_settings_page("meme")
        except Exception as e:
            self.append("system", f"❌ 表情包管理器加载失败: {e}")

    def _on_costume_triggered(self, name, cfg=None):
        safe_cfg = cfg if isinstance(cfg, dict) else {}
        path = safe_cfg.get("path", "")

        try:
            active_id = character_manager.data.get("active_id")
            if active_id:
                active_char = character_manager.get_character(active_id) or {}
                costumes = active_char.get("costumes") or {}
                costume_entry = costumes.get(name) or {}

                manager_path = (
                    costume_entry.get("path", "")
                    if isinstance(costume_entry, dict)
                    else ""
                )
                if manager_path:
                    path = manager_path

                runtime_cfg = character_manager.get_costume_runtime_config(
                    active_id, name
                )
                if isinstance(runtime_cfg, dict) and runtime_cfg:
                    safe_cfg = runtime_cfg

                character_manager.set_current_costume_name(active_id, name)
        except Exception:
            pass

        if not path:
            self.append("system", f"❌ 服装 [{name}] 缺少模型路径")
            return

        self._apply_costume_change(path, safe_cfg, costume_name=name)
        self.append("system", f"👗 正在切换服装: {name}")

    def _resolve_costume_name(self, model_path: str) -> str:
        if not model_path:
            return self._current_costume_name

        try:
            active_char = character_manager.get_active_character() or {}
            costumes = active_char.get("costumes") or {}
            target_norm = str(model_path).replace("\\", "/")
            for name, cfg in costumes.items():
                cpath = (cfg or {}).get("path", "")
                if str(cpath).replace("\\", "/") == target_norm:
                    return name
        except Exception:
            pass
        return self._current_costume_name

    def _resolve_initial_costume_name(self) -> str:
        try:
            active_id = character_manager.data.get("active_id")
            current_name = character_manager.get_current_costume_name(active_id)
            return current_name or "未设定"
        except Exception:
            return "未设定"

    def _refresh_character_status(self):
        try:
            active_id = character_manager.data.get("active_id")
            active_char = character_manager.get_active_character() or {}
            char_name = active_char.get("name", "未激活角色")
            managed_costume = character_manager.get_current_costume_name(active_id)
            self._current_costume_name = managed_costume or "未设定"
        except Exception:
            char_name = "未激活角色"
            self._current_costume_name = "未设定"

        costume_name = self._current_costume_name or "未设定"
        self._character_full_text = f"🌸 {char_name} · {costume_name}"
        self._sync_transcript_identity()
        win = getattr(self, "_win", None)
        if win is not None and not getattr(self, "_is_ball_mode", False):
            self._adapt_panel_for_width(win.width())
        else:
            self._lbl_character.setText(self._character_full_text)
        self._refresh_companion_summary()
        self._publish_display_state()

    def _apply_costume_change(
        self,
        path: str,
        cfg: Optional[dict] = None,
        *,
        costume_name: Optional[str] = None,
    ):
        if not self._external_costume_callback:
            return

        safe_cfg = cfg if isinstance(cfg, dict) else {}
        self._external_costume_callback(path, safe_cfg)
        self._current_costume_name = costume_name or self._resolve_costume_name(path)
        self._refresh_character_status()

    def preview_motion(self, motion_name: str, motion_type: int = 0):
        if not motion_name:
            return False
        if not self.on_preview_motion_callback:
            self.append("system", "预览动作失败：Live2D 预览通道未连接。")
            return False
        self.on_preview_motion_callback(motion_name, int(motion_type))
        return True

    def preview_expression(self, exp_id):
        if not self.on_preview_expression_callback:
            self.append("system", "预览表情失败：Live2D 预览通道未连接。")
            return False
        self.on_preview_expression_callback(exp_id)
        return True

    def _build_quick_costume_menu(self, parent_widget) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(parent_widget)
        try:
            self._refresh_character_status()
            active_char = character_manager.get_active_character()
            active_id = character_manager.data.get("active_id")
            if not active_char:
                action = menu.addAction("(暂无激活角色)")
                action.setEnabled(False)
                return menu

            active_name = active_char.get("name") or (active_id or "未知角色")
            title_action = menu.addAction(f"当前角色: {active_name}")
            title_action.setEnabled(False)
            menu.addSeparator()

            costumes = active_char.get("costumes") or {}
            if not costumes:
                no_action = menu.addAction("(该角色暂无服装)")
                no_action.setEnabled(False)
            else:
                current_name = character_manager.get_current_costume_name(active_id)
                for costume_name in costumes.keys():
                    prefix = "✅" if costume_name == current_name else "👕"
                    action = menu.addAction(f"{prefix} {costume_name}")
                    action.triggered.connect(
                        lambda checked=False,
                        n=costume_name: self._on_costume_triggered(n)
                    )

            menu.addSeparator()
            settings_action = menu.addAction("打开角色设置")
            settings_action.triggered.connect(
                lambda: self._open_settings_page("character")
            )
        except Exception as e:
            err_action = menu.addAction(f"加载换装菜单失败: {e}")
            err_action.setEnabled(False)
        return menu

    def _show_costume_menu_from_ball(self, pos):
        menu = self._build_quick_costume_menu(self._ball_btn)
        menu.exec(self._ball_btn.mapToGlobal(pos))

    def _on_quick_costume_clicked(self):
        anchor = (
            getattr(self, "_btn_costume", None)
            or getattr(self, "_btn_more", None)
            or self._win
        )
        menu = self._build_quick_costume_menu(anchor)
        popup_pos = (
            anchor.mapToGlobal(QtCore.QPoint(0, anchor.height()))
            if hasattr(anchor, "mapToGlobal")
            else self._win.mapToGlobal(QtCore.QPoint(0, 0))
        )
        menu.exec(popup_pos)

    def _on_send_or_stop(self):
        if self._chat_busy:
            self._on_stop_clicked()
        self._on_send_clicked()

    def _on_stop_clicked(self):
        if callable(self.on_cancel_callback):
            try:
                self.on_cancel_callback()
            except Exception as exc:
                self.append("system", f"没能停止：{exc}")
        self._set_chat_busy(False)
        self.set_status("就绪")

    def _on_send_clicked(self):
        if self._chat_busy:
            self._on_stop_clicked()
        if hasattr(self._input, "take_text"):
            text = self._input.take_text()
        else:
            text = str(self._input.toPlainText() if hasattr(self._input, "toPlainText") else "").strip()
            self._input.clear()
        if not text:
            return
        self.append("user", text)
        self.set_status("思考中…")
        self._set_chat_busy(True)
        self._dispatch_send(text)

    def _on_regenerate(self):
        text = ""
        if hasattr(self._history, "last_user_text"):
            text = self._history.last_user_text()
        if not text:
            return
        if self._chat_busy:
            self._on_stop_clicked()
        self.append("system", "重新生成上一条…")
        self._set_chat_busy(True)
        self._dispatch_send(text)

    def _on_new_topic(self):
        if hasattr(self._history, "clear_messages"):
            self._history.clear_messages()
            self._history.mark_new_topic()
        if callable(self.on_new_topic_callback):
            try:
                self.on_new_topic_callback()
            except Exception as exc:
                self.append("system", f"新话题没能清掉上下文：{exc}")
        else:
            self.append("system", "已开始新话题。")

    def _set_chat_busy(self, busy: bool):
        self._chat_busy = bool(busy)
        if not hasattr(self, "_btn_send"):
            return
        if self._chat_busy:
            self._btn_send.setText("■")
            self._btn_send.setToolTip("停止生成")
        else:
            self._btn_send.setText("➤")
            self._btn_send.setToolTip("发送")

    def _sync_transcript_identity(self):
        if not hasattr(self, "_history"):
            return
        try:
            active_id = character_manager.data.get("active_id")
            active = character_manager.get_active_character() or {}
            name = str(active.get("name") or "助手")

            # 优先使用角色设置中的徽章（包含缩放和偏移）：
            # 1. 当前服装独立徽章 (costume assistant_badge)
            # 2. 角色默认徽章 (character assistant_badge)
            # 3. 角色自定义头像 (active.avatar)
            # 4. 默认静态头像 assets/avatar.png
            avatar_data = None
            current_costume = character_manager.get_current_costume_name(active_id)
            costumes = active.get("costumes") or {}
            costume_cfg = (
                costumes.get(current_costume)
                if isinstance(costumes.get(current_costume), dict)
                else {}
            )
            costume_badge = costume_cfg.get("assistant_badge")
            if isinstance(costume_badge, dict) and costume_badge.get("path"):
                bp = Path(costume_badge["path"])
                if not bp.is_absolute():
                    bp = Path.cwd() / bp
                if bp.exists():
                    avatar_data = costume_badge

            if not avatar_data:
                char_badge = active.get("assistant_badge")
                if isinstance(char_badge, dict) and char_badge.get("path"):
                    bp = Path(char_badge["path"])
                    if not bp.is_absolute():
                        bp = Path.cwd() / bp
                    if bp.exists():
                        avatar_data = char_badge

            if not avatar_data:
                char_avatar = str(active.get("avatar") or "").strip()
                if char_avatar:
                    ap = Path(char_avatar)
                    if not ap.is_absolute():
                        ap = Path.cwd() / ap
                    if ap.exists():
                        avatar_data = str(ap)

            if not avatar_data:
                avatar_data = "assets/avatar.png"

            if hasattr(self._history, "set_assistant_identity"):
                self._history.set_assistant_identity(name, avatar_data)
        except Exception:
            pass

        try:
            settings = load_runtime_settings() or {}
            user_name = str(settings.get("napcat_owner_label") or "主人").strip() or "主人"
            user_avatar = ""
            for candidate in (
                "assets/user_avatar.png",
                "assets/user.png",
                "assets/master.png",
                "data/user_avatar.png",
            ):
                p = Path(candidate)
                if p.exists():
                    user_avatar = str(p)
                    break
            if hasattr(self._history, "set_user_identity"):
                self._history.set_user_identity(user_name, user_avatar)
        except Exception:
            pass

    def _dispatch_send(self, text: str, ctx: Optional[dict] = None):
        if not self.on_send_callback:
            return
        self.set_status("思考中…")
        self._set_chat_busy(True)
        try:
            self.on_send_callback(text, ctx)
        except TypeError:
            self.on_send_callback(text)

    def _send_react_command(self, command: str, preview: str):
        if not command:
            return
        if preview:
            self.append("system", preview)
        self._dispatch_send(command)

    def _on_monitor_clicked(self):
        self._send_react_command(
            "[CMD: llm_monitor | summary ||| 50]",
            "[MONITOR] 查看最近模型调用摘要",
        )

    def _sync_mode_status_buttons(self):
        try:
            import config

            tts_on = bool(getattr(config, "TTS_ENABLED", True))
            dnd_on = bool(getattr(config, "DND_MODE", False))
        except Exception:
            return
        if hasattr(self, "_btn_tts"):
            self._btn_tts.setText("🔊" if tts_on else "🔈")
            self._btn_tts.setToolTip(
                "语音播报 (当前: 开启)" if tts_on else "语音播报 (当前: 关闭)"
            )
        if hasattr(self, "_btn_dnd"):
            self._btn_dnd.setText("🔕" if dnd_on else "🔔")
            self._btn_dnd.setToolTip(
                "免打扰 (当前: 开启 - 静默观察不说话)"
                if dnd_on
                else "免打扰 (当前: 关闭 - 允许主动吐槽)"
            )
        if self.on_tts_toggle_callback:
            self.on_tts_toggle_callback(tts_on)

    def _apply_mode_preset(self, name: str):
        label = next((item[1] for item in MODE_PRESETS if item[0] == name), name)
        mode_icons = {"陪伴": "🌸", "安静": "🌙", "省资源": "🍃", "工作": "💼"}
        icon = mode_icons.get(label, "🌸")
        if hasattr(self, "_btn_mode"):
            self._btn_mode.setText(f"{icon} {label} ▾")
        self._current_mode_name = label
        app = getattr(self, "app", None)
        apply = getattr(app, "_apply_mode_preset", None)
        if not callable(apply):
            self.append("system", "模式切换还没准备好")
            return
        result = apply(name)
        self._sync_mode_status_buttons()
        self._refresh_companion_summary()
        self.append("system", str(result or f"已切换到{label}模式"))

    def _on_mode_menu_clicked(self):
        menu = QtWidgets.QMenu(self._win)
        for key, label, tip in MODE_PRESETS:
            action = menu.addAction(label)
            action.setToolTip(tip)
            action.triggered.connect(
                lambda _checked=False, name=key: self._apply_mode_preset(name)
            )
        anchor = getattr(self, "_btn_mode", None) or self._win
        popup_pos = (
            anchor.mapToGlobal(QtCore.QPoint(0, anchor.height()))
            if hasattr(anchor, "mapToGlobal")
            else self._win.mapToGlobal(QtCore.QPoint(0, 0))
        )
        menu.exec(popup_pos)

    def _append_history_message(self, role: str, text: str):
        if not hasattr(self, "_history"):
            return
        if hasattr(self._history, "append_message"):
            self._history.append_message(role, text)
            return
        self._history.append(f"{role}: {text}")

    def append(self, role, text):
        self._emit_bridge_signal("sig_append", role, text)

    def set_status(self, text):
        self._emit_bridge_signal("sig_status", text)

    def refresh_character_status(self):
        self._emit_bridge_signal("sig_refresh_character")

    def sync_active_character_visual(self):
        self._emit_bridge_signal("sig_sync_active_character_visual")

    def publish_display_snapshot(self):
        self._publish_display_state()

    def trigger_costume_by_name(self, costume_name: str):
        self._emit_bridge_signal("sig_trigger_costume_name", str(costume_name or ""))

    def apply_character_switch(
        self,
        model_path: str,
        cfg: Optional[dict] = None,
        *,
        costume_name: Optional[str] = None,
        character_name: Optional[str] = None,
    ):
        self._emit_bridge_signal(
            "sig_apply_character_switch",
            str(model_path or ""),
            cfg if isinstance(cfg, dict) else {},
            str(costume_name or ""),
            str(character_name or ""),
        )

    def show_sedentary_popup(
        self,
        app_name: str,
        active_minutes: int,
        image_path: str = "",
        on_result: Optional[Callable[[str], None]] = None,
    ):
        self._emit_bridge_signal(
            "sig_sedentary_popup",
            str(app_name or ""),
            int(active_minutes or 0),
            str(image_path or ""),
            on_result,
        )

    def request_action_confirm(self, title: str, summary: str) -> bool:
        """Thread-safe modal confirm for ActionGate. Blocks caller until user answers."""
        import threading

        done = threading.Event()
        box: dict[str, str] = {"value": "cancel"}

        def _on_result(value: str) -> None:
            box["value"] = str(value or "cancel")
            done.set()

        emitted = self._emit_bridge_signal(
            "sig_action_confirm",
            str(title or "确认操作"),
            str(summary or ""),
            _on_result,
        )
        if not emitted:
            return False
        # Wait on Qt UI thread response (modal dialog)
        if not done.wait(timeout=300):
            return False
        return str(box.get("value") or "").strip().lower() in {
            "confirm",
            "yes",
            "ok",
            "true",
            "1",
            "确认",
        }

    def _emit_bridge_signal(self, signal_name: str, *args) -> bool:
        try:
            bridge = getattr(self, "_bridge", None)
            signal = getattr(bridge, signal_name, None)
            if signal is None:
                return False
            signal.emit(*args)
            return True
        except RuntimeError as exc:
            if "already deleted" in str(exc):
                return False
            raise

    @QtCore.Slot(str, str, object)
    def _show_action_confirm_ui(self, title: str, summary: str, on_result: object):
        try:
            from modules.gui.action_confirm_popup import show_action_confirm_dialog

            result = show_action_confirm_dialog(
                self._win,
                title=str(title or "确认操作"),
                summary=str(summary or ""),
            )
        except Exception as exc:
            result = "error"
            try:
                self.append("system", f"操作确认弹窗失败: {exc}")
            except Exception:
                pass
        if callable(on_result):
            try:
                on_result(str(result or "cancel"))
            except Exception:
                pass

    @QtCore.Slot(str, int, str, object)
    def _show_sedentary_popup_ui(
        self, app_name: str, active_minutes: int, image_path: str, on_result: object
    ):
        try:
            import config

            options = build_sedentary_popup_options(
                config,
                app_name=app_name,
                active_minutes=active_minutes,
                image_path_override=image_path,
            )
            result = show_sedentary_popup_dialog(self._win, options)
        except Exception as exc:
            result = "error"
            try:
                self.append("system", f"久坐提醒弹窗失败: {exc}")
            except Exception:
                pass

        if callable(on_result):
            try:
                on_result(result)
            except Exception:
                pass

    @QtCore.Slot(str, object, str, str)
    def _apply_character_switch(
        self, model_path: str, cfg: object, costume_name: str, character_name: str
    ):
        safe_cfg = cfg if isinstance(cfg, dict) else {}
        if model_path:
            self._apply_costume_change(
                model_path,
                safe_cfg,
                costume_name=costume_name or None,
            )
        self._refresh_character_status()
        if character_name:
            self.set_status(f"角色已切换：{character_name}")
            self.append("system", f"🎭 已通过 QQ 切换角色：{character_name}")

    @QtCore.Slot()
    def _sync_active_character_visual(self):
        try:
            active_id = character_manager.data.get("active_id")
            if not active_id:
                self._refresh_character_status()
                return
            costume_name = character_manager.get_current_costume_name(active_id)
            if costume_name:
                self.append("system", f"[角色同步] 触发服装：{costume_name}")
                self._on_costume_triggered(costume_name)
            else:
                self._refresh_character_status()
        except Exception:
            self._refresh_character_status()

    def set_frontend_suppressed(self, suppressed: bool) -> None:
        # Live2D is only the desktop model now. It must not hide this tray,
        # because the tray is the only settings window.
        try:
            self._bridge.sig_set_frontend_suppressed.emit(False)
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

    def apply_connection_state(self, state: str, detail: str = "") -> None:
        """Thread-safe entry: bridge calls this from the WS thread."""
        try:
            self._bridge.sig_connection_state.emit(str(state or ""), str(detail or ""))
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

    @QtCore.Slot(str, str)
    def _apply_connection_state_ui(self, state: str, detail: str) -> None:
        """Update status dot/tooltip/ball and the display state on the Qt thread.

        Mirrors the existing set_status protocol but is driven by the local
        WebSocket lifecycle, so the user can tell "connecting / online /
        offline" apart even before the brain sends its own status events.
        """
        normalized = (state or "").strip().lower()
        self._connection_state = normalized or "connecting"
        self._connection_detail = detail or ""
        if normalized == "connected":
            label = "已连接"
            dot = "ok"
            tooltip = "大脑在线，遥控窗已连接。"
        elif normalized == "offline":
            label = "未连接"
            dot = "err"
            tooltip = "大脑未连接："
            if detail:
                tooltip += detail
            else:
                tooltip += "点击 ⋯ →「启动本机后端」重试。"
        else:  # connecting
            label = "连接中…"
            dot = "busy"
            tooltip = "正在连接大脑…"
        try:
            self._lbl_status.setText(label)
            set_dot_status(self._dot, dot)
            if hasattr(self, "_btn_runtime_health"):
                current = str(self._btn_runtime_health.toolTip() or "")
                if "点击查看组件详情" not in current and "运行健康状态读取失败" not in current:
                    self._btn_runtime_health.setToolTip(tooltip)
            # Re-publish the connection-aware state so display callbacks see it.
            self._publish_display_state()
        except RuntimeError:
            pass

    def _apply_frontend_suppressed(self, suppressed: bool) -> None:
        self._frontend_suppressed = bool(suppressed)
        if self._frontend_suppressed:
            # Enhanced owns the tray and global hotkeys. Qt often starts
            # minimized-to-tray; with that tray gone the remote must be shown.
            self._stop_global_listeners()
            tray = getattr(self, "_tray", None)
            if tray is not None:
                try:
                    tray.hide()
                except RuntimeError:
                    pass
            self._show_hud_ui()
            return
        self._start_global_listeners()
        tray = getattr(self, "_tray", None)
        if tray is not None:
            try:
                tray.show()
            except RuntimeError:
                pass
        if not getattr(self.cfg, "start_minimized_to_tray", False):
            try:
                win = getattr(self, "_win", None)
                if win is not None:
                    if getattr(self, "_is_ball_mode", True):
                        self._switch_to_ball()
                    win.show()
                    self._ensure_on_screen()
            except RuntimeError:
                pass

    def request_show_hud(self) -> None:
        self._emit_bridge_signal("sig_show_hud")

    def request_toggle_show_hide(self) -> None:
        self._emit_bridge_signal("sig_toggle_show_hide")

    def request_hud_hotkey(self) -> None:
        self._emit_bridge_signal("sig_toggle_gui")

    def _show_hud_ui(self) -> None:
        try:
            win = getattr(self, "_win", None)
            if win is None:
                return
            win.show()
            win.raise_()
            win.activateWindow()
            self.refresh_work_session_status()
            self._ensure_on_screen()
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

    def _toggle_ball_from_tray(self):
        """托盘左键：显示悬浮球，或把已经显示的遥控窗藏起来。"""
        try:
            win = getattr(self, "_win", None)
            if win is None:
                return
            visible = bool(win.isVisible()) and not bool(win.isMinimized())
            if visible:
                win.hide()
                return
            self._switch_to_ball()
            win.show()
            win.raise_()
            self._ensure_on_screen()
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

    def toggle_show_hide(self):
        try:
            win = getattr(self, "_win", None)
            if win is None:
                return
            minimized = False
            if hasattr(win, "isMinimized"):
                try:
                    minimized = bool(win.isMinimized())
                except Exception:
                    minimized = False
            visible = bool(win.isVisible()) and not minimized
            if visible:
                win.hide()
            else:
                self._show_hud_ui()
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

    def toggle_hotkey(self):
        """快捷键专用：球/隐藏状态 → 打开面板；面板可见 → 收回成球。"""
        try:
            win = getattr(self, "_win", None)
            if win is None:
                return
            if win.isVisible() and not self._is_ball_mode:
                # 面板开着 → 收回成球
                self._switch_to_ball()
            else:
                # 球状态或隐藏状态 → 打开面板
                if self._is_ball_mode:
                    self._switch_to_panel()
                win.show()
                win.raise_()
                win.activateWindow()
                self.refresh_work_session_status()
                self._ensure_on_screen()
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise

    def _on_app_state_changed(self, state):
        if state == QtCore.Qt.ApplicationState.ApplicationActive:
            QtCore.QTimer.singleShot(200, self._ensure_on_screen)

    def hide(self):
        self._win.hide()

    @QtCore.Slot(str, str)
    def _append_ui(self, role, text):
        self._append_history_message(role, text)

    @QtCore.Slot(str)
    def _set_status_ui(self, text):
        lowered = str(text or "").strip().lower().rstrip(".…! ")
        cute_status_map = {
            "idle": "空闲",
            "就绪": "就绪",
            "ready": "就绪",
            "thinking": "思考中…",
            "思考中": "思考中…",
            "speaking": "说话中…",
            "listen": "倾听中…",
            "listening": "倾听中…",
            "streaming": "说话中…",
        }
        display_text = cute_status_map.get(lowered)
        if display_text is None and lowered:
            display_text = cute_status_map.get(lowered.split()[0])
        if display_text is None:
            display_text = text
        self._lbl_status.setText(display_text)
        set_dot_status(self._dot, classify_status(display_text))
        busy = any(
            token in lowered
            for token in ("think", "speak", "listen", "stream", "处理", "思考", "说话", "visual", "tool")
        )
        idle = lowered in {"idle", "就绪", "ready", ""}
        if idle:
            self._set_chat_busy(False)
        elif busy:
            self._set_chat_busy(True)
        self._refresh_companion_summary(text)
        self._publish_display_state()

    def _publish_display_state(self):
        if not callable(self.on_display_state_callback):
            return
        try:
            active = character_manager.get_active_character() or {}
            emotion = "[idle]"
            app_ref = getattr(self, "app", None)
            emo_ctrl = getattr(app_ref, "emotion_controller", None)
            current_emo = str(getattr(emo_ctrl, "current_emotion", "") or "").strip()
            if current_emo:
                emotion = f"[{current_emo}]"
            payload = {
                "role": str(active.get("name") or "未激活角色"),
                "emotion": emotion,
                "status": str(
                    self._lbl_status.text() if hasattr(self, "_lbl_status") else "就绪"
                ),
            }
            print(f"[DisplayState] {payload}")
            self.on_display_state_callback(payload)
        except Exception:
            pass

    @QtCore.Slot(str)
    def _send_text_from_asr(self, text):
        self._dispatch_send(text)

    def _build_tray(self):
        t = QtWidgets.QSystemTrayIcon(self._icon)
        t.setToolTip(self.cfg.title)
        t.activated.connect(self._on_tray_activated)
        m = QtWidgets.QMenu()
        m.aboutToShow.connect(self._refresh_tray_menu)
        self._tray_menu = m
        self._refresh_tray_menu()
        t.setContextMenu(m)
        t.show()
        return t

    def _refresh_tray_menu(self):
        menu = getattr(self, "_tray_menu", None)
        if menu is None:
            return
        menu.clear()
        menu.addAction("显示/隐藏遥控窗").triggered.connect(self.toggle_show_hide)
        if callable(getattr(self, "on_toggle_live2d_callback", None)):
            menu.addAction("显示/隐藏桌面形象").triggered.connect(
                self._on_toggle_live2d
            )
        menu.addAction("设置").triggered.connect(self._on_settings_clicked)
        costume_menu = self._build_quick_costume_menu(menu)
        costume_menu.setTitle("快速换装")
        menu.addMenu(costume_menu)
        mode_menu = menu.addMenu("切换模式")
        for key, label, tip in MODE_PRESETS:
            action = mode_menu.addAction(label)
            action.setToolTip(tip)
            action.triggered.connect(
                lambda _checked=False, name=key: self._apply_mode_preset(name)
            )
        tts_action = menu.addAction("语音播报")
        tts_action.setCheckable(True)
        try:
            import config

            tts_action.setChecked(bool(getattr(config, "TTS_ENABLED", True)))
        except Exception:
            tts_action.setChecked(True)
        tts_action.triggered.connect(self._toggle_tts)
        menu.addSeparator()
        menu.addAction("运行健康").triggered.connect(self._on_runtime_health_clicked)
        if is_developer_mode():
            menu.addAction("控制台").triggered.connect(self._on_console_clicked)
        menu.addSeparator()
        if self.on_restart_callback:
            menu.addAction("重启程序").triggered.connect(self._handle_restart)
        if callable(self.on_stop_backend_callback):
            menu.addAction("停止本机后端").triggered.connect(self._on_stop_backend)
        if callable(self.on_stop_all_callback):
            menu.addAction("退出并停止所有服务").triggered.connect(self._on_stop_all)
        menu.addAction("退出").triggered.connect(self._quit)

    def _on_tray_activated(self, reason):
        if reason in {
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._toggle_ball_from_tray()

    def _handle_restart(self):
        reply = QtWidgets.QMessageBox.question(
            None,
            "重启确认",
            "确定要重启 Live2D-Suzu 吗？\n可能会中断当前的对话。",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            if self.on_restart_callback:
                box = QtWidgets.QProgressDialog("正在重启，请稍候…", "", 0, 0, None)
                box.setWindowTitle("重启")
                box.setCancelButton(None)
                box.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
                box.setMinimumDuration(0)
                box.show()
                QtWidgets.QApplication.processEvents()
                self.on_restart_callback()

    def _on_stop_backend(self):
        """「停止本机后端」：只停 Brain，桌面形象和外部服务策略不受影响。"""
        reply = QtWidgets.QMessageBox.question(
            None,
            "停止本机后端",
            "确定要停止本机大脑吗？\n聊天和 QQ 消息将暂停，桌面形象窗口保持运行。",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            callback = self.on_stop_backend_callback
            if callable(callback):
                callback()

    def _on_stop_all(self):
        """「退出并停止所有服务」：停 Live2D + 优雅停 Brain 后退出客户端。"""
        reply = QtWidgets.QMessageBox.question(
            None,
            "退出并停止所有服务",
            "确定要停止桌面形象、大脑和由大脑管理的外部服务（Ollama/GPT-SoVITS/NapCat 按其自停配置）吗？",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            callback = self.on_stop_all_callback
            if callable(callback):
                callback()

    def request_quit(self):
        """Thread-safe quit request (e.g. from a stop-all worker thread)."""
        self._emit_bridge_signal("sig_quit")

    def _quit(self):
        self._stop_global_listeners()
        self._tray.hide()
        if self.on_quit_callback:
            self.on_quit_callback()
        self._app.quit()

    def _enable_drag(self, widget):
        interactive_types = (
            QtWidgets.QAbstractButton,
            QtWidgets.QLineEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QComboBox,
            QtWidgets.QAbstractSlider,
            QtWidgets.QAbstractItemView,
            QtWidgets.QSizeGrip,
        )

        def _hit_interactive(pos) -> bool:
            child = widget.childAt(pos)
            while child is not None and child is not widget:
                if isinstance(child, interactive_types):
                    return True
                child = child.parentWidget()
            return False

        def mousePressEvent(event):
            if self._is_ball_mode:
                event.ignore()
                return
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                if _hit_interactive(event.position().toPoint()):
                    event.ignore()
                    return
                widget._drag_pos = (
                    event.globalPosition().toPoint() - widget.frameGeometry().topLeft()
                )
                event.accept()
                return
            event.ignore()

        def mouseMoveEvent(event):
            if self._is_ball_mode:
                event.ignore()
                return
            if hasattr(widget, "_drag_pos") and (
                event.buttons() & QtCore.Qt.MouseButton.LeftButton
            ):
                widget.move(event.globalPosition().toPoint() - widget._drag_pos)
                event.accept()
                return
            event.ignore()

        def mouseReleaseEvent(event):
            if hasattr(widget, "_drag_pos"):
                del widget._drag_pos
            event.ignore()

        widget.mousePressEvent = mousePressEvent
        widget.mouseMoveEvent = mouseMoveEvent
        widget.mouseReleaseEvent = mouseReleaseEvent
