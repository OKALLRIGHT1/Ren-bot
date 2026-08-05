"""
Conversation continuity baseline (offline).

Baseline note (pre-event-store era / legacy path):
- Old chat path relied on short_term + keyword sensor follow-up.
- Continuity fixtures are expectations for the event/selector path.
- Do not weaken case expectations to match legacy keyword-only behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.conversation_events.metrics import (
    KNOWN_CATEGORIES,
    CaseResult,
    ContinuityMetrics,
)
from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
    EventBudget,
)
from modules.conversation_events.prompt import format_recent_event_block
from modules.conversation_events.selector import RecentEventSelector
from datetime import datetime, timedelta, timezone


FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "conversation_continuity_cases.json"
)

_TYPE_MAP = {
    "user_message": ConversationEventType.USER_MESSAGE,
    "assistant_message": ConversationEventType.ASSISTANT_MESSAGE,
    "screen_observation": ConversationEventType.SCREEN_OBSERVATION,
    "proactive_utterance": ConversationEventType.PROACTIVE_UTTERANCE,
    "care_reminder": ConversationEventType.CARE_REMINDER,
    "tool_call": ConversationEventType.TOOL_CALL,
    "tool_result": ConversationEventType.TOOL_RESULT,
    "system_notice": ConversationEventType.SYSTEM_NOTICE,
}


def test_continuity_metrics_are_computed_from_case_results():
    metrics = ContinuityMetrics.from_results(
        [
            CaseResult(category="recent_causal_followup", passed=True),
            CaseResult(category="recent_causal_followup", passed=False),
            CaseResult(category="cross_channel_isolation", passed=True),
        ]
    )
    assert metrics.rate("recent_causal_followup") == 0.5
    assert metrics.rate("cross_channel_isolation") == 1.0
    assert metrics.rate("tool_followup") is None


def test_continuity_metrics_reject_unknown_category():
    with pytest.raises(ValueError, match="unknown continuity category"):
        CaseResult(category="not_a_real_category", passed=True)


def test_fixture_covers_all_categories_with_min_eight_each():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) >= 64
    by_cat: dict[str, int] = {}
    for case in cases:
        cat = case["category"]
        assert cat in KNOWN_CATEGORIES
        by_cat[cat] = by_cat.get(cat, 0) + 1
    for cat in KNOWN_CATEGORIES:
        assert by_cat.get(cat, 0) >= 8, f"{cat} has fewer than 8 cases"


def _scope_from(case_scope: dict, default_person: str = "owner") -> ConversationScope:
    return ConversationScope(
        persona_id="suzu",
        person_id=str(case_scope.get("person_id") or default_person),
        channel=str(case_scope["channel"]),
        conversation_id=str(case_scope["conversation_id"]),
    )


def _events_from_case(case: dict) -> list[ConversationEvent]:
    scope = _scope_from(case["scope"])
    built: list[ConversationEvent] = []
    base = datetime.now(timezone.utc)
    for index, raw in enumerate(case.get("events") or []):
        parents: tuple[str, ...] = ()
        if "parent" in raw:
            parent_idx = int(raw["parent"])
            parents = (built[parent_idx].event_id,)
        etype = _TYPE_MAP[str(raw["type"])]
        if (
            not parents
            and etype == ConversationEventType.ASSISTANT_MESSAGE
            and built
            and built[-1].event_type == ConversationEventType.USER_MESSAGE
        ):
            parents = (built[-1].event_id,)
        text = str(raw.get("text") or "")
        evidence = text if etype in {
            ConversationEventType.SCREEN_OBSERVATION,
            ConversationEventType.TOOL_RESULT,
            ConversationEventType.TOOL_CALL,
        } else ""
        exact = text if etype not in {
            ConversationEventType.SCREEN_OBSERVATION,
            ConversationEventType.TOOL_RESULT,
        } else text
        built.append(
            ConversationEvent(
                event_id=f"{case['id']}-{index}",
                scope=scope,
                event_type=etype,
                occurred_at=base + timedelta(microseconds=index),
                exact_text=exact,
                evidence_summary=evidence or text,
                causal_parent_ids=parents,
                status="active",
                metadata={},
            )
        )
    return built


def _evaluate_case(case: dict) -> CaseResult:
    selector = RecentEventSelector()
    write_scope = _scope_from(case["scope"])
    query_raw = case.get("query_scope") or case["scope"]
    query_scope = _scope_from(query_raw)
    events = _events_from_case(case)
    # Isolation: only candidates from matching scope
    candidates = [e for e in events if e.scope.as_tuple() == query_scope.as_tuple()]
    # If query scope differs, candidates should be empty for isolation cases
    if write_scope.as_tuple() != query_scope.as_tuple():
        candidates = []
    result = selector.select(
        case["user_text"],
        candidates,
        budget=EventBudget(max_events=3, max_chars=900),
    )
    block = format_recent_event_block(result.events)
    must = list(case.get("must_include_evidence") or [])
    must_not = list(case.get("must_not_include_evidence") or [])
    passed = True
    notes = []
    for token in must:
        if token not in block:
            # also allow match in selected event texts
            blob = " ".join(
                f"{e.exact_text} {e.evidence_summary}" for e in result.events
            )
            if token not in blob:
                passed = False
                notes.append(f"missing:{token}")
    for token in must_not:
        blob = block + " " + " ".join(
            f"{e.exact_text} {e.evidence_summary}" for e in result.events
        )
        if token in blob:
            passed = False
            notes.append(f"leaked:{token}")
    return CaseResult(
        category=case["category"],
        passed=passed,
        case_id=case["id"],
        notes=";".join(notes),
    )


def test_offline_continuity_cases_selector_baseline():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    results = [_evaluate_case(case) for case in data["cases"]]
    metrics = ContinuityMetrics.from_results(results)
    failures_by_category: dict[str, list[str]] = {}
    for result in results:
        if result.passed:
            continue
        failures_by_category.setdefault(result.category, []).append(
            f"{result.case_id}({result.notes})"
        )
    for category in sorted(KNOWN_CATEGORIES):
        assert metrics.rate(category) == 1.0, (
            f"{category} continuity gate failed: "
            + ", ".join(failures_by_category.get(category, []))
        )
