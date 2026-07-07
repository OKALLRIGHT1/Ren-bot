"""Reply preparation helpers for ChatService.process()."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Pattern


@dataclass(frozen=True)
class PreparedReply:
    text: str = ""
    emotion: str = "neutral"
    model_emo_seen: bool = False


@dataclass(frozen=True)
class FinalizedModelReply:
    final_reply: str = ""
    final_emo: str = "neutral"
    model_emo_seen: bool = False


@dataclass(frozen=True)
class ShortReaction:
    text: str = ""
    emotion: str = "neutral"


@dataclass(frozen=True)
class StreamFinalizedReply:
    text: str = ""
    feed_chunks: tuple[str, ...] = ()


@dataclass(frozen=True)
class StreamBufferFlush:
    chunk: str = ""
    buffer: str = ""


@dataclass(frozen=True)
class StreamEmotionTag:
    buffer: str = ""
    emotion: str = ""
    found: bool = False


def is_plain_direct_chat_candidate(
    *,
    need_tools: bool,
    effective_triggers: Iterable[str],
    codex_mode: bool,
    has_external_images: bool,
    preface_text: str,
    source_key: str,
    direct_chat_sources: Iterable[str],
) -> bool:
    return (
        not need_tools
        and not list(effective_triggers or [])
        and not codex_mode
        and not has_external_images
        and not preface_text
        and source_key in set(direct_chat_sources or [])
    )


def build_short_reaction(
    *,
    eligible: bool,
    user_text: str,
    build_reaction: Callable[[str], tuple[str, str]],
) -> ShortReaction:
    if not eligible:
        return ShortReaction()
    text, emotion = build_reaction(user_text)
    return ShortReaction(text=str(text or ""), emotion=str(emotion or "neutral"))


def should_use_non_stream_flow(
    *,
    need_tools: bool,
    deferred_tool_flow: bool,
    stream_available: bool,
    natural_reply_candidate: bool,
) -> bool:
    return (
        bool(need_tools)
        or bool(deferred_tool_flow)
        or not stream_available
        or bool(natural_reply_candidate)
    )


def build_stream_preface_chunk(preface_text: str) -> str:
    if not preface_text:
        return ""
    return f"{preface_text}\n\n"


def consume_stream_emotion_tag(
    buffer: str,
    *,
    emo_tag_re: Pattern[str],
    normalize_emo: Callable[[str], str],
    fallback: str = "neutral",
) -> StreamEmotionTag:
    current = str(buffer or "")
    if "<" not in current or ">" not in current:
        return StreamEmotionTag(buffer=current)
    match = emo_tag_re.search(current)
    if not match:
        return StreamEmotionTag(buffer=current)
    raw = normalize_emo(match.group(1)) or fallback
    cleaned = emo_tag_re.sub("", current, count=1)
    return StreamEmotionTag(buffer=cleaned, emotion=raw, found=True)


def should_flush_stream_buffer(buffer: str, *, min_chars: int = 15) -> bool:
    return len(str(buffer or "")) > min_chars and any(
        p in str(buffer or "") for p in "，。！？,.!?\n"
    )


def clean_stream_buffer(
    buffer: str,
    *,
    clean_text_for_tts: Callable[[str], str],
    strip_internal_tags: Callable[[str], str],
    strip_cmd_anywhere: Callable[[str], str],
    strip_emo_tags_anywhere: Callable[[str], str],
    strip_model_catchphrase: Callable[[str], str],
) -> str:
    safe = clean_text_for_tts(
        strip_internal_tags(strip_cmd_anywhere(strip_emo_tags_anywhere(str(buffer or ""))))
    )
    return strip_model_catchphrase(safe)


def flush_stream_buffer(
    buffer: str,
    *,
    final: bool = False,
    clean_text_for_tts: Callable[[str], str],
    strip_internal_tags: Callable[[str], str],
    strip_cmd_anywhere: Callable[[str], str],
    strip_emo_tags_anywhere: Callable[[str], str],
    strip_model_catchphrase: Callable[[str], str],
) -> StreamBufferFlush:
    current = str(buffer or "")
    if not current:
        return StreamBufferFlush(buffer="")
    if not final and not should_flush_stream_buffer(current):
        return StreamBufferFlush(buffer=current)

    safe = clean_stream_buffer(
        current,
        clean_text_for_tts=clean_text_for_tts,
        strip_internal_tags=strip_internal_tags,
        strip_cmd_anywhere=strip_cmd_anywhere,
        strip_emo_tags_anywhere=strip_emo_tags_anywhere,
        strip_model_catchphrase=strip_model_catchphrase,
    )
    return StreamBufferFlush(chunk=safe, buffer="" if safe else current)


def finalize_stream_reply(
    *,
    full_reply: str,
    ctx: Dict[str, Any],
    character_sharing_enabled: bool,
    try_share: Callable[[], str],
    apply_character_catchphrase: Callable[[str], str],
    prepare_reply_for_output: Callable[..., str],
) -> StreamFinalizedReply:
    text = str(full_reply or "")
    feed_chunks: list[str] = []

    if text and character_sharing_enabled:
        sharing = str(try_share() or "")
        if sharing:
            sharing_chunk = f"\n\n{sharing}"
            text += sharing_chunk
            feed_chunks.append(sharing_chunk)

    if text:
        with_catchphrase = apply_character_catchphrase(text)
        if with_catchphrase != text:
            extra_chunk = ""
            if str(with_catchphrase or "").startswith(text):
                extra_chunk = str(with_catchphrase)[len(text) :]
            text = str(with_catchphrase or "")
            if extra_chunk:
                feed_chunks.append(extra_chunk)
        text = prepare_reply_for_output(text, ctx, scene="chat")

    return StreamFinalizedReply(text=text, feed_chunks=tuple(feed_chunks))


def finalize_model_reply(
    *,
    reply: str,
    start_emo: str,
    extract_emo_tag: Callable[[str], tuple[str, str]],
    character_sharing_enabled: bool,
    try_share: Callable[[], str],
) -> FinalizedModelReply:
    emo, clean = extract_emo_tag(reply or "")
    final_reply = clean.strip() or "…"
    final_emo = emo or start_emo
    model_emo_seen = bool(emo)
    if character_sharing_enabled:
        sharing = try_share()
        if sharing:
            final_reply += f"\n\n{sharing}"
    return FinalizedModelReply(
        final_reply=final_reply,
        final_emo=final_emo,
        model_emo_seen=model_emo_seen,
    )


async def prepare_final_reply(
    *,
    final_reply: str,
    final_emo: str,
    model_emo_seen: bool,
    natural_reply_candidate: bool,
    triggered: bool,
    user_text: str,
    ctx: Dict[str, Any],
    preface_text: str,
    clean_text_for_tts: Callable[[str], str],
    strip_internal_tags: Callable[[str], str],
    strip_cmd_anywhere: Callable[[str], str],
    strip_emo_tags_anywhere: Callable[[str], str],
    should_suppress_followup_preface: Callable[[str], bool],
    merge_preface_texts: Callable[..., str],
    polish_natural_reply: Callable[..., Awaitable[str]],
    apply_character_catchphrase: Callable[[str], str],
    prepare_reply_for_output: Callable[..., str],
    infer_reply_emotion_with_llm: Callable[..., Awaitable[Optional[str]]],
) -> PreparedReply:
    text = clean_text_for_tts(
        strip_internal_tags(strip_cmd_anywhere(strip_emo_tags_anywhere(final_reply)))
    )
    if should_suppress_followup_preface(user_text or ""):
        text = text or preface_text
    else:
        text = merge_preface_texts(preface_text, text)

    if natural_reply_candidate and not triggered:
        text = await polish_natural_reply(
            user_text=user_text,
            draft_text=text,
            ctx=ctx,
            scene="chat",
        )

    text = apply_character_catchphrase(text)
    text = prepare_reply_for_output(text, ctx, scene="chat")

    emotion = final_emo
    seen = bool(model_emo_seen)
    if text and not seen:
        inferred_emo = await infer_reply_emotion_with_llm(text, scene="chat")
        if inferred_emo:
            emotion = inferred_emo

    return PreparedReply(text=text, emotion=emotion, model_emo_seen=seen)
