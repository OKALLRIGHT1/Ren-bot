"""Typed configuration for the standalone ASR package.

``AsrConfig`` consolidates the ASR-related constants that were previously
scattered across the host project's ``config.py`` (``BASE_DIR``,
``SHERPA_MODEL_CONFIG``, ``ASR_PROVIDER``, ``ASR_NUM_THREADS``,
``WAKE_KEYWORDS``, ``GATEKEEPER_ACTIVE_SESSION_WINDOW``, ``ASR_MIN_CHARS``,
``GATEKEEPER_BLACKLIST``, ``ASR_REQUIRE_WAKE_WORD``).

The host project builds an ``AsrConfig`` via :func:`modules.asr.adapter.build_asr_config`
by reading those legacy constants; standalone consumers construct one directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["AsrConfig", "default_asr_config"]


@dataclass(frozen=True)
class AsrConfig:
    """Immutable ASR configuration (follows project ``embeddings/catalog.py`` style)."""

    base_dir: str = "."
    sherpa_cfg: Dict[str, str] = field(default_factory=dict)
    qwen3_dir: str = ""
    vad_path: str = ""
    provider: str = "cpu"
    num_threads: int = 2
    wake_keywords: List[str] = field(default_factory=lambda: ["五十铃", "怜", "Suzu", "助手"])
    active_window_sec: int = 20
    min_chars: int = 2
    blacklist: List[str] = field(default_factory=list)
    require_wake_word: bool = True
    use_character_wake_words: bool = True
    include_global_wake_words: bool = True
    hotwords_from_wake: bool = True
    engine: str = "zipformer"
    rules_path: str = ""
    learned_path: str = ""

    def resolve_qwen3_dir(self) -> str:
        """Prefer the short local folder; also accept the official extract name."""
        if self.qwen3_dir and os.path.isdir(self.qwen3_dir):
            return self.qwen3_dir
        preferred = os.path.join(self.base_dir, "sherpa_model", "qwen3-0.6b-int8")
        official = os.path.join(
            self.base_dir,
            "sherpa_model",
            "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        )
        return preferred if os.path.isdir(preferred) else (official if os.path.isdir(official) else preferred)

    def resolve_vad_path(self) -> str:
        if self.vad_path and os.path.isfile(self.vad_path):
            return self.vad_path
        return os.path.join(self.base_dir, "sherpa_model", "silero_vad.onnx")

    def resolved_rules_path(self) -> str:
        return self.rules_path or os.path.join(self.base_dir, "data", "asr_correction_rules.json")

    def resolved_learned_path(self) -> str:
        return self.learned_path or os.path.join(self.base_dir, "data", "asr_learned_pairs.json")


def default_asr_config(base_dir: str = ".") -> AsrConfig:
    """Factory defaults for standalone use (no host config required)."""
    return AsrConfig(base_dir=base_dir)
