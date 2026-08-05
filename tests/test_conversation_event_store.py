from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)
from modules.conversation_events.store import ConversationEventStore
from modules.memory_sqlite import MemorySQLite


@pytest.fixture
def sqlite_store(tmp_path: Path):
    return MemorySQLite(str(tmp_path / "events.sqlite"))


@pytest.fixture
def store(sqlite_store):
    return ConversationEventStore(sqlite_store)


def scope(conversation_id: str, *, person_id: str = "owner", channel: str = "desktop"):
    return ConversationScope(
        persona_id="suzu",
        person_id=person_id,
        channel=channel,
        conversation_id=conversation_id,
    )


def make_event(
    *,
    scope: ConversationScope,
    text: str = "hello",
    event_type: ConversationEventType = ConversationEventType.USER_MESSAGE,
    causal_parent_ids: tuple[str, ...] = (),
    expires_at=None,
    event_id: str = "",
    occurred_at=None,
    evidence: str = "",
    metadata=None,
):
    return ConversationEvent(
        event_id=event_id or "",
        scope=scope,
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        exact_text=text,
        evidence_summary=evidence or text,
        causal_parent_ids=causal_parent_ids,
        expires_at=expires_at,
        status="active",
        metadata=dict(metadata or {}),
    )


def test_event_write_rejects_missing_conversation_id(store):
    bad = ConversationScope("suzu", "owner", "desktop", "")
    with pytest.raises(ValueError, match="conversation_id"):
        store.append(make_event(scope=bad))


def test_event_write_rejects_parent_from_other_conversation(store):
    first = store.append(make_event(scope=scope("local:desktop"), text="desktop"))
    child = make_event(
        scope=scope("private:42", channel="qq"),
        text="child",
        causal_parent_ids=(first.event_id,),
    )
    with pytest.raises(ValueError, match="causal parent scope mismatch"):
        store.append(child)


def test_recent_query_is_hard_scoped_and_excludes_expired(store):
    clock_now = datetime.now(timezone.utc)
    store.append(
        make_event(
            scope=scope("local:desktop"),
            text="desktop",
            occurred_at=clock_now - timedelta(seconds=10),
        )
    )
    store.append(
        make_event(
            scope=scope("private:42", channel="qq"),
            text="qq",
            occurred_at=clock_now - timedelta(seconds=5),
        )
    )
    store.append(
        make_event(
            scope=scope("local:desktop"),
            text="expired",
            occurred_at=clock_now - timedelta(seconds=3),
            expires_at=clock_now - timedelta(seconds=1),
        )
    )
    events = store.list_recent(scope("local:desktop"), now=clock_now, limit=10)
    assert [event.exact_text for event in events] == ["desktop"]


def test_get_and_mark_status(store):
    event = store.append(make_event(scope=scope("local:desktop"), text="x"))
    loaded = store.get(event.event_id)
    assert loaded is not None
    assert loaded.exact_text == "x"
    store.mark_status(event.event_id, "archived")
    assert store.list_recent(scope("local:desktop"), now=datetime.now(timezone.utc)) == []
    still = store.get(event.event_id)
    assert still is not None
    assert still.status == "archived"


def test_causal_parent_same_scope_ok(store):
    parent = store.append(
        make_event(
            scope=scope("local:desktop"),
            text="obs",
            event_type=ConversationEventType.SCREEN_OBSERVATION,
        )
    )
    child = store.append(
        make_event(
            scope=scope("local:desktop"),
            text="roast",
            event_type=ConversationEventType.PROACTIVE_UTTERANCE,
            causal_parent_ids=(parent.event_id,),
        )
    )
    assert child.causal_parent_ids == (parent.event_id,)
