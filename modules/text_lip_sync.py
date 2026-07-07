from __future__ import annotations

import math
import re
from typing import Dict, List


def estimate_text_speech_duration(text: str) -> float:
    clean = str(text or "").strip()
    if not clean:
        return 0.0
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", clean))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", clean))
    punct = len(re.findall(r"[，,。.!！？?；;、…]", clean))
    duration = cjk * 0.135 + latin_words * 0.28 + punct * 0.10 + 0.35
    return max(1.0, min(18.0, duration))


def build_text_lip_sync(text: str, duration_sec: float | None = None) -> List[Dict]:
    """Generate a lightweight fake mouth curve for silent text bubbles."""
    clean = str(text or "").strip()
    if not clean:
        return []

    duration = float(duration_sec or 0.0)
    if duration <= 0:
        duration = estimate_text_speech_duration(clean)
    duration = max(0.5, min(30.0, duration))

    chars = [ch for ch in clean if not ch.isspace()]
    if not chars:
        return []

    points: List[Dict] = [{"time": 0.0, "mouth": 0.0}]
    step = 0.085
    t = 0.04
    idx = 0
    while t < duration:
        ch = chars[min(idx, len(chars) - 1)]
        if ch in "，,。.!！？?；;、…":
            mouth = 0.02
        else:
            wave = abs(math.sin(idx * 1.37)) * 0.34
            mouth = 0.22 + wave
            if re.match(r"[aoeaiuAOEAIU啊呀哈]", ch):
                mouth += 0.12
            mouth = max(0.05, min(0.78, mouth))
        points.append({"time": round(t, 3), "mouth": float(mouth)})
        t += step
        idx += 1

    points.append({"time": round(duration + 0.08, 3), "mouth": 0.0})
    return points
