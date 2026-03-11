from __future__ import annotations
import html
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Any

from PySide6 import QtCore, QtGui, QtWidgets

# 引入我们的新模块
from modules.gui.config import QtGuiConfig, DEFAULT_BALL_CONFIG, OFFSET_X
from modules.gui.styles import get_main_styles, get_panel_styles, get_ui_palette, get_settings_styles, get_tool_dialog_styles, get_memory_dialog_styles
from modules.gui.utils import resolve_icon, set_dot_status, classify_status
from modules.gui.widgets.ball import DraggableBall
from modules.gui.dialogs.plugin_manager import PluginManagerDialog
from modules.gui.dialogs.settings import SettingsDialog
from modules.gui.dialogs.codex_assistant import CodexAssistantDialog
from modules.character_manager import character_manager

# 引入配置
try:
    from config import COSTUME_MAP
except ImportError:
    COSTUME_MAP = {}

try:
    from config import BALL_CONFIG
except ImportError:
    BALL_CONFIG = DEFAULT_BALL_CONFIG


class _Bridge(QtCore.QObject):
    sig_append = QtCore.Signal(str, str)
    sig_status = QtCore.Signal(str)
    sig_focus_input = QtCore.Signal()
    sig_set_wake = QtCore.Signal(bool)
    sig_set_tts = QtCore.Signal(bool)
    sig_toggle_gui = QtCore.Signal()
    sig_send_text = QtCore.Signal(str)


