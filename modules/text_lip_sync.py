from __future__ import annotations

import math
import re
from typing import Dict, List


def estimate_text_speech_duration(text: str) -> float:
    """估算静默说话时长（秒），用于无 TTS 时的口型与气泡对齐。"""
    clean = str(text or "").strip()
    if not clean:
        return 0.0
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", clean))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", clean))
    punct = len(re.findall(r"[，,。.!！？?；;、…]", clean))
    # 中间档：中文约 9~10 字/秒（比旧慢读快，比刚才那版慢一点）
    duration = cjk * 0.105 + latin_words * 0.25 + punct * 0.09 + 0.32
    return max(0.9, min(16.0, duration))


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
    # 更密的采样，避免看起来像“几乎没张嘴”
    step = 0.075
    t = 0.03
    idx = 0
    while t < duration:
        ch = chars[min(idx, len(chars) - 1)]
        if ch in "，,。.!！？?；;、…":
            mouth = 0.05
        else:
            wave = abs(math.sin(idx * 1.48)) * 0.40
            mouth = 0.30 + wave
            if re.match(r"[aoeaiuAOEAIU啊呀哈哦噢]", ch):
                mouth += 0.14
            mouth = max(0.10, min(0.90, mouth))
        points.append({"time": round(t, 3), "mouth": float(mouth)})
        t += step
        idx += 1

    points.append({"time": round(duration + 0.08, 3), "mouth": 0.0})
    return points
