"""Unit tests for Character Thought parse / clamp / format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.chat_support.character_thought import (
    CharacterThought,
    clamp_emotion_level,
    format_thought_for_prompt,
    generate_character_thought,
    has_distress_markers,
    looks_like_schedule_contrast,
    parse_thought_payload,
)

FIXTURES = (
    Path(__file__).resolve().parent / "fixtures" / "character_natural_chat_cases.json"
)


def test_parse_valid_json():
    thought = parse_thought_payload(
        {
            "situation": "对方在对比两天安排",
            "stance": "先当日常说说",
            "emotion_level": "light",
            "want": "light_ack",
            "avoid": ["abstract_label"],
            "angle": "daily_ack",
        }
    )
    assert thought.emotion_level == "light"
    assert thought.want == "light_ack"
    assert "abstract_label" in thought.avoid
    assert "paraphrase_summary" in thought.avoid


def test_parse_missing_fields_defaults():
    thought = parse_thought_payload("{}")
    assert thought.emotion_level == "light"
    assert thought.want == "light_ack"
    assert thought.angle == "daily_ack"
    assert "abstract_label" in thought.avoid


def test_parse_invalid_enum_fallback():
    thought = parse_thought_payload(
        {"emotion_level": "extreme", "want": "hug", "angle": "xyz"}
    )
    assert thought.emotion_level == "light"
    assert thought.want == "light_ack"
    assert thought.angle == "daily_ack"


def test_parse_fenced_json():
    raw = '```json\n{"emotion_level":"medium","want":"soft_care","angle":"care"}\n```'
    thought = parse_thought_payload(raw)
    assert thought.emotion_level == "medium"
    assert thought.want == "soft_care"


def test_schedule_contrast_clamp_to_light():
    text = "昨天台风居家办公今天一来就开会了"
    assert looks_like_schedule_contrast(text)
    assert not has_distress_markers(text)
    thought = CharacterThought(
        emotion_level="heavy", want="soft_care", angle="care"
    )
    clamped = clamp_emotion_level(text, thought)
    assert clamped.emotion_level == "light"
    assert clamped.want == "light_ack"
    assert clamped.angle == "daily_ack"


def test_distress_not_clamped_to_light():
    text = "好累不想动"
    assert has_distress_markers(text)
    thought = CharacterThought(
        emotion_level="light", want="light_ack", angle="daily_ack"
    )
    clamped = clamp_emotion_level(text, thought)
    assert clamped.emotion_level == "medium"
    assert clamped.want == "soft_care"


def test_distress_with_schedule_still_allows_medium():
    text = "今天开会但我快崩了"
    thought = CharacterThought(
        emotion_level="light", want="light_ack", angle="daily_ack"
    )
    clamped = clamp_emotion_level(text, thought)
    assert clamped.emotion_level != "light"
    assert clamped.emotion_level in {"medium", "heavy"}


def test_format_contains_emotion_and_want():
    block = format_thought_for_prompt(
        CharacterThought(
            situation="闲聊",
            stance="接一下",
            emotion_level="light",
            want="light_ack",
            avoid=["abstract_label"],
        )
    )
    assert "light" in block
    assert "light_ack" in block
    assert "本轮内心" in block
    # 压缩注入，不应再是长说明书
    assert block.count("\n") <= 3


def test_format_switch_character_note():
    block = format_thought_for_prompt(
        CharacterThought(), just_switched_character=True
    )
    assert "换角" in block


def test_generate_failure_returns_none():
    def boom(*_a, **_k):
        raise RuntimeError("llm down")

    thought, reason, latency = generate_character_thought(
        chat_fn=boom,
        character_name="高松灯",
        character_prompt_excerpt="安静",
        recent=[],
        user_text="今天开会",
        timeout_ms=500,
    )
    assert thought is None
    assert reason == "error"
    assert latency >= 0


def test_generate_timeout_returns_none():
    seen = {}

    def boom(*_a, **kwargs):
        seen.update(kwargs)
        raise TimeoutError("read timed out")

    thought, reason, _latency = generate_character_thought(
        chat_fn=boom,
        character_name="高松灯",
        character_prompt_excerpt="",
        recent=[],
        user_text="hi",
        timeout_ms=100,
        max_tokens=80,
    )
    assert thought is None
    assert reason == "timeout"
    assert seen["timeout_sec"] == 0.2
    assert seen["max_tokens"] == 80
    assert seen["caller"] == "character_thought"


def test_generate_block_reply_reraises_timeout():
    def boom(*_a, **_k):
        raise TimeoutError("read timed out")

    try:
        generate_character_thought(
            chat_fn=boom,
            character_name="高松灯",
            character_prompt_excerpt="",
            recent=[],
            user_text="hi",
            timeout_ms=100,
            on_error="block_reply",
        )
        assert False, "expected TimeoutError"
    except TimeoutError as exc:
        assert "character_thought_timeout" in str(exc)


def test_generate_treats_router_failure_as_timeout():
    thought, reason, _latency = generate_character_thought(
        chat_fn=lambda *_a, **_k: "❌ 系统繁忙，无法连接 AI。",
        character_name="高松灯",
        character_prompt_excerpt="",
        recent=[],
        user_text="hi",
        timeout_ms=500,
    )
    assert thought is None
    assert reason == "timeout"


def test_generate_success_parses_and_clamps():
    def fake(*_a, **_k):
        return json.dumps(
            {
                "situation": "日程对比",
                "stance": "当日常",
                "emotion_level": "heavy",
                "want": "soft_care",
                "avoid": ["abstract_label"],
                "angle": "care",
            },
            ensure_ascii=False,
        )

    text = "昨天台风居家办公今天一来就开会了"
    thought, reason, _ = generate_character_thought(
        chat_fn=fake,
        character_name="高松灯",
        character_prompt_excerpt="安静内向",
        recent=[{"role": "user", "content": "在吗"}],
        user_text=text,
        timeout_ms=2000,
    )
    assert reason == ""
    assert thought is not None
    assert thought.emotion_level == "light"


def test_fixture_distress_and_schedule_markers():
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for case in cases:
        text = case.get("user_text") or ""
        if case.get("expect_distress") is True:
            assert has_distress_markers(text), case["id"]
        if case.get("expected_emotion_max") == "light" and not case.get(
            "expect_distress"
        ):
            # 不强求全部是日程对比，但 daily_contrast 必须是
            if case.get("category") == "daily_contrast":
                assert looks_like_schedule_contrast(text), case["id"]
