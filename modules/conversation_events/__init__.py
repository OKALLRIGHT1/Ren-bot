"""Near-history conversation events: single source of truth + projections."""

from modules.conversation_events.metrics import (
    CaseResult,
    ContextTrace,
    ContinuityMetrics,
    KNOWN_CATEGORIES,
)
from modules.conversation_events.models import (
    AssembledContext,
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
    EventBudget,
    MidTermSegment,
    SelectionResult,
)
from modules.conversation_events.mid_term import (
    MidTermSegmentBuilder,
    MidTermSegmentStore,
    build_stub_summary,
    format_mid_term_block,
    validate_summary,
)
from modules.conversation_events.prompt import (
    RECENT_BLOCK_TITLE,
    detect_dual_inject,
    format_recent_event_block,
)
from modules.conversation_events.selector import RecentEventSelector
from modules.conversation_events.store import ConversationEventStore

__all__ = [
    "AssembledContext",
    "CaseResult",
    "ContextTrace",
    "ContinuityMetrics",
    "ConversationEvent",
    "ConversationEventStore",
    "ConversationEventType",
    "ConversationScope",
    "EventBudget",
    "KNOWN_CATEGORIES",
    "MidTermSegment",
    "MidTermSegmentBuilder",
    "MidTermSegmentStore",
    "RECENT_BLOCK_TITLE",
    "RecentEventSelector",
    "SelectionResult",
    "build_stub_summary",
    "detect_dual_inject",
    "format_mid_term_block",
    "format_recent_event_block",
    "validate_summary",
]
