"""Compatibility shim — real implementation moved to :mod:`modules.asr.settings`.

Preserved so legacy ``from modules.asr_settings import X`` keeps working.
New code should import from ``modules.asr`` instead.
"""

from __future__ import annotations

from modules.asr.settings import (  # noqa: F401
    ASR_ACTIVE_WINDOW_SEC_KEY,
    ASR_ENGINE_CHOICES,
    ASR_ENGINE_KEY,
    ASR_ENGINE_QWEN3,
    ASR_ENGINE_ZIPFORMER,
    ASR_EXTRA_WAKE_WORDS_KEY,
    ASR_GLOBAL_WAKE_WORDS_KEY,
    ASR_HOTWORDS_FROM_WAKE_KEY,
    ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY,
    ASR_MIN_CHARS_KEY,
    ASR_REQUIRE_WAKE_WORD_KEY,
    ASR_SETTING_KEYS,
    ASR_USE_CHARACTER_WAKE_WORDS_KEY,
    collect_character_wake_words,
    configure,
    default_asr_settings,
    default_global_wake_words,
    format_asr_hotwords,
    load_asr_settings,
    normalize_wake_word_list,
    resolve_asr_hotwords,
    resolve_wake_words,
    save_asr_settings,
    should_accept_voice_utterance,
    text_contains_wake_word,
)
