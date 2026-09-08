"""Standalone, host-decoupled ASR (speech recognition + correction) package.

Public API:
    - :class:`VoiceSensor` — capture + recognition + correction loop.
    - :class:`AsrConfig` — typed configuration (replaces scattered config constants).
    - :func:`create_asr_engine` — engine factory (Qwen3 / Zipformer fallback).
    - :class:`AsrCorrector` — post-recognition correction (rules + learned pairs).
    - Settings helpers: ``load_asr_settings``, ``resolve_wake_words``, …
    - Protocol ports for dependency injection.

Host projects wire concrete dependencies through :mod:`modules.asr.adapter`;
standalone consumers pass ``None`` and in-memory defaults take over, so
this directory can be copied out and used without the surrounding project.
"""

from __future__ import annotations

from .config import AsrConfig, default_asr_config
from .corrector import AsrCorrector, CorrectionRules, LearnedPairs
from .engine import (
    AsrEngine,
    HttpAsrEngine,
    SherpaQwen3Engine,
    SherpaZipformerEngine,
    SAMPLE_RATE,
    ZIPFORMER_SAMPLES_PER_READ,
    create_asr_engine,
    format_hotwords,
    missing_qwen3_reason,
    qwen3_api_available,
    qwen3_paths,
    resolve_qwen3_model_dir,
    resolve_vad_path,
)
from .ports import (
    CharacterProvider,
    HealthHook,
    LoggerPort,
    NullHealthHook,
    SettingsStore,
    default_logger,
)
from .sensor import VoiceSensor
from .settings import (
    ASR_ENGINE_CHOICES,
    ASR_ENGINE_KEY,
    ASR_ENGINE_QWEN3,
    ASR_ENGINE_ZIPFORMER,
    ASR_SETTING_KEYS,
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

__all__ = [
    # config
    "AsrConfig",
    "default_asr_config",
    # corrector
    "AsrCorrector",
    "CorrectionRules",
    "LearnedPairs",
    # engine
    "AsrEngine",
    "HttpAsrEngine",
    "SherpaQwen3Engine",
    "SherpaZipformerEngine",
    "SAMPLE_RATE",
    "ZIPFORMER_SAMPLES_PER_READ",
    "create_asr_engine",
    "format_hotwords",
    "missing_qwen3_reason",
    "qwen3_api_available",
    "qwen3_paths",
    "resolve_qwen3_model_dir",
    "resolve_vad_path",
    # ports
    "CharacterProvider",
    "HealthHook",
    "LoggerPort",
    "NullHealthHook",
    "SettingsStore",
    "default_logger",
    # sensor
    "VoiceSensor",
    # settings
    "ASR_ENGINE_CHOICES",
    "ASR_ENGINE_KEY",
    "ASR_ENGINE_QWEN3",
    "ASR_ENGINE_ZIPFORMER",
    "ASR_SETTING_KEYS",
    "collect_character_wake_words",
    "configure",
    "default_asr_settings",
    "default_global_wake_words",
    "format_asr_hotwords",
    "load_asr_settings",
    "normalize_wake_word_list",
    "resolve_asr_hotwords",
    "resolve_wake_words",
    "save_asr_settings",
    "should_accept_voice_utterance",
    "text_contains_wake_word",
]
