from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional


class GatewayContextService:
    def __init__(
        self,
        *,
        qq_remote_sources: set[str],
        owner_shared_session_id: str,
        owner_shared_local_sources: set[str],
        memory_store_getter: Optional[Callable[[], Any]] = None,
        logger: Any = None,
    ) -> None:
        self.qq_remote_sources = set(qq_remote_sources or set())
        self.owner_shared_session_id = str(owner_shared_session_id or "").strip()
        self.owner_shared_local_sources = set(owner_shared_local_sources or set())
        self._memory_store_getter = memory_store_getter
        self.logger = logger

    def is_qq_source(self, ctx: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(ctx, dict):
            return False
        source = str(ctx.get("source") or "").strip().lower()
        return source in self.qq_remote_sources

    def qq_session_label(self, session_id: str) -> str:
        text = str(session_id or "").strip().lower()
        if text.startswith("group:"):
            return "QQ-GROUP"
        if text.startswith("private:"):
            return "QQ-PRIVATE"
        return "QQ"

    def reply_effect_identity(
        self, ctx: Optional[Dict[str, Any]]
    ) -> tuple[str, str]:
        if not isinstance(ctx, dict):
            return "", ""
        channel_meta = (
            ctx.get("channel_meta") if isinstance(ctx.get("channel_meta"), dict) else {}
        )
        session_id = str(channel_meta.get("session_id") or "").strip()
        user_id = str(channel_meta.get("user_id") or ctx.get("user_id") or "").strip()
        if not session_id:
            session_id = str(ctx.get("session_id") or "").strip()
        if not session_id:
            source = str(ctx.get("source") or "local").strip() or "local"
            session_id = f"local:{source}"
        return session_id, user_id

    def conversation_session_key(self, ctx: Optional[Dict[str, Any]]) -> str:
        session_id, _user_id = self.reply_effect_identity(ctx)
        return str(session_id or "").strip()

    def event_channel(self, ctx: Optional[Dict[str, Any]]) -> str:
        """Normalize transport source into event channel (desktop vs qq)."""
        if self.is_qq_source(ctx):
            return "qq"
        return "desktop"

    def event_scope_parts(
        self, ctx: Optional[Dict[str, Any]]
    ) -> tuple[str, str]:
        """Return (channel, conversation_id) for ConversationScope.

        Reuses conversation_session_key(); never invents a second session key.
        """
        conversation_id = self.conversation_session_key(ctx)
        if not conversation_id:
            source = ""
            if isinstance(ctx, dict):
                source = str(ctx.get("source") or "").strip() or "local"
            else:
                source = "local"
            conversation_id = f"local:{source}"
        return self.event_channel(ctx), conversation_id

    def memory_session_id(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not isinstance(ctx, dict):
            return ""
        source = str(ctx.get("source") or "").strip().lower()
        if source in self.owner_shared_local_sources:
            return self.owner_shared_session_id
        if source not in self.qq_remote_sources:
            return ""
        channel_meta = ctx.get("channel_meta") or {}
        session_id = str(channel_meta.get("session_id") or "").strip()
        message_type = str(channel_meta.get("message_type") or "").strip().lower()
        if bool(channel_meta.get("is_owner")):
            return self.owner_shared_session_id
        if message_type == "group" or session_id.lower().startswith("group:"):
            return session_id
        return session_id

    def _store(self):
        getter = self._memory_store_getter
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _warn(self, message: str) -> None:
        if self.logger:
            try:
                self.logger.warning(message)
            except Exception:
                pass

    def get_active_user_address(self, ctx: Optional[Dict[str, Any]] = None) -> str:
        if self.is_qq_source(ctx):
            channel_meta = (ctx or {}).get("channel_meta") or {}
            if not bool(channel_meta.get("is_owner")):
                return ""
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character() or {}
            address = str(active_char.get("user_address") or "").strip()
            return address or "Master"
        except Exception:
            return "Master"

    def build_user_address_context(self, ctx: Optional[Dict[str, Any]] = None) -> str:
        address = self.get_active_user_address(ctx)
        if not address:
            return ""
        return (
            "【称呼规则】"
            f"当前角色称呼用户为「{address}」。"
            "回复中需要称呼用户时优先使用这个称呼，不要自行改成“主人”。"
        )

    async def sync_qq_user_profile(self, ctx: Optional[Dict[str, Any]]) -> None:
        if not self.is_qq_source(ctx):
            return
        channel_meta = (ctx or {}).get("channel_meta") or {}
        user_id = str(channel_meta.get("user_id") or "").strip()
        if not user_id:
            return
        store = self._store()
        if not store:
            return
        sender = channel_meta.get("sender") or {}
        sender_name = str(
            channel_meta.get("sender_name") or sender.get("nickname") or user_id
        ).strip()
        remark_name = str(sender.get("card") or sender.get("remark") or "").strip()
        message_type = (
            str(channel_meta.get("message_type") or "private").strip().lower()
            or "private"
        )
        is_owner = bool(channel_meta.get("is_owner"))
        relationship = (
            "owner"
            if is_owner
            else ("group_member" if message_type == "group" else "contact")
        )
        memory_scope = (
            self.owner_shared_session_id
            if is_owner
            else ("group_shared" if message_type == "group" else "private")
        )
        profile_payload = {
            "user_id": user_id,
            "nickname": sender_name,
            "remark_name": remark_name,
            "relationship_to_owner": relationship,
            "permission_level": "owner" if is_owner else "default",
            "memory_scope": memory_scope,
            "is_owner": is_owner,
        }
        try:
            await asyncio.to_thread(store.upsert_qq_user_profile, profile_payload)
        except Exception as exc:
            self._warn(f"QQ user profile sync failed: {exc}")

    def build_external_sender_context(self, ctx: Optional[Dict[str, Any]]) -> str:
        if not self.is_qq_source(ctx):
            return ""
        channel_meta = (ctx or {}).get("channel_meta") or {}
        sender_name = str(
            channel_meta.get("sender_name")
            or channel_meta.get("user_id")
            or "Unknown Contact"
        ).strip()
        user_id = str(channel_meta.get("user_id") or "").strip()
        session_id = str(channel_meta.get("session_id") or "").strip()
        group_id = str(channel_meta.get("group_id") or "").strip()
        message_type = (
            str(channel_meta.get("message_type") or "private").strip() or "private"
        )
        is_owner = bool(channel_meta.get("is_owner"))
        owner_label = (
            self.get_active_user_address(ctx)
            if is_owner
            else str(channel_meta.get("owner_label") or "Owner").strip()
        ) or "Owner"
        relation = (
            f"The current sender is {owner_label}."
            if is_owner
            else f"The current sender is not {owner_label}; they are another QQ contact or a group member."
        )
        parts = [
            "[QQ Message Context]",
            f"chat_type: {message_type}",
            f"sender: {sender_name}",
            relation,
            "Always distinguish the owner from other QQ contacts.",
            "Keep using the current local persona and tone.",
            "QQ messages are text-only and must not drive Live2D or desktop voice.",
            "Reply like an instant message, not a formal report or customer-service answer.",
        ]
        if user_id:
            parts.append(f"sender_qq: {user_id}")
        if group_id:
            parts.append(f"group_id: {group_id}")
        if session_id:
            parts.append(f"session_id: {session_id}")

        store = self._store()
        profile = None
        if store and user_id:
            try:
                profile = store.get_qq_user_profile(user_id)
            except Exception as exc:
                self._warn(f"Load QQ user profile failed: {exc}")
        if profile:
            relation_map = {
                "owner": f"{owner_label} (owner)",
                "contact": "QQ private contact",
                "group_member": "QQ group member",
            }
            scope_map = {
                self.owner_shared_session_id: "owner shared memory",
                "private": "private memory",
                "group_shared": "group shared memory",
            }
            display_name = str(
                profile.get("remark_name") or profile.get("nickname") or ""
            ).strip()
            if display_name and display_name != sender_name:
                parts.append(f"profile_name: {display_name}")
            profile_relation = relation_map.get(
                str(profile.get("relationship_to_owner") or "").strip()
            )
            if profile_relation:
                parts.append(f"profile_relation: {profile_relation}")
            identity_summary = str(profile.get("identity_summary") or "").strip()
            if identity_summary:
                parts.append(f"profile_summary: {identity_summary}")
            notes = str(profile.get("notes") or "").strip()
            if notes:
                parts.append(f"profile_notes: {notes}")
            permission_level = str(profile.get("permission_level") or "").strip()
            if permission_level:
                parts.append(f"permission_level: {permission_level}")
            scope_label = scope_map.get(str(profile.get("memory_scope") or "").strip())
            if scope_label:
                parts.append(f"memory_scope: {scope_label}")
        return "\n".join(parts)
