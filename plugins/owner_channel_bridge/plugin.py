"""On-demand owner bridge across desktop / QQ near-history.

Default conversation isolation stays hard. This plugin only runs when the
owner explicitly asks (tool / slash command), and only for local or QQ-owner
private chat. Group chat and non-owner QQ never get cross-channel near-history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from services.capability_manager import ToolCapability, ToolCapabilityMatch
from services.security.actor import (
    ActorChannel,
    ActorKind,
    resolve_actor_context,
)


_DESKTOP_CHANNELS = frozenset({"desktop", "local", "gui", "text_input", "voice"})
_QQ_CHANNELS = frozenset(
    {"qq", "qq_gateway", "napcat_qq", "qq_private", "qq_group", "remote_qq"}
)


class Plugin:
    name = "主人跨通道近史"
    type = "delegate"
    aliases = ["/桥接", "/跨通道", "/channel_bridge"]
    direct_command_aliases = ["/桥接", "/跨通道", "/channel_bridge"]
    description = "主人按需查看桌面或 QQ 另一侧的近史摘要（不自动混会话）。"
    example_arg = "desktop ||| 刚才在干嘛"

    def get_capabilities(self):
        return [
            ToolCapability(
                id="owner.cross_channel_recent",
                plugin="owner_channel_bridge",
                trigger_mode="natural",
                match=self._match_bridge,
                description="查询主人在桌面或 QQ 另一侧最近说过/发生过什么",
                examples=[
                    "我本地刚才在干嘛",
                    "QQ上我们刚聊了什么",
                    "桌面那边吐槽了啥",
                    "跨通道看一下刚才的对话",
                ],
            ),
        ]

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        actor = resolve_actor_context(ctx)
        if not self._actor_allowed(actor):
            return "跨通道近史仅限主人在本地或私聊使用，当前会话不可用。"

        store = self._event_store(ctx)
        if store is None:
            return "近史事件服务当前不可用。"

        raw = self._strip_alias(str(args or "").strip())
        target, query = self._parse_target(raw, ctx)
        if not target:
            return self._help_text()

        persona_id, person_id = self._owner_ids(ctx)
        current_scope = self._current_scope(ctx)
        channels = self._channels_for_target(target)
        exclude = ""
        if current_scope is not None:
            exclude = str(getattr(current_scope, "conversation_id", "") or "")

        try:
            events = store.list_recent_for_person(
                persona_id=persona_id,
                person_id=person_id,
                now=datetime.now(timezone.utc),
                channels=channels,
                exclude_conversation_id=exclude,
                limit=self._max_items(ctx),
            )
        except Exception as exc:
            return f"跨通道近史查询失败：{exc}"

        if query:
            events = self._filter_by_query(events, query)

        if not events:
            side = "桌面" if target == "desktop" else ("QQ" if target == "qq" else "其他通道")
            return f"在{side}侧没有找到可引用的近史记录。"

        return self._format_events(events, target=target, query=query, ctx=ctx)

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        return any(
            raw == alias or raw.startswith(alias + " ") for alias in self.direct_command_aliases
        )

    @staticmethod
    def _actor_allowed(actor: Any) -> bool:
        kind = getattr(actor, "kind", None)
        channel = getattr(actor, "channel", None)
        if kind == ActorKind.LOCAL:
            return True
        if kind == ActorKind.QQ_OWNER and channel == ActorChannel.PRIVATE:
            return True
        return False

    def _event_store(self, ctx: Dict[str, Any]) -> Any:
        service = (ctx or {}).get("conversation_event_service")
        if service is not None and getattr(service, "store", None) is not None:
            return service.store
        brain = (ctx or {}).get("brain")
        assembler = getattr(brain, "context_assembler", None)
        store = getattr(assembler, "store", None)
        if store is not None:
            return store
        # Lazy attach if ChatService put service on brain/app later.
        chat = (ctx or {}).get("chat_service")
        service = getattr(chat, "conversation_event_service", None)
        return getattr(service, "store", None)

    def _owner_ids(self, ctx: Dict[str, Any]) -> tuple[str, str]:
        persona = str(
            (ctx or {}).get("persona_id")
            or (ctx or {}).get("character_id")
            or "suzu"
        ).strip() or "suzu"
        person = str(
            (ctx or {}).get("memory_person_id")
            or (ctx or {}).get("person_id")
            or "owner"
        ).strip() or "owner"
        # Cross-channel bridge is owner-only; force owner person scope.
        if person not in {"owner", "master"}:
            person = "owner"
        return persona, person

    def _current_scope(self, ctx: Dict[str, Any]) -> Any:
        service = (ctx or {}).get("conversation_event_service")
        if service is not None and hasattr(service, "resolve_scope"):
            try:
                return service.resolve_scope(ctx)
            except Exception:
                return None
        return None

    def _max_items(self, ctx: Dict[str, Any]) -> int:
        settings = ((ctx or {}).get("plugin_settings") or {}).get(
            "owner_channel_bridge"
        ) or {}
        try:
            return max(1, min(20, int(settings.get("max_items", 8) or 8)))
        except Exception:
            return 8

    def _max_chars(self, ctx: Dict[str, Any]) -> int:
        settings = ((ctx or {}).get("plugin_settings") or {}).get(
            "owner_channel_bridge"
        ) or {}
        try:
            return max(200, min(4000, int(settings.get("max_chars", 1200) or 1200)))
        except Exception:
            return 1200

    def _parse_target(self, text: str, ctx: Dict[str, Any]) -> tuple[str, str]:
        raw = str(text or "").strip()
        user_text = str((ctx or {}).get("user_text") or "").strip()
        blob = raw or user_text
        parts = [p.strip() for p in raw.split("|||", 1)] if raw else [""]
        head = parts[0].lower() if parts else ""
        tail = parts[1] if len(parts) == 2 else ""

        explicit = {
            "desktop": "desktop",
            "local": "desktop",
            "gui": "desktop",
            "本地": "desktop",
            "桌面": "desktop",
            "qq": "qq",
            "remote": "qq",
            "other": "other",
            "另一侧": "other",
            "跨通道": "other",
        }
        if head in explicit:
            return explicit[head], tail or user_text

        # Natural language target from full text.
        source = str((ctx or {}).get("source") or "").strip().lower()
        on_desktop = source in _DESKTOP_CHANNELS or source in {
            "",
            "local",
            "text_input",
            "voice",
            "gui",
            "codex_input",
        }
        if any(k in blob for k in ("本地", "桌面", "电脑", "屏幕", "刚吐槽")):
            # Asking about desktop while on QQ, or about screen while local.
            if not on_desktop or any(k in blob for k in ("本地", "桌面", "电脑")):
                return "desktop", blob
        if any(k in blob for k in ("qq", "QQ", "群里", "私聊里", "手机上")):
            return "qq", blob
        if any(k in blob for k in ("另一侧", "那边", "跨通道", "另一个通道")):
            return "other", blob
        if raw:
            # Default: the other side relative to current channel.
            return ("qq" if on_desktop else "desktop"), raw
        return "", ""

    def _channels_for_target(self, target: str) -> Optional[Sequence[str]]:
        if target == "desktop":
            return ("desktop",)
        if target == "qq":
            return ("qq",)
        # other: no channel filter — all owner conversations except excluded current
        return None

    def _filter_by_query(self, events: Sequence[Any], query: str) -> List[Any]:
        tokens = [
            t
            for t in __import__("re").findall(r"[\w\u4e00-\u9fff]{2,}", str(query or ""))
            if t
        ]
        if not tokens:
            return list(events)
        kept: List[Any] = []
        for event in events:
            blob = f"{event.exact_text} {event.evidence_summary}".lower()
            if any(token.lower() in blob for token in tokens):
                kept.append(event)
        return kept or list(events)

    def _format_events(
        self,
        events: Sequence[Any],
        *,
        target: str,
        query: str,
        ctx: Dict[str, Any],
    ) -> str:
        side = {
            "desktop": "桌面/本地",
            "qq": "QQ",
            "other": "其他通道",
        }.get(target, target)
        lines = [
            f"【跨通道近史｜{side}｜按需查询】",
            "以下仅供回答当前问题参考；默认会话仍隔离，未自动混入上下文。",
        ]
        if query:
            lines.append(f"查询线索：{query[:80]}")
        budget = self._max_chars(ctx)
        used = sum(len(line) + 1 for line in lines)
        # Newest last already; show newest first for the owner summary.
        for event in reversed(list(events)):
            scope = getattr(event, "scope", None)
            channel = str(getattr(scope, "channel", "") or "")
            cid = str(getattr(scope, "conversation_id", "") or "")
            text = str(
                getattr(event, "exact_text", "")
                or getattr(event, "evidence_summary", "")
                or ""
            ).strip()
            if not text:
                continue
            if len(text) > 160:
                text = text[:157] + "..."
            etype = getattr(getattr(event, "event_type", None), "value", "event")
            line = f"- [{channel}/{cid}] ({etype}) {text}"
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1
        if len(lines) <= 2:
            return f"在{side}侧没有可展示的近史正文。"
        return "\n".join(lines)

    def _strip_alias(self, text: str) -> str:
        for alias in self.direct_command_aliases:
            if text == alias:
                return ""
            if text.startswith(alias + " "):
                return text[len(alias) :].strip()
        return text

    @staticmethod
    def _help_text() -> str:
        return (
            "用法：/桥接 desktop|qq|other ||| 可选关键词\n"
            "或自然语言：我本地刚才在干嘛 / QQ上刚聊了什么\n"
            "说明：仅主人本地或私聊可用；不会默认把两侧会话自动混在一起。"
        )

    @staticmethod
    def _match_bridge(text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
        actor = resolve_actor_context(ctx)
        if not Plugin._actor_allowed(actor):
            return None
        raw = str(text or "").strip()
        if not raw:
            return None
        cues = (
            "本地刚才",
            "桌面刚才",
            "电脑上",
            "QQ上",
            "qq上",
            "另一侧",
            "那边聊",
            "跨通道",
            "桌面那边",
            "私聊里刚",
            "屏幕上刚",
            "刚才在干嘛",
            "刚聊了什么",
        )
        if not any(cue in raw for cue in cues):
            return None
        score = 0.88 if any(
            cue in raw for cue in ("跨通道", "另一侧", "本地刚才", "QQ上", "qq上")
        ) else 0.72
        return ToolCapabilityMatch(
            capability_id="owner.cross_channel_recent",
            plugin="owner_channel_bridge",
            score=score,
            raw_text=raw,
            reason="owner_cross_channel_intent",
        )
