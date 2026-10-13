"""Decode and bound GUI WebSocket PCM frames for VoiceSensor."""

from __future__ import annotations

import base64
import time
from typing import Any, Mapping

import numpy as np

from .engine import SAMPLE_RATE

PCM_FORMAT_S16LE = "s16le"
MAX_PCM_SAMPLES = 8000  # 500ms at 16 kHz
MAX_PCM_BYTES = MAX_PCM_SAMPLES * 2
MIN_CHUNK_INTERVAL_SEC = 0.04


class AudioPcmError(ValueError):
    """Raised when a GUI audio_pcm frame is invalid or too large."""


def decode_audio_pcm(payload: Mapping[str, Any] | None) -> np.ndarray:
    data = dict(payload or {})
    message_type = str(data.get("type") or "").strip().lower()
    if message_type != "audio_pcm":
        raise AudioPcmError("type must be audio_pcm")

    try:
        sample_rate = int(data.get("sr") or 0)
    except (TypeError, ValueError) as exc:
        raise AudioPcmError("sr must be an integer") from exc
    if sample_rate != SAMPLE_RATE:
        raise AudioPcmError("unsupported sample rate")

    fmt = str(data.get("fmt") or PCM_FORMAT_S16LE).strip().lower()
    if fmt != PCM_FORMAT_S16LE:
        raise AudioPcmError("unsupported pcm format")

    raw_b64 = data.get("pcm")
    if not isinstance(raw_b64, str) or not raw_b64.strip():
        raise AudioPcmError("pcm is required")
    try:
        raw = base64.b64decode(raw_b64.encode("ascii"), validate=True)
    except Exception as exc:
        raise AudioPcmError("pcm is not valid base64") from exc
    if len(raw) > MAX_PCM_BYTES:
        raise AudioPcmError("pcm too large")
    if len(raw) < 2 or len(raw) % 2:
        raise AudioPcmError("pcm truncated")

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size > MAX_PCM_SAMPLES:
        raise AudioPcmError("pcm too large")
    return samples


class PcmRateLimiter:
    """Reject bursts faster than the capture cadence."""

    def __init__(self, min_interval: float = MIN_CHUNK_INTERVAL_SEC):
        self.min_interval = max(0.0, float(min_interval))
        self._last: dict[int, float] = {}

    def allow(self, key: int, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        previous = self._last.get(key)
        if previous is not None and (current - previous) < self.min_interval:
            return False
        self._last[key] = current
        return True

    def forget(self, key: int) -> None:
        self._last.pop(key, None)
