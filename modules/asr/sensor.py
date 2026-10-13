"""Voice capture + recognition + correction pipeline.

Migrated from ``modules/voice_sensor.py``.  External dependencies are now
fully injected (config object, logger, settings store, character provider,
health hook) instead of imported from the host project.  A
:class:`~modules.asr.corrector.AsrCorrector` runs between recognition and
the downstream ``chat_service`` callback so systematic recognition errors
can be fixed before the text reaches the model.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Optional

import numpy as np
import sounddevice as sd

from .config import AsrConfig
from .corrector import AsrCorrector
from .engine import (
    SAMPLE_RATE,
    ZIPFORMER_SAMPLES_PER_READ,
    create_asr_engine,
)
from .ports import CharacterProvider, HealthHook, LoggerPort, SettingsStore, default_logger
from .settings import (
    ASR_ENGINE_KEY,
    ASR_ENGINE_QWEN3,
    configure,
    load_asr_settings,
    resolve_asr_hotwords,
    resolve_wake_words,
    should_accept_voice_utterance,
)

__all__ = ["VoiceSensor"]


class VoiceSensor:
    def __init__(
        self,
        chat_service: Any,
        config: AsrConfig,
        *,
        event_bus: Any = None,
        logger: Optional[LoggerPort] = None,
        settings_store: Optional[SettingsStore] = None,
        character_provider: Optional[CharacterProvider] = None,
        health_hook: Optional[HealthHook] = None,
        engine: Any = None,
        corrector: Optional[AsrCorrector] = None,
    ):
        self.logger: LoggerPort = logger or default_logger()
        # Inject settings store / character provider into the settings module so
        # legacy-style resolve_* call sites pick them up without extra plumbing.
        configure(
            settings_store=settings_store,
            character_provider=character_provider,
            logger=self.logger,
        )
        self.chat_service = chat_service
        self.event_bus = event_bus
        self.config = config
        self.running = False
        self._thread = None
        self._loop = None
        self.use_local_capture = True

        self.zipformer_cfg = dict(config.sherpa_cfg or {})
        self.base_dir = config.base_dir
        self.audio_buffer: list[np.ndarray] = []
        self.engine = None
        self._rebuild_requested = False

        self.blacklist = list(config.blacklist or [])
        self.is_woken = False
        self.last_active_time = 0.0
        self.health_hook = health_hook

        # Push-to-talk state (coexists with always-on mode)
        self._ptt_active = False
        self._ptt_was_running = False

        self._settings_lock = threading.Lock()
        self._engine_lock = threading.Lock()
        self._asr_settings = load_asr_settings(config=config)
        self.wake_words = resolve_wake_words(settings=self._asr_settings, config=config)
        self.active_window = int(self._asr_settings.get("asr_active_window_sec") or 20)
        self.require_wake_word = bool(self._asr_settings.get("asr_require_wake_word", True))
        self.min_chars = int(self._asr_settings.get("asr_min_chars") or 2)
        self.engine_id = str(self._asr_settings.get(ASR_ENGINE_KEY) or ASR_ENGINE_QWEN3)
        self._hotwords = resolve_asr_hotwords(
            settings=self._asr_settings, wake_words=self.wake_words, config=config
        )

        self.corrector = corrector or AsrCorrector(
            config.resolved_rules_path(),
            config.resolved_learned_path(),
            logger=self.logger,
        )

        if engine is not None:
            self.engine = engine
        else:
            self._init_engine()

    def reload_settings(self, settings: dict | None = None) -> dict:
        """Hot-reload ASR policy (wake words, free-listen, window, engine)."""
        cfg = load_asr_settings(settings, config=self.config)
        words = resolve_wake_words(settings=cfg, config=self.config)
        hotwords = resolve_asr_hotwords(settings=cfg, wake_words=words, config=self.config)
        engine_id = str(cfg.get(ASR_ENGINE_KEY) or ASR_ENGINE_QWEN3)
        with self._settings_lock:
            self._asr_settings = cfg
            self.wake_words = words
            self.active_window = int(cfg.get("asr_active_window_sec") or 20)
            self.require_wake_word = bool(cfg.get("asr_require_wake_word", True))
            self.min_chars = int(cfg.get("asr_min_chars") or 2)
            self._hotwords = hotwords
            if not self.require_wake_word:
                self.is_woken = True
            engine_changed = engine_id != self.engine_id
            self.engine_id = engine_id
        with self._engine_lock:
            if self.engine is not None:
                self.engine.set_hotwords(hotwords)
        if engine_changed:
            if self.running:
                self._rebuild_requested = True
                self.logger.info(
                    "🎤 [Voice] 引擎将切换为 %s（下一轮音频生效）", engine_id
                )
            else:
                self._init_engine()
        self.logger.info(
            "🎤 [Voice] ASR 策略已更新: engine=%s require_wake=%s window=%ss words=%s",
            engine_id,
            self.require_wake_word,
            self.active_window,
            words,
        )
        return {
            "require_wake_word": self.require_wake_word,
            "active_window_sec": self.active_window,
            "wake_words": list(words),
            "min_chars": self.min_chars,
            "engine": engine_id,
        }

    def _snapshot_policy(self) -> tuple[dict, list[str]]:
        with self._settings_lock:
            return dict(self._asr_settings), list(self.wake_words)

    def _init_engine(self) -> None:
        with self._settings_lock:
            engine_id = self.engine_id
            hotwords = self._hotwords
        engine = create_asr_engine(
            engine_id,
            base_dir=self.base_dir,
            zipformer_cfg=self.zipformer_cfg,
            provider=str(self.config.provider or "cpu"),
            num_threads=int(self.config.num_threads or 2),
            hotwords=hotwords,
            logger=self.logger,
            health_hook=self.health_hook,
            allow_fallback=True,
        )
        with self._engine_lock:
            old = self.engine
            self.engine = engine
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        if engine is None or not engine.ready:
            self.logger.error(
                "❌ [Voice] ASR 引擎未初始化。默认 Qwen3 需要新模型与 sherpa-onnx>=1.12.34；"
                "也可在设置中切回 Zipformer。"
            )
        elif engine.name != engine_id:
            self.logger.warning(
                "🎤 [Voice] 已回退到 %s（请求的是 %s）", engine.name, engine_id
            )

    @property
    def recognizer(self):
        engine = self.engine
        if engine is None or not getattr(engine, "ready", False):
            return None
        return getattr(engine, "recognizer", engine)

    def start(self, loop, *, capture: bool | None = None):
        if capture is not None:
            self.use_local_capture = bool(capture)
        if self.engine is None or not getattr(self.engine, "ready", False):
            self._init_engine()
        if self.engine is None or not getattr(self.engine, "ready", False):
            self.logger.error(
                "❌ [Voice] 致命错误：监听线程启动中止。ASR 识别器未初始化！"
                "请检查开机时的日志：Qwen3 模型 / silero_vad.onnx / sherpa-onnx 版本。"
            )
            return

        if self.running:
            return

        self.reload_settings()
        self._loop = loop
        self.running = True
        if self.use_local_capture:
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            self.logger.info("🎤 [Voice] 监听线程已启动")
        else:
            self._thread = None
            self.logger.info("🎤 [Voice] 远程 PCM 模式（不打开本机麦克风）")

    def feed_audio(self, samples) -> None:
        """Ingest a float32 mono 16 kHz chunk from a remote capture client."""
        if not self.running:
            return
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return
        self._ingest_samples(audio)

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

        with sd.InputStream(
            channels=1,
            dtype="float32",
            samplerate=SAMPLE_RATE,
            latency="high",
        ) as stream:
            while self.running:
                if self._rebuild_requested:
                    self._rebuild_requested = False
                    self._init_engine()
                    if self.engine is None or not self.engine.ready:
                        time.sleep(0.2)
                        continue

                with self._engine_lock:
                    engine = self.engine
                if engine is None or not engine.ready:
                    time.sleep(0.2)
                    continue

                chunk = int(engine.samples_per_read or ZIPFORMER_SAMPLES_PER_READ)
                samples, _ = stream.read(chunk)
                samples = samples.reshape(-1)
                self._ingest_samples(samples)

    def _ingest_samples(self, samples) -> None:
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return
        if self._rebuild_requested:
            self._rebuild_requested = False
            self._init_engine()
        with self._engine_lock:
            engine = self.engine
        if engine is None or not engine.ready:
            return
        if getattr(self, "speaker_extractor", None) and getattr(
            self, "owner_embedding", None
        ):
            self.audio_buffer.append(audio)
        utterances = engine.accept_audio(audio)
        for result_text in utterances:
            is_owner = True
            similarity = 1.0
            if getattr(self, "speaker_extractor", None) and self.audio_buffer:
                full_sentence_audio = np.concatenate(self.audio_buffer)
                is_owner, similarity = self._verify_speaker(full_sentence_audio)
            self.audio_buffer = []
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
            config=self.config,
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

        # ── 识别纠错层：在交给模型前修正系统性识别错误 ─────────────────────
        corrected, applied = self.corrector.correct(text)
        if applied:
            self.logger.info("🔧 [Voice] 纠错: %s → %s (规则 %s)", text, corrected, applied)
            text = corrected

        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.chat_service.process(text, ctx={"source": "voice"}),
                self._loop,
            )

    # ── Push-to-talk ──────────────────────────────────────────────────
    # Hold right-Ctrl to temporarily bypass the wake-word requirement and
    # listen freely; release to stop.  This coexists with the existing
    # always-on / wake-word mode — when PTT is active the sensor behaves
    # as if in free-listen mode regardless of the configured policy.

    def start_ptt(self) -> None:
        """Begin a push-to-talk session: temporarily enable listening."""
        if self._ptt_active:
            return
        self._ptt_active = True

        # Remember whether the monitor loop was already running so we can
        # restore the original state on stop.
        self._ptt_was_running = self.running

        # Force free-listen: bypass wake word + mark as woken.
        with self._settings_lock:
            self._ptt_saved_require_wake = self.require_wake_word
            self._ptt_saved_woken = self.is_woken
            self.require_wake_word = False
            self.is_woken = True

        if not self._ptt_was_running:
            # Sensor was off — start the monitor loop now.
            if self._loop is not None:
                self.start(self._loop)
            else:
                self.logger.warning(
                    "🎤 [Voice] PTT: 无事件循环，无法启动监听线程"
                )
        self.logger.info("🎤 [Voice] PTT 开始（免唤醒模式）")

    def stop_ptt(self) -> None:
        """End a push-to-talk session: restore prior policy."""
        if not self._ptt_active:
            return
        self._ptt_active = False

        with self._settings_lock:
            self.require_wake_word = getattr(
                self, "_ptt_saved_require_wake", self.require_wake_word
            )
            self.is_woken = getattr(self, "_ptt_saved_woken", self.is_woken)

        # If the sensor was originally off, stop it again.
        if not self._ptt_was_running:
            self.stop()

        self.logger.info("🎤 [Voice] PTT 结束（恢复原模式）")
