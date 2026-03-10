import threading
import asyncio
import sys
import time
import numpy as np
import sounddevice as sd

from config import WAKE_KEYWORDS, GATEKEEPER_ACTIVE_SESSION_WINDOW, GATEKEEPER_BLACKLIST

try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

from core.logger import get_logger
from core.event_bus import Events

# 默认音频配置
SAMPLE_RATE = 16000
SAMPLES_PER_READ = 8000  # 0.5秒的数据块


class VoiceSensor:
    def __init__(self, chat_service, event_bus, config_path: dict):
        self.logger = get_logger()
        self.chat_service = chat_service
        self.event_bus = event_bus
        self.running = False
        self._thread = None
        self._loop = None

        # Sherpa-ONNX 配置
        self.tokens_path = config_path.get("tokens")
        self.encoder_path = config_path.get("encoder")
        self.decoder_path = config_path.get("decoder")
        self.joiner_path = config_path.get("joiner")
        # 如果是 zipformer 这种模型，可能参数略有不同，这里以 streaming transducer 为例

        self.recognizer = None
        self.stream = None
        self._init_recognizer()

        # 唤醒词列表（如果不想让它一直插话，可以用这个做简单的过滤）
        self.wake_words = WAKE_KEYWORDS
        self.active_window = GATEKEEPER_ACTIVE_SESSION_WINDOW
        self.blacklist = GATEKEEPER_BLACKLIST

        # 是否处于“已唤醒”状态（简易状态机）
        self.is_woken = False
        self.last_active_time = 0

    def _init_recognizer(self):
        if not sherpa_onnx:
            self.logger.error("❌ 未安装 sherpa-onnx，语音功能不可用")
            return

        import os

        # 1. 强制绝对路径转换与存在性校验
        paths = {
            "tokens": self.tokens_path,
            "encoder": self.encoder_path,
            "decoder": self.decoder_path,
            "joiner": self.joiner_path
        }

        for name, path in paths.items():
            abs_p = os.path.abspath(path)
            if not os.path.exists(abs_p):
                self.logger.error(f"❌ [Voice] 物理文件丢失: [{name}] 期望路径为 -> {abs_p}")
                self.recognizer = None
                return

            # 2. 识别 Git LFS 指针陷阱
            if name != "tokens":
                size_kb = os.path.getsize(abs_p) / 1024
                if size_kb < 100:
                    self.logger.error(
                        f"❌ [Voice] 模型文件体积异常 ({size_kb:.1f} KB): {abs_p}。你下载的可能是 Git LFS 指针文件！")
                    self.recognizer = None
                    return

        try:
            # 3. 🟢 适配 sherpa-onnx 1.12.x 最新 API (舍弃旧的 Config 类)
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
                rule3_min_utterance_length=300,  # 相当于无限长
            )

            self.stream = self.recognizer.create_stream()
            self.logger.info("🎤 [Voice] Sherpa-ONNX 语音识别引擎已就绪")

        except Exception as e:
            self.logger.error(f"❌ [Voice] ASR模型底层加载失败: {e}")
            self.recognizer = None

    def start(self, loop):
        if not self.recognizer:
            self.logger.error(
                "❌ [Voice] 致命错误：监听线程启动中止。ASR 识别器未初始化！请检查开机时的日志，确认模型文件是否已正确下载并放置在指定路径。")
            return

        self._loop = loop
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self.logger.info("🎤 [Voice] 监听线程已启动")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _monitor_loop(self):
        self.logger.info(f"🎤 [Voice] 正在使用设备: {sd.query_devices(kind='input')['name']}")

        last_partial_text = ""  # 用于记录上一次的动态输出片段

        with sd.InputStream(channels=1, dtype="float32", samplerate=SAMPLE_RATE) as s:
            while self.running:
                samples, _ = s.read(SAMPLES_PER_READ)
                samples = samples.reshape(-1)

                self.stream.accept_waveform(SAMPLE_RATE, samples)

                if getattr(self, "speaker_extractor", None) and getattr(self, "owner_embedding", None):
                    self.audio_buffer.append(samples)

                while self.recognizer.is_ready(self.stream):
                    self.recognizer.decode_stream(self.stream)

                is_endpoint = self.recognizer.is_endpoint(self.stream)
                result_text = self.recognizer.get_result(self.stream).strip()

                # 🟢 核心重构：实时流式输出（使用回车符 \r 和终端清除符 \033[K）
                if result_text and result_text != last_partial_text:
                    print(f"\r👂 [实时听写]: {result_text}\033[K", end="", flush=True)
                    last_partial_text = result_text

                if is_endpoint and result_text:
                    print()  # 端点检测完成，执行换行释放当前控制台行
                    last_partial_text = ""  # 清理状态

                    is_owner = True
                    similarity = 1.0

                    if getattr(self, "speaker_extractor", None) and self.audio_buffer:
                        full_sentence_audio = np.concatenate(self.audio_buffer)
                        is_owner, similarity = self._verify_speaker(full_sentence_audio)

                    self.audio_buffer = []
                    self.recognizer.reset(self.stream)

                    if is_owner:
                        if getattr(self, "speaker_extractor", None):
                            self.logger.info(f"✅ [Voice] 声纹匹配 (相似度: {similarity:.2f})")
                        self._process_sentence(result_text)
                    else:
                        self.logger.warning(f"🚫 [Voice] 声纹不匹配 (相似度: {similarity:.2f})，已拦截: {result_text}")

    def _process_sentence(self, text):
        """处理识别到的完整句子"""
        self.logger.info(f"🎤 [Voice] 听到: {text}")

        # 🟢 3. 新增：基础黑名单过滤（直接拦截杂音或太短的误触发）
        if any(b in text for b in self.blacklist) or len(text) < 2:
            self.logger.debug(f"🔇 [Voice] 命中黑名单或过短，已过滤: {text}")
            return

        now = time.time()

        # 1. 检查是否包含唤醒词
        has_wake_word = any(w in text for w in self.wake_words)

        # 2. 判断是否处于活跃对话窗口期
        is_active_session = (now - self.last_active_time) < self.active_window

        should_reply = False

        if has_wake_word:
            should_reply = True
            self.last_active_time = now
            self.is_woken = True
            self.logger.info("⚡ [Voice] 触发唤醒词")

        elif self.is_woken and is_active_session:
            should_reply = True
            self.last_active_time = now  # 续杯，重置活跃窗口期

        elif not self.is_woken:
            # 未唤醒状态，忽略
            self.logger.debug("💤 [Voice] 未唤醒，忽略该语音")
            return

        if should_reply and self._loop:
            # 发送给 ChatService 处理
            # source='voice' 可以让 ChatService 知道这是语音输入的，做相应处理
            asyncio.run_coroutine_threadsafe(
                self.chat_service.process(text, ctx={"source": "voice"}),
                self._loop
            )