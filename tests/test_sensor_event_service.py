import pytest

from services.chat_support.sensor_event_service import (
    SensorEventService,
    SensorGenerationContext,
)


def _service() -> SensorEventService:
    return SensorEventService(
        screen_sensor_ref_getter=lambda: None,
        format_sensor_observations=lambda *args, **kwargs: "",
        build_sensor_usage_context=lambda *args, **kwargs: "",
        build_sensor_interaction_context=lambda: "",
        build_sensor_persona_prompt=lambda *args, **kwargs: "persona",
        format_recent_sensor_reply_block=lambda: "",
        build_sensor_spontaneous_style_block=lambda *args, **kwargs: "",
        build_live2d_self_awareness_hint=lambda *args, **kwargs: "",
        compress_sensor_text=lambda text, max_len=None: str(text)[:max_len],
    )


def _context() -> SensorGenerationContext:
    return SensorGenerationContext(
        context_block="",
        sensor_persona_prompt="persona",
        recent_sensor_reply_block="",
        text_style_block="",
        vision_style_block="",
        record_observation=lambda content, source: None,
    )


@pytest.mark.asyncio
async def test_vision_separate_generation_does_not_send_format_instruction_as_user_message():
    captured = {}

    async def fake_analyze_image(image_base64, prompt, caller=None):
        return "The user is reading a page."

    def fake_chat_with_ai(messages, *args, **kwargs):
        captured["messages"] = messages
        return "<emo=neutral>继续看也可以。"

    result = await _service().run_vision_separate_generation(
        context=_context(),
        image_base64="image",
        analyze_image=fake_analyze_image,
        chat_with_ai=fake_chat_with_ai,
    )

    assert result.reason == "generated"
    user_contents = [
        message.get("content", "")
        for message in captured["messages"]
        if message.get("role") == "user"
    ]
    assert "现在只输出给我的那一句话。" not in user_contents
