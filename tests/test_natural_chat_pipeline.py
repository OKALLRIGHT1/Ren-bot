"""Desktop / QQ parity + polish off + group switch."""

from __future__ import annotations

import json
from pathlib import Path

from services.chat_support.natural_chat_pipeline import (
    NaturalChatConfig,
    SCENE_CORE_LINES,
    build_scene_prompt,
    decide_thought_gate,
    format_expression_block,
    scope_matches,
)
from services.chat_support.reply_flow_service import should_use_non_stream_flow

FIXTURES = (
    Path(__file__).resolve().parent / "fixtures" / "character_natural_chat_cases.json"
)


def _default_cfg(**overrides) -> NaturalChatConfig:
    data = {
        "character_thought_enabled": True,
        "character_thought_scope": "desktop_and_qq_private",
        "group_chat_natural_enabled": False,
        "detail_intent_bypass_short_shell": True,
    }
    data.update(overrides)
    return NaturalChatConfig.from_mapping(data)


def test_default_scope_desktop_and_qq_private():
    cfg = _default_cfg()
    assert scope_matches("desktop", "private", cfg)
    assert scope_matches("text_input", "private", cfg)
    assert scope_matches("voice", "private", cfg)
    assert scope_matches("napcat_qq", "private", cfg)
    assert scope_matches("qq_gateway", "private", cfg)
    assert not scope_matches("napcat_qq", "group", cfg)


def test_group_toggle():
    cfg_off = _default_cfg(group_chat_natural_enabled=False)
    cfg_on = _default_cfg(group_chat_natural_enabled=True)
    assert not scope_matches("napcat_qq", "group", cfg_off)
    assert scope_matches("napcat_qq", "group", cfg_on)


def test_desktop_qq_gate_parity_same_user_text():
    cfg = _default_cfg()
    text = "昨天台风居家办公今天一来就开会了"
    d = decide_thought_gate(
        user_text=text,
        source="desktop",
        message_type="private",
        config=cfg,
    )
    q = decide_thought_gate(
        user_text=text,
        source="napcat_qq",
        message_type="private",
        config=cfg,
    )
    assert d.should_run and q.should_run
    assert d.scope_matched and q.scope_matched
    assert d.short_shell == q.short_shell


def test_scene_core_shared_across_channels():
    desktop = build_scene_prompt("desktop", "private")
    qq = build_scene_prompt("napcat_qq", "private")
    for line in SCENE_CORE_LINES:
        assert line in desktop
        assert line in qq


def test_detail_intent_bypasses_short_shell():
    cfg = _default_cfg()
    gate = decide_thought_gate(
        user_text="详细分析一下原因",
        source="desktop",
        message_type="private",
        config=cfg,
        wants_detailed_answer=True,
    )
    assert gate.should_run
    assert gate.detail_intent
    assert gate.short_shell is False
    prompt = build_scene_prompt(
        "napcat_qq",
        "private",
        detail_intent=True,
        bypass_short_shell=True,
    )
    assert "展开" in prompt
    # 场景提示应保持短小，避免参考话过多
    assert prompt.count("\n") <= 6


def test_tools_bypass_thought():
    cfg = _default_cfg()
    gate = decide_thought_gate(
        user_text="查一下天气",
        source="desktop",
        message_type="private",
        config=cfg,
        need_tools=True,
    )
    assert not gate.should_run
    assert gate.reason == "tools_or_codex"


def test_command_bypass():
    cfg = _default_cfg()
    gate = decide_thought_gate(
        user_text="/help",
        source="desktop",
        message_type="private",
        config=cfg,
        is_command=True,
    )
    assert not gate.should_run


def test_expression_inject_default_is_one():
    cfg = NaturalChatConfig.from_mapping({})
    assert cfg.expression_inject_max_items == 1
    block = format_expression_block(
        ["当确认时，可以短回。", "当难过时，可以陪伴。"],
        max_items=cfg.expression_inject_max_items,
    )
    assert block.count("- ") == 1
    assert "陪伴" not in block


def test_guard_requires_non_stream_when_speaking():
    assert should_use_non_stream_flow(
        need_tools=False,
        deferred_tool_flow=False,
        stream_available=True,
        natural_reply_candidate=False,
        guard_requires_non_stream=True,
    )
    assert not should_use_non_stream_flow(
        need_tools=False,
        deferred_tool_flow=False,
        stream_available=True,
        natural_reply_candidate=False,
        guard_requires_non_stream=False,
    )


def test_qq_scene_prompt_contains_channel_shell():
    text = build_scene_prompt("napcat_qq", "private")
    assert "QQ" in text
    for line in SCENE_CORE_LINES:
        assert line in text


def test_fixture_group_and_parity_cases():
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for case in cases:
        if case.get("category") == "group":
            cfg = _default_cfg(
                group_chat_natural_enabled=bool(case.get("group_enabled"))
            )
            matched = scope_matches(
                case.get("source") or "napcat_qq",
                case.get("message_type") or "group",
                cfg,
            )
            assert matched is bool(case.get("expect_scope_matched")), case["id"]
        if case.get("expect_gate_parity"):
            cfg = _default_cfg()
            text = case["user_text"]
            d = decide_thought_gate(
                user_text=text, source="desktop", message_type="private", config=cfg
            )
            q = decide_thought_gate(
                user_text=text, source="napcat_qq", message_type="private", config=cfg
            )
            assert d.should_run == q.should_run, case["id"]
