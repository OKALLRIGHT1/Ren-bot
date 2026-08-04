import pytest

from services.chat_support.sensor_event_guard import titles_soft_match
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


def _assert_conversation_ends_with_user(messages):
    assert messages
    assert messages[-1].get("role") == "user"
    assert str(messages[-1].get("content") or "").strip()


@pytest.mark.asyncio
async def test_self_generation_ends_request_with_user_message():
    captured = {}

    def fake_chat_with_ai(messages, *args, **kwargs):
        captured["messages"] = messages
        return "在看我吗？"

    result = await _service().run_self_generation(
        context=_context(),
        clean_title="Live2D-Suzu",
        count=1,
        chat_with_ai=fake_chat_with_ai,
    )

    assert result.reason == "generated"
    _assert_conversation_ends_with_user(captured["messages"])


@pytest.mark.asyncio
async def test_text_generation_ends_request_with_user_message():
    captured = {}

    def fake_chat_with_ai(messages, *args, **kwargs):
        captured["messages"] = messages
        return "还在忙吗？"

    result = await _service().run_text_generation(
        context=_context(),
        clean_title="Docs",
        category="work",
        count=3,
        chat_with_ai=fake_chat_with_ai,
    )

    assert result.reason == "generated"
    _assert_conversation_ends_with_user(captured["messages"])


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
    _assert_conversation_ends_with_user(captured["messages"])
    user_contents = [
        message.get("content", "")
        for message in captured["messages"]
        if message.get("role") == "user"
    ]
    assert "现在只输出给我的那一句话。" not in user_contents


def test_titles_soft_match_allows_title_jitter_and_rejects_unrelated():
    assert titles_soft_match(
        "main.py - Visual Studio Code",
        "main.py - Visual Studio Code",
    )
    assert titles_soft_match(
        "main.py - Visual Studio Code",
        "main.py - Visual Studio Code - Insiders",
    )
    assert titles_soft_match(
        "Code.exe",
        "main.py - Visual Studio Code",
        app_name="Code.exe",
    ) or titles_soft_match(
        "Visual Studio Code",
        "main.py - Visual Studio Code",
        app_name="Code",
    )
    assert not titles_soft_match(
        "main.py - Visual Studio Code",
        "Docs - Google Chrome",
        app_name="Code.exe",
    )


@pytest.mark.asyncio
async def test_text_event_generation_never_probes_focus_or_vision():
    calls = {"focus": 0, "capture": 0, "vision": 0}

    def active_title_getter():
        calls["focus"] += 1
        raise AssertionError("local foreground lookup must stay disabled")

    def take_screenshot_base64(**kwargs):
        calls["capture"] += 1
        raise AssertionError("screen capture must stay disabled")

    async def analyze_image(*args, **kwargs):
        calls["vision"] += 1
        raise AssertionError("vision model must stay disabled")

    def chat_with_ai(messages, *, task_type, caller):
        if caller == "sensor_gatekeeper":
            return "YES"
        return "只根据 Rust 事件生成的文本回复"

    result = await _service().run_event_generation(
        clean_title="main.py - Code",
        display_app="Code.exe",
        category="coding",
        count=3,
        reason="switch",
        use_vision=False,
        vision_mode="separate",
        app_duration_sec=10,
        current_stay_sec=4,
        chat_with_ai=chat_with_ai,
        analyze_image=analyze_image,
        active_title_getter=active_title_getter,
        take_screenshot_base64=take_screenshot_base64,
    )

    assert result.branch == "text"
    assert result.reply == "只根据 Rust 事件生成的文本回复"
    assert calls == {"focus": 0, "capture": 0, "vision": 0}


@pytest.mark.asyncio
async def test_run_vision_generation_uses_active_monitor_and_skips_after_focus_change(
    monkeypatch,
):
    service = _service()
    capture_calls = []

    def fake_capture(max_size=1024, target="primary", monitor_index=1):
        capture_calls.append(
            {"max_size": max_size, "target": target, "monitor_index": monitor_index}
        )
        return "image-bytes"

    titles = iter(
        [
            "main.py - Visual Studio Code",  # before capture
            "Docs - Google Chrome",  # after capture
        ]
    )

    monkeypatch.setattr(
        "config.SENSOR_VISION_CAPTURE_TARGET",
        "active_monitor",
        raising=False,
    )

    result = await service.run_vision_generation(
        context=_context(),
        clean_title="main.py - Visual Studio Code",
        vision_mode="separate",
        analyze_image=lambda *args, **kwargs: "should-not-run",
        chat_with_ai=lambda *args, **kwargs: "should-not-run",
        display_app="Code.exe",
        take_screenshot_base64=fake_capture,
        active_title_getter=lambda: next(titles),
    )

    assert capture_calls
    assert capture_calls[0]["target"] == "active_monitor"
    assert result.reason == "focus_mismatch"
    assert result.branch == "guard"


@pytest.mark.asyncio
async def test_run_vision_generation_proceeds_when_focus_matches(monkeypatch):
    service = _service()
    capture_calls = []

    def fake_capture(max_size=1024, target="primary", monitor_index=1):
        capture_calls.append(target)
        return "image-bytes"

    async def fake_analyze_image(image_base64, prompt, caller=None):
        return "The user is coding."

    def fake_chat_with_ai(messages, *args, **kwargs):
        return "<emo=neutral>还在抠这里？"

    monkeypatch.setattr(
        "config.SENSOR_VISION_CAPTURE_TARGET",
        "active_monitor",
        raising=False,
    )

    result = await service.run_vision_generation(
        context=_context(),
        clean_title="main.py - Visual Studio Code",
        vision_mode="separate",
        analyze_image=fake_analyze_image,
        chat_with_ai=fake_chat_with_ai,
        display_app="Code.exe",
        take_screenshot_base64=fake_capture,
        active_title_getter=lambda: "main.py - Visual Studio Code",
    )

    assert capture_calls == ["active_monitor"]
    assert result.reason == "generated"
    assert "抠" in result.reply
