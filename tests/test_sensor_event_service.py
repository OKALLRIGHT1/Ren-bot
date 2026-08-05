import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.conversation_events.models import ConversationEventType
from modules.conversation_events.store import ConversationEventStore
from modules.memory_sqlite import MemorySQLite
from services.chat_support.conversation_event_service import ConversationEventService
from services.chat_support.gateway_context_service import GatewayContextService
from services.chat_support.sensor_event_guard import titles_soft_match
from services.chat_support.sensor_event_service import (
    SensorEventService,
    SensorGenerationContext,
)
from services.chat_support.sensor_reply_service import SensorReplyService


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


def _context_with_observation_id(event_id: str) -> SensorGenerationContext:
    return SensorGenerationContext(
        context_block="",
        sensor_persona_prompt="persona",
        recent_sensor_reply_block="",
        text_style_block="",
        vision_style_block="",
        record_observation=lambda content, source: event_id,
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
async def test_text_generation_returns_current_observation_event_id():
    result = await _service().run_text_generation(
        context=_context_with_observation_id("obs-current"),
        clean_title="Docs",
        category="work",
        count=3,
        chat_with_ai=lambda *args, **kwargs: "还在忙吗？",
    )

    assert result.observation_event_id == "obs-current"


@pytest.mark.asyncio
async def test_self_generation_never_inherits_previous_observation_event_id():
    # Self-window replies are not observation-linked; no shared last-id fallback.
    result = await _service().run_self_generation(
        context=_context(),
        clean_title="Live2D-Suzu",
        count=1,
        chat_with_ai=lambda *args, **kwargs: "在看我吗？",
    )

    assert result.observation_event_id == ""


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


async def _async_identity_polish(**kwargs):
    return kwargs.get("draft_text") or ""


async def _async_none(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_sensor_reply_links_to_observation_in_desktop_scope(tmp_path):
    sqlite = MemorySQLite(str(tmp_path / "sensor_events.sqlite"))
    store = ConversationEventStore(sqlite)
    gateway = GatewayContextService(
        qq_remote_sources={"qq_gateway", "napcat_qq"},
        owner_shared_session_id="owner_shared",
        owner_shared_local_sources={"desktop", "text_input"},
    )
    event_service = ConversationEventService(
        store=store,
        gateway_context_service=gateway,
        enabled=True,
    )
    sensor_events = SensorEventService(
        screen_sensor_ref_getter=lambda: None,
        format_sensor_observations=lambda *args, **kwargs: "",
        build_sensor_usage_context=lambda *args, **kwargs: "",
        build_sensor_interaction_context=lambda: "",
        build_sensor_persona_prompt=lambda *args, **kwargs: "persona",
        format_recent_sensor_reply_block=lambda: "",
        build_sensor_spontaneous_style_block=lambda *args, **kwargs: "",
        build_live2d_self_awareness_hint=lambda *args, **kwargs: "",
        compress_sensor_text=lambda text, max_len=None: str(text)[: max_len or 80],
        conversation_event_service=event_service,
    )
    observation_id = sensor_events.record_observation(
        content="DeepSeek 页面中出现了原神",
        source="vision",
        clean_title="DeepSeek",
        category="browser",
        display_app="Chrome",
        reason="switch",
        ctx={"source": "desktop"},
    )
    assert observation_id

    remembered = []

    class _Bus:
        async def emit(self, *args, **kwargs):
            return None

    class _Presenter:
        async def present(self, *args, **kwargs):
            return None

    class _Logger:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    reply_service = SensorReplyService(
        event_bus=_Bus(),
        presenter=_Presenter(),
        logger=_Logger(),
        extract_emo_tag=lambda text: (None, text),
        strip_wrapping_quotes=lambda text: text,
        polish_natural_reply=_async_identity_polish,
        apply_character_catchphrase=lambda text: text,
        prepare_reply_for_output=lambda text, *args, **kwargs: text,
        looks_like_sensor_template_reply=lambda text: False,
        rescue_sensor_template_reply=lambda *args, **kwargs: "",
        remember_sensor_reply=lambda text: remembered.append(text),
        update_active_time=lambda: None,
        infer_reply_emotion_with_llm=_async_none,
        get_current_live2d_emotion=lambda: ("neutral", 0.4),
        reset_sensor_motion_after=_async_none,
        add_memory_safe=_async_none,
        last_reply_time_getter=lambda: 0.0,
        conversation_event_service=event_service,
    )

    ok = await reply_service.send_sensor_reply(
        "原神又肝起来了？",
        "vision",
        1,
        "DeepSeek",
        True,
        observation_event_id=observation_id,
        ctx={"source": "desktop"},
    )
    assert ok
    desktop_scope = event_service.resolve_scope(
        {"source": "desktop"}, persona_id="suzu", person_id="owner"
    )
    recent = store.list_recent(
        desktop_scope, now=datetime.now(timezone.utc), limit=5
    )
    assert any(
        e.event_type is ConversationEventType.PROACTIVE_UTTERANCE
        and observation_id in e.causal_parent_ids
        for e in recent
    )
    # Isolation: QQ scopes must not see desktop sensor events.
    for cid in ("private:42", "group:7"):
        qq_scope = event_service.resolve_scope(
            {
                "source": "qq_gateway",
                "channel_meta": {"session_id": cid, "user_id": "1"},
            },
            persona_id="suzu",
            person_id="owner",
        )
        assert store.list_recent(qq_scope, now=datetime.now(timezone.utc), limit=5) == []


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
async def test_text_event_generation_skips_vision_when_disabled():
    parameters = inspect.signature(
        SensorEventService.run_event_generation
    ).parameters
    assert "use_vision" in parameters
    assert "analyze_image" in parameters

    def chat_with_ai(messages, *, task_type=None, caller=None, **kwargs):
        if caller == "sensor_gatekeeper":
            return "YES"
        return "只根据 Rust 事件生成的文本回复"

    analyze_calls = []

    async def analyze_image(*args, **kwargs):
        analyze_calls.append((args, kwargs))
        return "should-not-run"

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
        active_title_getter=lambda: "main.py - Code",
    )

    assert result.branch == "text"
    assert result.reply == "只根据 Rust 事件生成的文本回复"
    assert analyze_calls == []


@pytest.mark.asyncio
async def test_event_generation_uses_vision_when_enabled():
    def chat_with_ai(messages, *args, **kwargs):
        return "<emo=neutral>还在抠这里？"

    async def analyze_image(image_base64, prompt, caller=None):
        return "The user is coding."

    result = await _service().run_event_generation(
        clean_title="main.py - Visual Studio Code",
        display_app="Code.exe",
        category="coding",
        count=3,
        reason="duration",
        use_vision=True,
        vision_mode="separate",
        app_duration_sec=120,
        current_stay_sec=90,
        chat_with_ai=chat_with_ai,
        analyze_image=analyze_image,
        take_screenshot_base64=lambda max_size=1024, target="primary", monitor_index=1: "image-bytes",
        active_title_getter=lambda: "main.py - Visual Studio Code",
    )

    assert result.branch == "vision_separate"
    assert result.reason == "generated"
    assert "抠" in result.reply


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
