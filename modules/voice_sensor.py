"""Compatibility shim — real implementation moved to :mod:`modules.asr.sensor`.

Provides a :class:`VoiceSensor` wrapper that accepts the *legacy* constructor
signature (``config_path=`` keyword, or positional ``chat_service, event_bus,
config_path``) and forwards to the new injected-signature implementation in
:mod:`modules.asr.sensor`.  New code should import from ``modules.asr`` instead.
"""

from __future__ import annotations

from typing import Any, Optional

from modules.asr.adapter import (
    build_asr_config,
    project_character_provider,
    project_health_hook,
    project_logger,
    project_settings_store,
)
from modules.asr.config import AsrConfig
from modules.asr.sensor import VoiceSensor as _StandaloneVoiceSensor

__all__ = ["VoiceSensor"]


class VoiceSensor(_StandaloneVoiceSensor):
    """Backward-compatible VoiceSensor accepting the legacy call signature.

    Legacy callers did ``VoiceSensor(chat_service, event_bus, config_path={})``.
    This wrapper detects ``config_path`` and builds an :class:`AsrConfig` from
    the host project, then delegates to the new injected constructor.
    """

    def __init__(
        self,
        chat_service: Any,
        event_bus: Any = None,
        config_path: Optional[dict] = None,
        *,
        engine: Any = None,
        config: Optional[AsrConfig] = None,
        logger: Any = None,
        settings_store: Any = None,
        character_provider: Any = None,
        health_hook: Any = None,
        **kwargs: Any,
    ) -> None:
        cfg = config or build_asr_config()
        if config_path and not config:
            # Merge legacy sherpa config override into the resolved config.
            cfg = AsrConfig(
                base_dir=cfg.base_dir,
                sherpa_cfg=dict(config_path),
                qwen3_dir=cfg.qwen3_dir,
                vad_path=cfg.vad_path,
                provider=cfg.provider,
                num_threads=cfg.num_threads,
                wake_keywords=list(cfg.wake_keywords),
                active_window_sec=cfg.active_window_sec,
                min_chars=cfg.min_chars,
                blacklist=list(cfg.blacklist),
                require_wake_word=cfg.require_wake_word,
                use_character_wake_words=cfg.use_character_wake_words,
                include_global_wake_words=cfg.include_global_wake_words,
                hotwords_from_wake=cfg.hotwords_from_wake,
                engine=cfg.engine,
                rules_path=cfg.rules_path,
                learned_path=cfg.learned_path,
            )
        super().__init__(
            chat_service=chat_service,
            config=cfg,
            event_bus=event_bus,
            logger=logger or project_logger(),
            settings_store=settings_store or project_settings_store(),
            character_provider=character_provider or project_character_provider(),
            health_hook=health_hook or project_health_hook(),
            engine=engine,
        )
