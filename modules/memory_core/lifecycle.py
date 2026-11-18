"""MaiBot-style memory lifecycle for short-lived tasks.

A_Memorix decays relations only. Paragraphs, episodes, and person profiles stay
until an explicit expires_at. This project stores those stable facts as
preference, fact, rule, profile, relation, and episode rows, so they are left
alone. 「待办与承诺」 rows (key=user_task) are the ephemeral layer and follow
the relation lifecycle:

    strength = base * 2 ** (-elapsed_hours / half_life_hours)

Defaults match [a_memorix.memory]: 24h half-life, freeze below 0.1, revive
above 0.15, archive 24h after freezing. New evidence resets strength to 1.
Access reinforcement adds a small bump and restarts the clock from that bump,
it does not jump back to 1. Rows are kept; archived is the recycle bin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MemoryLifecycleConfig:
    enabled: bool = True
    half_life_hours: float = 24.0
    interval_hours: float = 1.0
    prune_threshold: float = 0.1
    revive_threshold: float = 0.15
    freeze_duration_hours: float = 24.0
    access_reinforcement_alpha: float = 0.05
    access_reinforcement_cooldown_minutes: float = 60.0
    explicit_reinforcement_alpha: float = 0.5
    weaken_alpha: float = 0.5
    batch_size: int = 1000


@dataclass(frozen=True)
class TaskLifecycleDecision:
    status: str
    base_strength: float
    anchor_ts: float
    frozen_at_ts: Optional[float]
    strength: float
    reason: str
    changed: bool


def _unit(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _clamped_unit(value: Any, default: float) -> float:
    return max(0.0, min(1.0, _unit(value, default)))


def config_from_mapping(mapping: Optional[dict[str, Any]] = None) -> MemoryLifecycleConfig:
    source = mapping or {}
    defaults = MemoryLifecycleConfig()
    prune = _clamped_unit(source.get("memory_prune_threshold"), defaults.prune_threshold)
    revive = _clamped_unit(source.get("memory_revive_threshold"), defaults.revive_threshold)
    if revive <= prune:
        revive = min(1.0, prune + 0.05)
    half_life = _unit(source.get("memory_half_life_hours"), defaults.half_life_hours)
    interval = _unit(source.get("memory_lifecycle_interval_hours"), defaults.interval_hours)
    freeze_for = _unit(source.get("memory_freeze_duration_hours"), defaults.freeze_duration_hours)
    try:
        batch = int(source.get("memory_lifecycle_batch_size", defaults.batch_size))
    except (TypeError, ValueError):
        batch = defaults.batch_size
    enabled = source.get("memory_lifecycle_enabled", defaults.enabled)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}
    return MemoryLifecycleConfig(
        enabled=bool(enabled),
        half_life_hours=max(1e-6, half_life),
        interval_hours=max(0.0, interval),
        prune_threshold=prune,
        revive_threshold=revive,
        freeze_duration_hours=max(0.0, freeze_for),
        access_reinforcement_alpha=_clamped_unit(
            source.get("memory_access_reinforcement_alpha"),
            defaults.access_reinforcement_alpha,
        ),
        access_reinforcement_cooldown_minutes=max(
            0.0,
            _unit(
                source.get("memory_access_reinforcement_cooldown_minutes"),
                defaults.access_reinforcement_cooldown_minutes,
            ),
        ),
        explicit_reinforcement_alpha=_clamped_unit(
            source.get("memory_explicit_reinforcement_alpha"),
            defaults.explicit_reinforcement_alpha,
        ),
        weaken_alpha=_clamped_unit(source.get("memory_weaken_alpha"), defaults.weaken_alpha),
        batch_size=max(1, min(1000, batch)),
    )


def retention_strength(base: float, elapsed_hours: float, half_life_hours: float) -> float:
    """MaiBot: initial strength × 2^(-elapsed hours / half_life_hours)."""
    safe_base = max(0.0, min(1.0, float(base)))
    elapsed = max(0.0, float(elapsed_hours))
    half_life = float(half_life_hours)
    if half_life <= 1e-9:
        return safe_base
    return safe_base * (2.0 ** (-elapsed / half_life))


def hours_until_below(base: float, threshold: float, half_life_hours: float) -> float:
    """Hours until strength drops below threshold. 0 when it is already below."""
    safe_base = max(0.0, min(1.0, float(base)))
    if safe_base < threshold:
        return 0.0
    if threshold <= 0 or half_life_hours <= 1e-9:
        return math.inf
    return half_life_hours * math.log2(safe_base / threshold)


def fresh_lifecycle(now_ts: float) -> dict[str, Any]:
    return {
        "state": "active",
        "base_strength": 1.0,
        "anchor_ts": float(now_ts),
        "frozen_at_ts": None,
        "strength": 1.0,
        "reason": "evidence",
        "last_access_ts": None,
    }


def decide_user_task(
    *,
    status: str,
    base_strength: float,
    anchor_ts: Optional[float],
    frozen_at_ts: Optional[float],
    valid_until_ts: Optional[float],
    protected_until_ts: Optional[float],
    manual_lock: bool,
    now_ts: float,
    config: MemoryLifecycleConfig,
) -> TaskLifecycleDecision:
    """Decide the next status from the closed-form curve, without writing."""
    current = str(status or "active").strip() or "active"
    base = max(0.0, min(1.0, float(base_strength or 1.0)))
    anchor = float(anchor_ts) if anchor_ts is not None else float(now_ts)
    strength = retention_strength(base, (now_ts - anchor) / 3600.0, config.half_life_hours)

    def keep() -> TaskLifecycleDecision:
        return TaskLifecycleDecision(
            status=current,
            base_strength=base,
            anchor_ts=anchor,
            frozen_at_ts=frozen_at_ts,
            strength=strength,
            reason="",
            changed=False,
        )

    if manual_lock or (protected_until_ts is not None and protected_until_ts > now_ts):
        return keep()
    # An explicit end date is MaiBot's expires_at path: no half-life, drop when due.
    if valid_until_ts is not None and valid_until_ts > now_ts:
        return keep()
    if valid_until_ts is not None and valid_until_ts <= now_ts:
        return TaskLifecycleDecision(
            status="archived",
            base_strength=base,
            anchor_ts=anchor,
            frozen_at_ts=frozen_at_ts,
            strength=strength,
            reason="valid_until",
            changed=current != "archived",
        )

    if current == "frozen":
        frozen_at = float(frozen_at_ts) if frozen_at_ts is not None else anchor
        if strength >= config.revive_threshold:
            return _activated(base, now_ts, strength, "revive")
        if (now_ts - frozen_at) / 3600.0 >= config.freeze_duration_hours:
            return TaskLifecycleDecision(
                status="archived",
                base_strength=base,
                anchor_ts=anchor,
                frozen_at_ts=frozen_at,
                strength=strength,
                reason="freeze_expired",
                changed=True,
            )
        return keep()

    if strength < config.prune_threshold:
        crossed = anchor + hours_until_below(base, config.prune_threshold, config.half_life_hours) * 3600.0
        if (now_ts - crossed) / 3600.0 >= config.freeze_duration_hours:
            return TaskLifecycleDecision(
                status="archived",
                base_strength=base,
                anchor_ts=anchor,
                frozen_at_ts=crossed,
                strength=strength,
                reason="freeze_expired",
                changed=current != "archived",
            )
        return TaskLifecycleDecision(
            status="frozen",
            base_strength=base,
            anchor_ts=anchor,
            frozen_at_ts=crossed,
            strength=strength,
            reason="prune",
            changed=current != "frozen",
        )
    return keep()


def _activated(base: float, now_ts: float, strength: float, reason: str) -> TaskLifecycleDecision:
    del base
    bumped = max(0.0, min(1.0, strength))
    return TaskLifecycleDecision(
        status="active",
        base_strength=bumped,
        anchor_ts=now_ts,
        frozen_at_ts=None,
        strength=bumped,
        reason=reason,
        changed=True,
    )


def apply_reinforcement(
    *,
    mode: str,
    status: str,
    base_strength: float,
    anchor_ts: Optional[float],
    frozen_at_ts: Optional[float],
    last_access_ts: Optional[float],
    manual_lock: bool,
    now_ts: float,
    config: MemoryLifecycleConfig,
) -> Optional[TaskLifecycleDecision]:
    """Return the reinforced state, or None when the event is ignored."""
    if manual_lock:
        return None
    action = str(mode or "").strip().lower()
    if action not in {"access", "explicit", "evidence", "weaken"}:
        return None
    base = max(0.0, min(1.0, float(base_strength or 1.0)))
    anchor = float(anchor_ts) if anchor_ts is not None else float(now_ts)
    current_strength = retention_strength(
        base, (now_ts - anchor) / 3600.0, config.half_life_hours
    )
    if action == "access":
        if (
            last_access_ts is not None
            and config.access_reinforcement_cooldown_minutes > 0
            and (now_ts - float(last_access_ts)) / 60.0
            < config.access_reinforcement_cooldown_minutes
        ):
            return None
        bumped = min(1.0, current_strength + config.access_reinforcement_alpha)
        reason = "access"
    elif action == "explicit":
        bumped = min(1.0, current_strength + config.explicit_reinforcement_alpha)
        reason = "explicit"
    elif action == "evidence":
        bumped = 1.0
        reason = "evidence"
    else:
        bumped = max(0.0, min(1.0, current_strength * config.weaken_alpha))
        reason = "weaken"

    next_status, frozen_at = _status_after_bump(
        status=status,
        strength=bumped,
        frozen_at_ts=frozen_at_ts,
        now_ts=now_ts,
        config=config,
    )
    return TaskLifecycleDecision(
        status=next_status,
        base_strength=bumped,
        anchor_ts=now_ts,
        frozen_at_ts=frozen_at,
        strength=bumped,
        reason=reason,
        changed=True,
    )


def _status_after_bump(
    *,
    status: str,
    strength: float,
    frozen_at_ts: Optional[float],
    now_ts: float,
    config: MemoryLifecycleConfig,
) -> tuple[str, Optional[float]]:
    current = str(status or "active").strip() or "active"
    if strength >= config.revive_threshold:
        return "active", None
    if strength < config.prune_threshold:
        if current == "frozen":
            frozen_at = float(frozen_at_ts) if frozen_at_ts is not None else now_ts
            if (now_ts - frozen_at) / 3600.0 >= config.freeze_duration_hours:
                return "archived", frozen_at
            return "frozen", frozen_at
        return "frozen", now_ts
    if current == "frozen":
        frozen_at = float(frozen_at_ts) if frozen_at_ts is not None else now_ts
        return "frozen", frozen_at
    return "active", None
