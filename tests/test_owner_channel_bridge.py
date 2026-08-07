"""Owner on-demand cross-channel near-history bridge."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from modules.conversation_events.models import (
    ConversationEvent,
    ConversationEventType,
    ConversationScope,
)
from modules.conversation_events.store import ConversationEventStore
from modules.memory_sqlite import MemorySQLite
from plugins.owner_channel_bridge.plugin import Plugin
from services.security.actor import ActorChannel, ActorKind, ActorContext


def _scope(channel: str, cid: str) -> ConversationScope:
    return ConversationScope("suzu", "owner", channel, cid)


def _append(store, channel, cid, text, *, etype=ConversationEventType.USER_MESSAGE, at=None):
    return store.append(
        ConversationEvent(
            event_id="",
            scope=_scope(channel, cid),
            event_type=etype,
            occurred_at=at or datetime.now(timezone.utc),
            exact_text=text,
            evidence_summary=text,
            status="active",
            metadata={},
        )
    )


@pytest.fixture
def store(tmp_path: Path):
    sqlite = MemorySQLite(str(tmp_path / "bridge.sqlite"))
    return ConversationEventStore(sqlite)


@pytest.mark.asyncio
async def test_list_recent_for_person_crosses_conversation_but_not_person(store):
    t0 = datetime.now(timezone.utc)
    _append(store, "desktop", "local:desktop", "桌面在改登录", at=t0)
    _append(store, "qq", "qq:private:1", "QQ在聊晚饭", at=t0 + timedelta(seconds=1))
    store.append(
        ConversationEvent(
            event_id="",
            scope=ConversationScope("suzu", "other_user", "qq", "qq:private:2"),
            event_type=ConversationEventType.USER_MESSAGE,
            occurred_at=t0 + timedelta(seconds=2),
            exact_text="别人的话",
            evidence_summary="别人的话",
            status="active",
            metadata={},
        )
    )

    hits = store.list_recent_for_person(
        persona_id="suzu",
        person_id="owner",
        now=datetime.now(timezone.utc),
        limit=10,
    )
    texts = [e.exact_text for e in hits]
    assert "桌面在改登录" in texts
    assert "QQ在聊晚饭" in texts
    assert "别人的话" not in texts

    desktop_only = store.list_recent_for_person(
        persona_id="suzu",
        person_id="owner",
        now=datetime.now(timezone.utc),
        channels=("desktop",),
        limit=10,
    )
    assert [e.exact_text for e in desktop_only] == ["桌面在改登录"]


@pytest.mark.asyncio
async def test_plugin_owner_private_can_read_desktop_side(store):
    t0 = datetime.now(timezone.utc)
    _append(store, "desktop", "local:desktop", "本地吐槽了原神", at=t0)
    _append(store, "qq", "qq:private:owner", "QQ说晚饭吃面", at=t0 + timedelta(seconds=1))

    plugin = Plugin()
    class Service:
        def __init__(self, event_store):
            self.store = event_store

        def resolve_scope(self, ctx):
            return _scope("qq", "qq:private:owner")

    ctx = {
        "source": "napcat_qq",
        "channel_meta": {"is_owner": True, "message_type": "private", "user_id": "1"},
        "conversation_event_service": Service(store),
        "persona_id": "suzu",
        "memory_person_id": "owner",
        "user_text": "本地刚才在干嘛",
    }
    # Force actor via explicit fields used by resolve_actor_context
    out = await plugin.run("desktop ||| 原神", ctx)
    assert "原神" in out
    assert "晚饭吃面" not in out
    assert "按需查询" in out


@pytest.mark.asyncio
async def test_plugin_non_owner_denied(store):
    _append(store, "desktop", "local:desktop", "秘密")
    plugin = Plugin()

    class Service:
        store = store

        def resolve_scope(self, ctx):
            return _scope("qq", "qq:private:2")

    ctx = {
        "source": "napcat_qq",
        "channel_meta": {"is_owner": False, "message_type": "private", "user_id": "2"},
        "conversation_event_service": Service(),
        "user_text": "本地刚才在干嘛",
    }
    out = await plugin.run("desktop", ctx)
    assert "仅限主人" in out


@pytest.mark.asyncio
async def test_plugin_group_owner_denied(store):
    """Even owner group chat must not pull private desktop near-history."""
    _append(store, "desktop", "local:desktop", "本地秘密")
    plugin = Plugin()

    class Service:
        store = store

        def resolve_scope(self, ctx):
            return _scope("qq", "group:9")

    ctx = {
        "source": "napcat_qq",
        "channel_meta": {
            "is_owner": True,
            "message_type": "group",
            "user_id": "1",
            "session_id": "group:9",
        },
        "conversation_event_service": Service(),
        "user_text": "本地刚才在干嘛",
    }
    out = await plugin.run("desktop", ctx)
    assert "仅限主人" in out or "不可用" in out


def test_match_requires_owner_actor():
    plugin = Plugin()
    owner_ctx = {
        "source": "text_input",
        "channel_meta": {},
    }
    match = plugin._match_bridge("我本地刚才在干嘛", owner_ctx)
    assert match is not None
    assert match.capability_id == "owner.cross_channel_recent"

    stranger_ctx = {
        "source": "napcat_qq",
        "channel_meta": {"is_owner": False, "message_type": "private", "user_id": "x"},
    }
    assert plugin._match_bridge("我本地刚才在干嘛", stranger_ctx) is None
