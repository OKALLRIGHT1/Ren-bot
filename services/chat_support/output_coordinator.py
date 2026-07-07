"""Output side-effect coordinators for ChatService.process()."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional


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

    short_meta: dict[str, Any] = {"path": "short_reaction"}
    if memory_session_id:
        short_meta["session_id"] = memory_session_id
    await add_memory_safe("user", user_text, meta=short_meta)
    await add_memory_safe("assistant", text, meta=short_meta)
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
        if output_profile.get("ui_append", True):
            await event_bus.emit("ui.append", role="assistant", text=final_reply)
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

        chat_meta: dict[str, Any] = {"path": "chat"}
        if memory_session_id:
            chat_meta["session_id"] = memory_session_id
        await add_memory_safe("user", user_text, meta=chat_meta)
        await add_memory_safe("assistant", final_reply, meta=chat_meta)

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
    if stream_context.output_profile.get("ui_append", True):
        await event_bus.emit("ui.append", role="assistant", text=full_reply)

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

    stream_chat_meta: dict[str, Any] = {"path": "chat"}
    if stream_context.memory_session_id:
        stream_chat_meta["session_id"] = stream_context.memory_session_id
    await add_memory_safe("user", user_text, meta=stream_chat_meta)
    await add_memory_safe("assistant", full_reply, meta=stream_chat_meta)
    if stream_context.codex_mode:
        set_codex_task_state(stream_context.ctx, "finalize", summary=full_reply[:200])

    return True
