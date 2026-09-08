"""Residual streaming reply turn. Desktop/QQ plain chat stays non-stream."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Pattern, Sequence

from services.chat_support import output_coordinator, reply_flow_service


@dataclass
class StreamTurnHooks:
    event_bus: Any
    add_codex_session_event: Callable[..., None]
    reply_start_emotion: Callable[..., tuple[str, float]]
    strip_complete_cmd: Callable[[str], str]
    emo_tag_re: Pattern[str]
    normalize_emo: Callable[[str], str]
    clean_text_for_tts: Callable[[str], str]
    strip_internal_tags: Callable[[str], str]
    strip_cmd_anywhere: Callable[[str], str]
    strip_emo_tags_anywhere: Callable[[str], str]
    strip_model_catchphrase: Callable[[str], str]
    apply_forbidden_guard: Callable[..., Awaitable[tuple[str, Dict[str, Any]]]]
    try_share: Callable[[], str]
    apply_character_catchphrase: Callable[[str], str]
    prepare_reply_for_output: Callable[..., str]
    presenter_output_controls_idle: Callable[..., bool]
    sensor_emotion_intensity: Callable[..., float]
    trace_process: Optional[Callable[..., None]] = None
    logger: Any = None


@dataclass
class StreamTurnResult:
    reply: str = ""
    emotion: str = "neutral"
    start_emo: str = "neutral"
    start_intensity: float = 0.4


async def run_stream_turn(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    context_messages: list,
    chat_with_ai_stream: Callable[..., Any],
    task_default: str,
    preface_text: str,
    output_profile: Dict[str, Any],
    live2d_enabled: bool,
    character_sharing_enabled: bool,
    effective_triggers: Sequence[str],
    hooks: StreamTurnHooks,
) -> StreamTurnResult:
    """Own stream.start → feed → guard → stream.end. Caller still emits memory."""
    hooks.add_codex_session_event(
        "assistant_reasoning",
        text="进入流式回复，正在生成中。",
        ctx=ctx,
        meta={"stream": True, "triggers": list(effective_triggers or [])[:8]},
    )
    await hooks.event_bus.emit("ui.status", text="Streaming...")
    await hooks.event_bus.emit(
        "assistant.stream.start",
        interrupt=True,
        speak=output_profile.get("speak", True),
        show_bubble=output_profile.get("show_bubble", True),
        append_ui=output_coordinator.should_pace_assistant_ui(output_profile),
    )

    buffer = ""
    start_emo, start_intensity = hooks.reply_start_emotion(ctx)
    curr_emo = start_emo
    model_emo_seen = False
    first = False
    full_reply = ""

    if preface_text:
        first = True
        proactive_chunk = reply_flow_service.build_stream_preface_chunk(preface_text)
        full_reply += proactive_chunk
        await hooks.event_bus.emit(
            "assistant.stream.feed",
            chunk=proactive_chunk,
            emotion=curr_emo,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
        )

    try:
        async for chunk in chat_with_ai_stream(
            context_messages,
            task_type=task_default,
            caller="chat_stream_reply",
        ):
            if not chunk:
                continue
            if not first:
                first = True

            buffer += chunk

            if "[CMD:" in buffer and "]" in buffer:
                buffer = hooks.strip_complete_cmd(buffer)

            emotion_tag = reply_flow_service.consume_stream_emotion_tag(
                buffer,
                emo_tag_re=hooks.emo_tag_re,
                normalize_emo=hooks.normalize_emo,
            )
            buffer = emotion_tag.buffer
            if emotion_tag.found:
                curr_emo = emotion_tag.emotion
                model_emo_seen = True
                print(f"🎭 [Stream] 检测到模型情绪标签: {curr_emo}")
                if live2d_enabled:
                    defer_motion = hooks.presenter_output_controls_idle(
                        output_profile,
                        had_presenter_output=True,
                    )
                    asyncio.create_task(
                        hooks.event_bus.emit(
                            "live2d.emotion",
                            emotion=curr_emo,
                            intensity=hooks.sensor_emotion_intensity(curr_emo),
                            prefer_motion=not defer_motion,
                            reason="model_stream_reply",
                        )
                    )

            flushed = reply_flow_service.flush_stream_buffer(
                buffer,
                clean_text_for_tts=hooks.clean_text_for_tts,
                strip_internal_tags=hooks.strip_internal_tags,
                strip_cmd_anywhere=hooks.strip_cmd_anywhere,
                strip_emo_tags_anywhere=hooks.strip_emo_tags_anywhere,
                strip_model_catchphrase=hooks.strip_model_catchphrase,
            )
            if flushed.chunk:
                full_reply += flushed.chunk
                await hooks.event_bus.emit(
                    "assistant.stream.feed",
                    chunk=flushed.chunk,
                    emotion=curr_emo,
                    speak=output_profile.get("speak", True),
                    show_bubble=output_profile.get("show_bubble", True),
                )
            buffer = flushed.buffer

        flushed = reply_flow_service.flush_stream_buffer(
            buffer,
            final=True,
            clean_text_for_tts=hooks.clean_text_for_tts,
            strip_internal_tags=hooks.strip_internal_tags,
            strip_cmd_anywhere=hooks.strip_cmd_anywhere,
            strip_emo_tags_anywhere=hooks.strip_emo_tags_anywhere,
            strip_model_catchphrase=hooks.strip_model_catchphrase,
        )
        if flushed.chunk:
            full_reply += flushed.chunk
            await hooks.event_bus.emit(
                "assistant.stream.feed",
                chunk=flushed.chunk,
                emotion=curr_emo,
                speak=output_profile.get("speak", True),
                show_bubble=output_profile.get("show_bubble", True),
            )
        buffer = flushed.buffer

    except Exception as exc:
        if hooks.logger:
            hooks.logger.error(f"Stream error: {exc}")

    full_reply, stream_guard = await hooks.apply_forbidden_guard(
        text=full_reply,
        user_text=user_text,
        ctx=ctx,
    )
    stream_finalized = reply_flow_service.finalize_stream_reply(
        full_reply=full_reply,
        ctx=ctx,
        character_sharing_enabled=character_sharing_enabled,
        try_share=hooks.try_share,
        apply_character_catchphrase=hooks.apply_character_catchphrase,
        prepare_reply_for_output=hooks.prepare_reply_for_output,
    )
    full_reply = stream_finalized.text
    if hooks.trace_process:
        try:
            if stream_guard.get("forbidden_phrase_hit") or stream_guard.get(
                "forbidden_retries"
            ):
                hooks.trace_process("forbidden_phrase_guard_stream", **stream_guard)
        except Exception:
            pass
    for feed_chunk in stream_finalized.feed_chunks:
        await hooks.event_bus.emit(
            "assistant.stream.feed",
            chunk=feed_chunk,
            emotion=curr_emo,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
        )

    try:
        if live2d_enabled and not model_emo_seen:
            defer_motion = hooks.presenter_output_controls_idle(
                output_profile,
                had_presenter_output=bool(full_reply),
            )
            await hooks.event_bus.emit(
                "live2d.emotion",
                emotion=curr_emo,
                intensity=start_intensity,
                prefer_motion=not defer_motion,
                reason="model_stream_keep_previous",
            )
        await hooks.event_bus.emit(
            "assistant.stream.end",
            emotion=curr_emo,
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
        )
    except Exception as exc:
        if hooks.logger:
            hooks.logger.warning(f"assistant.stream.end failed: {exc}")

    return StreamTurnResult(
        reply=full_reply,
        emotion=curr_emo,
        start_emo=start_emo,
        start_intensity=start_intensity,
    )
