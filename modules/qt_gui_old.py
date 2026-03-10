# # modules/qt_gui.py


# from __future__ import annotations
#
# import re
# import time
# import threading
# from dataclasses import dataclass
# from typing import Callable, Optional, Any
# from pathlib import Path
#
# # 尝试导入 PySide6，如果失败则尝试 PyQt6
# try:
#     from PySide6 import QtCore, QtGui, QtWidgets
# except ImportError:
#     try:
#         from PyQt6 import QtCore, QtGui, QtWidgets
#     except ImportError:
#         raise ImportError("Neither PySide6 nor PyQt6 is available. Please install one of them.")
#
# try:
#     from pynput import keyboard as pynput_keyboard
# except Exception:
#     pynput_keyboard = None
#
# try:
#     import sounddevice as sd
#     import sherpa_onnx
#     import numpy as np
#     from pypinyin import lazy_pinyin
#
#     _ASR_DEPS_OK = True
# except Exception:
#     sd = None
#     sherpa_onnx = None
#     np = None
#     lazy_pinyin = None
#     _ASR_DEPS_OK = False
#
# try:
#     from config import WAKE_KEYWORDS, PLAY_WAKE_SOUND
# except Exception:
#     WAKE_KEYWORDS = ["怜酱", "怜"]
#     PLAY_WAKE_SOUND = False
#
# try:
#     import winsound
# except Exception:
#     winsound = None
#
# try:
#     from config import QT_ICON_PATH
# except Exception:
#     QT_ICON_PATH = None
#
# try:
#     from config import HOTKEY_TOGGLE_GUI
# except Exception:
#     HOTKEY_TOGGLE_GUI = "<ctrl>+<alt>+<space>"
#
# try:
#     from config import HOTKEY_TOGGLE_WAKE
# except Exception:
#     HOTKEY_TOGGLE_WAKE = "<ctrl>+<alt>+w"
#
# try:
#     from config import ASR_MIN_CHARS
# except Exception:
#     ASR_MIN_CHARS = 2
#
# try:
#     from config import ASR_BLACKLIST
# except Exception:
#     ASR_BLACKLIST = ["嗯", "啊", "哈", "哦", "好的", "好", "对", "是", "不是", "行", "可以"]
#
# # 🔴 新增：导入服装配置
# try:
#     from config import COSTUME_MAP
# except Exception:
#     COSTUME_MAP = {}
#
#
# try:
#     from config import BALL_CONFIG
# except Exception:
#     # 默认配置
#     BALL_CONFIG = {
#         "text": "L2D",         # 显示文字 (如果 enable_image=False)
#         "image_path": None,    # 图片路径 (例如 "icon.png")
#         "enable_image": False, # 是否启用图片模式
#         "size": 60,            # 球体大小
#         "font_size": 14,       # 文字大小
#         "bg_color": "#3B82F6", # 背景颜色 (蓝色)
#         "text_color": "white"  # 文字颜色
#     }
# # ==========================================
# # 👇 这里是可以修改偏移量的地方
# OFFSET_X = 200  # 数值越大，球越往右
#
#
# # ==========================================
#
# def _make_default_icon() -> QtGui.QIcon:
#     pm = QtGui.QPixmap(64, 64)
#     pm.fill(QtCore.Qt.GlobalColor.transparent)
#     p = QtGui.QPainter(pm)
#     p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
#     rect = QtCore.QRectF(8, 8, 48, 48)
#     p.setBrush(QtGui.QColor(37, 99, 235, 230))
#     p.setPen(QtCore.Qt.PenStyle.NoPen)
#     p.drawRoundedRect(rect, 12, 12)
#     p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
#     p.setFont(QtGui.QFont("Segoe UI", 16, QtGui.QFont.Weight.Bold))
#     p.drawText(pm.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "L2")
#     p.end()
#     return QtGui.QIcon(pm)
#
#
# def _html_escape(s: str) -> str:
#     return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
#
#
# def _resolve_icon(path_str: Optional[str]) -> QtGui.QIcon:
#     if not path_str:
#         return _make_default_icon()
#     p = Path(path_str)
#     if not p.is_absolute():
#         root = Path(__file__).resolve().parent.parent
#         p = (root / p).resolve()
#     if p.exists():
#         icon = QtGui.QIcon(str(p))
#         if not icon.isNull():
#             return icon
#     return _make_default_icon()
#
#
# def _normalize_hotkey(combo: str) -> str:
#     t = (combo or "").strip()
#     if not t:
#         return t
#     t = re.sub(r"\+space$", "+<space>", t, flags=re.IGNORECASE)
#     t = re.sub(r"\+<\s*space\s*>$", "+<space>", t, flags=re.IGNORECASE)
#     return t
#
#
# def _set_dot(label: QtWidgets.QLabel, level: str) -> None:
#     if level == "busy":
#         color = "#F59E0B"
#     elif level == "err":
#         color = "#EF4444"
#     else:
#         color = "#22C55E"
#
#     label.setStyleSheet(
#         "QLabel{background:%s; border-radius:6px; min-width:12px; min-height:12px;}" % color
#     )
#
#
# def _classify_status(text: str) -> str:
#     t = (text or "").lower()
#     if any(k in t for k in ["error", "fail", "timeout", "异常", "失败", "错误"]):
#         return "err"
#     if any(k in t for k in
#            ["think", "thinking", "listen", "listening", "connect", "voice", "speaking", "处理中", "思考"]):
#         return "busy"
#     return "ok"
#
#
# def _norm_text_for_wake(t: str) -> str:
#     t = (t or "").strip().lower()
#     t = re.sub(r"\s+", "", t)
#     t = re.sub(r"[，。！？!?,\.]", "", t)
#     return t
#
#
# def to_pinyin_str(t: str) -> str:
#     t = _norm_text_for_wake(t)
#     if not t:
#         return ""
#     return "".join(lazy_pinyin(t)) if lazy_pinyin else ""
#
#
# def levenshtein(a: str, b: str) -> int:
#     if a == b:
#         return 0
#     if not a:
#         return len(b)
#     if not b:
#         return len(a)
#     prev = list(range(len(b) + 1))
#     for i, ca in enumerate(a, 1):
#         cur = [i]
#         for j, cb in enumerate(b, 1):
#             ins = cur[j - 1] + 1
#             dele = prev[j] + 1
#             sub = prev[j - 1] + (0 if ca == cb else 1)
#             cur.append(min(ins, dele, sub))
#         prev = cur
#     return prev[-1]
#
#
# def wake_match_once(asr_text: str, keywords: list, max_edit: int = 1) -> bool:
#     raw = _norm_text_for_wake(asr_text)
#     if not raw:
#         return False
#
#     for kw in keywords:
#         kw_raw = _norm_text_for_wake(kw)
#         if kw_raw and (kw_raw in raw):
#             return True
#
#     text_py = to_pinyin_str(raw)
#     kw_pys = []
#     for kw in keywords:
#         kw_py = to_pinyin_str(kw)
#         if not kw_py:
#             continue
#         kw_pys.append(kw_py)
#         if kw_py in text_py:
#             return True
#
#     for kw_py in kw_pys:
#         if len(kw_py) <= 8:
#             if levenshtein(text_py, kw_py) <= max_edit:
#                 return True
#
#     return False
#
#
# @dataclass
# class QtGuiConfig:
#     title: str = "Live2D Agent"
#     start_minimized_to_tray: bool = False
#     compact_size: tuple = (600, 190)
#     full_size: tuple = (760, 560)
#     single_click_toggle: bool = True
#     double_click_full: bool = True
#     icon_path: Optional[str] = None
#
#
# class _Bridge(QtCore.QObject):
#     sig_append = QtCore.Signal(str, str)
#     sig_status = QtCore.Signal(str)
#     sig_focus_input = QtCore.Signal()
#     sig_set_wake = QtCore.Signal(bool)
#     sig_set_tts = QtCore.Signal(bool)
#     sig_toggle_gui = QtCore.Signal()
#     sig_toggle_wake = QtCore.Signal()
#     sig_send_text = QtCore.Signal(str)
#
#
# # -----------------------------------------------------------------------------
# # 🟢 1. 辅助类：可拖拽的悬浮球
# # -----------------------------------------------------------------------------
# class DraggableBall(QtWidgets.QPushButton):
#     """
#     自定义悬浮球按钮：
#     - 支持拖拽移动窗口
#     - 支持短按点击触发功能
#     - 屏蔽了普通 QPushButton 的鼠标事件以防止冲突
#     """
#
#     def __init__(self, parent=None, main_window=None):
#         super().__init__(parent)
#         self.main_window = main_window
#         self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
#         self._drag_start_pos = None
#         self._window_start_pos = None
#         self._is_dragging = False
#         self._press_time = 0
#
#     def mousePressEvent(self, event):
#         if event.button() == QtCore.Qt.MouseButton.LeftButton:
#             self._drag_start_pos = event.globalPosition().toPoint()
#             if self.main_window:
#                 self._window_start_pos = self.main_window.pos()
#             self._is_dragging = False
#             self._press_time = time.time()
#             self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
#         # 不调用 super().mousePressEvent 以免触发默认的样式变化干扰拖拽体验
#         # 但我们需要手动处理 setDown 状态来实现点击效果（如果需要的话）
#         self.setDown(True)
#
#     def mouseMoveEvent(self, event):
#         if self._drag_start_pos and self.main_window:
#             delta = event.globalPosition().toPoint() - self._drag_start_pos
#             # 只有移动超过 5 像素才视为拖拽
#             if not self._is_dragging and delta.manhattanLength() > 5:
#                 self._is_dragging = True
#
#             if self._is_dragging:
#                 self.main_window.move(self._window_start_pos + delta)
#                 return
#
#         super().mouseMoveEvent(event)
#
#     def mouseReleaseEvent(self, event):
#         self._drag_start_pos = None
#         self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
#         self.setDown(False)
#
#         # 计算是否是长按（超过 0.5秒）
#         is_long_press = (time.time() - self._press_time) > 0.5
#
#         # 如果是拖拽行为或长按，则消耗事件，不触发 clicked
#         if self._is_dragging or is_long_press:
#             self._is_dragging = False
#             event.ignore()
#         else:
#             # 短按，手动触发 clicked 信号
#             self.clicked.emit()
#
#
# # -----------------------------------------------------------------------------
# # 🟢 2. 主应用程序类
# # -----------------------------------------------------------------------------
# class QtChatTrayApp(QtCore.QObject):
#     def __init__(
#             self,
#             on_send_callback: Callable[[str], None],
#             *,
#             on_tts_toggle_callback: Optional[Callable[[bool], None]] = None,
#             on_costume_callback: Optional[Callable[[str, dict], None]] = None,
#             on_quit_callback: Optional[Callable[[], None]] = None,
#             plugin_manager: Optional[Any] = None,
#             cfg: Optional[QtGuiConfig] = None,
#
#     ):
#         super().__init__()
#         self.cfg = cfg or QtGuiConfig()
#         self.on_send_callback = on_send_callback
#         self.on_tts_toggle_callback = on_tts_toggle_callback
#         self.on_costume_callback = on_costume_callback
#         self.on_quit_callback = on_quit_callback
#         self.plugin_manager = plugin_manager
#
#
#         # --- 内部状态 ---
#         self._is_ball_mode = True  # 默认为悬浮球模式
#         self._last_panel_size = QtCore.QSize(600, 160)  # 记住面板大小
#
#         # --- 语音/ASR 状态 ---
#         self._mic_lock = threading.Lock()
#         self._wake_was_running_before_voice = False
#         self._wake_enabled = False
#         self._is_wake_listening = False
#         self._voice_once_running = False
#         self._awaiting_command = False
#         self._awaiting_deadline = 0.0
#         self._awaiting_timeout_sec = 4.0
#         self._asr_recognizer = None
#         self._hotkey_thread = None
#         self._hotkey_listener = None
#
#         # --- Qt 初始化 ---
#         self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
#         self._app.setQuitOnLastWindowClosed(False)
#         self._icon = _resolve_icon(self.cfg.icon_path or QT_ICON_PATH)
#
#         # --- 信号桥接 ---
#         self._bridge = _Bridge()
#         self._bridge.sig_append.connect(self._append_ui)
#         self._bridge.sig_status.connect(self._set_status_ui)
#         self._bridge.sig_focus_input.connect(self._focus_input_ui)
#         self._bridge.sig_set_wake.connect(self._set_wake_ui)
#         self._bridge.sig_set_tts.connect(self._set_tts_ui)
#         self._bridge.sig_toggle_gui.connect(self.toggle_show_hide)
#         self._bridge.sig_toggle_wake.connect(self._toggle_wake_from_hotkey)
#         self._bridge.sig_send_text.connect(self._send_text_from_asr)
#
#         # --- 构建 UI ---
#         self._win = self._build_window()
#         self._tray = self._build_tray()
#
#         self._memory_dialog = None
#         self._plugin_dialog = None
#
#         self._init_asr()
#         self._start_global_hotkeys()
#
#         if self.cfg.start_minimized_to_tray:
#             self.hide()
#         else:
#             # 默认启动显示悬浮球
#             self._switch_to_ball()
#
#             # 🔴 新增：第一次启动时居中显示
#             try:
#                 screen_geo = self._app.primaryScreen().geometry()
#                 win_geo = self._win.geometry()
#                 # 计算居中坐标
#                 cx = (screen_geo.width() - win_geo.width()) // 2
#                 cy = (screen_geo.height() - win_geo.height()) // 2
#                 self._win.move(cx, cy)
#             except Exception as e:
#                 print(f"Center window failed: {e}")
#
#             self._win.show()
#
#         self.set_status("Idle")
#
#     # ... Public API ...
#     def run(self) -> int:
#         return self._app.exec()
#
#     def append(self, role: str, text: str) -> None:
#         self._bridge.sig_append.emit(role, text)
#
#     def set_status(self, text: str) -> None:
#         self._bridge.sig_status.emit(text)
#
#     def set_wake(self, enabled: bool) -> None:
#         self._bridge.sig_set_wake.emit(bool(enabled))
#
#     def set_tts(self, enabled: bool) -> None:
#         self._bridge.sig_set_tts.emit(bool(enabled))
#
#     def show_compact(self) -> None:
#         self._switch_to_panel()
#         self._win.show()
#         self._win.raise_()
#         self._win.activateWindow()
#
#     def show_full(self) -> None:
#         self._switch_to_panel()
#         self._toggle_full(force_expand=True)
#         self._win.show()
#
#     def hide(self) -> None:
#         self._win.hide()
#
#     def toggle_show_hide(self) -> None:
#         if self._win.isVisible():
#             self._win.hide()
#         else:
#             # 🔴 修改：直接 show 即可，不要调用 _switch_to_... 函数
#             # 这些函数包含相对位移逻辑，重复调用会导致窗口移位
#             self._win.show()
#             self._win.raise_()
#             self._win.activateWindow()
#
#     # ---------- UI 构建核心 logic ----------
#     def _build_window(self) -> QtWidgets.QWidget:
#         w = QtWidgets.QWidget()
#         w.setWindowTitle(self.cfg.title)
#         w.setWindowIcon(self._icon)
#
#         # 核心窗口属性：无边框、置顶、工具窗口、背景透明
#         w.setWindowFlags(
#             QtCore.Qt.WindowType.WindowStaysOnTopHint |
#             QtCore.Qt.WindowType.Tool |
#             QtCore.Qt.WindowType.FramelessWindowHint
#         )
#         w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
#         w.setObjectName("main_window")
#
#         # 使用 StackedLayout 管理双模式
#         self._stack = QtWidgets.QStackedLayout(w)
#         self._stack.setContentsMargins(0, 0, 0, 0)
#
#         # ==========================
#         # 模式 1: 悬浮球 (Ball)
#         # ==========================
#         self._ball_widget = QtWidgets.QWidget()
#         ball_layout = QtWidgets.QVBoxLayout(self._ball_widget)
#         ball_layout.setContentsMargins(5, 5, 5, 5)  # 预留阴影空间
#
#         self._ball_btn = DraggableBall(main_window=w)
#         self._ball_btn.setObjectName("ball_btn")
#
#         # 安全获取配置
#         try:
#             from config import BALL_CONFIG
#         except ImportError:
#             BALL_CONFIG = {}
#
#         ball_size = BALL_CONFIG.get("size", 60)
#         self._ball_btn.setFixedSize(ball_size, ball_size)
#
#         # 点击切换
#         self._ball_btn.clicked.connect(self._switch_to_panel)
#
#         # 悬浮球阴影
#         ball_shadow = QtWidgets.QGraphicsDropShadowEffect(self._ball_btn)
#         ball_shadow.setBlurRadius(15)
#         ball_shadow.setColor(QtGui.QColor(0, 0, 0, 80))
#         ball_shadow.setOffset(0, 3)
#         self._ball_btn.setGraphicsEffect(ball_shadow)
#
#         ball_layout.addWidget(self._ball_btn)
#         ball_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
#
#         self._stack.addWidget(self._ball_widget)
#
#         # ==========================
#         # 模式 2: 控制面板 (Panel)
#         # ==========================
#         self._panel_widget = QtWidgets.QWidget()
#         # 外部布局处理阴影边距
#         panel_outer_layout = QtWidgets.QVBoxLayout(self._panel_widget)
#         # 这里给10px边距，防止阴影被切
#         panel_outer_layout.setContentsMargins(10, 10, 10, 10)
#
#         # 白色圆角容器
#         self._round_container = QtWidgets.QFrame()
#         self._round_container.setObjectName("container")
#
#         # 面板阴影
#         panel_shadow = QtWidgets.QGraphicsDropShadowEffect(self._round_container)
#         panel_shadow.setBlurRadius(20)
#         panel_shadow.setOffset(0, 5)
#         panel_shadow.setColor(QtGui.QColor(0, 0, 0, 50))
#         self._round_container.setGraphicsEffect(panel_shadow)
#
#         panel_outer_layout.addWidget(self._round_container)
#
#         # 面板内部布局
#         main_layout = QtWidgets.QVBoxLayout(self._round_container)
#         main_layout.setContentsMargins(15, 12, 15, 15)
#         main_layout.setSpacing(10)
#
#         # --- 顶部栏 ---
#         top_bar = QtWidgets.QHBoxLayout()
#         top_bar.setContentsMargins(2, 0, 2, 0)
#
#         self._dot = QtWidgets.QLabel("")
#         self._dot.setFixedSize(8, 8)
#         _set_dot(self._dot, "ok")
#         top_bar.addWidget(self._dot)
#
#         self._lbl_status = QtWidgets.QLabel("Ready")
#         self._lbl_status.setObjectName("status_label")
#         top_bar.addWidget(self._lbl_status)
#
#         top_bar.addStretch()
#
#         # 最小化回悬浮球
#         btn_shrink = QtWidgets.QPushButton("─")
#         btn_shrink.setObjectName("win_control_btn")
#         btn_shrink.setToolTip("缩回悬浮球")
#         btn_shrink.clicked.connect(self._switch_to_ball)
#         top_bar.addWidget(btn_shrink)
#
#         # 关闭/隐藏
#         btn_close = QtWidgets.QPushButton("✕")
#         btn_close.setObjectName("win_control_btn")
#         btn_close.setToolTip("隐藏到托盘")
#         btn_close.clicked.connect(self.hide)
#         top_bar.addWidget(btn_close)
#
#         main_layout.addLayout(top_bar)
#
#         # --- 历史记录 ---
#         self._history = QtWidgets.QTextEdit()
#         self._history.setReadOnly(True)
#         self._history.setObjectName("history_box")
#         self._history.setVisible(False)
#         main_layout.addWidget(self._history, 1)
#
#         # --- 输入区 ---
#         input_container = QtWidgets.QFrame()
#         input_container.setObjectName("input_container")
#         input_row = QtWidgets.QHBoxLayout(input_container)
#         input_row.setContentsMargins(5, 5, 5, 5)
#         input_row.setSpacing(5)
#
#         self._input = QtWidgets.QLineEdit()
#         self._input.setPlaceholderText("发送指令...")
#         self._input.returnPressed.connect(self._on_send_clicked)
#
#         self._btn_send = QtWidgets.QPushButton("➤")
#         self._btn_send.setObjectName("send_btn")
#         self._btn_send.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         self._btn_send.clicked.connect(self._on_send_clicked)
#
#         input_row.addWidget(self._input, 1)
#         input_row.addWidget(self._btn_send)
#         main_layout.addWidget(input_container)
#
#         # --- 工具栏 ---
#         tools_layout = QtWidgets.QHBoxLayout()
#         tools_layout.setSpacing(8)
#         tools_layout.setContentsMargins(2, 5, 2, 0)
#
#         def create_tool_btn(text, callback=None, checkable=False, tooltip=""):
#             btn = QtWidgets.QPushButton(text)
#             btn.setObjectName("tool_btn")
#             btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#             btn.setToolTip(tooltip)
#             if checkable:
#                 btn.setCheckable(True)
#                 if callback:
#                     btn.clicked.connect(callback)
#             elif callback:
#                 btn.clicked.connect(callback)
#             return btn
#
#         self._btn_voice = create_tool_btn("🎙️", self._on_voice_once_clicked, tooltip="语音输入")
#         self._chk_wake = create_tool_btn("🔔", self._on_wake_toggle, checkable=True, tooltip="唤醒监听")
#         self._chk_tts = create_tool_btn("🔊", self._on_tts_toggle, checkable=True, tooltip="语音回复")
#         self._chk_tts.setChecked(True)
#         self._btn_costume = create_tool_btn("👕", self._show_costume_menu, tooltip="换装")
#         self._btn_memory = create_tool_btn("🧠", self._on_memory_clicked, tooltip="记忆库")
#         self._btn_plugin = create_tool_btn("🔌", self._on_plugin_clicked, tooltip="插件管理")
#         self._btn_full = create_tool_btn("⌄", self._toggle_full, tooltip="展开/收起日志")
#         self._btn_full.setObjectName("expand_btn")
#
#         tools_layout.addWidget(self._btn_voice)
#         tools_layout.addWidget(self._chk_wake)
#         tools_layout.addWidget(self._chk_tts)
#         tools_layout.addStretch()
#         tools_layout.addWidget(self._btn_costume)
#         tools_layout.addWidget(self._btn_memory)
#         tools_layout.addWidget(self._btn_plugin)
#         tools_layout.addWidget(self._btn_full)
#
#         main_layout.addLayout(tools_layout)
#
#         # --- 🔴 调整大小手柄 (SizeGrip) ---
#         self._size_grip = QtWidgets.QSizeGrip(self._round_container)
#         self._size_grip.setStyleSheet("background: transparent; width: 20px; height: 20px;")
#
#         self._stack.addWidget(self._panel_widget)
#
#         # 应用样式
#         self._apply_styles(w)
#
#         # 初始化面板拖拽 (仅针对面板模式的空白区)
#         self._enable_drag(w)
#
#         w.closeEvent = self._on_close_event
#
#         # 重写 resizeEvent 以固定 SizeGrip 位置
#         original_resize = self._round_container.resizeEvent
#
#         def container_resize(event):
#             if original_resize: original_resize(event)
#             rect = self._round_container.rect()
#             self._size_grip.move(rect.right() - 20, rect.bottom() - 20)
#
#         self._round_container.resizeEvent = container_resize
#
#         return w
#
#     def _switch_to_ball(self):
#         """切换到悬浮球模式（仅在从面板切换过来时移动）"""
#         # 如果已经是悬浮球模式，只确保显示和尺寸，不移动位置
#         if self._is_ball_mode:
#             self._stack.setCurrentIndex(0)
#             # 确保球体大小正确
#             try:
#                 from config import BALL_CONFIG
#             except ImportError:
#                 BALL_CONFIG = {}
#             s = BALL_CONFIG.get("size", 60)
#             self._win.resize(s + 10, s + 10)
#             return
#
#         # --- 以下是正常的切换逻辑 ---
#
#         # 1. 记录当前面板的位置作为锚点
#         current_geo = self._win.geometry()
#         anchor_point = current_geo.topLeft()
#
#         # 记录面板大小
#         if not getattr(self._win, "_is_full_mode", False):
#             self._last_panel_size = self._win.size()
#
#         self._is_ball_mode = True
#         self._stack.setCurrentIndex(0)
#
#         # 获取球体配置
#         try:
#             from config import BALL_CONFIG
#         except ImportError:
#             BALL_CONFIG = {}
#         s = BALL_CONFIG.get("size", 60)
#
#         self._win.resize(s + 10, s + 10)
#
#         # 只有从面板变球时，才进行偏移 (向右)
#         new_x = anchor_point.x() + OFFSET_X
#         new_y = anchor_point.y()
#         self._win.move(new_x, new_y)
#
#     def _switch_to_panel(self):
#         """切换到面板模式（仅在从悬浮球切换过来时移动）"""
#         # 如果已经是面板模式，只确保显示，不移动位置
#         if not self._is_ball_mode:
#             self._stack.setCurrentIndex(1)
#             # 可以在这里确保大小，但不要移动
#             return
#
#         # --- 以下是正常的切换逻辑 ---
#
#         # 1. 获取当前球的左上角
#         current_geo = self._win.geometry()
#         anchor_point = current_geo.topLeft()
#
#         self._is_ball_mode = False
#         self._stack.setCurrentIndex(1)
#
#         # 恢复上次的尺寸
#         target_size = self._last_panel_size
#         # 这里的硬性限制建议保留，防止太小，但要确保 target_size 是宽面板
#         if target_size.width() < 300: target_size.setWidth(600)
#         if target_size.height() < 100: target_size.setHeight(160)
#
#         # 如果之前是全屏模式，强制重置状态，避免错乱
#         if getattr(self._win, "_is_full_mode", False):
#             self._win._is_full_mode = False
#             self._history.setVisible(False)
#             self._btn_full.setText("⌄")
#             # 这里的 target_size.height() 已经是之前存下的紧凑高度了
#
#         self._win.resize(target_size)
#
#         # 只有从球变面板时，才进行偏移 (向左)
#         new_x = anchor_point.x() - OFFSET_X
#         new_y = anchor_point.y()
#         self._win.move(new_x, new_y)
#
#     def _apply_styles(self, w: QtWidgets.QWidget):
#         # 安全获取配置
#         try:
#             from config import BALL_CONFIG
#         except ImportError:
#             BALL_CONFIG = {}
#
#         bg_color = BALL_CONFIG.get("bg_color", "#3B82F6")
#         text_color = BALL_CONFIG.get("text_color", "white")
#         font_size = BALL_CONFIG.get("font_size", 14)
#         ball_size = BALL_CONFIG.get("size", 60)
#         radius = ball_size // 2
#
#         # 处理图片背景
#         image_style = ""
#         enable_image = BALL_CONFIG.get("enable_image", False)
#         img_path = BALL_CONFIG.get("image_path", "")
#
#         if enable_image and img_path:
#             p = Path(img_path)
#             if not p.is_absolute():
#                 root = Path(__file__).resolve().parent.parent
#                 p = (root / p).resolve()
#
#             clean_path = str(p).replace("\\", "/")
#             if p.exists():
#                 image_style = f"border-image: url({clean_path});"
#                 self._ball_btn.setText("")
#             else:
#                 self._ball_btn.setText(BALL_CONFIG.get("text", "L2D"))
#         else:
#             self._ball_btn.setText(BALL_CONFIG.get("text", "L2D"))
#
#         w.setStyleSheet(f"""
#             /* --- 悬浮球样式 (动态) --- */
#             QPushButton#ball_btn {{
#                 background-color: {bg_color};
#                 color: {text_color};
#                 border-radius: {radius}px;
#                 border: 2px solid white;
#                 font-weight: bold;
#                 font-size: {font_size}px;
#                 font-family: 'Segoe UI Black', 'Microsoft YaHei';
#                 {image_style}
#             }}
#             QPushButton#ball_btn:hover {{
#                 border: 2px solid #DBEAFE;
#                 background-color: {bg_color};
#                 margin-top: -2px;
#             }}
#             QPushButton#ball_btn:pressed {{
#                 margin-top: 0px;
#                 border-color: #93C5FD;
#             }}
#
#             /* --- 面板样式 --- */
#             QFrame#container {{
#                 background-color: #FFFFFF;
#                 border-radius: 16px;
#                 border: 1px solid #F3F4F6;
#             }}
#
#             /* 状态文字 */
#             QLabel#status_label {{
#                 color: #9CA3AF;
#                 font-size: 10px;
#                 font-family: 'Segoe UI', sans-serif;
#                 font-weight: 600;
#                 margin-left: 4px;
#             }}
#
#             QPushButton#win_control_btn {{
#                 background: transparent;
#                 color: #D1D5DB;
#                 border: none;
#                 font-weight: bold;
#                 font-size: 14px;
#                 width: 20px;
#                 height: 20px;
#             }}
#             QPushButton#win_control_btn:hover {{
#                 color: #EF4444;
#             }}
#
#             QTextEdit#history_box {{
#                 background-color: #111827;
#                 border: none;
#                 border-radius: 8px;
#                 color: #10B981;
#                 font-family: 'Consolas', monospace;
#                 font-size: 11px;
#                 padding: 8px;
#                 margin-bottom: 5px;
#             }}
#
#             QFrame#input_container {{
#                 background-color: #F3F4F6;
#                 border-radius: 20px;
#                 border: 1px solid transparent;
#             }}
#             QFrame#input_container:hover {{
#                 background-color: #FFFFFF;
#                 border: 1px solid #E5E7EB;
#             }}
#
#             QLineEdit {{
#                 background: transparent;
#                 border: none;
#                 color: #1F2937;
#                 font-size: 13px;
#                 padding: 0 8px;
#             }}
#
#             QPushButton#send_btn {{
#                 background-color: #3B82F6;
#                 color: white;
#                 border-radius: 15px;
#                 font-weight: bold;
#                 font-size: 12px;
#                 width: 30px;
#                 height: 30px;
#                 border: none;
#             }}
#             QPushButton#send_btn:hover {{
#                 background-color: #2563EB;
#             }}
#
#             QPushButton#tool_btn {{
#                 background-color: transparent;
#                 color: #6B7280;
#                 border: 1px solid transparent;
#                 border-radius: 8px;
#                 padding: 4px;
#                 font-size: 16px;
#                 width: 28px;
#                 height: 28px;
#             }}
#             QPushButton#tool_btn:hover {{
#                 background-color: #F3F4F6;
#                 color: #374151;
#             }}
#             QPushButton#tool_btn:checked {{
#                 background-color: #EFF6FF;
#                 color: #3B82F6;
#                 border: 1px solid #DBEAFE;
#             }}
#
#             QMenu {{
#                 background-color: #FFFFFF;
#                 border: 1px solid #E5E7EB;
#                 border-radius: 8px;
#                 padding: 4px;
#             }}
#             QMenu::item {{
#                 padding: 6px 20px;
#                 border-radius: 4px;
#                 color: #374151;
#             }}
#             QMenu::item:selected {{
#                 background-color: #EFF6FF;
#                 color: #2563EB;
#             }}
#
#             QSizeGrip {{
#                 background: transparent;
#                 width: 20px;
#                 height: 20px;
#             }}
#         """)
#
#     def _enable_drag(self, widget):
#         """让无边框窗口可以被拖拽移动，并支持边缘缩放"""
#         MARGIN = 10  # 边缘检测距离
#
#         def _get_region(pos, size):
#             x, y = pos.x(), pos.y()
#             w, h = size.width(), size.height()
#             l, r = x < MARGIN, x > w - MARGIN
#             t, b = y < MARGIN, y > h - MARGIN
#             if t and l: return "top_left"
#             if t and r: return "top_right"
#             if b and l: return "bottom_left"
#             if b and r: return "bottom_right"
#             if t: return "top"
#             if b: return "bottom"
#             if l: return "left"
#             if r: return "right"
#             return None
#
#         def _set_cursor(region):
#             if not region:
#                 widget.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
#             elif region in ["top_left", "bottom_right"]:
#                 widget.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
#             elif region in ["top_right", "bottom_left"]:
#                 widget.setCursor(QtCore.Qt.CursorShape.SizeBDiagCursor)
#             elif region in ["top", "bottom"]:
#                 widget.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
#             elif region in ["left", "right"]:
#                 widget.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
#
#         def mousePressEvent(event):
#             if self._is_ball_mode:
#                 event.ignore()
#                 return
#             if event.button() == QtCore.Qt.MouseButton.LeftButton:
#                 region = _get_region(event.position().toPoint(), widget.size())
#                 if region:
#                     # 记录调整大小的状态
#                     widget._resize_region = region
#                     widget._resize_start_pos = event.globalPosition().toPoint()
#                     widget._resize_start_geo = widget.geometry()
#                     event.accept()
#                 else:
#                     # 记录移动窗口的状态
#                     widget._drag_pos = event.globalPosition().toPoint() - widget.frameGeometry().topLeft()
#                     event.accept()
#
#         def mouseMoveEvent(event):
#             if self._is_ball_mode:
#                 event.ignore()
#                 return
#
#             # 1. 处理调整大小 (Resize)
#             if getattr(widget, '_resize_region', None) and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
#                 delta = event.globalPosition().toPoint() - widget._resize_start_pos
#                 geo = widget._resize_start_geo
#                 reg = widget._resize_region
#                 x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
#                 MIN_W, MIN_H = 300, 100
#
#                 if "right" in reg: w = max(MIN_W, geo.width() + delta.x())
#                 if "bottom" in reg: h = max(MIN_H, geo.height() + delta.y())
#                 if "left" in reg:
#                     new_w = max(MIN_W, geo.width() - delta.x())
#                     x = geo.right() - new_w
#                     w = new_w
#                 if "top" in reg:
#                     new_h = max(MIN_H, geo.height() - delta.y())
#                     y = geo.bottom() - new_h
#                     h = new_h
#
#                 widget.setGeometry(x, y, w, h)
#
#                 # 🔴 核心修复：只有在【非展开】模式下，才记录用户喜欢的大小
#                 # 这样你就不会把“展开后的高度”误存为默认高度了
#                 if not getattr(self, "_is_full_mode", False):
#                     self._last_panel_size = widget.size()
#
#                 event.accept()
#                 return
#
#             # 2. 处理移动窗口 (Move)
#             if hasattr(widget, '_drag_pos') and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
#                 widget.move(event.globalPosition().toPoint() - widget._drag_pos)
#                 event.accept()
#                 return
#
#             # 3. 更新鼠标光标
#             _set_cursor(_get_region(event.position().toPoint(), widget.size()))
#
#
#         def mouseReleaseEvent(event):
#             if hasattr(widget, '_resize_region'):
#                 del widget._resize_region
#                 del widget._resize_start_pos
#                 del widget._resize_start_geo
#                 widget.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
#             if hasattr(widget, '_drag_pos'):
#                 del widget._drag_pos
#
#         widget.setMouseTracking(True)
#         widget.mousePressEvent = mousePressEvent
#         widget.mouseMoveEvent = mouseMoveEvent
#         widget.mouseReleaseEvent = mouseReleaseEvent
#
#     # --- 辅助功能 ---
#     def _show_costume_menu(self):
#         if not COSTUME_MAP:
#             self.append("system", "⚠️ 未在 config.py 中配置 COSTUME_MAP")
#             return
#
#         menu = QtWidgets.QMenu(self._win)
#         for name, cfg in COSTUME_MAP.items():
#             action = menu.addAction(name)
#             action.triggered.connect(lambda checked=False, n=name, c=cfg: self._on_costume_triggered(n, c))
#
#         pos = self._btn_costume.mapToGlobal(QtCore.QPoint(0, 0))
#         menu.exec(QtCore.QPoint(pos.x(), pos.y() - menu.sizeHint().height()))
#
#     def _on_costume_triggered(self, name: str, cfg: dict):
#         path = cfg.get("path")
#         if not path:
#             self.append("system", f"❌ 服装 [{name}] 缺少 path 配置")
#             return
#
#         self.append("system", f"👗 正在切换: {name}")
#         model_config = {k: v for k, v in cfg.items() if k != "path"}
#
#         if self.on_costume_callback:
#             self.on_costume_callback(path, model_config)
#
#     def _build_tray(self) -> QtWidgets.QSystemTrayIcon:
#         tray = QtWidgets.QSystemTrayIcon(self._icon)
#         tray.setToolTip(self.cfg.title)
#         menu = QtWidgets.QMenu()
#         act_ball = menu.addAction("显示悬浮球")
#         act_ball.triggered.connect(self._switch_to_ball)
#         act_panel = menu.addAction("显示面板")
#         act_panel.triggered.connect(self._switch_to_panel)
#         menu.addSeparator()
#         act_quit = menu.addAction("退出")
#         act_quit.triggered.connect(self._quit)
#         tray.setContextMenu(menu)
#         tray.activated.connect(self._on_tray_activated)
#         tray.show()
#         return tray
#
#     def _start_global_hotkeys(self) -> None:
#         if pynput_keyboard is None:
#             self.append("system", "⚠️ 未安装 pynput：全局快捷键不可用。")
#             return
#         combo_gui = _normalize_hotkey(HOTKEY_TOGGLE_GUI or "<ctrl>+<alt>+<space>")
#         combo_wake = _normalize_hotkey(HOTKEY_TOGGLE_WAKE or "<ctrl>+<alt>+w")
#
#         def _toggle_gui():
#             try:
#                 self._bridge.sig_toggle_gui.emit()
#             except Exception:
#                 pass
#
#         def _toggle_wake():
#             try:
#                 self._bridge.sig_toggle_wake.emit()
#             except Exception:
#                 pass
#
#         def _run():
#             try:
#                 mapping = {combo_gui: _toggle_gui, combo_wake: _toggle_wake}
#                 with pynput_keyboard.GlobalHotKeys(mapping) as listener:
#                     self._hotkey_listener = listener
#                     listener.join()
#             except Exception as e:
#                 self.append("system", f"⚠️ 热键监听失败: {e}")
#                 self.set_status("Error: hotkey failed")
#
#         self._hotkey_thread = threading.Thread(target=_run, daemon=True)
#         self._hotkey_thread.start()
#         self.append("system", f"✅ 全局热键：显示/隐藏={combo_gui}；切换唤醒={combo_wake}")
#
#     def _toggle_wake_from_hotkey(self) -> None:
#         try:
#             self._chk_wake.animateClick()
#         except Exception:
#             pass
#
#     def _on_send_clicked(self) -> None:
#         text = (self._input.text() or "").strip()
#         if not text:
#             return
#         self._input.clear()
#         self.append("user", text)
#         try:
#             self.on_send_callback(text)
#         except Exception as e:
#             self.append("system", f"❌ 发送失败: {e}")
#             self.set_status("Error: send failed")
#
#     def _toggle_full(self, force_expand=False) -> None:
#         is_full = getattr(self._win, "_is_full_mode", False)
#
#         if not force_expand and is_full:
#             # ==============================
#             # 🔽 收起日志 (Collapse)
#             # ==============================
#             self._win._is_full_mode = False
#             self._history.setVisible(False)
#             self._btn_full.setText("⌄")
#
#             # 获取当前宽度（保持用户调整后的宽度）
#             current_width = self._win.width()
#
#             # 获取要恢复的高度：
#             # 1. 优先使用展开前保存的高度 (_saved_compact_height)
#             # 2. 如果没有保存，使用配置中的默认高度 (cfg.compact_size[1])
#             # 3. 如果配置也没有，保底使用 160
#             default_h = self.cfg.compact_size[1] if self.cfg else 160
#             restore_h = getattr(self, "_saved_compact_height", default_h)
#
#             # 🔴 关键修改：已删除 "if restore_h > 250: restore_h = 160" 限制
#             # 只要高度合理（比如大于100），就完全信任之前保存的值
#             if restore_h < 100:
#                 restore_h = 160
#
#             self._win.resize(current_width, restore_h)
#
#         else:
#             # ==============================
#             # 🔼 展开日志 (Expand)
#             # ==============================
#             if not is_full:
#                 # 关键：在展开前，立刻保存当前的“紧凑模式”高度
#                 # 这样下次收起时，就能恢复成现在这个样子
#                 self._saved_compact_height = self._win.height()
#
#             self._win._is_full_mode = True
#             self._history.setVisible(True)
#             self._btn_full.setText("⌃")
#
#             # 展开时的高度
#             current_width = self._win.width()
#
#             # 优先使用配置的全尺寸高度，默认 450
#             target_height = 450
#             if self.cfg and hasattr(self.cfg, 'full_size'):
#                 target_height = self.cfg.full_size[1]
#
#             self._win.resize(current_width, target_height)
#
#     def _on_close_event(self, event: QtGui.QCloseEvent) -> None:
#         event.ignore()
#         self.hide()
#
#     def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
#         if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
#             if self.cfg.single_click_toggle:
#                 self.toggle_show_hide()
#         elif reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
#             if self.cfg.double_click_full:
#                 self.show_full()
#
#     def _quit(self) -> None:
#         try:
#             self._stop_wake_listening()
#             self._stop_voice_once(send_partial=False)
#             try:
#                 if self._hotkey_listener is not None:
#                     self._hotkey_listener.stop()
#             except Exception:
#                 pass
#             if self.on_quit_callback:
#                 self.on_quit_callback()
#         finally:
#             self._tray.hide()
#             self._app.quit()
#
#     @QtCore.Slot(str, str)
#     def _append_ui(self, role: str, text: str) -> None:
#         role = (role or "system").strip()
#         text = (text or "").strip()
#         if not text:
#             return
#         prefix = {"user": ">>>", "assistant": "AI:", "system": "[SYS]"}.get(role, role)
#         self._history.append(f"{prefix} {text}")
#         self._history.moveCursor(QtGui.QTextCursor.MoveOperation.End)
#
#     @QtCore.Slot(str)
#     def _set_status_ui(self, text: str) -> None:
#         self._lbl_status.setText(text or "")
#         _set_dot(self._dot, _classify_status(text))
#
#     @QtCore.Slot()
#     def _focus_input_ui(self) -> None:
#         try:
#             if not self._is_ball_mode:
#                 self._input.setFocus()
#         except Exception:
#             pass
#
#     @QtCore.Slot(bool)
#     def _set_wake_ui(self, enabled: bool) -> None:
#         self._chk_wake.setChecked(bool(enabled))
#
#     @QtCore.Slot(bool)
#     def _set_tts_ui(self, enabled: bool) -> None:
#         self._chk_tts.setChecked(bool(enabled))
#
#     def _on_tts_toggle(self) -> None:
#         enabled = self._chk_tts.isChecked()
#         if self.on_tts_toggle_callback:
#             try:
#                 self.on_tts_toggle_callback(bool(enabled))
#             except Exception as e:
#                 self.append("system", f"⚠️ TTS 回调失败: {e}")
#
#     def _on_memory_clicked(self) -> None:
#         try:
#             from modules.memory_editor_qt import MemoryEditorDialog
#         except Exception as e:
#             self.append("system", f"❌ 记忆编辑器加载失败: {e}")
#             self.set_status("Error: memory editor load failed")
#             return
#
#         try:
#             if self._memory_dialog is None:
#                 self._memory_dialog = MemoryEditorDialog(parent=self._win)
#             self._memory_dialog.show()
#             self._memory_dialog.raise_()
#             self._memory_dialog.activateWindow()
#         except Exception as e:
#             self.append("system", f"❌ 打开记忆编辑器失败: {e}")
#             self.set_status("Error: open memory editor failed")
#
#     def _on_plugin_clicked(self) -> None:
#         if not self.plugin_manager:
#             self.append("system", "⚠️ 插件管理器未初始化")
#             return
#
#         try:
#             if self._plugin_dialog is None:
#                 self._plugin_dialog = PluginManagerDialog(parent=self._win, plugin_manager=self.plugin_manager,
#                                                           main_app=self)
#             self._plugin_dialog.show()
#             self._plugin_dialog.raise_()
#             self._plugin_dialog.activateWindow()
#         except Exception as e:
#             self.append("system", f"❌ 打开插件管理失败: {e}")
#             self.set_status("Error: open plugin manager failed")
#
#     def _filter_asr_text(self, text: str) -> Optional[str]:
#         t = (text or "").strip()
#         if not t:
#             return None
#         t2 = re.sub(r"[\s\r\n]+", "", t)
#         t2 = re.sub(r"[，。！？!?,\.]+$", "", t2)
#         if len(t2) < int(ASR_MIN_CHARS):
#             return None
#         for bad in (ASR_BLACKLIST or []):
#             if bad and t2 == str(bad).strip():
#                 return None
#         return t.strip()
#
#     def _on_wake_toggle(self) -> None:
#         enabled = self._chk_wake.isChecked()
#         self._wake_enabled = bool(enabled)
#         if enabled:
#             if self._asr_recognizer is None:
#                 self.append("system", "⚠️ 唤醒不可用：ASR 模型/依赖未就绪。")
#                 self._chk_wake.setChecked(False)
#                 return
#             self._start_wake_listening()
#         else:
#             self._stop_wake_listening()
#             self.set_status("Idle")
#
#     def _on_voice_once_clicked(self) -> None:
#         if self._voice_once_running:
#             self._stop_voice_once(send_partial=True)
#             return
#         if self._asr_recognizer is None:
#             self.append("system", "⚠️ 语音输入不可用：ASR 模型/依赖未就绪。")
#             return
#         if self._is_wake_listening:
#             self._wake_was_running_before_voice = True
#             self._stop_wake_listening()
#         else:
#             self._wake_was_running_before_voice = False
#         self._start_voice_once(send_on_stop=True)
#
#     def _start_voice_once(self, *, send_on_stop: bool) -> None:
#         if self._voice_once_running:
#             return
#         if not self._mic_lock.acquire(blocking=False):
#             self.append("system", "🎙️ 麦克风忙：请先停止唤醒/其他录音。")
#             if self._wake_enabled and self._wake_was_running_before_voice:
#                 self._start_wake_listening()
#             return
#         self._voice_once_running = True
#         self._voice_once_send_on_stop = bool(send_on_stop)
#         self._voice_once_last_text = ""
#         self._btn_voice.setStyleSheet(
#             "QPushButton#tool_btn { color: #EF4444; border: 1px solid #FECACA; background-color: #FEF2F2; }")
#         self.set_status("Voice Input...")
#         threading.Thread(target=self._listen_once_loop, daemon=True).start()
#
#     def _stop_voice_once(self, *, send_partial: bool = False) -> None:
#         if send_partial:
#             self._voice_once_send_on_stop = True
#         self._voice_once_running = False
#         self._btn_voice.setStyleSheet("")
#
#     @QtCore.Slot(str)
#     def _send_text_from_asr(self, text: str) -> None:
#         text = (text or "").strip()
#         if not text:
#             return
#         filtered = self._filter_asr_text(text)
#         if not filtered:
#             self.append("system", "（语音内容过短/无效，已忽略）")
#             return
#         self.append("system", f"🎙️ 识别：{filtered}")
#         try:
#             self.on_send_callback(filtered)
#         except Exception as e:
#             self.append("system", f"❌ 发送失败: {e}")
#             self.set_status("Error: send failed")
#
#     def _listen_once_loop(self) -> None:
#         stream = self._asr_recognizer.create_stream()
#         SAMPLE_RATE = 16000
#         CHUNK_SIZE = 4800
#         DEVICE_ID = None
#         GAIN_FACTOR = 1.0
#         SILENCE_TIMEOUT = 0.9
#         last_active_time = time.time()
#         has_speech = False
#         last_text = ""
#         try:
#             with sd.InputStream(device=DEVICE_ID, channels=1, dtype="float32", samplerate=SAMPLE_RATE) as s:
#                 calib_secs = 0.6
#                 calib_chunks = max(1, int((SAMPLE_RATE * calib_secs) / CHUNK_SIZE))
#                 noise_rms = []
#                 for _ in range(calib_chunks):
#                     if not self._voice_once_running:
#                         return
#                     data, _ = s.read(CHUNK_SIZE)
#                     x = data.reshape(-1) * GAIN_FACTOR
#                     rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
#                     noise_rms.append(rms)
#                 base = float(np.median(noise_rms)) if noise_rms else 0.01
#                 NOISE_THRESHOLD = max(0.02, base * 3.0)
#                 while True:
#                     data, _ = s.read(CHUNK_SIZE)
#                     if not self._voice_once_running:
#                         final_text = (self._voice_once_last_text or last_text or "").strip()
#                         if self._voice_once_send_on_stop and final_text:
#                             self._bridge.sig_send_text.emit(final_text)
#                         break
#                     x = data.reshape(-1) * GAIN_FACTOR
#                     current_rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
#                     if current_rms > NOISE_THRESHOLD:
#                         last_active_time = time.time()
#                         has_speech = True
#                     stream.accept_waveform(SAMPLE_RATE, x)
#                     while self._asr_recognizer.is_ready(stream):
#                         self._asr_recognizer.decode_stream(stream)
#                     partial_text = (self._asr_recognizer.get_result(stream) or "").strip()
#                     if partial_text:
#                         last_text = partial_text
#                         self._voice_once_last_text = partial_text
#                     silence_duration = time.time() - last_active_time
#                     if has_speech and silence_duration > SILENCE_TIMEOUT:
#                         final_text = (last_text or "").strip()
#                         self._asr_recognizer.reset(stream)
#                         if final_text:
#                             self._bridge.sig_send_text.emit(final_text)
#                         break
#         except Exception as e:
#             self.append("system", f"⚠️ 语音输入异常: {e}")
#             self.set_status("Error: voice input")
#         finally:
#             self._voice_once_running = False
#             self._voice_once_send_on_stop = False
#             try:
#                 if self._mic_lock.locked():
#                     self._mic_lock.release()
#             except Exception:
#                 pass
#             QtCore.QTimer.singleShot(0, lambda: self._btn_voice.setStyleSheet(""))
#             QtCore.QTimer.singleShot(0, lambda: self.set_status("Idle"))
#             if self._wake_enabled and self._wake_was_running_before_voice:
#                 self._wake_was_running_before_voice = False
#                 self._start_wake_listening()
#
#     def _init_asr(self) -> None:
#         if not _ASR_DEPS_OK:
#             self._chk_wake.setEnabled(False)
#             self._btn_voice.setEnabled(False)
#             self.append("system", "⚠️ 未安装 ASR 依赖，唤醒/语音输入已禁用。")
#             self.set_status("Error: ASR deps missing")
#             return
#         try:
#             model_dir = "./sherpa_model"
#             self._asr_recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
#                 tokens=f"{model_dir}/tokens.txt",
#                 encoder=f"{model_dir}/encoder-epoch-99-avg-1.onnx",
#                 decoder=f"{model_dir}/decoder-epoch-99-avg-1.onnx",
#                 joiner=f"{model_dir}/joiner-epoch-99-avg-1.onnx",
#                 num_threads=1,
#                 sample_rate=16000,
#                 feature_dim=80,
#                 decoding_method="greedy_search",
#                 provider="cpu",
#             )
#             self.append("system", f"✅ ASR 模型已加载：{model_dir}")
#         except Exception as e:
#             self._asr_recognizer = None
#             self._chk_wake.setEnabled(False)
#             self._btn_voice.setEnabled(False)
#             self.append("system", f"⚠️ ASR 模型加载失败：{e}")
#             self.set_status("Error: ASR model load")
#
#     def _start_wake_listening(self) -> None:
#         if self._asr_recognizer is None:
#             return
#         if self._is_wake_listening:
#             return
#         if not self._mic_lock.acquire(blocking=False):
#             self.append("system", "🔔 唤醒启动失败：麦克风被占用。")
#             self.set_status("Error: mic busy")
#             return
#         self._is_wake_listening = True
#         self.set_status("Wake Listening...")
#         threading.Thread(target=self._wake_listen_loop, daemon=True).start()
#
#     def _stop_wake_listening(self) -> None:
#         self._is_wake_listening = False
#         self._awaiting_command = False
#         try:
#             if self._mic_lock.locked():
#                 self._mic_lock.release()
#         except Exception:
#             pass
#
#     def _wake_listen_loop(self) -> None:
#         stream = self._asr_recognizer.create_stream()
#         SAMPLE_RATE = 16000
#         CHUNK_SIZE = 4800
#         DEVICE_ID = None
#         GAIN_FACTOR = 1.0
#         SILENCE_TIMEOUT = 0.9
#         last_active_time = time.time()
#         has_speech = False
#         last_text = ""
#         try:
#             with sd.InputStream(device=DEVICE_ID, channels=1, dtype="float32", samplerate=SAMPLE_RATE) as s:
#                 calib_secs = 0.6
#                 calib_chunks = max(1, int((SAMPLE_RATE * calib_secs) / CHUNK_SIZE))
#                 noise_rms = []
#                 for _ in range(calib_chunks):
#                     if not self._is_wake_listening:
#                         return
#                     data, _ = s.read(CHUNK_SIZE)
#                     x = data.reshape(-1) * GAIN_FACTOR
#                     rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
#                     noise_rms.append(rms)
#                 base = float(np.median(noise_rms)) if noise_rms else 0.01
#                 NOISE_THRESHOLD = max(0.02, base * 3.0)
#                 while self._is_wake_listening:
#                     data, _ = s.read(CHUNK_SIZE)
#                     if not self._is_wake_listening:
#                         break
#                     x = data.reshape(-1) * GAIN_FACTOR
#                     current_rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
#                     if self._wake_enabled and self._awaiting_command:
#                         if time.time() > self._awaiting_deadline:
#                             self._awaiting_command = False
#                     if current_rms > NOISE_THRESHOLD:
#                         last_active_time = time.time()
#                         has_speech = True
#                     stream.accept_waveform(SAMPLE_RATE, x)
#                     while self._asr_recognizer.is_ready(stream):
#                         self._asr_recognizer.decode_stream(stream)
#                     partial_text = (self._asr_recognizer.get_result(stream) or "").strip()
#                     if partial_text:
#                         last_text = partial_text
#                     silence_duration = time.time() - last_active_time
#                     if has_speech and silence_duration > SILENCE_TIMEOUT:
#                         final_text = (last_text or "").strip()
#                         self._asr_recognizer.reset(stream)
#                         has_speech = False
#                         last_text = ""
#                         last_active_time = time.time()
#                         if not final_text:
#                             continue
#                         if self._wake_enabled:
#                             if self._awaiting_command:
#                                 short_ok = len(final_text) <= 6
#                                 if short_ok and wake_match_once(final_text, WAKE_KEYWORDS, max_edit=1):
#                                     self._awaiting_deadline = time.time() + self._awaiting_timeout_sec
#                                     continue
#                                 self._awaiting_command = False
#                                 filtered = self._filter_asr_text(final_text)
#                                 if filtered:
#                                     self.append("system", f"🎙️ 识别到指令：{filtered}")
#                                     try:
#                                         self.on_send_callback(filtered)
#                                     except Exception as e:
#                                         self.append("system", f"❌ 发送失败: {e}")
#                                         self.set_status("Error: send failed")
#                                 else:
#                                     self.append("system", "（指令过短/无效，已忽略）")
#                                 continue
#                             short_ok = len(final_text) <= 6
#                             triggered = short_ok and wake_match_once(final_text, WAKE_KEYWORDS, max_edit=1)
#                             if triggered:
#                                 if PLAY_WAKE_SOUND and winsound:
#                                     try:
#                                         winsound.Beep(1000, 200)
#                                     except Exception:
#                                         pass
#                                 self._awaiting_command = True
#                                 self._awaiting_deadline = time.time() + self._awaiting_timeout_sec
#                                 self.append("system", "🔔 已唤醒，等待指令…")
#                                 continue
#         except Exception as e:
#             self.append("system", f"⚠️ 唤醒监听异常: {e}")
#             self.set_status("Error: wake listen")
#         finally:
#             self._is_wake_listening = False
#             self._awaiting_command = False
#             try:
#                 if self._mic_lock.locked():
#                     self._mic_lock.release()
#             except Exception:
#                 pass
#
#
# # 🔴 新增：插件管理对话框
# import functools
#
#
# class PluginManagerDialog(QtWidgets.QDialog):
#     def __init__(self, parent=None, plugin_manager=None, main_app=None):
#         super().__init__(parent)
#         self.plugin_manager = plugin_manager
#         self.main_app = main_app
#         self.setWindowTitle("插件管理")
#         self.resize(700, 500)  # 稍微增加宽度以适应内容
#
#         # 1. 现代化的样式表 (QSS)
#         self.setStyleSheet("""
#             QDialog {
#                 background-color: #F3F4F6; /* 浅灰底色更护眼 */
#             }
#             QWidget#container {
#                 background-color: #FFFFFF;
#                 border-radius: 16px;
#                 border: 1px solid #E5E7EB;
#             }
#             QLabel#title_label {
#                 font-family: 'Segoe UI', sans-serif;
#                 font-size: 18px;
#                 font-weight: 700;
#                 color: #111827;
#                 padding-left: 5px;
#             }
#             /* 表格样式优化 */
#             QTableWidget {
#                 background-color: #FFFFFF;
#                 border: none;
#                 gridline-color: transparent; /* 隐藏网格线 */
#                 selection-background-color: #EFF6FF;
#                 selection-color: #1F2937;
#                 outline: none;
#             }
#             QTableWidget::item {
#                 padding: 5px;
#                 border-bottom: 1px solid #F3F4F6; /* 仅保留下划线 */
#             }
#             QTableWidget::item:selected {
#                 background-color: #EFF6FF;
#             }
#             QHeaderView::section {
#                 background-color: #FFFFFF;
#                 border: none;
#                 border-bottom: 2px solid #E5E7EB;
#                 color: #6B7280;
#                 font-weight: 600;
#                 padding: 10px 5px;
#                 text-align: left;
#             }
#             /* 滚动条美化 */
#             QScrollBar:vertical {
#                 border: none;
#                 background: #F3F4F6;
#                 width: 8px;
#                 border-radius: 4px;
#             }
#             QScrollBar::handle:vertical {
#                 background: #D1D5DB;
#                 border-radius: 4px;
#             }
#             QScrollBar::handle:vertical:hover {
#                 background: #9CA3AF;
#             }
#             /* 底部按钮 */
#             QPushButton#main_btn {
#                 background-color: #FFFFFF;
#                 color: #374151;
#                 border: 1px solid #D1D5DB;
#                 border-radius: 8px;
#                 padding: 8px 20px;
#                 font-weight: 600;
#                 font-size: 13px;
#             }
#             QPushButton#main_btn:hover {
#                 background-color: #F9FAFB;
#                 border-color: #9CA3AF;
#                 color: #111827;
#             }
#             QPushButton#primary_btn {
#                 background-color: #3B82F6;
#                 color: white;
#                 border: none;
#                 border-radius: 8px;
#                 padding: 8px 20px;
#                 font-weight: 600;
#                 font-size: 13px;
#             }
#             QPushButton#primary_btn:hover {
#                 background-color: #2563EB;
#             }
#         """)
#
#         # 2. 布局结构
#         layout = QtWidgets.QVBoxLayout(self)
#         layout.setContentsMargins(15, 15, 15, 15)
#
#         # 创建白色圆角容器
#         self.container = QtWidgets.QWidget()
#         self.container.setObjectName("container")
#         container_layout = QtWidgets.QVBoxLayout(self.container)
#         container_layout.setContentsMargins(20, 20, 20, 20)
#         container_layout.setSpacing(15)
#
#         # --- 顶部标题栏 ---
#         header_layout = QtWidgets.QHBoxLayout()
#
#         icon_label = QtWidgets.QLabel("🔌")
#         icon_label.setStyleSheet("font-size: 20px;")
#         header_layout.addWidget(icon_label)
#
#         title = QtWidgets.QLabel("插件列表")
#         title.setObjectName("title_label")
#         header_layout.addWidget(title)
#         header_layout.addStretch()
#
#         # 刷新按钮移到右上角，更符合操作习惯
#         refresh_btn = QtWidgets.QPushButton("⟳ 刷新列表")
#         refresh_btn.setObjectName("main_btn")
#         refresh_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         refresh_btn.clicked.connect(self._refresh_plugins)
#         header_layout.addWidget(refresh_btn)
#
#         container_layout.addLayout(header_layout)
#
#         # --- 表格区域 ---
#         self.table = QtWidgets.QTableWidget()
#         self.table.setColumnCount(4)
#         self.table.setHorizontalHeaderLabels(["名称 / 触发词", "类型", "状态", "操作"])
#
#         # 表格交互设置
#         self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectionBehavior.SelectRows)
#         self.table.setEditTriggers(QtWidgets.QTableWidget.EditTrigger.NoEditTriggers)
#         self.table.verticalHeader().setVisible(False)
#         self.table.setShowGrid(False)  # 去除网格线
#         self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)  # 去除选中虚线框
#
#         # 调整列宽模式
#         header = self.table.horizontalHeader()
#         header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)  # 名称自适应
#         header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
#         header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Fixed)
#         header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Fixed)
#
#         self.table.setColumnWidth(1, 80)  # 类型
#         self.table.setColumnWidth(2, 100)  # 状态
#         self.table.setColumnWidth(3, 160)  # 操作
#
#         # 增加行高，不再拥挤
#         self.table.verticalHeader().setDefaultSectionSize(65)
#
#         container_layout.addWidget(self.table)
#
#         # --- 底部区域 ---
#         footer_layout = QtWidgets.QHBoxLayout()
#         footer_layout.addStretch()
#
#         close_btn = QtWidgets.QPushButton("关闭")
#         close_btn.setObjectName("main_btn")
#         close_btn.setMinimumWidth(100)
#         close_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         close_btn.clicked.connect(self.close)
#         footer_layout.addWidget(close_btn)
#
#         container_layout.addLayout(footer_layout)
#         layout.addWidget(self.container)
#
#         # 初始化数据
#         self._refresh_plugins()
#
#     def _refresh_plugins(self):
#         self.table.setRowCount(0)
#         if not self.plugin_manager:
#             return
#
#         plugins_info = self.plugin_manager.get_all_plugins_info()
#
#         for row, info in enumerate(plugins_info):
#             self.table.insertRow(row)
#
#             # 1. 插件名称与触发词 (合并显示，更紧凑)
#             name_widget = QtWidgets.QWidget()
#             name_layout = QtWidgets.QVBoxLayout(name_widget)
#             name_layout.setContentsMargins(10, 5, 0, 5)
#             name_layout.setSpacing(2)
#             name_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
#
#             lbl_name = QtWidgets.QLabel(info["name"])
#             lbl_name.setStyleSheet("font-weight: bold; font-size: 14px; color: #1F2937;")
#
#             lbl_trigger = QtWidgets.QLabel(f"触发: {info['trigger']}")
#             lbl_trigger.setStyleSheet("font-size: 12px; color: #6B7280;")
#
#             name_layout.addWidget(lbl_name)
#             name_layout.addWidget(lbl_trigger)
#             self.table.setCellWidget(row, 0, name_widget)
#
#             # 2. 类型
#             type_item = QtWidgets.QTableWidgetItem(info["type"])
#             type_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
#             type_item.setForeground(QtGui.QColor("#4B5563"))
#             self.table.setItem(row, 1, type_item)
#
#             # 3. 状态 (使用徽章样式 Widget)
#             status_widget = QtWidgets.QWidget()
#             status_layout = QtWidgets.QHBoxLayout(status_widget)
#             status_layout.setContentsMargins(0, 0, 0, 0)
#             status_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
#
#             lbl_status = QtWidgets.QLabel()
#             if info["enabled"]:
#                 lbl_status.setText("● 已启用")
#                 lbl_status.setStyleSheet("""
#                     background-color: #D1FAE5; color: #047857;
#                     padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 12px;
#                 """)
#             else:
#                 lbl_status.setText("○ 已禁用")
#                 lbl_status.setStyleSheet("""
#                     background-color: #F3F4F6; color: #6B7280;
#                     padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 12px;
#                     border: 1px solid #E5E7EB;
#                 """)
#
#             status_layout.addWidget(lbl_status)
#             self.table.setCellWidget(row, 2, status_widget)
#
#             # 4. 操作按钮组
#             btn_widget = QtWidgets.QWidget()
#             btn_layout = QtWidgets.QHBoxLayout(btn_widget)
#             btn_layout.setContentsMargins(5, 5, 5, 5)
#             btn_layout.setSpacing(8)
#             btn_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
#
#             # 切换按钮
#             toggle_btn = QtWidgets.QPushButton()
#             toggle_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#             if info["enabled"]:
#                 toggle_btn.setText("禁用")
#                 # 红色边框样式
#                 toggle_btn.setStyleSheet("""
#                     QPushButton {
#                         background-color: white; color: #DC2626; border: 1px solid #FECACA;
#                         border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 600;
#                     }
#                     QPushButton:hover { background-color: #FEF2F2; border-color: #DC2626; }
#                 """)
#             else:
#                 toggle_btn.setText("启用")
#                 # 绿色填充样式
#                 toggle_btn.setStyleSheet("""
#                     QPushButton {
#                         background-color: #059669; color: white; border: none;
#                         border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 600;
#                     }
#                     QPushButton:hover { background-color: #047857; }
#                 """)
#             toggle_btn.clicked.connect(functools.partial(self._toggle_plugin, info["trigger"]))
#
#             # 编辑按钮
#             edit_btn = QtWidgets.QPushButton("编辑")
#             edit_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#             edit_btn.setStyleSheet("""
#                 QPushButton {
#                     background-color: white; color: #2563EB; border: 1px solid #BFDBFE;
#                     border-radius: 6px; padding: 5px 10px; font-size: 12px; font-weight: 600;
#                 }
#                 QPushButton:hover { background-color: #EFF6FF; border-color: #2563EB; }
#             """)
#             edit_btn.clicked.connect(functools.partial(self._edit_plugin, info["trigger"]))
#
#             btn_layout.addWidget(toggle_btn)
#             btn_layout.addWidget(edit_btn)
#             self.table.setCellWidget(row, 3, btn_widget)
#
#     def _toggle_plugin(self, trigger: str):
#         if not self.plugin_manager: return
#         try:
#             if self.plugin_manager.is_plugin_enabled(trigger):
#                 if self.plugin_manager.disable_plugin(trigger):
#                     self._show_toast(f"🔌 插件 [{trigger}] 已禁用", False)
#             else:
#                 if self.plugin_manager.enable_plugin(trigger):
#                     self._show_toast(f"🔌 插件 [{trigger}] 已启用", True)
#             self._refresh_plugins()
#         except Exception as e:
#             QtWidgets.QMessageBox.critical(self, "错误", f"操作失败: {str(e)}")
#
#     def _show_toast(self, message, is_success):
#         """简单的系统消息反馈"""
#         if self.main_app:
#             self.main_app.append("system", message)
#
#     def _edit_plugin(self, trigger: str):
#         if not self.plugin_manager: return
#         try:
#             dialog = PluginEditorDialog(parent=self, plugin_manager=self.plugin_manager, trigger=trigger)
#             dialog.exec()
#             self._refresh_plugins()
#         except Exception as e:
#             QtWidgets.QMessageBox.critical(self, "错误", f"打开编辑对话框失败: {str(e)}")
#
#
# # 🔴 新增：插件编辑对话框
# class PluginEditorDialog(QtWidgets.QDialog):
#     def __init__(self, parent=None, plugin_manager=None, trigger=None):
#         super().__init__(parent)
#         self.plugin_manager = plugin_manager
#         self.trigger = trigger
#         self.original_config = None
#         self.config_fields = {}
#
#         self.setWindowTitle(f"编辑插件 - {trigger}")
#         self.setFixedSize(600, 500)
#
#         # 样式
#         self.setStyleSheet("""
#             QDialog {
#                 background-color: #FFFFFF;
#                 border: 1px solid rgba(0, 0, 0, 0.15);
#                 border-radius: 24px;
#             }
#             QLabel {
#                 color: #1F2937;
#                 font-size: 12px;
#             }
#             QLabel.section_title {
#                 font-size: 14px;
#                 font-weight: bold;
#                 color: #111827;
#                 padding: 5px 0;
#                 border-bottom: 2px solid #E5E7EB;
#             }
#             QLineEdit, QTextEdit, QSpinBox, QComboBox {
#                 background-color: #F9FAFB;
#                 border: 1px solid #D1D5DB;
#                 border-radius: 6px;
#                 padding: 8px;
#                 color: #1F2937;
#             }
#             QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
#                 border: 2px solid #3B82F6;
#                 background-color: #FFFFFF;
#             }
#             QTabWidget::pane {
#                 border: 1px solid #D1D5DB;
#                 border-radius: 8px;
#                 background-color: #FFFFFF;
#             }
#             QTabBar::tab {
#                 background-color: #F3F4F6;
#                 color: #6B7280;
#                 padding: 8px 20px;
#                 border-top-left-radius: 6px;
#                 border-top-right-radius: 6px;
#                 margin-right: 2px;
#             }
#             QTabBar::tab:selected {
#                 background-color: #FFFFFF;
#                 color: #3B82F6;
#                 font-weight: bold;
#             }
#             QGroupBox {
#                 border: 1px solid #E5E7EB;
#                 border-radius: 8px;
#                 margin-top: 10px;
#                 padding-top: 10px;
#             }
#             QGroupBox::title {
#                 color: #6B7280;
#                 font-size: 11px;
#             }
#             QPushButton {
#                 background-color: #059669;
#                 color: #FFFFFF;
#                 border: none;
#                 border-radius: 6px;
#                 padding: 10px 24px;
#                 font-size: 13px;
#                 font-weight: bold;
#             }
#             QPushButton:hover {
#                 background-color: #047857;
#             }
#             QPushButton:pressed {
#                 background-color: #065F46;
#             }
#             QPushButton#cancel_btn {
#                 background-color: #6B7280;
#             }
#             QPushButton#cancel_btn:hover {
#                 background-color: #4B5563;
#             }
#             QPushButton#reset_btn {
#                 background-color: #D97706;
#             }
#             QPushButton#reset_btn:hover {
#                 background-color: #B45309;
#             }
#             QPushButton#path_btn {
#                 background-color: #8B5CF6;
#                 padding: 5px 12px;
#                 font-size: 11px;
#             }
#             QPushButton#path_btn:hover {
#                 background-color: #7C3AED;
#             }
#         """)
#
#         layout = QtWidgets.QVBoxLayout(self)
#         layout.setContentsMargins(20, 20, 20, 20)
#         layout.setSpacing(15)
#
#         # 创建标签页
#         self.tab_widget = QtWidgets.QTabWidget()
#         layout.addWidget(self.tab_widget)
#
#         # 基本信息标签页
#         self.basic_tab = QtWidgets.QWidget()
#         self._setup_basic_tab()
#         self.tab_widget.addTab(self.basic_tab, "基本信息")
#
#         # 自定义配置标签页
#         self.settings_tab = QtWidgets.QWidget()
#         self._setup_settings_tab()
#         self.tab_widget.addTab(self.settings_tab, "自定义配置")
#
#         # 按钮区域
#         btn_layout = QtWidgets.QHBoxLayout()
#         btn_layout.addStretch()
#
#         reset_btn = QtWidgets.QPushButton("🔄 重置")
#         reset_btn.setObjectName("reset_btn")
#         reset_btn.clicked.connect(self._reset_config)
#         btn_layout.addWidget(reset_btn)
#
#         cancel_btn = QtWidgets.QPushButton("取消")
#         cancel_btn.setObjectName("cancel_btn")
#         cancel_btn.clicked.connect(self.reject)
#         btn_layout.addWidget(cancel_btn)
#
#         save_btn = QtWidgets.QPushButton("💾 保存")
#         save_btn.clicked.connect(self._save_config)
#         btn_layout.addWidget(save_btn)
#
#         layout.addLayout(btn_layout)
#
#         # 加载配置
#         self._load_config()
#
#     def _setup_basic_tab(self):
#         """设置基本信息标签页"""
#         layout = QtWidgets.QVBoxLayout(self.basic_tab)
#         layout.setSpacing(15)
#
#         # 标题
#         title = QtWidgets.QLabel("📋 基本信息")
#         title.setObjectName("section_title")
#         layout.addWidget(title)
#
#         form_layout = QtWidgets.QFormLayout()
#         form_layout.setSpacing(12)
#
#         # 名称
#         self.name_input = QtWidgets.QLineEdit()
#         form_layout.addRow("插件名称:", self.name_input)
#
#         # 触发词
#         self.trigger_input = QtWidgets.QLineEdit()
#         self.trigger_input.setPlaceholderText("插件触发关键词")
#         form_layout.addRow("触发词:", self.trigger_input)
#
#         # 类型
#         self.type_combo = QtWidgets.QComboBox()
#         self.type_combo.addItems(["react", "direct"])
#         form_layout.addRow("类型:", self.type_combo)
#
#         # 别名
#         self.aliases_input = QtWidgets.QTextEdit()
#         self.aliases_input.setMaximumHeight(80)
#         self.aliases_input.setPlaceholderText("每行一个别名")
#         form_layout.addRow("别名:", self.aliases_input)
#
#         # 描述
#         self.desc_input = QtWidgets.QTextEdit()
#         self.desc_input.setMaximumHeight(80)
#         self.desc_input.setPlaceholderText("插件功能描述")
#         form_layout.addRow("描述:", self.desc_input)
#
#         # 示例参数
#         self.example_input = QtWidgets.QLineEdit()
#         self.example_input.setPlaceholderText("示例使用参数")
#         form_layout.addRow("示例参数:", self.example_input)
#
#         # 超时时间
#         self.timeout_input = QtWidgets.QSpinBox()
#         self.timeout_input.setRange(1, 300)
#         self.timeout_input.setSuffix(" 秒")
#         form_layout.addRow("超时时间:", self.timeout_input)
#
#         layout.addLayout(form_layout)
#         layout.addStretch()
#
#     def _setup_settings_tab(self):
#         """设置自定义配置标签页"""
#         layout = QtWidgets.QVBoxLayout(self.settings_tab)
#         layout.setSpacing(15)
#
#         # 标题
#         title = QtWidgets.QLabel("⚙️ 自定义配置")
#         title.setObjectName("section_title")
#         layout.addWidget(title)
#
#         self.settings_scroll = QtWidgets.QScrollArea()
#         self.settings_scroll.setWidgetResizable(True)
#         self.settings_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
#         layout.addWidget(self.settings_scroll)
#
#         self.settings_container = QtWidgets.QWidget()
#         self.settings_layout = QtWidgets.QVBoxLayout(self.settings_container)
#         self.settings_layout.setSpacing(12)
#         self.settings_layout.addStretch()
#         self.settings_scroll.setWidget(self.settings_container)
#
#     def _load_config(self):
#         """加载插件配置"""
#         if not self.plugin_manager or not self.trigger:
#             return
#
#         try:
#             # 获取插件配置
#             config = self.plugin_manager.get_plugin_config(self.trigger)
#             if not config:
#                 return
#
#             self.original_config = config.copy()
#
#             # 加载基本信息
#             self.name_input.setText(config.get("name", ""))
#             self.trigger_input.setText(config.get("trigger", ""))
#             self.type_combo.setCurrentText(config.get("type", "react"))
#
#             aliases = config.get("aliases", [])
#             if isinstance(aliases, list):
#                 self.aliases_input.setPlainText("\n".join(aliases))
#
#             self.desc_input.setPlainText(config.get("description", ""))
#             self.example_input.setText(config.get("example_arg", ""))
#             self.timeout_input.setValue(config.get("timeout_sec", 6))
#
#             # 加载自定义配置
#             self._load_custom_settings(config)
#
#         except Exception as e:
#             QtWidgets.QMessageBox.warning(self, "错误", f"加载配置失败: {str(e)}")
#
#     def _load_custom_settings(self, config):
#         """加载自定义配置项（支持增强的元数据格式）"""
#         # 清空现有配置项
#         for i in reversed(range(self.settings_layout.count() - 1)):
#             item = self.settings_layout.itemAt(i)
#             if item and item.widget():
#                 item.widget().deleteLater()
#
#         settings = config.get("settings", {})
#         if not settings:
#             no_settings = QtWidgets.QLabel("此插件没有自定义配置项")
#             no_settings.setStyleSheet("color: #9CA3AF; padding: 20px;")
#             self.settings_layout.insertWidget(0, no_settings)
#             return
#
#         for key, setting_info in settings.items():
#             # 支持两种格式：
#             # 1. 简单格式: {"key": value}
#             # 2. 增强格式: {"key": {"type": "...", "default": ..., "label": "...", "description": "..."}}
#
#             if isinstance(setting_info, dict) and "type" in setting_info:
#                 # 增强格式
#                 setting_type = setting_info.get("type", "string")
#                 label = setting_info.get("label", key)
#                 description = setting_info.get("description", "")
#                 value = setting_info.get("default")
#                 min_val = setting_info.get("min", 0)
#                 max_val = setting_info.get("max", 10000)
#                 choices = setting_info.get("choices", [])
#             else:
#                 # 简单格式（向后兼容）
#                 setting_type = self._infer_type(setting_info)
#                 label = key
#                 description = ""
#                 value = setting_info
#                 min_val = 0
#                 max_val = 10000
#                 choices = []
#
#             # 创建配置项组
#             group = QtWidgets.QGroupBox(label)
#             group_layout = QtWidgets.QVBoxLayout(group)
#             group_layout.setSpacing(8)
#
#             # 添加说明文字
#             if description:
#                 desc_label = QtWidgets.QLabel(description)
#                 desc_label.setStyleSheet("color: #6B7280; font-size: 11px; padding: 2px 0;")
#                 desc_label.setWordWrap(True)
#                 group_layout.addWidget(desc_label)
#
#             # 根据类型创建对应的输入控件
#             widget = self._create_setting_widget(
#                 key, setting_info, setting_type, value, min_val, max_val, choices
#             )
#
#             if widget:
#                 group_layout.addWidget(widget)
#
#             self.settings_layout.insertWidget(self.settings_layout.count() - 1, group)
#
#     def _infer_type(self, value):
#         """推断值的类型"""
#         if isinstance(value, list):
#             return "list"
#         elif isinstance(value, bool):
#             return "boolean"
#         elif isinstance(value, (int, float)):
#             return "number"
#         elif isinstance(value, str):
#             if ("\\" in value or "/" in value) and ("." in value):
#                 return "path" if "." not in value.split("\\")[-1].split("/")[-1] else "file"
#             return "string"
#         return "string"
#
#     def _create_path_item(self, layout, path_value, path_inputs):
#         """创建路径列表项（输入框 + 选择按钮 + 删除按钮）"""
#         item_widget = QtWidgets.QWidget()
#         item_layout = QtWidgets.QHBoxLayout(item_widget)
#         item_layout.setContentsMargins(0, 0, 0, 0)
#         item_layout.setSpacing(5)
#
#         # 路径输入框
#         path_input = QtWidgets.QLineEdit()
#         path_input.setText(str(path_value) if path_value else "")
#         path_input.setPlaceholderText("选择或输入路径...")
#         path_input.setStyleSheet("""
#             QLineEdit {
#                 background-color: #F9FAFB;
#                 border: 1px solid #D1D5DB;
#                 border-radius: 6px;
#                 padding: 8px;
#                 color: #1F2937;
#             }
#             QLineEdit:focus {
#                 border: 2px solid #3B82F6;
#                 background-color: #FFFFFF;
#             }
#         """)
#
#         # 选择按钮
#         select_btn = QtWidgets.QPushButton("选择...")
#         select_btn.setObjectName("path_btn")
#         select_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         select_btn.clicked.connect(lambda: self._select_directory(path_input))
#
#         # 删除按钮
#         delete_btn = QtWidgets.QPushButton("✕")
#         delete_btn.setStyleSheet("""
#             QPushButton {
#                 background-color: #EF4444; color: white; border: none;
#                 border-radius: 6px; padding: 8px 12px; font-weight: bold;
#             }
#             QPushButton:hover { background-color: #DC2626; }
#         """)
#         delete_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         delete_btn.setFixedSize(40, 35)
#         delete_btn.clicked.connect(lambda: self._delete_path_item(layout, item_widget, path_input))
#
#         item_layout.addWidget(path_input, 1)
#         item_layout.addWidget(select_btn)
#         item_layout.addWidget(delete_btn)
#
#         # 插入到布局中（在添加按钮之前）
#         layout.insertWidget(layout.count() - 1, item_widget)
#
#         return path_input
#
#     def _delete_path_item(self, layout, item_widget, path_input):
#         """删除路径项"""
#         item_widget.deleteLater()
#         # 从 path_inputs 列表中移除
#         if path_input in self.config_fields:
#             for key, (field_type, widget) in self.config_fields.items():
#                 if field_type == "path_list" and isinstance(widget, list):
#                     if path_input in widget:
#                         widget.remove(path_input)
#                         break
#
#     def _create_app_list_item(self, layout, app_config, app_inputs):
#         """创建应用列表项（别名输入框 + 程序路径输入框 + 选择按钮 + 删除按钮）"""
#         # 解析配置：格式为 "别名|路径"
#         alias = ""
#         path = ""
#         if app_config and "|" in str(app_config):
#             parts = str(app_config).split("|", 1)
#             alias = parts[0].strip()
#             path = parts[1].strip() if len(parts) > 1 else ""
#
#         item_widget = QtWidgets.QWidget()
#         item_layout = QtWidgets.QHBoxLayout(item_widget)
#         item_layout.setContentsMargins(0, 0, 0, 0)
#         item_layout.setSpacing(5)
#
#         # 别名输入框
#         alias_input = QtWidgets.QLineEdit()
#         alias_input.setText(alias)
#         alias_input.setPlaceholderText("别名")
#         alias_input.setMinimumWidth(120)
#         alias_input.setMaximumWidth(150)
#         alias_input.setStyleSheet("""
#             QLineEdit {
#                 background-color: #F9FAFB;
#                 border: 1px solid #D1D5DB;
#                 border-radius: 6px;
#                 padding: 8px;
#                 color: #1F2937;
#             }
#             QLineEdit:focus {
#                 border: 2px solid #3B82F6;
#                 background-color: #FFFFFF;
#             }
#         """)
#
#         # 路径输入框
#         path_input = QtWidgets.QLineEdit()
#         path_input.setText(path)
#         path_input.setPlaceholderText("程序路径")
#         path_input.setStyleSheet("""
#             QLineEdit {
#                 background-color: #F9FAFB;
#                 border: 1px solid #D1D5DB;
#                 border-radius: 6px;
#                 padding: 8px;
#                 color: #1F2937;
#             }
#             QLineEdit:focus {
#                 border: 2px solid #3B82F6;
#                 background-color: #FFFFFF;
#             }
#         """)
#
#         # 选择程序按钮
#         select_btn = QtWidgets.QPushButton("选择...")
#         select_btn.setObjectName("path_btn")
#         select_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         select_btn.clicked.connect(lambda: self._select_executable(path_input))
#
#         # 删除按钮
#         delete_btn = QtWidgets.QPushButton("✕")
#         delete_btn.setStyleSheet("""
#             QPushButton {
#                 background-color: #EF4444; color: white; border: none;
#                 border-radius: 6px; padding: 8px 12px; font-weight: bold;
#             }
#             QPushButton:hover { background-color: #DC2626; }
#         """)
#         delete_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#         delete_btn.setFixedSize(40, 35)
#         delete_btn.clicked.connect(lambda: self._delete_app_list_item(layout, item_widget, app_inputs))
#
#         item_layout.addWidget(alias_input)
#         item_layout.addWidget(path_input, 1)
#         item_layout.addWidget(select_btn)
#         item_layout.addWidget(delete_btn)
#
#         # 插入到布局中（在添加按钮之前）
#         layout.insertWidget(layout.count() - 1, item_widget)
#
#         # 存储引用
#         app_inputs.append({"alias": alias_input, "path": path_input})
#         return app_inputs[-1]
#
#     def _delete_app_list_item(self, layout, item_widget, app_inputs):
#         """删除应用列表项"""
#         # 获取当前项的别名输入框
#         alias_widget = item_widget.layout().itemAt(0).widget()
#         path_widget = item_widget.layout().itemAt(1).widget()
#
#         # 从 app_inputs 列表中移除
#         for key, (field_type, widget) in self.config_fields.items():
#             if field_type == "app_list" and isinstance(widget, list):
#                 # 找到对应的数据并移除（通过比较对象引用）
#                 for i, app_input in enumerate(widget):
#                     if app_input["alias"] is alias_widget and app_input["path"] is path_widget:
#                         widget.pop(i)
#                         break
#                 break
#
#         # 删除UI组件
#         item_widget.deleteLater()
#
#     def _create_setting_widget(self, key, setting_info, setting_type, value, min_val, max_val, choices):
#         """根据类型创建配置控件"""
#         widget = None
#
#         if setting_type == "string" or setting_type == "text":
#             # 文本类型
#             if isinstance(value, list):
#                 text = "\n".join(str(v) for v in value)
#             else:
#                 text = str(value) if value is not None else ""
#
#             text_edit = QtWidgets.QTextEdit()
#             text_edit.setMaximumHeight(80)
#             text_edit.setPlaceholderText("请输入文本...")
#             text_edit.setPlainText(text)
#             self.config_fields[key] = ("text", text_edit)
#             widget = text_edit
#
#         elif setting_type == "number":
#             # 数字类型
#             spinbox = QtWidgets.QSpinBox()
#             spinbox.setRange(min_val, max_val)
#             spinbox.setValue(int(value) if value is not None else min_val)
#             self.config_fields[key] = ("number", spinbox)
#             widget = spinbox
#
#         elif setting_type == "boolean" or setting_type == "bool":
#             # 布尔类型
#             checkbox = QtWidgets.QCheckBox("启用")
#             checkbox.setChecked(bool(value) if value is not None else False)
#             self.config_fields[key] = ("boolean", checkbox)
#             widget = checkbox
#
#         elif setting_type == "list":
#             # 列表类型
#             # 检查是否是 path 类型列表或 app_list 类型
#             is_path_list = False
#             is_app_list = False
#             if isinstance(setting_info, dict) and "item_type" in setting_info:
#                 is_path_list = setting_info["item_type"] == "path"
#                 is_app_list = setting_info["item_type"] == "app_list"
#
#             if is_path_list:
#                 # 路径列表：创建可动态添加/删除行的界面
#                 scroll = QtWidgets.QScrollArea()
#                 scroll.setWidgetResizable(True)
#                 scroll.setMaximumHeight(200)
#                 scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
#
#                 container = QtWidgets.QWidget()
#                 list_layout = QtWidgets.QVBoxLayout(container)
#                 list_layout.setSpacing(8)
#
#                 # 存储路径输入框的引用
#                 path_inputs = []
#
#                 # 添加现有路径
#                 if isinstance(value, list):
#                     for path in value:
#                         if path:
#                             path_inputs.append(self._create_path_item(list_layout, path, path_inputs))
#
#                 # 添加"添加路径"按钮
#                 add_btn = QtWidgets.QPushButton("+ 添加路径")
#                 add_btn.setStyleSheet("""
#                     QPushButton {
#                         background-color: #10B981; color: white; border: none;
#                         border-radius: 6px; padding: 8px; font-weight: bold;
#                     }
#                     QPushButton:hover { background-color: #059669; }
#                 """)
#                 add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#                 add_btn.clicked.connect(lambda: path_inputs.append(
#                     self._create_path_item(list_layout, "", path_inputs)
#                 ))
#                 list_layout.addWidget(add_btn)
#
#                 scroll.setWidget(container)
#                 self.config_fields[key] = ("path_list", path_inputs)
#                 widget = scroll
#             elif is_app_list:
#                 # 应用列表：创建可动态添加/删除行的界面（包含别名和路径）
#                 scroll = QtWidgets.QScrollArea()
#                 scroll.setWidgetResizable(True)
#                 scroll.setMaximumHeight(200)
#                 scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
#
#                 container = QtWidgets.QWidget()
#                 list_layout = QtWidgets.QVBoxLayout(container)
#                 list_layout.setSpacing(8)
#
#                 # 存储应用输入框的引用
#                 app_inputs = []
#
#                 # 添加现有应用
#                 if isinstance(value, list):
#                     for app in value:
#                         if app:
#                             self._create_app_list_item(list_layout, app, app_inputs)
#
#                 # 添加"添加应用"按钮
#                 add_btn = QtWidgets.QPushButton("+ 添加应用")
#                 add_btn.setStyleSheet("""
#                     QPushButton {
#                         background-color: #10B981; color: white; border: none;
#                         border-radius: 6px; padding: 8px; font-weight: bold;
#                     }
#                     QPushButton:hover { background-color: #059669; }
#                 """)
#                 add_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#                 # 使用默认参数确保列表引用正确
#                 add_btn.clicked.connect(lambda checked=False, inputs=app_inputs: self._create_app_list_item(list_layout, "", inputs))
#                 list_layout.addWidget(add_btn)
#
#                 scroll.setWidget(container)
#                 self.config_fields[key] = ("app_list", app_inputs)
#                 widget = scroll
#             else:
#                 # 普通列表：使用多行文本框
#                 if isinstance(value, list):
#                     text = "\n".join(str(v) for v in value)
#                 else:
#                     text = ""
#
#                 text_edit = QtWidgets.QTextEdit()
#                 text_edit.setMaximumHeight(100)
#                 text_edit.setPlaceholderText("每行一个项目...")
#                 text_edit.setPlainText(text)
#                 self.config_fields[key] = ("list", text_edit)
#                 widget = text_edit
#
#         elif setting_type == "path":
#             # 路径类型
#             path_layout = QtWidgets.QHBoxLayout()
#             line_edit = QtWidgets.QLineEdit()
#             line_edit.setText(str(value) if value is not None else "")
#             line_edit.setPlaceholderText("选择或输入路径...")
#
#             path_btn = QtWidgets.QPushButton("选择...")
#             path_btn.setObjectName("path_btn")
#             path_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#             path_btn.clicked.connect(lambda checked, le=line_edit: self._select_directory(le))
#
#             path_layout.addWidget(line_edit, 1)
#             path_layout.addWidget(path_btn)
#             self.config_fields[key] = ("path", line_edit)
#
#             # 创建容器widget返回
#             container = QtWidgets.QWidget()
#             container.setLayout(path_layout)
#             widget = container
#
#         elif setting_type == "file":
#             # 文件类型
#             file_layout = QtWidgets.QHBoxLayout()
#             line_edit = QtWidgets.QLineEdit()
#             line_edit.setText(str(value) if value is not None else "")
#             line_edit.setPlaceholderText("选择或输入文件路径...")
#
#             file_btn = QtWidgets.QPushButton("选择...")
#             file_btn.setObjectName("path_btn")
#             file_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
#             file_btn.clicked.connect(lambda checked, le=line_edit: self._select_file(le))
#
#             file_layout.addWidget(line_edit, 1)
#             file_layout.addWidget(file_btn)
#             self.config_fields[key] = ("file", line_edit)
#
#             container = QtWidgets.QWidget()
#             container.setLayout(file_layout)
#             widget = container
#
#         elif setting_type == "choice":
#             # 选择类型（下拉框）
#             combo = QtWidgets.QComboBox()
#             combo.addItems(choices)
#             if value is not None and str(value) in choices:
#                 combo.setCurrentText(str(value))
#             self.config_fields[key] = ("choice", combo)
#             widget = combo
#
#         return widget
#
#     def _select_directory(self, line_edit):
#         """选择目录对话框"""
#         path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
#         if path:
#             line_edit.setText(path)
#
#     def _select_file(self, line_edit):
#         """选择文件对话框"""
#         path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择文件")
#         if path:
#             line_edit.setText(path)
#
#     def _select_executable(self, line_edit):
#         """选择可执行文件对话框"""
#         path, _ = QtWidgets.QFileDialog.getOpenFileName(
#             self,
#             "选择可执行文件",
#             "",
#             "可执行文件 (*.exe *.bat *.cmd *.sh);;所有文件 (*.*)"
#         )
#         if path:
#             line_edit.setText(path)
#
#     def _select_path(self, line_edit):
#         """选择路径对话框"""
#         path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择文件夹")
#         if path:
#             line_edit.setText(path)
#
#     def _reset_config(self):
#         """重置配置到原始值"""
#         if not self.original_config:
#             return
#
#         reply = QtWidgets.QMessageBox.question(
#             self, "确认重置",
#             "确定要重置所有配置到原始值吗？",
#             QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
#         )
#
#         if reply == QtWidgets.QMessageBox.StandardButton.Yes:
#             self._load_config()
#
#     def _save_config(self):
#         """保存配置"""
#         if not self.plugin_manager or not self.trigger:
#             return
#
#         try:
#             # 验证必填字段
#             name = self.name_input.text().strip()
#             trigger = self.trigger_input.text().strip()
#             if not name or not trigger:
#                 QtWidgets.QMessageBox.warning(self, "验证失败", "插件名称和触发词不能为空")
#                 return
#
#             # 构建新配置
#             new_config = self.original_config.copy()
#             new_config["name"] = name
#             new_config["trigger"] = trigger
#             new_config["type"] = self.type_combo.currentText()
#
#             aliases_text = self.aliases_input.toPlainText().strip()
#             aliases = [line.strip() for line in aliases_text.split("\n") if line.strip()]
#             new_config["aliases"] = aliases
#
#             new_config["description"] = self.desc_input.toPlainText().strip()
#             new_config["example_arg"] = self.example_input.text().strip()
#             new_config["timeout_sec"] = self.timeout_input.value()
#
#             # 保存自定义配置
#             settings = {}
#             for key, (field_type, widget) in self.config_fields.items():
#                 if field_type == "list":
#                     value = [line.strip() for line in widget.toPlainText().split("\n") if line.strip()]
#                     settings[key] = value
#                 elif field_type == "bool":
#                     value = widget.isChecked()
#                     settings[key] = value
#                 elif field_type == "number":
#                     value = widget.value()
#                     settings[key] = value
#                 elif field_type == "path_list":
#                     # path_list 类型：widget 是 line_edit 的列表
#                     value = []
#                     if isinstance(widget, list):
#                         for line_edit in widget:
#                             path = line_edit.text().strip()
#                             if path:
#                                 value.append(path)
#                     settings[key] = value
#                 elif field_type == "app_list":
#                     # app_list 类型：widget 是包含 alias 和 path 的字典列表
#                     value = []
#                     if isinstance(widget, list):
#                         for app_input in widget:
#                             alias = app_input["alias"].text().strip()
#                             path = app_input["path"].text().strip()
#                             if alias and path:
#                                 value.append(f"{alias}|{path}")
#
#                     # 🔴 修复：保持原始配置结构
#                     # 如果原始配置中该字段是一个字典（包含 default 等元数据），则保持该结构
#                     original_setting = self.original_config.get("settings", {}).get(key, {})
#                     if isinstance(original_setting, dict):
#                         settings[key] = {
#                             **original_setting,  # 保留元数据
#                             "default": value     # 更新 default 值
#                         }
#                     else:
#                         settings[key] = value
#                 else:
#                     # text, choice, path, file 等类型
#                     value = widget.text().strip()
#                     settings[key] = value
#
#             new_config["settings"] = settings
#
#             # 打印调试信息
#             print(f"💾 保存配置 [{trigger}]:")
#             print(f"  - 自定义配置: {settings}")
#
#             # 保存到文件
#             if self.plugin_manager.save_plugin_config(self.trigger, new_config):
#                 QtWidgets.QMessageBox.information(self, "成功", "插件配置已保存")
#                 self.accept()
#             else:
#                 QtWidgets.QMessageBox.warning(self, "失败", "保存插件配置失败")
#
#         except Exception as e:
#             import traceback
#             print(f"❌ 保存配置异常: {e}")
#             traceback.print_exc()
#             QtWidgets.QMessageBox.critical(self, "错误", f"保存配置失败: {str(e)}")
