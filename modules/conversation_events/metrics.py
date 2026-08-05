"""Continuity regression metrics (offline, no model dependency)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

# Categories fixed by the continuity plan.
KNOWN_CATEGORIES = frozenset(
    {
        "recent_causal_followup",
        "paraphrased_followup",
        "irrelevant_recent_event",
        "tool_followup",
        "care_followup",
        "long_conversation",
        "cross_channel_isolation",
        "cross_user_isolation",
    }
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    category: str
    passed: bool
    case_id: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        category = str(self.category or "").strip()
        if category not in KNOWN_CATEGORIES:
            raise ValueError(f"unknown continuity category: {category!r}")


@dataclass(slots=True)
class ContinuityMetrics:
    """Aggregate pass rates by category for offline continuity cases."""

    totals: dict[str, int] = field(default_factory=dict)
    passed: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_results(cls, results: Iterable[CaseResult]) -> "ContinuityMetrics":
        metrics = cls()
        for result in results:
            if not isinstance(result, CaseResult):
                raise TypeError(f"expected CaseResult, got {type(result)!r}")
            category = result.category
            metrics.totals[category] = metrics.totals.get(category, 0) + 1
            if result.passed:
                metrics.passed[category] = metrics.passed.get(category, 0) + 1
        return metrics

    def rate(self, category: str) -> Optional[float]:
        category = str(category or "").strip()
        if category not in KNOWN_CATEGORIES:
            raise ValueError(f"unknown continuity category: {category!r}")
        total = int(self.totals.get(category, 0) or 0)
        if total <= 0:
            return None
        return float(self.passed.get(category, 0) or 0) / float(total)

    def as_dict(self) -> Mapping[str, Optional[float]]:
        return {category: self.rate(category) for category in sorted(KNOWN_CATEGORIES)}


@dataclass(slots=True)
class ContextTrace:
    """Per-turn assembly / selection trace for dual-inject and budget checks."""

    candidate_event_ids: Sequence[str] = ()
    selected_event_ids: Sequence[str] = ()
    selected_reasons: Mapping[str, str] = field(default_factory=dict)
    recent_block_chars: int = 0
    dual_inject_detected: bool = False
    used_legacy_sensor_followup: bool = False
    notes: Mapping[str, object] = field(default_factory=dict)
