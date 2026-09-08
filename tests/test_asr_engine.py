import numpy as np

from modules.asr.engine import (
    HttpAsrEngine,
    create_asr_engine,
    format_hotwords,
    missing_qwen3_reason,
    qwen3_paths,
    resolve_vad_path,
)


def test_format_hotwords_dedupes():
    assert format_hotwords(["五十铃", "怜", "五十铃", " ", ""]) == "五十铃/怜"


def test_qwen3_paths_requires_complete_tree(tmp_path):
    folder = tmp_path / "qwen3-0.6b-int8"
    folder.mkdir()
    assert qwen3_paths(str(folder)) is None
    for name in ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx"):
        (folder / name).write_bytes(b"x" * (120 * 1024))
    (folder / "tokenizer").mkdir()
    paths = qwen3_paths(str(folder))
    assert paths is not None
    assert paths["tokenizer"].endswith("tokenizer")


def test_create_qwen3_falls_back_to_zipformer(monkeypatch, tmp_path):
    from modules.asr import engine as mod

    monkeypatch.setattr(mod, "missing_qwen3_reason", lambda _base: "blocked")

    class FakeZip:
        name = "zipformer"

        def __init__(self, cfg, num_threads=1, logger=None, health_hook=None):
            self.cfg = cfg
            self.ready = True

    monkeypatch.setattr(mod, "SherpaZipformerEngine", FakeZip)
    engine = create_asr_engine(
        "qwen3",
        base_dir=str(tmp_path),
        zipformer_cfg={"tokens": "x"},
        allow_fallback=True,
    )
    assert isinstance(engine, FakeZip)
    assert engine.ready is True


def test_create_qwen3_without_fallback_returns_none(monkeypatch, tmp_path):
    from modules.asr import engine as mod

    monkeypatch.setattr(mod, "missing_qwen3_reason", lambda _base: "blocked")
    engine = create_asr_engine(
        "qwen3",
        base_dir=str(tmp_path),
        allow_fallback=False,
    )
    assert engine is None


def test_missing_qwen3_reason_mentions_vad_or_api(tmp_path):
    reason = missing_qwen3_reason(str(tmp_path))
    assert reason
    assert (
        "from_qwen3_asr" in reason
        or "silero_vad" in reason.lower()
        or "Qwen3" in reason
        or "sherpa-onnx" in reason
    )
    assert resolve_vad_path(str(tmp_path)).endswith("silero_vad.onnx")


def test_http_engine_is_stub():
    engine = HttpAsrEngine()
    assert engine.ready is False
    try:
        engine.transcribe(np.zeros(16000, dtype=np.float32))
        raise AssertionError("stub should raise")
    except NotImplementedError:
        pass


def test_voice_sensor_process_sentence_uses_wake_policy():
    from modules.asr.config import AsrConfig
    from modules.asr.sensor import VoiceSensor

    class FakeEngine:
        name = "qwen3"
        ready = True
        samples_per_read = 512
        hotwords = ""

        def set_hotwords(self, hotwords):
            self.hotwords = hotwords

        def accept_audio(self, samples):
            return []

        def close(self):
            return None

    class FakeChat:
        def __init__(self):
            self.seen = []

        async def process(self, text, ctx=None):
            self.seen.append((text, ctx))

    chat = FakeChat()
    sensor = VoiceSensor(
        chat_service=chat,
        config=AsrConfig(),
        engine=FakeEngine(),
    )
    sensor.require_wake_word = True
    sensor.is_woken = False
    sensor.wake_words = ["五十铃"]
    with sensor._settings_lock:
        sensor._asr_settings = {
            "asr_require_wake_word": True,
            "asr_active_window_sec": 20,
            "asr_min_chars": 2,
        }
        sensor.wake_words = ["五十铃"]
    sensor._loop = None
    sensor._process_sentence("今天天气怎么样")
    assert chat.seen == []
    sensor._process_sentence("五十铃，今天天气怎么样")
    # No event loop, so chat.process is not scheduled; accept path must not raise.
    assert sensor.is_woken is True
