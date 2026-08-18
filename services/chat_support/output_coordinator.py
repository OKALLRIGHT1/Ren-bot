"""Output side-effect coordinators for ChatService.process()."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from services.chat_support.text_splitter import split_assistant_display_parts


async def emit_assistant_ui_parts(event_bus: Any, text: str) -> list[str]:
    """Immediate fallback when the presenter is not pacing chat lines."""
    parts = split_assistant_display_parts(text)
    for part in parts:
        await event_bus.emit("ui.append", role="assistant", text=part)
    return parts


def should_pace_assistant_ui(output_profile: Optional[Dict[str, Any]]) -> bool:
    profile = output_profile or {}
    return bool(profile.get("ui_append", True)) and (
        bool(profile.get("speak", True)) or bool(profile.get("show_bubble", True))
    )


@dataclass(frozen=True)
class StreamOutputContext:
    ctx: Optional[Dict[str, Any]]
    output_profile: Dict[str, Any]
    transcript_meta: Dict[str, Any]
    chat_log_source: str
    memory_session_id: Optional[str]
    feedback_type: str
    feedback_reaction: str
    codex_mode: bool
    proactive_followup: Any
    task_followup: Any


async def emit_short_reaction(
    *,
    text: str,
    emotion: str,
    user_text: str,
    ctx: Optional[Dict[str, Any]],
    transcript_meta: Dict[str, Any],
    chat_log_source: str,
    output_profile: Dict[str, Any],
    feedback_type: str,
    feedback_reaction: str,
    memory_session_id: Optional[str],
    learning: Any,
    emit_assistant_text: Callable[..., Awaitable[None]],
    add_memory_safe: Callable[..., Awaitable[Any]],
    emit_idle_status_when_safe: Callable[..., Awaitable[Any]],
    record_message_pair: Optional[Callable[..., Awaitable[Any]]] = None,
) -> bool:
    if not text:
        return False

    await emit_assistant_text(
        text,
        ctx=ctx,
        emotion=emotion,
        transcript_meta=transcript_meta,
        chat_log_source=chat_log_source,
        output_profile=output_profile,
        tool=False,
    )
    if learning:
        learning.record_interaction(
            user_text,
            text,
            emotion,
            feedback_type,
            feedback_reaction,
        )

    short_meta: dict[str, Any] = {
        "path": "short_reaction",
        "source": chat_log_source,
        **transcript_meta,
    }
    if memory_session_id:
        short_meta["session_id"] = memory_session_id
    user_event_id = ""
    assistant_event_id = ""
    if record_message_pair is not None:
        pair_ids = await record_message_pair(
            ctx=ctx,
            user_text=user_text,
            assistant_text=text,
            metadata=short_meta,
        )
        if pair_ids:
            user_event_id, assistant_event_id = pair_ids
    await add_memory_safe(
        "user", user_text, meta={**short_meta, "event_id": user_event_id}
    )
    await add_memory_safe(
        "assistant", text, meta={**short_meta, "event_id": assistant_event_id}
    )
    await emit_idle_status_when_safe(
        output_profile,
        reason="short_reaction",
        had_presenter_output=True,
    )
    return True


async def emit_background_delegate_reply(
    *,
    text: str,
    emotion: str,
    ctx: Optional[Dict[str, Any]],
    transcript_meta: Dict[str, Any],
    chat_log_source: str,
    output_profile: Dict[str, Any],
    emit_assistant_text: Callable[..., Awaitable[None]],
) -> bool:
    if not text:
        return False
    await emit_assistant_text(
        text,
        ctx=ctx,
        emotion=emotion,
        transcript_meta=transcript_meta,
        chat_log_source=chat_log_source,
        output_profile=output_profile,
        tool=True,
    )
    return True


async def emit_hardware_status_reply(
    *,
    final_text: str,
    user_text: str,
    ctx: Dict[str, Any],
    transcript_meta: Optional[Dict[str, Any]],
    chat_log_source: str,
    output_profile: Dict[str, Any],
    memory_session_id: Optional[str],
    used_triggers: list[str],
    split_gateway_text_parts: Callable[[str], list[str]],
    event_bus: Any,
    emit_assistant_text: Callable[..., Awaitable[None]],
    add_memory_safe: Callable[..., Awaitable[Any]],
) -> bool:
    reply_parts = split_gateway_text_parts(final_text)
    if not reply_parts:
        reply_parts = [final_text]

    user_meta = {
        "path": "hardware_status",
        "source": chat_log_source,
        **(transcript_meta or {}),
    }
    if memory_session_id:
        user_meta["session_id"] = memory_session_id
    await event_bus.emit("chat.log", role="user", content=user_text, meta=user_meta)
    await add_memory_safe("user", user_text, meta=user_meta)

    for index, reply_part in enumerate(reply_parts):
        await emit_assistant_text(
            reply_part,
            ctx=ctx,
            emotion="neutral",
            transcript_meta=transcript_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            tool=True,
            interrupt=(index == 0),
            apply_catchphrase=(index == len(reply_parts) - 1),
        )
        if index < len(reply_parts) - 1:
            await asyncio.sleep(0.25)

    assistant_meta = {
        "path": "hardware_status",
        "source": chat_log_source,
        "tool": True,
        "used_triggers": list(used_triggers or ["monitor"]),
        **(transcript_meta or {}),
    }
    if memory_session_id:
        assistant_meta["session_id"] = memory_session_id
    await add_memory_safe("assistant", "\n".join(reply_parts), meta=assistant_meta)
    return True


async def emit_non_stream_reply(
    *,
    final_reply: str,
    final_emo: str,
    user_text: str,
    ctx: Optional[Dict[str, Any]],
    output_profile: Dict[str, Any],
    transcript_meta: Dict[str, Any],
    chat_log_source: str,
    memory_session_id: Optional[str],
    feedback_type: str,
    feedback_reaction: str,
    triggered: bool,
    learning: Any,
    live2d_enabled: bool,
    start_emo: str,
    start_intensity: float,
    codex_mode: bool,
    proactive_followup: Any,
    task_followup: Any,
    event_bus: Any,
    presenter: Any,
    update_active_time: Callable[[], None],
    add_codex_session_event: Callable[..., None],
    presenter_output_controls_idle: Callable[..., bool],
    sensor_emotion_intensity: Callable[[str], float],
    send_gateway_reply: Callable[..., Awaitable[Any]],
    maybe_send_auto_meme_reply: Callable[..., Awaitable[Any]],
    record_reply_effect: Callable[..., None],
    record_proactive_followup: Callable[..., Awaitable[Any]],
    record_task_followup: Callable[..., Awaitable[Any]],
    set_codex_task_state: Callable[..., None],
    add_memory_safe: Callable[..., Awaitable[Any]],
    emit_idle_status_when_safe: Callable[..., Awaitable[Any]],
    record_message_pair: Optional[Callable[..., Awaitable[Any]]] = None,
) -> bool:
    if learning:
        learning.record_interaction(
            user_text,
            final_reply,
            final_emo,
            feedback_type,
            feedback_reaction,
        )

    if final_reply:
        update_active_time()
        add_codex_session_event(
            "assistant_reply",
            text=final_reply,
            ctx=ctx,
            meta={"emotion": final_emo, "tool": True},
        )
        assistant_log_meta = {
            "tool": True,
            "emotion": final_emo,
            "source": chat_log_source,
            **transcript_meta,
        }
        if memory_session_id:
            assistant_log_meta["session_id"] = memory_session_id
        await event_bus.emit(
            "chat.log",
            role="assistant",
            content=final_reply,
            meta=assistant_log_meta,
        )
        pace_ui = should_pace_assistant_ui(output_profile)
        if output_profile.get("ui_append", True) and not pace_ui:
            await emit_assistant_ui_parts(event_bus, final_reply)
        if live2d_enabled:
            defer_motion = presenter_output_controls_idle(
                output_profile,
                had_presenter_output=True,
            )
            await event_bus.emit(
                "live2d.emotion",
                emotion=final_emo,
                intensity=(
                    sensor_emotion_intensity(final_emo)
                    if final_emo != start_emo
                    else start_intensity
                ),
                prefer_motion=not defer_motion,
                reason="model_reply",
            )
        await presenter.present(
            final_reply,
            final_emo,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
            append_ui=pace_ui,
        )
        await send_gateway_reply(final_reply, ctx, emotion=final_emo)
        await maybe_send_auto_meme_reply(
            user_text=user_text,
            reply_text=final_reply,
            emotion=final_emo,
            ctx=ctx,
        )
        record_reply_effect(final_reply, ctx, source=chat_log_source)
        await record_proactive_followup(proactive_followup)
        await record_task_followup(task_followup)
        if codex_mode:
            set_codex_task_state(ctx, "finalize", summary=final_reply[:200])

        chat_meta: dict[str, Any] = {
            "path": "chat",
            "source": chat_log_source,
            "tool": bool(triggered),
            **transcript_meta,
        }
        if memory_session_id:
            chat_meta["session_id"] = memory_session_id
        # T1 dual-write: transcript via add_memory_safe; events = near-history authority.
        user_event_id = ""
        assistant_event_id = ""
        if record_message_pair is not None:
            pair_ids = await record_message_pair(
                ctx=ctx,
                user_text=user_text,
                assistant_text=final_reply,
                metadata=chat_meta,
            )
            if pair_ids:
                user_event_id, assistant_event_id = pair_ids
        await add_memory_safe(
            "user", user_text, meta={**chat_meta, "event_id": user_event_id}
        )
        await add_memory_safe(
            "assistant",
            final_reply,
            meta={**chat_meta, "event_id": assistant_event_id},
        )

    await emit_idle_status_when_safe(
        output_profile,
        reason="tool_end",
        had_presenter_output=bool(final_reply),
    )
    return bool(final_reply)


async def emit_stream_reply(
    *,
    full_reply: str,
    emotion: str,
    user_text: str,
    stream_context: StreamOutputContext,
    learning: Any,
    event_bus: Any,
    update_active_time: Callable[[], None],
    add_codex_session_event: Callable[..., None],
    send_gateway_reply: Callable[..., Awaitable[Any]],
    maybe_send_auto_meme_reply: Callable[..., Awaitable[Any]],
    record_reply_effect: Callable[..., None],
    record_proactive_followup: Callable[..., Awaitable[Any]],
    record_task_followup: Callable[..., Awaitable[Any]],
    set_codex_task_state: Callable[..., None],
    add_memory_safe: Callable[..., Awaitable[Any]],
    record_message_pair: Optional[Callable[..., Awaitable[Any]]] = None,
    presenter: Any = None,
) -> bool:
    if not full_reply:
        return False

    update_active_time()
    add_codex_session_event(
        "assistant_reply",
        text=full_reply,
        ctx=stream_context.ctx,
        meta={"emotion": emotion, "stream": True},
    )
    output_profile = stream_context.output_profile
    pace_ui = should_pace_assistant_ui(output_profile)
    tts_will_pace = (
        pace_ui
        and bool(output_profile.get("speak", True))
        and bool(getattr(presenter, "tts_enabled", False))
    )
    if output_profile.get("ui_append", True) and not tts_will_pace:
        if pace_ui and presenter is not None:
            await presenter.present(
                full_reply,
                emotion,
                speak=False,
                show_bubble=output_profile.get("show_bubble", True),
                append_ui=True,
            )
        else:
            await emit_assistant_ui_parts(event_bus, full_reply)

    stream_log_meta = {
        "stream": True,
        "emotion": emotion,
        "source": stream_context.chat_log_source,
        **stream_context.transcript_meta,
    }
    if stream_context.memory_session_id:
        stream_log_meta["session_id"] = stream_context.memory_session_id
    await event_bus.emit(
        "chat.log",
        role="assistant",
        content=full_reply,
        meta=stream_log_meta,
    )
    await send_gateway_reply(full_reply, stream_context.ctx, emotion=emotion)
    await maybe_send_auto_meme_reply(
        user_text=user_text,
        reply_text=full_reply,
        emotion=emotion,
        ctx=stream_context.ctx,
    )
    record_reply_effect(full_reply, stream_context.ctx, source=stream_context.chat_log_source)
    await record_proactive_followup(stream_context.proactive_followup)
    await record_task_followup(stream_context.task_followup)

    if learning:
        learning.record_interaction(
            user_text,
            full_reply,
            emotion,
            stream_context.feedback_type,
            stream_context.feedback_reaction,
        )

    stream_chat_meta: dict[str, Any] = {
        "path": "chat",
        "source": stream_context.chat_log_source,
        **stream_context.transcript_meta,
    }
    if stream_context.memory_session_id:
        stream_chat_meta["session_id"] = stream_context.memory_session_id
    # T1 dual-write: transcript via add_memory_safe; events = near-history authority.
    user_event_id = ""
    assistant_event_id = ""
    if record_message_pair is not None:
        pair_ids = await record_message_pair(
            ctx=stream_context.ctx,
            user_text=user_text,
            assistant_text=full_reply,
            metadata=stream_chat_meta,
        )
        if pair_ids:
            user_event_id, assistant_event_id = pair_ids
    await add_memory_safe(
        "user", user_text, meta={**stream_chat_meta, "event_id": user_event_id}
    )
    await add_memory_safe(
        "assistant",
        full_reply,
        meta={**stream_chat_meta, "event_id": assistant_event_id},
    )
    if stream_context.codex_mode:
        set_codex_task_state(stream_context.ctx, "finalize", summary=full_reply[:200])

    return True
