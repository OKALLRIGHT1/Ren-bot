# modules/tts/emotions.py
import os
from config import REF_WAV, PROMPT_TEXT

# ⚠️ 所有路径都可以来自 .env 或 config.py
# 如果某个情绪没有，就自动 fallback 到 neutral

TTS_EMO_MAP = {
    "neutral": {
        "ref": REF_WAV,
        "prompt": PROMPT_TEXT,
    },

    # # 示例（你可以之后慢慢补）
    # "happy": {
    #     "ref": os.getenv("TTS_REF_HAPPY", REF_WAV),
    #     "prompt": "ふふ……少し嬉しいですね。",
    # },
    #
    # "sad": {
    #     "ref": os.getenv("TTS_REF_SAD", REF_WAV),
    #     "prompt": "……少し、寂しいです。",
    # },
    #
    # "angry": {
    #     "ref": os.getenv("TTS_REF_ANGRY", REF_WAV),
    #     "prompt": "……少し、苛立っています。",
    # },
}
