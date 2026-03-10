# modules/gui.py
import tkinter as tk
import threading
import re
import time
from typing import Optional

import sounddevice as sd
import sherpa_onnx
import numpy as np
from pypinyin import lazy_pinyin

from config import WAKE_KEYWORDS, PLAY_WAKE_SOUND, TTS_ENABLED

# 可选：是否打印音频设备（默认不打印，减少启动刷屏）
try:
    from config import PRINT_AUDIO_DEVICES
except Exception:
    PRINT_AUDIO_DEVICES = False

# 可选：思考动作默认开关（GUI 不再提供按钮，仅在启动时回调一次）
try:
    from config import THINK_MOTION_ENABLED
except Exception:
    THINK_MOTION_ENABLED = True

# 可选：热键（可以放到 .env 里）
# pynput GlobalHotKeys 格式示例："<ctrl>+<alt>+space"
try:
    from config import HOTKEY_TOGGLE_GUI
except Exception:
    HOTKEY_TOGGLE_GUI = "<ctrl>+<alt>+space"

# 可选：是否启用托盘
try:
    from config import TRAY_ENABLED
except Exception:
    TRAY_ENABLED = True

# 可选：启动后是否直接隐藏到托盘
try:
    from config import START_MINIMIZED_TO_TRAY
except Exception:
    START_MINIMIZED_TO_TRAY = False

# Windows beep
try:
    import winsound
except ImportError:
    winsound = None

# 依赖：托盘
try:
    import pystray
    from pystray import MenuItem as _TrayItem
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None
    _TrayItem = None

# 依赖：全局热键
try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None


if PRINT_AUDIO_DEVICES:
    print("\n🎤 ========== 音频设备列表 ==========")
    try:
        print(sd.query_devices())
    except Exception as e:
        print(f"(query_devices failed) {e}")
    print("======================================\n")


