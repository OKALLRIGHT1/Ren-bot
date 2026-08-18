"""Conservative auto-retrieval gate for the knowledge base.

Chat auto-inject only fires on explicit source words. Plugin / GUI search
must not call this module.
"""

from __future__ import annotations

SOURCE_MARKERS = ("资料", "设定", "知识库", "文档里", "词条")
_SKIP_MEMORY_INTENTS = frozenset({"episode", "profile"})


def knowledge_retrieval_decision(
    user_text: str,
    *,
    memory_intent: str = "none",
    tool_mode: bool = False,
    enabled: bool = True,
) -> tuple[bool, str]:
    """Return (should_retrieve, reason). Reason is a short code, never the user text."""
    if not enabled:
        return False, "disabled"
    if tool_mode:
        return False, "tool_mode"
    intent = str(memory_intent or "none").strip().lower()
    if intent in _SKIP_MEMORY_INTENTS:
        return False, f"memory_intent:{intent}"
    text = str(user_text or "").strip()
    if not text:
        return False, "empty"
    if text.startswith("/"):
        return False, "command"
    if any(marker in text for marker in SOURCE_MARKERS):
        return True, "source_marker"
    return False, "no_source_marker"


def should_retrieve_knowledge(
    user_text: str,
    *,
    memory_intent: str = "none",
    tool_mode: bool = False,
    enabled: bool = True,
) -> bool:
    allowed, _reason = knowledge_retrieval_decision(
        user_text,
        memory_intent=memory_intent,
        tool_mode=tool_mode,
        enabled=enabled,
    )
    return allowed
