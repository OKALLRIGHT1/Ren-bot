import base64

import numpy as np
import pytest

from modules.asr.pcm import (
    MAX_PCM_BYTES,
    AudioPcmError,
    PcmRateLimiter,
    decode_audio_pcm,
)


def _frame(samples: np.ndarray, *, sr: int = 16000, fmt: str = "s16le") -> dict:
    pcm = np.asarray(samples, dtype=np.int16).tobytes()
    return {
        "type": "audio_pcm",
        "sr": sr,
        "fmt": fmt,
        "pcm": base64.b64encode(pcm).decode("ascii"),
    }


def test_decode_audio_pcm_round_trip():
    source = np.array([0, 16384, -16384, 32767], dtype=np.int16)
    decoded = decode_audio_pcm(_frame(source))
    assert decoded.dtype == np.float32
    assert decoded.shape == (4,)
    np.testing.assert_allclose(decoded, source.astype(np.float32) / 32768.0, atol=1e-4)


def test_decode_audio_pcm_rejects_oversized_payload():
    too_big = bytes(MAX_PCM_BYTES + 2)
    payload = {
        "type": "audio_pcm",
        "sr": 16000,
        "fmt": "s16le",
        "pcm": base64.b64encode(too_big).decode("ascii"),
    }
    with pytest.raises(AudioPcmError, match="too large"):
        decode_audio_pcm(payload)


def test_decode_audio_pcm_rejects_wrong_rate_and_format():
    payload = _frame(np.array([0, 1], dtype=np.int16), sr=48000)
    with pytest.raises(AudioPcmError, match="sample rate"):
        decode_audio_pcm(payload)
    payload = _frame(np.array([0, 1], dtype=np.int16), fmt="f32le")
    with pytest.raises(AudioPcmError, match="format"):
        decode_audio_pcm(payload)


def test_pcm_rate_limiter_blocks_bursts():
    limiter = PcmRateLimiter(min_interval=0.1)
    assert limiter.allow(1, now=1.0) is True
    assert limiter.allow(1, now=1.05) is False
    assert limiter.allow(1, now=1.2) is True
    assert limiter.allow(2, now=1.2) is True
    limiter.forget(1)
    assert limiter.allow(1, now=1.21) is True
