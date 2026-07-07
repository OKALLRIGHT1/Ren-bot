"""Input context helpers for ChatService.process()."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from core.message_source import build_output_profile


@dataclass(frozen=True)
class ChatInputContext:
    input_source: str
    source_key: str
    channel_meta: Dict[str, Any]
    transcript_channel_meta: Dict[str, Any]
    has_external_images: bool
    memory_session_id: str
    chat_log_source: str
    output_profile: Dict[str, Any]
    live2d_enabled: bool
    codex_mode: bool
    direct_chat_sources: set[str]
    should_bypass_gatekeeper: bool


def build_transcript_channel_meta(
    ctx: Optional[Dict[str, Any]],
    *,
    is_qq_source: Callable[[Optional[Dict[str, Any]]], bool],
) -> Dict[str, Any]:
    if not is_qq_source(ctx):
        return {}
    channel_meta = (ctx or {}).get("channel_meta") or {}
    result: Dict[str, Any] = {}
    for key in (
        "adapter",
        "user_id",
        "sender_name",
        "message_type",
        "group_id",
        "is_owner",
        "owner_label",
        "message_id",
    ):
        value = channel_meta.get(key)
        if value in (None, "", [], {}):
            continue
        result[key] = value
    return result


def build_chat_input_context(
    ctx: Optional[Dict[str, Any]],
    *,
    is_qq_source: Callable[[Optional[Dict[str, Any]]], bool],
    get_memory_session_id: Callable[[Optional[Dict[str, Any]]], str],
    remote_chat_sources: Optional[set[str]] = None,
) -> ChatInputContext:
    data = ctx if isinstance(ctx, dict) else {}
    input_source = str(data.get("source", "unknown") or "unknown").strip()
    channel_meta = data.get("channel_meta") or {}
    if not isinstance(channel_meta, dict):
        channel_meta = {}
    transcript_channel_meta = build_transcript_channel_meta(
        data, is_qq_source=is_qq_source
    )
    has_external_images = bool(channel_meta.get("has_image"))
    memory_session_id = get_memory_session_id(data)
    chat_log_source = input_source if input_source != "unknown" else "chat"
    output_profile = build_output_profile(str(input_source or "text_input"))
    live2d_enabled = bool(output_profile.get("live2d_enabled", True))
    codex_mode = (
        bool(data.get("codex_mode", False))
        if "codex_mode" in data
        else input_source == "codex_input"
    )
    source_key = str(input_source or "").strip().lower()
    remote_sources = {
        str(source or "").strip().lower()
        for source in set(remote_chat_sources or set())
        if str(source or "").strip()
    }
    direct_chat_sources = {
        "text_input",
        "voice",
        "desktop",
        "codex_input",
        *remote_sources,
    }
    should_bypass_gatekeeper = (
        source_key in direct_chat_sources or codex_mode or has_external_images
    )
    return ChatInputContext(
        input_source=input_source,
        source_key=source_key,
        channel_meta=channel_meta,
        transcript_channel_meta=transcript_channel_meta,
        has_external_images=has_external_images,
        memory_session_id=memory_session_id,
        chat_log_source=chat_log_source,
        output_profile=output_profile,
        live2d_enabled=live2d_enabled,
        codex_mode=codex_mode,
        direct_chat_sources=direct_chat_sources,
        should_bypass_gatekeeper=should_bypass_gatekeeper,
    )
