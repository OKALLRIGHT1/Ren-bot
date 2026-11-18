"""QQ private outbound pacing: read receipt, typing indicator, short delay.

Inbound typing wait lives in qq_private_buffer / QQ_PRIVATE_CONTINUOUS_*.
This module only covers what the other person sees before our reply.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional


DEFAULT_PART_GAP_SEC = 0.35


@dataclass(frozen=True)
class QqHumanizeConfig:
    enabled: bool = True
    mark_read: bool = True
    typing: bool = True
    set_online_on_connect: bool = True
    ms_per_char: float = 90.0
    min_delay_sec: float = 0.45
    max_delay_sec: float = 2.2
    part_gap_min_sec: float = 0.45
    part_gap_max_sec: float = 1.05

    @classmethod
    def from_env(cls) -> "QqHumanizeConfig":
        try:
            from config import (
                NAPCAT_HUMANIZE_ENABLED,
                NAPCAT_HUMANIZE_MARK_READ,
                NAPCAT_HUMANIZE_MAX_DELAY_SEC,
                NAPCAT_HUMANIZE_MIN_DELAY_SEC,
                NAPCAT_HUMANIZE_MS_PER_CHAR,
                NAPCAT_HUMANIZE_PART_GAP_MAX_SEC,
                NAPCAT_HUMANIZE_PART_GAP_MIN_SEC,
                NAPCAT_HUMANIZE_SET_ONLINE,
                NAPCAT_HUMANIZE_TYPING,
            )
        except Exception:
            return cls(enabled=False)
        min_delay = float(NAPCAT_HUMANIZE_MIN_DELAY_SEC)
        max_delay = max(min_delay, float(NAPCAT_HUMANIZE_MAX_DELAY_SEC))
        gap_min = float(NAPCAT_HUMANIZE_PART_GAP_MIN_SEC)
        gap_max = max(gap_min, float(NAPCAT_HUMANIZE_PART_GAP_MAX_SEC))
        return cls(
            enabled=bool(NAPCAT_HUMANIZE_ENABLED),
            mark_read=bool(NAPCAT_HUMANIZE_MARK_READ),
            typing=bool(NAPCAT_HUMANIZE_TYPING),
            set_online_on_connect=bool(NAPCAT_HUMANIZE_SET_ONLINE),
            ms_per_char=float(NAPCAT_HUMANIZE_MS_PER_CHAR),
            min_delay_sec=min_delay,
            max_delay_sec=max_delay,
            part_gap_min_sec=gap_min,
            part_gap_max_sec=gap_max,
        )

    def applies_to_session(self, session_id: str) -> bool:
        return bool(self.enabled) and is_private_session(session_id)


def is_private_session(session_id: str) -> bool:
    return str(session_id or "").strip().lower().startswith("private:")


def typing_delay_sec(
    text: str,
    cfg: QqHumanizeConfig,
    rng: Optional[random.Random] = None,
) -> float:
    if not cfg.enabled:
        return 0.0
    n = max(1, len(str(text or "").strip()))
    dice = rng.random() if rng is not None else random.random()
    jitter = 0.85 + 0.30 * float(dice)
    raw = (n * float(cfg.ms_per_char) / 1000.0) * jitter
    return max(float(cfg.min_delay_sec), min(float(cfg.max_delay_sec), raw))


def part_gap_sec(
    cfg: Optional[QqHumanizeConfig],
    session_id: str,
    rng: Optional[random.Random] = None,
) -> float:
    if cfg is None or not cfg.applies_to_session(session_id):
        return DEFAULT_PART_GAP_SEC
    lo = float(cfg.part_gap_min_sec)
    hi = float(cfg.part_gap_max_sec)
    if hi <= lo:
        return lo
    dice = rng.random() if rng is not None else random.random()
    return lo + (hi - lo) * float(dice)
