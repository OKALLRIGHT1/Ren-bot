"""Canonical actor identity for permission and media decisions.

Long-term contract:
  - local: desktop GUI / voice / sensors
  - qq_owner: NAPCAT owner QQ ids only
  - qq_other: every other QQ sender

Channel:
  - local_ui / private / group

HIGH-risk side effects are allowed only for:
  local, or (qq_owner AND private). Group chat never allows HIGH even for owners.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class ActorKind(str, Enum):
    LOCAL = "local"
    QQ_OWNER = "qq_owner"
    QQ_OTHER = "qq_other"


class ActorChannel(str, Enum):
    LOCAL_UI = "local_ui"
    PRIVATE = "private"
    GROUP = "group"


REMOTE_QQ_SOURCES = frozenset({"qq_gateway", "napcat_qq", "qq_private", "qq_group"})
LOCAL_SOURCES = frozenset(
    {
        "text_input",
        "voice",
        "codex_input",
        "screen_sensor",
        "music_sensor",
        "local",
        "gui",
    }
)


@dataclass(frozen=True, slots=True)
class ActorContext:
    kind: ActorKind
    channel: ActorChannel
    source: str = "text_input"
    user_id: str = ""
    is_owner: bool = False
    message_type: str = ""

    @property
    def is_remote_qq(self) -> bool:
        return self.kind in {ActorKind.QQ_OWNER, ActorKind.QQ_OTHER}

    @property
    def allows_high_risk(self) -> bool:
        """HIGH side effects: local, or owner private chat only."""
        if self.kind == ActorKind.LOCAL:
            return True
        return self.kind == ActorKind.QQ_OWNER and self.channel == ActorChannel.PRIVATE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "channel": self.channel.value,
            "source": self.source,
            "user_id": self.user_id,
            "is_owner": self.is_owner,
            "message_type": self.message_type,
            "allows_high_risk": self.allows_high_risk,
        }


def _channel_meta(ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(ctx, dict):
        return {}
    meta = ctx.get("channel_meta")
    return meta if isinstance(meta, dict) else {}


def resolve_actor_context(ctx: Optional[Dict[str, Any]] = None) -> ActorContext:
    """Derive ActorContext from chat/plugin ctx. Prefer precomputed actor fields."""
    raw = ctx if isinstance(ctx, dict) else {}
    meta = _channel_meta(raw)

    pre = raw.get("actor")
    if isinstance(pre, ActorContext):
        return pre
    if isinstance(pre, dict) and pre.get("kind") and pre.get("channel"):
        try:
            return ActorContext(
                kind=ActorKind(str(pre["kind"])),
                channel=ActorChannel(str(pre["channel"])),
                source=str(pre.get("source") or raw.get("source") or "text_input"),
                user_id=str(pre.get("user_id") or ""),
                is_owner=bool(pre.get("is_owner")),
                message_type=str(pre.get("message_type") or ""),
            )
        except Exception:
            pass

    source = str(raw.get("source") or meta.get("source") or "text_input").strip().lower()
    adapter = str(meta.get("adapter") or "").strip().lower()
    is_remote = source in REMOTE_QQ_SOURCES or adapter == "napcat_qq"

    message_type = str(meta.get("message_type") or "").strip().lower()
    group_id = str(meta.get("group_id") or "").strip()
    if not is_remote:
        channel = ActorChannel.LOCAL_UI
    elif message_type == "group" or bool(group_id):
        channel = ActorChannel.GROUP
    elif message_type in {"private", "friend"} or source == "qq_private":
        channel = ActorChannel.PRIVATE
    elif source == "qq_group":
        channel = ActorChannel.GROUP
    else:
        # Default remote without group markers to private (safer for owner HIGH).
        channel = ActorChannel.PRIVATE

    user_id = str(
        meta.get("user_id")
        or meta.get("sender_id")
        or raw.get("user_id")
        or ""
    ).strip()
    is_owner = bool(meta.get("is_owner")) or str(meta.get("sender_role") or "").lower() == "owner"

    if not is_remote:
        kind = ActorKind.LOCAL
        is_owner = True
    elif is_owner:
        kind = ActorKind.QQ_OWNER
    else:
        kind = ActorKind.QQ_OTHER

    return ActorContext(
        kind=kind,
        channel=channel,
        source=source or "text_input",
        user_id=user_id,
        is_owner=is_owner if is_remote else True,
        message_type=message_type or channel.value,
    )


def ensure_actor_context(ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a shallow copy of ctx with actor / actor_kind / actor_channel filled."""
    runtime = dict(ctx or {})
    actor = resolve_actor_context(runtime)
    runtime["actor"] = actor
    runtime["actor_kind"] = actor.kind.value
    runtime["actor_channel"] = actor.channel.value
    runtime["actor_allows_high_risk"] = actor.allows_high_risk
    return runtime


def actor_from_mapping(data: Any) -> Optional[ActorContext]:
    if isinstance(data, ActorContext):
        return data
    if not isinstance(data, dict):
        return None
    try:
        return ActorContext(
            kind=ActorKind(str(data["kind"])),
            channel=ActorChannel(str(data["channel"])),
            source=str(data.get("source") or "text_input"),
            user_id=str(data.get("user_id") or ""),
            is_owner=bool(data.get("is_owner")),
            message_type=str(data.get("message_type") or ""),
        )
    except Exception:
        return None


def dump_actor(actor: ActorContext) -> Dict[str, Any]:
    payload = asdict(actor)
    payload["kind"] = actor.kind.value
    payload["channel"] = actor.channel.value
    payload["allows_high_risk"] = actor.allows_high_risk
    return payload
