import asyncio
import threading
import time

import numpy as np
import sounddevice as sd

from config import GATEKEEPER_BLACKLIST, SHERPA_MODEL_CONFIG

try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

from core.logger import get_logger
from modules.asr_settings import (
    load_asr_settings,
    resolve_wake_words,
    should_accept_voice_utterance,
)

# 默认音频配置
SAMPLE_RATE = 16000
SAMPLES_PER_READ = 8000  # 0.5秒的数据块


class VoiceSensor:
    def __init__(self, chat_service, event_bus, config_path: dict | None = None):
        self.logger = get_logger()
        self.chat_service = chat_service
        self.event_bus = event_bus
        self.running = False
        self._thread = None
        self._loop = None

        model_cfg = dict(config_path or SHERPA_MODEL_CONFIG or {})
        self.tokens_path = model_cfg.get("tokens")
        self.encoder_path = model_cfg.get("encoder")
        self.decoder_path = model_cfg.get("decoder")
        self.joiner_path = model_cfg.get("joiner")

        self.recognizer = None
        self.stream = None
        self.audio_buffer = []
        self._init_recognizer()

        self.blacklist = list(GATEKEEPER_BLACKLIST or [])
        self.is_woken = False
        self.last_active_time = 0.0

        self._settings_lock = threading.Lock()
        self._asr_settings = load_asr_settings()
        self.wake_words = resolve_wake_words(settings=self._asr_settings)
        self.active_window = int(
            self._asr_settings.get("asr_active_window_sec") or 20
        )
        self.require_wake_word = bool(
            self._asr_settings.get("asr_require_wake_word", True)
        )
        self.min_chars = int(self._asr_settings.get("asr_min_chars") or 2)

    def reload_settings(self, settings: dict | None = None) -> dict:
        """Hot-reload ASR policy (wake words, free-listen, window)."""
        cfg = load_asr_settings(settings)
        words = resolve_wake_words(settings=cfg)
        with self._settings_lock:
            self._asr_settings = cfg
            self.wake_words = words
            self.active_window = int(cfg.get("asr_active_window_sec") or 20)
            self.require_wake_word = bool(cfg.get("asr_require_wake_word", True))
            self.min_chars = int(cfg.get("asr_min_chars") or 2)
            if not self.require_wake_word:
                # Free-listen: treat sensor as already awake for window bookkeeping.
                self.is_woken = True
        self.logger.info(
            "🎤 [Voice] ASR 策略已更新: require_wake=%s window=%ss words=%s",
            self.require_wake_word,
            self.active_window,
            words,
        )
        return {
            "require_wake_word": self.require_wake_word,
            "active_window_sec": self.active_window,
            "wake_words": list(words),
            "min_chars": self.min_chars,
        }

    def _snapshot_policy(self) -> tuple[dict, list[str]]:
        with self._settings_lock:
            return dict(self._asr_settings), list(self.wake_words)

    def _init_recognizer(self):
        if not sherpa_onnx:
            self.logger.error("❌ 未安装 sherpa-onnx，语音功能不可用")
            return

        import os

        paths = {
            "tokens": self.tokens_path,
            "encoder": self.encoder_path,
            "decoder": self.decoder_path,
            "joiner": self.joiner_path,
        }

        for name, path in paths.items():
            abs_p = os.path.abspath(path or "")
            if not path or not os.path.exists(abs_p):
                self.logger.error(
                    f"❌ [Voice] 物理文件丢失: [{name}] 期望路径为 -> {abs_p}"
                )
                self.recognizer = None
                return

            if name != "tokens":
                size_kb = os.path.getsize(abs_p) / 1024
                if size_kb < 100:
                    self.logger.error(
                        f"❌ [Voice] 模型文件体积异常 ({size_kb:.1f} KB): {abs_p}。"
                        "你下载的可能是 Git LFS 指针文件！"
                    )
                    self.recognizer = None
                    return

        try:
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=os.path.abspath(self.tokens_path),
                encoder=os.path.abspath(self.encoder_path),
                decoder=os.path.abspath(self.decoder_path),
                joiner=os.path.abspath(self.joiner_path),
                num_threads=1,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=1.2,
                rule3_min_utterance_length=300,
            )

            self.stream = self.recognizer.create_stream()
            self.logger.info("🎤 [Voice] Sherpa-ONNX 语音识别引擎已就绪")

        except Exception as e:
            self.logger.error(f"❌ [Voice] ASR模型底层加载失败: {e}")
            self.recognizer = None

    def start(self, loop):
        if not self.recognizer:
            self.logger.error(
                "❌ [Voice] 致命错误：监听线程启动中止。ASR 识别器未初始化！"
                "请检查开机时的日志，确认模型文件是否已正确下载并放置在指定路径。"
            )
            return

        if self.running:
            return

        # Refresh policy on each start so role switch / settings apply.
        self.reload_settings()
        self._loop = loop
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self.logger.info("🎤 [Voice] 监听线程已启动")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _monitor_loop(self):
        try:
            device_name = sd.query_devices(kind="input")["name"]
        except Exception:
            device_name = "default"
        self.logger.info(f"🎤 [Voice] 正在使用设备: {device_name}")

        last_partial_text = ""

        with sd.InputStream(channels=1, dtype="float32", samplerate=SAMPLE_RATE) as s:
            while self.running:
                samples, _ = s.read(SAMPLES_PER_READ)
                samples = samples.reshape(-1)

                self.stream.accept_waveform(SAMPLE_RATE, samples)

                if getattr(self, "speaker_extractor", None) and getattr(
                    self, "owner_embedding", None
                ):
                    self.audio_buffer.append(samples)

                while self.recognizer.is_ready(self.stream):
                    self.recognizer.decode_stream(self.stream)

                is_endpoint = self.recognizer.is_endpoint(self.stream)
                result_text = self.recognizer.get_result(self.stream).strip()

                if result_text and result_text != last_partial_text:
                    print(
                        f"\r👂 [实时听写]: {result_text}\033[K",
                        end="",
                        flush=True,
                    )
                    last_partial_text = result_text

                if is_endpoint and result_text:
                    print()
                    last_partial_text = ""

                    is_owner = True
                    similarity = 1.0

                    if getattr(self, "speaker_extractor", None) and self.audio_buffer:
                        full_sentence_audio = np.concatenate(self.audio_buffer)
                        is_owner, similarity = self._verify_speaker(full_sentence_audio)

                    self.audio_buffer = []
                    self.recognizer.reset(self.stream)

                    if is_owner:
                        if getattr(self, "speaker_extractor", None):
                            self.logger.info(
                                f"✅ [Voice] 声纹匹配 (相似度: {similarity:.2f})"
                            )
                        self._process_sentence(result_text)
                    else:
                        self.logger.warning(
                            f"🚫 [Voice] 声纹不匹配 (相似度: {similarity:.2f})，已拦截: {result_text}"
                        )

    def _verify_speaker(self, audio) -> tuple[bool, float]:
        """Optional speaker verification hook; default accept."""
        return True, 1.0

    def _process_sentence(self, text):
        """处理识别到的完整句子"""
        self.logger.info(f"🎤 [Voice] 听到: {text}")

        settings, wake_words = self._snapshot_policy()
        decision = should_accept_voice_utterance(
            text,
            settings=settings,
            wake_words=wake_words,
            is_woken=self.is_woken,
            last_active_time=self.last_active_time,
            now=time.time(),
            blacklist=self.blacklist,
        )

        if not decision.get("accept"):
            reason = decision.get("reason")
            if reason == "not_woken":
                self.logger.info(
                    "💤 [Voice] 未唤醒，忽略该语音（可在设置中关闭“需要唤醒词”）: %s",
                    text,
                )
            elif reason == "blacklist":
                self.logger.debug(f"🔇 [Voice] 命中黑名单，已过滤: {text}")
            elif reason == "too_short":
                self.logger.debug(f"🔇 [Voice] 过短，已过滤: {text}")
            else:
                self.logger.debug(f"🔇 [Voice] 已过滤 ({reason}): {text}")
            return

        if decision.get("wake"):
            self.logger.info("⚡ [Voice] 触发唤醒词")
        elif decision.get("reason") == "free_listen":
            self.logger.debug("🎤 [Voice] 免唤醒模式，直接受理")
        elif decision.get("reason") == "active_window":
            self.logger.debug("🎤 [Voice] 连续对话窗口内，直接受理")

        self.is_woken = bool(decision.get("woken"))
        self.last_active_time = time.time()

        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.chat_service.process(text, ctx={"source": "voice"}),
                self._loop,
            )
