import re

import pytest

from services.chat_support.stream_turn import StreamTurnHooks, run_stream_turn


class _Bus:
    def __init__(self):
        self.events = []

    async def emit(self, name, **kwargs):
        self.events.append((name, kwargs))


async def _identity_guard(*, text, user_text, ctx):
    del user_text, ctx
    return text, {}


@pytest.mark.asyncio
async def test_run_stream_turn_emits_start_feed_end():
    async def fake_stream(*args, **kwargs):
        del args, kwargs
        yield "流式"
        yield "回复"

    bus = _Bus()
    hooks = StreamTurnHooks(
        event_bus=bus,
        add_codex_session_event=lambda *args, **kwargs: None,
        reply_start_emotion=lambda ctx: ("neutral", 0.4),
        strip_complete_cmd=lambda buffer: buffer,
        emo_tag_re=re.compile(r"<\s*emo\s*=\s*([a-zA-Z_]+)\s*>", re.I),
        normalize_emo=lambda value: str(value or "").lower(),
        clean_text_for_tts=lambda text: text,
        strip_internal_tags=lambda text: text,
        strip_cmd_anywhere=lambda text: text,
        strip_emo_tags_anywhere=lambda text: text,
        strip_model_catchphrase=lambda text: text,
        apply_forbidden_guard=_identity_guard,
        try_share=lambda: "",
        apply_character_catchphrase=lambda text: text,
        prepare_reply_for_output=lambda text, ctx, scene="chat": text,
        presenter_output_controls_idle=lambda profile, had_presenter_output=False: False,
        sensor_emotion_intensity=lambda emotion: 0.4,
    )
    result = await run_stream_turn(
        user_text="请流式回答",
        ctx={"source": "unknown"},
        context_messages=[{"role": "user", "content": "请流式回答"}],
        chat_with_ai_stream=fake_stream,
        task_default="default",
        preface_text="",
        output_profile={"speak": True, "show_bubble": True, "ui_append": True},
        live2d_enabled=False,
        character_sharing_enabled=False,
        effective_triggers=[],
        hooks=hooks,
    )
    names = [name for name, _ in bus.events]
    assert names.count("assistant.stream.start") == 1
    assert names.count("assistant.stream.end") == 1
    feed = "".join(
        kwargs["chunk"] for name, kwargs in bus.events if name == "assistant.stream.feed"
    )
    assert feed == "流式回复"
    assert result.reply == "流式回复"
    assert result.emotion == "neutral"
