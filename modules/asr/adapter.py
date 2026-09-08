"""Host-project glue: wraps project services into ASR package ports.

This is the *only* module in :mod:`modules.asr` that imports from the host
project (``config``, ``modules.runtime_settings``, ``modules.character_manager``,
``services.runtime_health``, ``core.logger``).  It builds an :class:`AsrConfig`
from the legacy scattered constants and wires the four dependency-injection
ports, then offers :func:`build_voice_sensor` as a one-shot constructor that
the rest of the project calls instead of ``VoiceSensor(...)`` directly.

Everything under :mod:`modules.asr` except this file remains host-free, so
the package can be lifted out by copying the directory and replacing this
single adapter.
"""

from __future__ import annotations

from typing import Any, Optional

from .config import AsrConfig
from .ports import (
    CharacterProvider,
    HealthHook,
    LoggerPort,
    SettingsStore,
)
from .sensor import VoiceSensor

__all__ = [
    "build_asr_config",
    "project_settings_store",
    "project_character_provider",
    "project_health_hook",
    "project_logger",
    "build_voice_sensor",
    "build_corrector",
]


def build_asr_config() -> AsrConfig:
    """Build an :class:`AsrConfig` from legacy ``config.py`` constants."""
    import config as project_config

    return AsrConfig(
        base_dir=getattr(project_config, "BASE_DIR", "."),
        sherpa_cfg=dict(getattr(project_config, "SHERPA_MODEL_CONFIG", {}) or {}),
        qwen3_dir=getattr(project_config, "QWEN3_ASR_DIR", ""),
        vad_path=getattr(project_config, "SILERO_VAD_MODEL", ""),
        provider=str(getattr(project_config, "ASR_PROVIDER", "cpu")),
        num_threads=int(getattr(project_config, "ASR_NUM_THREADS", 2)),
        wake_keywords=list(getattr(project_config, "WAKE_KEYWORDS", ["五十铃", "怜", "Suzu", "助手"])),
        active_window_sec=int(getattr(project_config, "GATEKEEPER_ACTIVE_SESSION_WINDOW", 20)),
        min_chars=int(getattr(project_config, "ASR_MIN_CHARS", 2)),
        blacklist=list(getattr(project_config, "GATEKEEPER_BLACKLIST", [])),
        require_wake_word=bool(getattr(project_config, "ASR_REQUIRE_WAKE_WORD", True)),
        use_character_wake_words=bool(getattr(project_config, "ASR_USE_CHARACTER_WAKE_WORDS", True)),
        include_global_wake_words=bool(getattr(project_config, "ASR_INCLUDE_GLOBAL_WAKE_WORDS", True)),
        hotwords_from_wake=True,
        engine=str(getattr(project_config, "ASR_DEFAULT_ENGINE", "qwen3")),
    )


def project_logger() -> LoggerPort:
    """Return the project-wide logger, satisfying :class:`LoggerPort`."""
    try:
        from core.logger import get_logger

        return get_logger()
    except ImportError:
        from .ports import default_logger

        return default_logger()


class _ProjectSettingsStore:
    """Adapts ``modules.runtime_settings`` to the :class:`SettingsStore` protocol."""

    def load(self) -> dict:
        try:
            from modules.runtime_settings import load_runtime_settings

            return load_runtime_settings()
        except ImportError:
            return {}

    def save(self, patch: dict) -> dict:
        try:
            from modules.runtime_settings import update_runtime_settings

            return update_runtime_settings(patch or {})
        except ImportError:
            return dict(patch or {})


def project_settings_store() -> SettingsStore:
    return _ProjectSettingsStore()


class _ProjectCharacterProvider:
    """Adapts ``modules.character_manager`` to :class:`CharacterProvider`."""

    def get_active_character(self):  # type: ignore[override]
        try:
            from modules.character_manager import character_manager

            return character_manager.get_active_character()
        except ImportError:
            return None


def project_character_provider() -> CharacterProvider:
    return _ProjectCharacterProvider()


class _ProjectHealthHook:
    """Adapts ``services.runtime_health`` to :class:`HealthHook`."""

    def report(self, component: str, state: str, summary: str) -> None:
        try:
            from services.runtime_health import report_runtime_health

            report_runtime_health(component, state, summary)
        except ImportError:
            pass


def project_health_hook() -> HealthHook:
    return _ProjectHealthHook()


def build_corrector():
    """Build a standalone :class:`AsrCorrector` for the GUI settings page."""
    from .corrector import AsrCorrector

    cfg = build_asr_config()
    return AsrCorrector(
        cfg.resolved_rules_path(),
        cfg.resolved_learned_path(),
        logger=project_logger(),
    )


def build_voice_sensor(chat_service: Any) -> Optional[VoiceSensor]:
    """One-shot constructor used by ``core.application``.

    Reads legacy config, wires all ports, and returns a ready
    :class:`VoiceSensor`.  Returns ``None`` if construction fails (the host
    logs the error).
    """
    try:
        cfg = build_asr_config()
        return VoiceSensor(
            chat_service=chat_service,
            config=cfg,
            logger=project_logger(),
            settings_store=project_settings_store(),
            character_provider=project_character_provider(),
            health_hook=project_health_hook(),
        )
    except Exception as exc:  # pragma: no cover - defensive, host logs
        try:
            project_logger().error(f"❌ [Voice] VoiceSensor 构造失败: {exc}")
        except Exception:
            pass
        return None
