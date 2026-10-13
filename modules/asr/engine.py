"""Sherpa-ONNX ASR backends for the standalone ASR package.

Qwen3-ASR-0.6B is the default (VAD + offline). Zipformer streaming is the
fallback. ``HttpAsrEngine`` is a reserved stub for a later remote box.

Migrated from ``modules/asr_engine.py``; the only change is that runtime
health reporting goes through an injected :class:`~modules.asr.ports.HealthHook`
instead of importing ``services.runtime_health`` directly, keeping this
module free of host-project imports.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional, Sequence

import numpy as np

try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

from .ports import HealthHook, LoggerPort

SAMPLE_RATE = 16000
ZIPFORMER_SAMPLES_PER_READ = 8000  # 0.5s
MIN_ONNX_BYTES = 100 * 1024

QWEN3_ENGINE = "qwen3"
ZIPFORMER_ENGINE = "zipformer"
HTTP_ENGINE = "http"

KNOWN_ENGINES = (QWEN3_ENGINE, ZIPFORMER_ENGINE, HTTP_ENGINE)


def _report_asr_health(state: str, summary: str, hook: Optional[HealthHook]) -> None:
    if hook is None:
        return
    try:
        hook.report("asr", state, summary)
    except Exception:
        pass


def format_hotwords(words: Sequence[str]) -> str:
    items: List[str] = []
    seen: set[str] = set()
    for raw in words or ():
        token = str(raw or "").strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(token)
    return "/".join(items)


def _abs(path: Optional[str]) -> str:
    return os.path.abspath(path or "")


def _file_ok(path: Optional[str], *, min_bytes: int = MIN_ONNX_BYTES) -> bool:
    abs_p = _abs(path)
    if not path or not os.path.isfile(abs_p):
        return False
    try:
        return os.path.getsize(abs_p) >= int(min_bytes)
    except OSError:
        return False


def _dir_ok(path: Optional[str]) -> bool:
    abs_p = _abs(path)
    return bool(path) and os.path.isdir(abs_p)


def _first_file(directory: str, names: Sequence[str]) -> Optional[str]:
    for name in names:
        candidate = os.path.join(directory, name)
        if _file_ok(candidate):
            return candidate
    return None


def resolve_qwen3_model_dir(base_dir: str) -> str:
    """Prefer overlay / selected.json, then the short local folder."""
    from .model_select import asr_model_root, load_selected

    root = asr_model_root(base_dir)
    selected = load_selected(base_dir).get("model_dir") or ""
    candidates = []
    if selected:
        extra = Path(selected) if os.path.isabs(selected) else root / selected
        candidates.append(str(extra))
    candidates.extend(
        [
            str(root / "qwen3-0.6b-int8"),
            str(root / "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"),
            os.path.join(base_dir, "sherpa_model", "qwen3-0.6b-int8"),
            os.path.join(
                base_dir, "sherpa_model", "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
            ),
        ]
    )
    seen = set()
    for folder in candidates:
        if folder in seen:
            continue
        seen.add(folder)
        if qwen3_paths(folder) is not None:
            return folder
    return candidates[0] if candidates else os.path.join(base_dir, "sherpa_model", "qwen3-0.6b-int8")


def qwen3_paths(model_dir: str) -> Optional[dict]:
    conv = _first_file(model_dir, ("conv_frontend.onnx",))
    encoder = _first_file(model_dir, ("encoder.int8.onnx", "encoder.onnx"))
    decoder = _first_file(model_dir, ("decoder.int8.onnx", "decoder.onnx"))
    tokenizer = os.path.join(model_dir, "tokenizer")
    if not (conv and encoder and decoder and _dir_ok(tokenizer)):
        return None
    return {
        "conv_frontend": conv,
        "encoder": encoder,
        "decoder": decoder,
        "tokenizer": tokenizer,
    }


def resolve_vad_path(base_dir: str) -> str:
    from .model_select import resolve_vad_path as _resolve_vad

    return _resolve_vad(base_dir)


def qwen3_api_available() -> bool:
    if sherpa_onnx is None:
        return False
    offline = getattr(sherpa_onnx, "OfflineRecognizer", None)
    return bool(offline) and callable(getattr(offline, "from_qwen3_asr", None))


def missing_qwen3_reason(base_dir: str) -> Optional[str]:
    """Human-readable blocker for the Qwen3 backend, or None if ready."""
    if sherpa_onnx is None:
        return "未安装 sherpa-onnx。conda 环境 live2d-llm 中执行: pip install -U 'sherpa-onnx>=1.12.34'"
    if not qwen3_api_available():
        version = getattr(sherpa_onnx, "__version__", "?")
        return (
            f"当前 sherpa-onnx {version} 没有 OfflineRecognizer.from_qwen3_asr。"
            "请升级: pip install -U 'sherpa-onnx>=1.12.34'"
        )
    vad = resolve_vad_path(base_dir)
    if not _file_ok(vad, min_bytes=50 * 1024):
        return (
            f"缺少 Silero VAD: {vad}。"
            "下载 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
        )
    folder = resolve_qwen3_model_dir(base_dir)
    if qwen3_paths(folder) is None:
        return (
            f"缺少 Qwen3-ASR-0.6B 模型目录: {folder}。"
            "下载 sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2 并解压到该路径"
        )
    return None


def _stream_text(stream: Any) -> str:
    result = getattr(stream, "result", None)
    if result is None:
        return ""
    text = getattr(result, "text", None)
    if text is None:
        text = str(result)
    return str(text or "").strip()


class AsrEngine:
    name = "base"
    ready = False
    samples_per_read = ZIPFORMER_SAMPLES_PER_READ

    def set_hotwords(self, hotwords: str) -> None:
        return None

    def accept_audio(self, samples: np.ndarray) -> List[str]:
        return []

    def close(self) -> None:
        return None


class SherpaZipformerEngine(AsrEngine):
    name = ZIPFORMER_ENGINE
    samples_per_read = ZIPFORMER_SAMPLES_PER_READ

    def __init__(
        self,
        cfg: dict,
        *,
        num_threads: int = 1,
        logger: Optional[LoggerPort] = None,
        health_hook: Optional[HealthHook] = None,
    ):
        self.logger = logger
        self.health_hook = health_hook
        self.ready = False
        self.recognizer = None
        self.stream = None
        self._last_partial = ""
        if not sherpa_onnx:
            self._error("未安装 sherpa-onnx")
            return
        paths = {
            "tokens": cfg.get("tokens"),
            "encoder": cfg.get("encoder"),
            "decoder": cfg.get("decoder"),
            "joiner": cfg.get("joiner"),
        }
        for key, path in paths.items():
            abs_p = _abs(path)
            if not path or not os.path.exists(abs_p):
                self._error(f"物理文件丢失: [{key}] -> {abs_p}")
                return
            if key != "tokens" and not _file_ok(path):
                self._error(
                    f"模型文件体积异常 ({os.path.getsize(abs_p) / 1024:.1f} KB): {abs_p}。"
                    "你下载的可能是 Git LFS 指针文件！"
                )
                return
        try:
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=_abs(paths["tokens"]),
                encoder=_abs(paths["encoder"]),
                decoder=_abs(paths["decoder"]),
                joiner=_abs(paths["joiner"]),
                num_threads=max(1, int(num_threads or 1)),
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=1.2,
                rule3_min_utterance_length=300,
            )
            self.stream = self.recognizer.create_stream()
            self.ready = True
            _report_asr_health("healthy", "语音识别已就绪", self.health_hook)
        except Exception as exc:
            self._error(f"Zipformer 加载失败: {exc}")

    def _error(self, message: str) -> None:
        self.ready = False
        if self.logger:
            self.logger.error(f"❌ [Voice] {message}")
        _report_asr_health("offline", f"语音输入未就绪：{message}", self.health_hook)

    def accept_audio(self, samples: np.ndarray) -> List[str]:
        if not self.ready or self.recognizer is None or self.stream is None:
            return []
        self.stream.accept_waveform(SAMPLE_RATE, samples)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        is_endpoint = self.recognizer.is_endpoint(self.stream)
        result_text = (self.recognizer.get_result(self.stream) or "").strip()
        if result_text and result_text != self._last_partial:
            print(f"\r👂 [实时听写]: {result_text}\033[K", end="", flush=True)
            self._last_partial = result_text
        if is_endpoint:
            self.recognizer.reset(self.stream)
            self._last_partial = ""
            if result_text:
                print()
                return [result_text]
        return []


class SherpaQwen3Engine(AsrEngine):
    name = QWEN3_ENGINE

    def __init__(
        self,
        *,
        model_dir: str,
        vad_path: str,
        num_threads: int = 2,
        provider: str = "cpu",
        hotwords: str = "",
        logger: Optional[LoggerPort] = None,
        health_hook: Optional[HealthHook] = None,
    ):
        self.logger = logger
        self.health_hook = health_hook
        self.ready = False
        self.recognizer = None
        self.vad = None
        self.samples_per_read = 512
        self._hotwords = str(hotwords or "")
        reason = None
        if sherpa_onnx is None:
            reason = "未安装 sherpa-onnx"
        elif not qwen3_api_available():
            reason = (
                f"sherpa-onnx {getattr(sherpa_onnx, '__version__', '?')} "
                "没有 from_qwen3_asr，请升级到 >=1.12.34"
            )
        paths = qwen3_paths(model_dir) if reason is None else None
        if reason is None and paths is None:
            reason = f"Qwen3 模型文件不完整: {model_dir}"
        if reason is None and not _file_ok(vad_path, min_bytes=50 * 1024):
            reason = f"缺少 Silero VAD: {vad_path}"
        if reason:
            self._error(reason)
            return
        assert paths is not None
        try:
            vad_config = sherpa_onnx.VadModelConfig()
            vad_config.silero_vad.model = _abs(vad_path)
            vad_config.silero_vad.threshold = 0.5
            vad_config.silero_vad.min_silence_duration = 0.5
            vad_config.silero_vad.min_speech_duration = 0.2
            vad_config.silero_vad.max_speech_duration = 15.0
            vad_config.sample_rate = SAMPLE_RATE
            window = int(getattr(vad_config.silero_vad, "window_size", 0) or 512)
            self.samples_per_read = window
            self.vad = sherpa_onnx.VoiceActivityDetector(
                vad_config, buffer_size_in_seconds=30
            )
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
                conv_frontend=paths["conv_frontend"],
                encoder=paths["encoder"],
                decoder=paths["decoder"],
                tokenizer=paths["tokenizer"],
                num_threads=max(1, int(num_threads or 2)),
                sample_rate=SAMPLE_RATE,
                feature_dim=128,
                provider=str(provider or "cpu"),
                hotwords=self._hotwords,
                max_total_len=1024,
                max_new_tokens=256,
            )
            self.ready = True
            if self.logger:
                self.logger.info("🎤 [Voice] Qwen3-ASR-0.6B 已就绪（VAD 切段）")
            _report_asr_health("healthy", "语音识别已就绪", self.health_hook)
        except Exception as exc:
            self._error(f"Qwen3 加载失败: {exc}")

    def _error(self, message: str) -> None:
        self.ready = False
        if self.logger:
            self.logger.error(f"❌ [Voice] {message}")
        _report_asr_health("offline", f"语音输入未就绪：{message}", self.health_hook)

    def set_hotwords(self, hotwords: str) -> None:
        self._hotwords = str(hotwords or "")

    def accept_audio(self, samples: np.ndarray) -> List[str]:
        if not self.ready or self.vad is None or self.recognizer is None:
            return []
        self.vad.accept_waveform(samples)
        utterances: List[str] = []
        while not self.vad.empty():
            segment = self.vad.front
            self.vad.pop()
            audio = np.asarray(getattr(segment, "samples", segment), dtype=np.float32)
            if audio.size < int(SAMPLE_RATE * 0.2):
                continue
            text = self._transcribe(audio)
            if text:
                utterances.append(text)
        return utterances

    def _transcribe(self, audio: np.ndarray) -> str:
        hotwords = self._hotwords or None
        try:
            stream = self.recognizer.create_stream(hotwords)
        except TypeError:
            stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self.recognizer.decode_stream(stream)
        return _stream_text(stream)


class HttpAsrEngine(AsrEngine):
    """Reserved remote backend. Not wired this round."""

    name = HTTP_ENGINE

    def transcribe(self, pcm_16k: np.ndarray, hotwords: str = "") -> str:
        raise NotImplementedError("HttpAsrEngine is a stub for a later remote ASR box")


def create_asr_engine(
    engine_id: str,
    *,
    base_dir: str,
    zipformer_cfg: Optional[dict] = None,
    provider: str = "cpu",
    num_threads: int = 2,
    hotwords: str = "",
    logger: Optional[LoggerPort] = None,
    health_hook: Optional[HealthHook] = None,
    allow_fallback: bool = True,
) -> Optional[AsrEngine]:
    """Build the requested engine; fall back to Zipformer if Qwen3 cannot load."""
    wanted = str(engine_id or QWEN3_ENGINE).strip().lower()
    if wanted not in KNOWN_ENGINES:
        wanted = QWEN3_ENGINE

    def log(message: str) -> None:
        if logger:
            logger.warning(message)

    if wanted == HTTP_ENGINE:
        log("🎤 [Voice] HttpAsrEngine 尚未实现，回退本地引擎")
        wanted = QWEN3_ENGINE

    if wanted == QWEN3_ENGINE:
        reason = missing_qwen3_reason(base_dir)
        if reason is None:
            engine = SherpaQwen3Engine(
                model_dir=resolve_qwen3_model_dir(base_dir),
                vad_path=resolve_vad_path(base_dir),
                num_threads=num_threads,
                provider=provider,
                hotwords=hotwords,
                logger=logger,
                health_hook=health_hook,
            )
            if engine.ready:
                return engine
            reason = "Qwen3 引擎未就绪"
        if logger:
            logger.error(f"❌ [Voice] {reason}")
        if not allow_fallback:
            return None
        log("🎤 [Voice] Qwen3 不可用，回退 Zipformer 流式模型")
        wanted = ZIPFORMER_ENGINE

    if wanted == ZIPFORMER_ENGINE:
        engine = SherpaZipformerEngine(
            dict(zipformer_cfg or {}),
            num_threads=1,
            logger=logger,
            health_hook=health_hook,
        )
        return engine if engine.ready else None
    return None
