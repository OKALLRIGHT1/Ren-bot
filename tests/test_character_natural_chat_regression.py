"""Light regression: config defaults + ChatService polish gate + angle empty."""

from __future__ import annotations

from config import CHARACTER_NATURAL_CHAT
from services.chat_support.natural_chat_pipeline import NaturalChatConfig


def test_config_defaults_unified_scope():
    cfg = NaturalChatConfig.from_mapping(CHARACTER_NATURAL_CHAT)
    assert cfg.character_thought_enabled is True
    assert cfg.character_thought_scope == "desktop_and_qq_private"
    assert cfg.group_chat_natural_enabled is False
    assert cfg.expression_inject_max_items == 1
    assert not hasattr(cfg, "qq_polish_mode")


def test_should_use_natural_reply_layer_off_for_chat():
    from services.chat_service import ChatService

    svc = object.__new__(ChatService)
    svc._clean_text_for_tts = lambda t: t
    svc._strip_internal_tags = lambda t: t
    svc._strip_cmd_anywhere = lambda t: t
    svc._strip_emo_tags_anywhere = lambda t: t
    svc._strip_model_catchphrase = lambda t: t
    svc._contains_cmd = lambda t: False
    svc._looks_structured_reply = lambda t: False
    svc._wants_detailed_answer = lambda t: False
    svc._needs_natural_polish = lambda t, scene="chat": True

    assert (
        ChatService._should_use_natural_reply_layer(
            svc,
            user_text="今天开会了",
            draft_text="啊今天开会啊。还挺突然的。",
            ctx={"source": "napcat_qq"},
            scene="chat",
        )
        is False
    )
    assert (
        ChatService._should_use_natural_reply_layer(
            svc,
            user_text="今天开会了",
            draft_text="啊今天开会啊。还挺突然的。",
            ctx={"source": "desktop"},
            scene="chat",
        )
        is False
    )


def test_chat_expression_hints_are_lexical_and_capped():
    from services.chat_service import ChatService

    captured = {}

    class Core:
        def select_expressions(self, **kwargs):
            captured.update(kwargs)
            return ["当确认时，可以短回。", "当难过时，可以陪伴。"]

    svc = object.__new__(ChatService)
    svc._load_expression_library_runtime = lambda: {
        "expression_library_enabled": True,
        "expression_library_use_in_chat": True,
        "expression_library_max_prompt_items": 4,
    }
    svc.brain = type("Brain", (), {"memory_core": Core()})()
    svc._get_active_character_profile = lambda: {"name": "高松灯"}
    svc._get_active_character_context = lambda: ("高松灯", "char_1", "")
    svc._get_memory_session_id = lambda ctx: "sess"
    svc._get_memory_person_id = lambda ctx: "owner"

    hints = ChatService._load_expression_library_hints(
        svc, "已经修好了", "chat", {}, recent=[], limit=1
    )
    assert hints
    assert captured["use_llm"] is False
    assert captured["limit"] == 1


def test_sensor_scene_does_not_always_polish():
    from services.chat_service import ChatService

    svc = object.__new__(ChatService)
    svc._clean_text_for_tts = lambda t: t
    svc._strip_internal_tags = lambda t: t
    svc._strip_cmd_anywhere = lambda t: t
    svc._strip_emo_tags_anywhere = lambda t: t
    svc._strip_model_catchphrase = lambda t: t
    svc._contains_cmd = lambda t: False
    svc._looks_structured_reply = lambda t: False
    svc._wants_detailed_answer = lambda t: False

    def needs(text, scene="chat"):
        return "用户正在" in text

    svc._needs_natural_polish = needs
    assert (
        ChatService._should_use_natural_reply_layer(
            svc,
            user_text="Chrome 浏览器",
            draft_text="你又在抠这里？",
            ctx={"source": "desktop"},
            scene="sensor",
        )
        is False
    )
    assert (
        ChatService._should_use_natural_reply_layer(
            svc,
            user_text="Chrome 浏览器",
            draft_text="用户正在浏览当前窗口。",
            ctx={"source": "desktop"},
            scene="sensor",
        )
        is True
    )