class QtChatTrayApp(QtCore.QObject):
    def __init__(
            self,
            on_send_callback: Callable[..., None],
            *,
            on_tts_toggle_callback: Optional[Callable[[bool], None]] = None,
            on_voice_toggle_callback: Optional[Callable[[bool], None]] = None,  # 🟢 新增语音回调
            on_costume_callback: Optional[Callable[[str, dict], None]] = None,
            on_preview_motion_callback: Optional[Callable[[str, int], None]] = None,
            on_preview_expression_callback: Optional[Callable[[int], None]] = None,
            on_quit_callback: Optional[Callable[[], None]] = None,
            on_restart_callback: Optional[Callable[[], None]] = None,
            on_apply_external_settings_callback: Optional[Callable[[dict], dict]] = None,
            plugin_manager: Optional[Any] = None,
            cfg: Optional[QtGuiConfig] = None,
    ):
        super().__init__()
        self.cfg = cfg or QtGuiConfig()
        self.on_send_callback = on_send_callback
        self.on_tts_toggle_callback = on_tts_toggle_callback
        self.on_voice_toggle_callback = on_voice_toggle_callback  # 🟢 保存回调
        self._external_costume_callback = on_costume_callback
        self.on_costume_callback = self._apply_costume_change
        self.on_preview_motion_callback = on_preview_motion_callback
        self.on_preview_expression_callback = on_preview_expression_callback
        self.on_quit_callback = on_quit_callback
        self.on_restart_callback = on_restart_callback
        self.on_apply_external_settings_callback = on_apply_external_settings_callback
        self.plugin_manager = plugin_manager

        # --- 内部状态 ---
        self._is_ball_mode = True
        self._last_panel_size = QtCore.QSize(604, 156)
        self._current_mode_name = "Companion"
        self._wake_enabled = False
        self._current_costume_name = "未设定"

        # --- ASR 占位符 ---
        self._mic_lock = threading.Lock()
        self._asr_recognizer = None
        self._is_wake_listening = False

        # --- Qt 初始化 ---
        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._app.applicationStateChanged.connect(self._on_app_state_changed)
        self._icon = resolve_icon(self.cfg.icon_path)

        # --- 信号桥接 ---
        self._bridge = _Bridge()
        self._bridge.sig_append.connect(self._append_ui)
        self._bridge.sig_status.connect(self._set_status_ui)
        self._bridge.sig_toggle_gui.connect(self.toggle_show_hide)
        self._bridge.sig_send_text.connect(self._send_text_from_asr)

        # --- 构建 UI ---
        self._win = self._build_window()
        self._tray = self._build_tray()

        self._settings_dialog = None
        self._plugin_dialog = None
        self._memory_dialog = None
        self._codex_dialog = None

        # 启动逻辑
        if self.cfg.start_minimized_to_tray:
            self.hide()
        else:
            self._switch_to_ball()
            self._center_window()
            self._win.show()

        self.set_status("Ready")

    def apply_external_settings(self, settings: Optional[dict] = None):
        if callable(self.on_apply_external_settings_callback):
            return self.on_apply_external_settings_callback(settings or {})
        return {}

    # --- 核心 UI 构建 ---

    def apply_ui_palette(self):
        try:
            self._win.setStyleSheet(get_panel_styles())
            if self._settings_dialog:
                self._settings_dialog.setStyleSheet(get_settings_styles())
            if self._codex_dialog:
                self._codex_dialog.setStyleSheet(get_tool_dialog_styles())
                if hasattr(self._codex_dialog, "_refresh_history"):
                    try:
                        self._codex_dialog._refresh_history(force=True)
                    except Exception:
                        pass
            if self._memory_dialog:
                self._memory_dialog.setStyleSheet(get_memory_dialog_styles())
            if self._plugin_dialog:
                self._plugin_dialog.setStyleSheet(get_tool_dialog_styles())
        except Exception:
            pass
    def _build_window(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setWindowTitle(self.cfg.title)
        w.setWindowIcon(self._icon)
        w.setWindowFlags(
            QtCore.Qt.WindowType.WindowStaysOnTopHint | QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint)
        w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        w.setStyleSheet(get_panel_styles())

        self._stack = QtWidgets.QStackedLayout(w)

        self._ball_widget = QtWidgets.QWidget()
        self._ball_widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._ball_widget.setStyleSheet("background: transparent;")

        ball_layout = QtWidgets.QVBoxLayout(self._ball_widget)
        ball_layout.setContentsMargins(5, 5, 5, 5)

        self._ball_btn = DraggableBall(main_window=w)
        self._ball_btn.setObjectName("ball_btn")
        s = BALL_CONFIG.get("size", 60)
        self._ball_btn.setFixedSize(s, s)
        self._ball_btn.clicked.connect(self._switch_to_panel)
        self._ball_btn.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._ball_btn.customContextMenuRequested.connect(self._show_costume_menu_from_ball)

        img_path = BALL_CONFIG.get("image_path", "")
        enable_image = BALL_CONFIG.get("enable_image", False)

        valid_icon = False
        if enable_image and img_path:
            p = Path(img_path)
            if not p.is_absolute():
                p = Path.cwd() / p
            if p.exists():
                icon = QtGui.QIcon(str(p))
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
            self._ball_btn.setText(BALL_CONFIG.get("text", "L2D"))
            self._ball_btn.setStyleSheet(get_main_styles(BALL_CONFIG))

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
        main_vbox.setContentsMargins(10, 8, 10, 8)
        main_vbox.setSpacing(4)

        top_bar = QtWidgets.QHBoxLayout()
        self._dot = QtWidgets.QLabel("")
        self._dot.setFixedSize(9, 9)
        set_dot_status(self._dot, "ok")

        self._lbl_status = QtWidgets.QLabel("Ready")
        self._lbl_status.setObjectName("statusLabel")

        self._lbl_character = QtWidgets.QLabel("")
        self._lbl_character.setObjectName("characterLabel")
        self._current_costume_name = self._resolve_initial_costume_name()
        self._refresh_character_status()

        btn_shrink = QtWidgets.QPushButton("─")
        btn_shrink.setObjectName("windowCtl")
        btn_shrink.clicked.connect(self._switch_to_ball)

        btn_close = QtWidgets.QPushButton("✕")
        btn_close.setObjectName("windowCtl")
        btn_close.clicked.connect(self.hide)

        top_bar.addWidget(self._dot)
        top_bar.addWidget(self._lbl_status)
        top_bar.addWidget(self._lbl_character)
        top_bar.addStretch()
        top_bar.addWidget(btn_shrink)
        top_bar.addWidget(btn_close)
        main_vbox.addLayout(top_bar)

        self._history = QtWidgets.QTextEdit()
        self._history.setObjectName("historyView")
        self._history.setReadOnly(True)
        self._history.setVisible(False)
        self._history.document().setDocumentMargin(6)
        self._history.setUndoRedoEnabled(False)
        main_vbox.addWidget(self._history, 1)

        input_box = QtWidgets.QHBoxLayout()
        self._input = QtWidgets.QLineEdit()
        self._input.setObjectName("chatInput")
        self._input.setFixedHeight(30)
        self._input.setPlaceholderText("和我聊聊，或直接输入任务 / 指令…")
        self._input.returnPressed.connect(self._on_send_clicked)

        btn_send = QtWidgets.QPushButton("➤")
        btn_send.setObjectName("sendButton")
        btn_send.setFixedSize(30, 30)
        btn_send.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_send.clicked.connect(self._on_send_clicked)

        input_box.setContentsMargins(7, 2, 4, 2)
        input_box.setSpacing(5)
        input_box.addWidget(self._input, 1)
        input_box.addWidget(btn_send)

        input_container = QtWidgets.QFrame()
        input_container.setObjectName("inputShell")
        input_container.setLayout(input_box)
        main_vbox.addWidget(input_container)

        tools_layout = QtWidgets.QHBoxLayout()
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(3)

        def mk_btn(text, cb, tip=""):
            b = QtWidgets.QPushButton(text)
            b.setObjectName("toolbarBtn")
            b.setToolTip(tip)
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(cb)
            return b

        self._btn_mode = mk_btn("🎛", self._on_mode_menu_clicked, "模式预设")
        self._btn_expand = mk_btn("⌄", self._toggle_full, "展开或收起对话记录")

        import config
        init_tts_state = getattr(config, 'TTS_ENABLED', True)
        tts_icon = "🔊" if init_tts_state else "🔈"
        tts_tip = "语音播报 (当前: 开启)" if init_tts_state else "语音播报 (当前: 关闭)"
        self._btn_tts = mk_btn(tts_icon, self._toggle_tts, tts_tip)

        init_voice_state = getattr(config, 'VOICE_SENSOR_ENABLED', False)
        voice_icon = "🎙️" if init_voice_state else "🔇"
        voice_tip = "语音唤醒 (当前: 开启)" if init_voice_state else "语音唤醒 (当前: 关闭)"
        self._btn_voice = mk_btn(voice_icon, self._toggle_voice, voice_tip)

        init_dnd_state = getattr(config, 'DND_MODE', False)
        dnd_icon = "🔕" if init_dnd_state else "🔔"
        dnd_tip = "免打扰 (当前: 开启)" if init_dnd_state else "免打扰 (当前: 关闭)"
        self._btn_dnd = mk_btn(dnd_icon, self._toggle_dnd, dnd_tip)
        self._btn_costume = mk_btn("👗", self._on_quick_costume_clicked, "快速换装")
        self._btn_settings = mk_btn("⚙️", self._on_settings_clicked, "设置中心")

        self._btn_more = mk_btn("⋯", self._show_more_menu, "更多功能")

        tools_layout.addWidget(self._btn_tts)
        tools_layout.addWidget(self._btn_voice)
        tools_layout.addWidget(self._btn_dnd)
        tools_layout.addWidget(self._btn_costume)
        tools_layout.addStretch()
        tools_layout.addWidget(self._btn_mode)
        tools_layout.addWidget(self._btn_settings)
        tools_layout.addWidget(self._btn_more)
        tools_layout.addWidget(self._btn_expand)

        main_vbox.addLayout(tools_layout)

        self._size_grip = QtWidgets.QSizeGrip(self._container)
        self._size_grip.setStyleSheet("background: transparent; width: 20px; height: 20px;")
        self._stack.addWidget(self._panel_widget)
        self._enable_drag(w)
        self._refresh_companion_summary()

        return w

    def _refresh_companion_summary(self, status_text: Optional[str] = None):
        try:
            import config
        except Exception:
            config = object()

        tts_enabled = bool(getattr(config, "TTS_ENABLED", True))
        voice_enabled = bool(getattr(config, "VOICE_SENSOR_ENABLED", False))
        dnd_enabled = bool(getattr(config, "DND_MODE", False))
        mode_name = getattr(self, "_current_mode_name", "Companion")
        status_value = status_text or (self._lbl_status.text() if hasattr(self, "_lbl_status") else "Ready")

        if hasattr(self, "_pill_mode"):
            self._pill_mode.setText(f"模式 · {mode_name}")
            self._pill_tts.setText(f"播报 · {'开' if tts_enabled else '关'}")
            self._pill_voice.setText(f"唤醒 · {'开' if voice_enabled else '关'}")
            self._pill_focus.setText("免打扰 · 开" if dnd_enabled else f"状态 · {status_value}")

        if hasattr(self, "_input"):
            if dnd_enabled:
                self._input.setPlaceholderText("安静模式中，也可以继续输入任务或聊天…")
            elif voice_enabled:
                self._input.setPlaceholderText("可以直接说话，也可以继续输入…")
            else:
                self._input.setPlaceholderText("和我聊聊，或直接输入任务 / 指令…")

    def _show_more_menu(self):
        menu = QtWidgets.QMenu(self._btn_more)
        menu.addAction("💻 代码助手").triggered.connect(self._on_codex_clicked)
        menu.addAction("📊 模型监控").triggered.connect(self._on_monitor_clicked)
        menu.addSeparator()
        menu.addAction("🧩 插件管理").triggered.connect(self._on_plugin_clicked)
        menu.addAction("🧠 记忆编辑").triggered.connect(self._on_memory_clicked)
        if self.on_restart_callback:
            menu.addSeparator()
            menu.addAction("🔄 重启程序").triggered.connect(self._handle_restart)
        popup_pos = self._btn_more.mapToGlobal(QtCore.QPoint(0, self._btn_more.height()))
        menu.exec(popup_pos)

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

    def _center_window(self):
        try:
            geo = self._app.primaryScreen().geometry()
            win = self._win.geometry()
            self._win.move((geo.width() - win.width()) // 2, (geo.height() - win.height()) // 2)
        except:
            pass

    def _ensure_on_screen(self):
        win = self._win.frameGeometry()
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos()) or self._app.primaryScreen()
        if not screen:
            return
        avail = screen.availableGeometry()
        x = min(max(win.x(), avail.left()), avail.right() - win.width())
        y = min(max(win.y(), avail.top()), avail.bottom() - win.height())
        if (x, y) != (win.x(), win.y()):
            self._win.move(x, y)

    def _switch_to_ball(self):
        if self._is_ball_mode: return
        curr = self._win.geometry()
        self._is_ball_mode = True
        self._stack.setCurrentIndex(0)
        s = BALL_CONFIG.get("size", 60)
        self._win.resize(s + 10, s + 10)
        self._win.move(curr.x() + OFFSET_X, curr.y())
        self._ensure_on_screen()

    def _switch_to_panel(self):
        if not self._is_ball_mode: return
        curr = self._win.geometry()
        self._is_ball_mode = False
        self._stack.setCurrentIndex(1)
        self._win.resize(self._last_panel_size)
        self._win.move(curr.x() - OFFSET_X, curr.y())
        self._ensure_on_screen()

    def _toggle_full(self):
        is_full = getattr(self._win, "_is_full_mode", False)
        if is_full:
            self._win._is_full_mode = False
            self._history.setVisible(False)
            self._btn_expand.setText("⌄")
            self._win.resize(self._win.width(), 156)
        else:
            self._win._is_full_mode = True
            self._history.setVisible(True)
            self._btn_expand.setText("⌃")
            self._win.resize(self._win.width(), 376)

    # 🟢 新增：免打扰切换逻辑
    def _toggle_dnd(self):
        import config
        # 如果 config 里没有定义 DND_MODE，赋个初值
        if not hasattr(config, "DND_MODE"):
            config.DND_MODE = False

        # 翻转状态
        config.DND_MODE = not config.DND_MODE

        # 更新 UI 和提示
        if config.DND_MODE:
            self._btn_dnd.setText("🔕")
            self._btn_dnd.setToolTip("免打扰 (当前: 开启 - 静默观察不说话)")
            self.append("system", "🔕 已开启手动免打扰：助手将只默默记录，不再主动发声打断。")
        else:
            self._btn_dnd.setText("🔔")
            self._btn_dnd.setToolTip("免打扰 (当前: 关闭 - 允许主动吐槽)")
            self.append("system", "🔔 已关闭手动免打扰：助手恢复吐槽与主动关怀。")

        self._refresh_companion_summary()

    def _on_settings_clicked(self):
        if not self._settings_dialog:
            self._settings_dialog = SettingsDialog(parent=None, main_app=self)
        self._refresh_character_status()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_plugin_clicked(self):
        self._on_settings_clicked()
        if self._settings_dialog and hasattr(self._settings_dialog, "open_page"):
            self._settings_dialog.open_page(3)

    def _on_memory_clicked(self):
        try:
            self._on_settings_clicked()
            if self._settings_dialog and hasattr(self._settings_dialog, "open_page"):
                self._settings_dialog.open_page(4)
        except Exception as e:
            self.append("system", f"❌ 记忆编辑器加载失败: {e}")

    def _on_costume_triggered(self, name, cfg=None):
        safe_cfg = cfg if isinstance(cfg, dict) else {}
        path = safe_cfg.get("path", "")

        try:
            active_id = character_manager.data.get("active_id")
            if active_id:
                active_char = character_manager.get_character(active_id) or {}
                costumes = active_char.get("costumes") or {}
                costume_entry = costumes.get(name) or {}

                manager_path = costume_entry.get("path", "") if isinstance(costume_entry, dict) else ""
                if manager_path:
                    path = manager_path

                runtime_cfg = character_manager.get_costume_runtime_config(active_id, name)
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
        self._lbl_character.setText(f"角色: {char_name} | 服装: {costume_name}")
        self._refresh_companion_summary()

    def _apply_costume_change(self, path: str, cfg: Optional[dict] = None, *, costume_name: Optional[str] = None):
        if not self._external_costume_callback:
            return

        safe_cfg = cfg if isinstance(cfg, dict) else {}
        self._external_costume_callback(path, safe_cfg)
        self._current_costume_name = costume_name or self._resolve_costume_name(path)
        self._refresh_character_status()

    def preview_motion(self, motion_name: str, motion_type: int = 0):
        if not motion_name:
            return
        if self.on_preview_motion_callback:
            self.on_preview_motion_callback(motion_name, int(motion_type))

    def preview_expression(self, exp_id: int):
        if self.on_preview_expression_callback:
            self.on_preview_expression_callback(int(exp_id))

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
                        lambda checked=False, n=costume_name: self._on_costume_triggered(n)
                    )

            menu.addSeparator()
            settings_action = menu.addAction("打开角色管理")
            settings_action.triggered.connect(self._on_settings_clicked)
        except Exception as e:
            err_action = menu.addAction(f"加载换装菜单失败: {e}")
            err_action.setEnabled(False)
        return menu

    def _show_costume_menu_from_ball(self, pos):
        menu = self._build_quick_costume_menu(self._ball_btn)
        menu.exec(self._ball_btn.mapToGlobal(pos))

    def _on_quick_costume_clicked(self):
        anchor = getattr(self, "_btn_costume", None) or getattr(self, "_btn_more", None) or self._win
        menu = self._build_quick_costume_menu(anchor)
        popup_pos = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height())) if hasattr(anchor, "mapToGlobal") else self._win.mapToGlobal(QtCore.QPoint(0, 0))
        menu.exec(popup_pos)

    def _on_send_clicked(self):
        text = self._input.text().strip()
        if not text: return
        self._input.clear()
        self.append("user", text)
        self._dispatch_send(text)

    def _dispatch_send(self, text: str, ctx: Optional[dict] = None):
        if not self.on_send_callback:
            return
        try:
            self.on_send_callback(text, ctx)
        except TypeError:
            self.on_send_callback(text)

    def _on_codex_clicked(self):
        if not self._codex_dialog:
            # 独立窗口，避免受主窗口 Tool/隐藏状态影响
            self._codex_dialog = CodexAssistantDialog(parent=None, on_submit=self._on_codex_submit)
        self._codex_dialog.showNormal()
        self._codex_dialog.raise_()
        self._codex_dialog.activateWindow()

    def _send_react_command(self, command: str, preview: str):
        if not command:
            return
        self.append("user", preview)
        self._dispatch_send(command)

    def _on_monitor_clicked(self):
        self._send_react_command(
            "[CMD: llm_monitor | summary ||| 50]",
            "[MONITOR] 查看最近模型调用摘要",
        )

    def _apply_mode_preset(self, name: str):
        self._current_mode_name = name.title()
        self._refresh_companion_summary()
        self._send_react_command(
            f"[CMD: mode_preset | apply ||| {name}]",
            f"[MODE] 切换预设: {name}",
        )

    def _on_mode_menu_clicked(self):
        menu = QtWidgets.QMenu(self._btn_mode)
        menu.addAction("Companion").triggered.connect(lambda: self._apply_mode_preset("companion"))
        menu.addAction("Codex").triggered.connect(lambda: self._apply_mode_preset("codex"))
        menu.addAction("Quiet").triggered.connect(lambda: self._apply_mode_preset("quiet"))
        menu.addAction("Eco").triggered.connect(lambda: self._apply_mode_preset("eco"))
        menu.addSeparator()
        menu.addAction("查看当前模式").triggered.connect(
            lambda: self._send_react_command("[CMD: mode_preset | status]", "[MODE] 查看当前模式")
        )
        popup_pos = self._btn_mode.mapToGlobal(QtCore.QPoint(0, self._btn_mode.height()))
        menu.exec(popup_pos)

    def _on_codex_submit(self, text: str, payload: dict):
        path = (payload or {}).get("code_path", "")
        prefix = f"[CODEX:{path}] " if path else "[CODEX] "
        self.append("user", f"{prefix}{text}")
        self._dispatch_send(text, payload)


    def _append_history_message(self, role: str, text: str):
        if not hasattr(self, "_history"):
            return

        palette = get_ui_palette()
        console = palette.get("console_main", {}) if isinstance(palette, dict) else {}
        fg = console.get("fg", "#E5E7EB")
        muted = console.get("muted", "#94A3B8")
        label_color = console.get("label", "#CBD5E1")

        safe_text = html.escape(str(text or "")).replace("\n", "<br>")
        role_key = str(role or "system").lower()
        timestamp = time.strftime("%H:%M:%S")
        role_map = {"user": "YOU", "assistant": "AI", "system": "SYS"}
        label = role_map.get(role_key, role_key.upper())

        html_block = (
            "<div style='margin: 2px 0; font-family: \"Cascadia Mono\", \"Consolas\", \"JetBrains Mono\", monospace; "
            f"font-size: 12px; color: {fg};'>"
            f"<span style='color:{muted};'>[{timestamp}]</span> "
            f"<span style='color:{label_color}; font-weight:600;'>[{label}]</span> "
            f"<span style='color:{fg};'>{safe_text}</span>"
            "</div>"
        )

        cursor = self._history.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_block)
        cursor.insertBlock()
        self._history.setTextCursor(cursor)
        self._history.ensureCursorVisible()

    def append(self, role, text):
        self._bridge.sig_append.emit(role, text)

    def set_status(self, text):
        self._bridge.sig_status.emit(text)

    def toggle_show_hide(self):
        if self._win.isVisible():
            self._win.hide()
        else:
            self._win.show()
            self._ensure_on_screen()

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
        self._lbl_status.setText(text)
        set_dot_status(self._dot, classify_status(text))
        self._refresh_companion_summary(text)

    @QtCore.Slot(str)
    def _send_text_from_asr(self, text):
        self._dispatch_send(text)

    def _build_tray(self):
        t = QtWidgets.QSystemTrayIcon(self._icon)
        t.setToolTip(self.cfg.title)
        t.activated.connect(self._on_tray_activated)
        m = QtWidgets.QMenu()
        m.addAction("显示/隐藏").triggered.connect(self.toggle_show_hide)
        m.addAction("代码助手").triggered.connect(self._on_codex_clicked)
        m.addAction("模型监控").triggered.connect(self._on_monitor_clicked)
        m.addAction("模式预设").triggered.connect(self._on_mode_menu_clicked)
        if self.on_restart_callback:
            m.addSeparator()
            m.addAction("🔄 重启程序").triggered.connect(self._handle_restart)
        m.addSeparator()
        m.addAction("退出").triggered.connect(self._quit)
        t.setContextMenu(m)
        t.show()
        return t

    def _on_tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_show_hide()

    def _handle_restart(self):
        reply = QtWidgets.QMessageBox.question(
            None, "重启确认", "确定要重启 Live2D Agent 吗？\n(可能会中断当前的对话)",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            if self.on_restart_callback: self.on_restart_callback()

    def _quit(self):
        self._tray.hide()
        if self.on_quit_callback: self.on_quit_callback()
        self._app.quit()

    def _enable_drag(self, widget):
        def mousePressEvent(event):
            if self._is_ball_mode:
                event.ignore()
                return
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                widget._drag_pos = event.globalPosition().toPoint() - widget.frameGeometry().topLeft()
                event.accept()

        def mouseMoveEvent(event):
            if self._is_ball_mode:
                event.ignore()
                return
            if hasattr(widget, '_drag_pos') and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
                widget.move(event.globalPosition().toPoint() - widget._drag_pos)
                event.accept()

        def mouseReleaseEvent(event):
            if hasattr(widget, '_drag_pos'):
                del widget._drag_pos

        widget.mousePressEvent = mousePressEvent
        widget.mouseMoveEvent = mouseMoveEvent
        widget.mouseReleaseEvent = mouseReleaseEvent