class ChatWindow:
    """
    Tk GUI + Win 托盘 + 全局热键
    - 关闭按钮默认最小化到托盘（不退出）
    - Ctrl+Alt+Space 切换显示/隐藏（可配置）
    - 托盘默认动作：显示/隐藏（切换）
    """

    def __init__(
        self,
        on_send_callback,
        on_tts_toggle_callback=None,
        on_think_toggle_callback=None,
    ):
        self.on_send_callback = on_send_callback
        self.on_tts_toggle_callback = on_tts_toggle_callback
        self.on_think_toggle_callback = on_think_toggle_callback

        # ---------- 外观：低存在感 ----------
        self.alpha_idle = 0.78
        self.alpha_active = 0.92

        self.root = tk.Tk()
        self.root.title("Live2D Terminal")
        self.root.geometry("560x135")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.alpha_idle)

        # 工具窗（Windows）
        try:
            self.root.wm_attributes("-toolwindow", True)
        except Exception:
            pass

        self.C_BG = "#F3F4F6"
        self.C_PANEL = "#FFFFFF"
        self.C_TEXT = "#111827"
        self.C_MUTED = "#6B7280"
        self.C_BTN = "#E5E7EB"
        self.C_BTN_HOVER = "#D1D5DB"
        self.C_DANGER = "#F87171"

        self.root.configure(bg=self.C_BG)

        self.root.bind("<Enter>", lambda e: self._set_alpha(self.alpha_active))
        self.root.bind("<Leave>", lambda e: self._set_alpha(self.alpha_idle))
        self.root.bind("<FocusIn>", lambda e: self._set_alpha(self.alpha_active))
        self.root.bind("<FocusOut>", lambda e: self._set_alpha(self.alpha_idle))

        # 快捷键：Ctrl+Shift+T 切 topmost
        self.root.bind("<Control-Shift-T>", lambda e: self._toggle_topmost())

        # 唤醒状态机
        self.awaiting_command = False
        self.awaiting_deadline = 0.0
        self.awaiting_timeout_sec = 4.0

        # UI
        self._build_ui()

        # ASR
        self.is_listening = False
        self.recognizer = None
        self._init_asr()
        self._refresh_status()

        # 托盘 / 热键
        self._tray_icon = None
        self._tray_thread = None
        self._hotkey_thread = None
        self._hotkey_listener = None

        self._closing = False

        # 关闭窗口：TRAY 开 -> 隐藏；TRAY 关 -> 退出
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray if TRAY_ENABLED else self.quit_app)

        if TRAY_ENABLED:
            self._start_tray()

        self._start_global_hotkey()

        # ✅ GUI 不再提供“思考按钮”，但把配置值回调给主程序（只回调一次）
        if self.on_think_toggle_callback:
            try:
                self.on_think_toggle_callback(bool(THINK_MOTION_ENABLED))
            except Exception as e:
                print(f"❌ [GUI] 思考回调失败: {e}")

        # 可选：启动即最小化到托盘
        if TRAY_ENABLED and START_MINIMIZED_TO_TRAY:
            self.root.after(250, self.hide_to_tray)

    # ---------------- UI ----------------
    def _build_ui(self):
        frame_top = tk.Frame(self.root, bg=self.C_BG)
        frame_top.pack(fill=tk.X, padx=8, pady=(8, 6))

        self.entry = tk.Entry(
            frame_top,
            font=("微软雅黑", 11),
            bg=self.C_PANEL,
            fg=self.C_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#D1D5DB",
            highlightcolor="#9CA3AF",
        )
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.entry.bind("<Return>", self.handle_send)

        self.btn_send = tk.Button(
            frame_top,
            text="发送",
            command=self.handle_send,
            bg=self.C_BTN,
            fg=self.C_TEXT,
            relief="flat",
            padx=10,
            pady=6,
            activebackground=self.C_BTN_HOVER,
        )
        self.btn_send.pack(side=tk.LEFT, padx=(6, 0))

        frame_bottom = tk.Frame(self.root, bg=self.C_BG)
        frame_bottom.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.btn_voice = tk.Button(
            frame_bottom,
            text="🎤 按下说话",
            command=self.toggle_manual_voice,
            bg=self.C_BTN,
            fg=self.C_TEXT,
            relief="flat",
            padx=10,
            pady=6,
            activebackground=self.C_BTN_HOVER,
        )
        self.btn_voice.pack(side=tk.LEFT)

        self.wake_mode_var = tk.BooleanVar()
        self.chk_wake = tk.Checkbutton(
            frame_bottom,
            text="🔔 唤醒",
            variable=self.wake_mode_var,
            command=self.on_wake_mode_change,
            bg=self.C_BG,
            fg=self.C_TEXT,
            activebackground=self.C_BG,
            selectcolor=self.C_BG,
            relief="flat",
        )
        self.chk_wake.pack(side=tk.LEFT, padx=(10, 0))

        self.tts_var = tk.BooleanVar(value=bool(TTS_ENABLED))
        self.chk_tts = tk.Checkbutton(
            frame_bottom,
            text="🔊 语音",
            variable=self.tts_var,
            command=self.on_tts_toggle,
            bg=self.C_BG,
            fg=self.C_TEXT,
            activebackground=self.C_BG,
            selectcolor=self.C_BG,
            relief="flat",
        )
        self.chk_tts.pack(side=tk.LEFT, padx=(10, 0))

        # ✅ 移除“🧠 思考”按钮（你说没必要了）

        self.lbl_status = tk.Label(
            frame_bottom,
            text="Idle",
            bg=self.C_BG,
            fg=self.C_MUTED,
            font=("微软雅黑", 9),
        )
        self.lbl_status.pack(side=tk.RIGHT)

    def _set_alpha(self, a: float):
        try:
            self.root.attributes("-alpha", float(a))
        except Exception:
            pass

    def _toggle_topmost(self):
        try:
            cur = bool(self.root.attributes("-topmost"))
            self.root.attributes("-topmost", not cur)
            self._refresh_status()
        except Exception:
            pass

    def _refresh_status(self):
        parts = []
        parts.append("Listening" if self.is_listening else "Idle")
        if self.wake_mode_var.get():
            parts.append("Wake")
        if self.tts_var.get():
            parts.append("TTS")
        try:
            if bool(self.root.attributes("-topmost")):
                parts.append("Top")
        except Exception:
            pass
        self.lbl_status.config(text=" | ".join(parts))

    # ---------------- Tray ----------------
    def _make_tray_image(self):
        if Image is None:
            return None
        # 64x64 简易图标（你也可以换成读取 ico/png）
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([8, 8, 56, 56], radius=12, fill=(37, 99, 235, 220))
        d.text((22, 18), "L2", fill=(255, 255, 255, 255))
        return img

    def _start_tray(self):
        if pystray is None:
            print("⚠️ [Tray] 未安装 pystray/pillow，托盘功能不可用。")
            return

        def _on_toggle(icon, item):
            # 关键：默认动作改成“切换显示/隐藏”
            self.root.after(0, self.toggle_show_hide)

        def _on_top(icon, item):
            self.root.after(0, self._toggle_topmost)

        def _on_quit(icon, item):
            self.root.after(0, self.quit_app)

        menu = pystray.Menu(
            _TrayItem("显示/隐藏", _on_toggle, default=True),
            _TrayItem("切换置顶", _on_top),
            pystray.Menu.SEPARATOR,
            _TrayItem("退出", _on_quit),
        )

        self._tray_icon = pystray.Icon(
            "live2d_agent",
            icon=self._make_tray_image(),
            title="Live2D Agent",
            menu=menu,
        )

        def _run():
            try:
                self._tray_icon.run()
            except Exception as e:
                print(f"⚠️ [Tray] run error: {e}")

        self._tray_thread = threading.Thread(target=_run, daemon=True)
        self._tray_thread.start()
        print("✅ [Tray] 托盘已启动（默认动作：显示/隐藏）")

    # ---------------- Global Hotkey ----------------
    def _start_global_hotkey(self):
        if pynput_keyboard is None:
            print("⚠️ [Hotkey] 未安装 pynput，全局热键不可用。")
            return

        combo = HOTKEY_TOGGLE_GUI or "<ctrl>+<alt>+space"

        def _toggle():
            # 注意：这是在 pynput 线程里触发，必须 root.after 回到 Tk 主线程
            self.root.after(0, self.toggle_show_hide)

        def _run():
            try:
                with pynput_keyboard.GlobalHotKeys({combo: _toggle}) as listener:
                    self._hotkey_listener = listener
                    listener.join()
            except Exception as e:
                print(f"⚠️ [Hotkey] 监听失败: {e}")

        self._hotkey_thread = threading.Thread(target=_run, daemon=True)
        self._hotkey_thread.start()
        print(f"✅ [Hotkey] 全局热键已启用: {combo}")

    # ---------------- Window show/hide ----------------
    def hide_to_tray(self):
        # 不退出，只隐藏
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show_from_tray(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            try:
                self.entry.focus_set()
            except Exception:
                pass
            self._set_alpha(self.alpha_active)
        except Exception:
            pass

    def toggle_show_hide(self):
        try:
            # state: "withdrawn" / "normal"
            if str(self.root.state()) == "withdrawn":
                self.show_from_tray()
            else:
                self.hide_to_tray()
        except Exception:
            # 兜底：尝试 show
            self.show_from_tray()

    def quit_app(self):
        if self._closing:
            return
        self._closing = True
        print("👋 [GUI] 退出程序...")

        # 停掉托盘
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass

        # 停掉热键 listener
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass

        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------------- ASR ----------------
    def _init_asr(self):
        try:
            print("⏳ [GUI] 正在加载 Sherpa-ONNX 模型...")
            model_dir = "./sherpa_model"
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=f"{model_dir}/tokens.txt",
                encoder=f"{model_dir}/encoder-epoch-99-avg-1.onnx",
                decoder=f"{model_dir}/decoder-epoch-99-avg-1.onnx",
                joiner=f"{model_dir}/joiner-epoch-99-avg-1.onnx",
                num_threads=1,
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
            )
            print("✅ [GUI] ASR 模型加载成功！")
        except Exception as e:
            print(f"❌ [GUI] 模型加载失败: {e}")
            try:
                self.chk_wake.config(state="disabled", text="❌ 模型异常")
            except Exception:
                pass

    # ---------------- Callbacks ----------------
    def on_tts_toggle(self):
        enabled = bool(self.tts_var.get())
        print(f"🔊 [GUI] 语音开关: {'开' if enabled else '关'}")
        self._refresh_status()
        if self.on_tts_toggle_callback:
            try:
                self.on_tts_toggle_callback(enabled)
            except Exception as e:
                print(f"❌ [GUI] 语音回调失败: {e}")

    def handle_send(self, event=None, text_override=None):
        text = text_override if text_override else self.entry.get().strip()
        if text:
            if not text_override:
                self.entry.delete(0, tk.END)
            print(f"📨 [GUI] 发送指令: {text}")
            self.on_send_callback(text)

    def send_voice_text(self, text):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, text)
        self.handle_send(text_override=text)

    def toggle_manual_voice(self):
        if self.is_listening:
            self.is_listening = False
            self.btn_voice.config(text="🎤 按下说话", bg=self.C_BTN)
            self._refresh_status()
        else:
            self.is_listening = True
            self.btn_voice.config(text="🛑 停止", bg=self.C_DANGER)
            self._refresh_status()
            threading.Thread(target=self.listen_loop, args=(False,), daemon=True).start()

    def on_wake_mode_change(self):
        if self.wake_mode_var.get():
            if not self.is_listening:
                self.is_listening = True
                self.btn_voice.config(state="disabled")
                self._refresh_status()
                threading.Thread(target=self.listen_loop, args=(True,), daemon=True).start()
        else:
            self.is_listening = False
            self.btn_voice.config(state="normal")
            self._refresh_status()

    # ---------------- Listen Loop ----------------
    def listen_loop(self, is_wake_mode: bool):
        if not self.recognizer:
            return

        stream = self.recognizer.create_stream()
        SAMPLE_RATE = 16000
        CHUNK_SIZE = 4800

        DEVICE_ID = None
        GAIN_FACTOR = 1.0
        SILENCE_TIMEOUT = 0.9

        last_active_time = time.time()
        has_speech = False
        last_text = ""

        try:
            with sd.InputStream(device=DEVICE_ID, channels=1, dtype="float32", samplerate=SAMPLE_RATE) as s:
                # 噪声校准
                calib_secs = 0.6
                calib_chunks = max(1, int((SAMPLE_RATE * calib_secs) / CHUNK_SIZE))
                noise_rms = []
                for _ in range(calib_chunks):
                    if not self.is_listening:
                        return
                    data, _ = s.read(CHUNK_SIZE)
                    x = data.reshape(-1) * GAIN_FACTOR
                    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
                    noise_rms.append(rms)

                base = float(np.median(noise_rms)) if noise_rms else 0.01
                NOISE_THRESHOLD = max(0.02, base * 3.0)

                while self.is_listening:
                    data, _ = s.read(CHUNK_SIZE)
                    if not self.is_listening:
                        break

                    x = data.reshape(-1) * GAIN_FACTOR
                    current_rms = float(np.sqrt(np.mean(x * x)) + 1e-12)

                    if is_wake_mode and self.awaiting_command:
                        if time.time() > self.awaiting_deadline:
                            self.awaiting_command = False

                    if current_rms > NOISE_THRESHOLD:
                        last_active_time = time.time()
                        has_speech = True

                    stream.accept_waveform(SAMPLE_RATE, x)
                    while self.recognizer.is_ready(stream):
                        self.recognizer.decode_stream(stream)

                    partial_text = self.recognizer.get_result(stream).strip()
                    if partial_text:
                        last_text = partial_text

                    silence_duration = time.time() - last_active_time
                    if has_speech and silence_duration > SILENCE_TIMEOUT:
                        final_text = (last_text or "").strip()
                        self.recognizer.reset(stream)
                        has_speech = False
                        last_text = ""
                        last_active_time = time.time()

                        if is_wake_mode:
                            if self.awaiting_command:
                                short_ok = len(final_text) <= 6
                                if short_ok and wake_match_once(final_text, WAKE_KEYWORDS, max_edit=1):
                                    self.awaiting_deadline = time.time() + self.awaiting_timeout_sec
                                    continue
                                if final_text:
                                    self.awaiting_command = False
                                    self.root.after(0, lambda t=final_text: self.send_voice_text(t))
                                else:
                                    self.awaiting_deadline = time.time() + self.awaiting_timeout_sec
                                continue

                            short_ok = len(final_text) <= 6
                            triggered = short_ok and wake_match_once(final_text, WAKE_KEYWORDS, max_edit=1)
                            if triggered:
                                if PLAY_WAKE_SOUND and winsound:
                                    try:
                                        winsound.Beep(1000, 200)
                                    except Exception:
                                        pass
                                self.awaiting_command = True
                                self.awaiting_deadline = time.time() + self.awaiting_timeout_sec
                        else:
                            if final_text:
                                self.root.after(0, lambda t=final_text: self.send_voice_text(t))
                            self.is_listening = False
                            self.root.after(0, lambda: self.btn_voice.config(text="🎤 按下说话", bg=self.C_BTN))
                            self.root.after(0, self._refresh_status)
                            return

        except Exception as e:
            print(f"\n❌ 录音异常: {e}")
        finally:
            if not self.wake_mode_var.get():
                self.is_listening = False
                self.root.after(0, lambda: self.btn_voice.config(text="🎤 按下说话", bg=self.C_BTN))
                self.root.after(0, self._refresh_status)

    def run(self):
        self.root.mainloop()


# ====== Wake 同音触发工具函数 ======
def _norm_text_for_wake(t: str) -> str:
    t = (t or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[，。！？!?,\.]", "", t)
    return t


def to_pinyin_str(t: str) -> str:
    t = _norm_text_for_wake(t)
    if not t:
        return ""
    return "".join(lazy_pinyin(t))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, dele, sub))
        prev = cur
    return prev[-1]


def wake_match_once(asr_text: str, keywords: list[str], max_edit: int = 1) -> bool:
    raw = _norm_text_for_wake(asr_text)
    if not raw:
        return False

    for kw in keywords:
        kw_raw = _norm_text_for_wake(kw)
        if kw_raw and (kw_raw in raw):
            return True

    text_py = to_pinyin_str(raw)
    kw_pys = []
    for kw in keywords:
        kw_py = to_pinyin_str(kw)
        if not kw_py:
            continue
        kw_pys.append(kw_py)
        if kw_py in text_py:
            return True

    for kw_py in kw_pys:
        if len(kw_py) <= 8:
            if levenshtein(text_py, kw_py) <= max_edit:
                return True

    return False
