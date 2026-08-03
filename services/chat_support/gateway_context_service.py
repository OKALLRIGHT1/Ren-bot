from __future__ import annotations

from typing import Any, Dict, Optional


class GatewayContextService:
    def __init__(
        self,
        *,
        qq_remote_sources: set[str],
        owner_shared_session_id: str,
        owner_shared_local_sources: set[str],
    ) -> None:
        self.qq_remote_sources = set(qq_remote_sources or set())
        self.owner_shared_session_id = str(owner_shared_session_id or "").strip()
        self.owner_shared_local_sources = set(owner_shared_local_sources or set())

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
