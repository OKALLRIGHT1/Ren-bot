from __future__ import annotations

from unittest.mock import Mock

from core.logger import AppLogger
from modules.memory.knowledge_gate import (
    knowledge_retrieval_decision,
    should_retrieve_knowledge,
)


def test_source_marker_questions_retrieve():
    cases = (
        "设定里皮卡丘的特性是什么",
        "资料里写了房间怎么布置",
        "知识库有没有这个角色",
        "文档里怎么解释这个技能",
        "词条：暴击是什么意思",
    )
    for text in cases:
        allowed, reason = knowledge_retrieval_decision(text)
        assert allowed is True, text
        assert reason == "source_marker"


def test_chitchat_and_bare_entity_questions_do_not_retrieve():
    cases = (
        "嗯",
        "好累",
        "你觉得幸福是什么",
        "皮卡丘有什么特性",
        "为什么会这样",
        "这是怎么回事",
        "今天天气不错",
    )
    for text in cases:
        allowed, reason = knowledge_retrieval_decision(text)
        assert allowed is False, text
        assert reason == "no_source_marker"


def test_gate_skips_tools_commands_and_memory_intents():
    assert should_retrieve_knowledge("设定里有什么", tool_mode=True) is False
    assert should_retrieve_knowledge("/help 设定", tool_mode=False) is False
    assert (
        should_retrieve_knowledge("设定里上次开会", memory_intent="episode") is False
    )
    assert (
        should_retrieve_knowledge("资料里我喜欢什么", memory_intent="profile")
        is False
    )
    assert should_retrieve_knowledge("设定里有什么", enabled=False) is False
    assert knowledge_retrieval_decision("设定里有什么", tool_mode=True)[1] == "tool_mode"
    assert knowledge_retrieval_decision("/help 设定")[1] == "command"
    assert (
        knowledge_retrieval_decision("设定", memory_intent="episode")[1]
        == "memory_intent:episode"
    )
    assert knowledge_retrieval_decision("设定", enabled=False)[1] == "disabled"


def test_build_prompt_uses_source_gate_not_length(monkeypatch):
    import modules.advanced_memory as advanced_memory
    from modules.memory_core.models import ReplyMemoryContext

    calls = []

    class FakeCore:
        profile_max_items = 6

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="none")

        def get_person_profile(self, *args, **kwargs):
            return ReplyMemoryContext(intent="none")

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = []
    brain.session_short_term_memory = {}
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain.knowledge_auto_retrieval_enabled = True
    brain.context_assembler = None
    brain._logger = AppLogger()
    brain._logger.logger = Mock()

    def capture(text, k=2):
        calls.append((text, k))
        return ["假知识"]

    brain._retrieve_knowledge = capture
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        type("CM", (), {"data": {}, "get_active_character": staticmethod(lambda: None)})(),
    )

    skipped = brain.build_prompt("皮卡丘有什么特性", "系统设定")
    assert calls == []
    assert "假知识" not in skipped[0]["content"]
    assert brain._last_knowledge_skip_reason == "no_source_marker"

    retrieved = brain.build_prompt("设定里皮卡丘的特性是什么", "系统设定")
    assert calls == [("设定里皮卡丘的特性是什么", 2)]
    assert "假知识" in retrieved[0]["content"]
    assert brain._last_knowledge_skip_reason is None


def test_disabled_auto_retrieval_does_not_search(monkeypatch):
    import modules.advanced_memory as advanced_memory
    from modules.memory_core.models import ReplyMemoryContext

    class FakeCore:
        profile_max_items = 6

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="none")

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = []
    brain.session_short_term_memory = {}
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain.knowledge_auto_retrieval_enabled = False
    brain.context_assembler = None
    brain._logger = None
    brain._retrieve_knowledge = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("auto retrieval should stay off")
    )
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        type("CM", (), {"data": {}, "get_active_character": staticmethod(lambda: None)})(),
    )

    messages = brain.build_prompt("设定里皮卡丘的特性是什么", "系统设定")
    assert "相关知识库" not in messages[0]["content"]
    assert brain._last_knowledge_skip_reason == "disabled"


def test_config_reads_knowledge_auto_retrieval_env(monkeypatch):
    import importlib
    import os

    import config as config_module

    original = os.getenv("KNOWLEDGE_AUTO_RETRIEVAL_ENABLED")
    try:
        monkeypatch.setenv("KNOWLEDGE_AUTO_RETRIEVAL_ENABLED", "0")
        importlib.reload(config_module)
        assert config_module.MEMORY_SETTINGS["knowledge_auto_retrieval_enabled"] is False
        monkeypatch.setenv("KNOWLEDGE_AUTO_RETRIEVAL_ENABLED", "1")
        importlib.reload(config_module)
        assert config_module.MEMORY_SETTINGS["knowledge_auto_retrieval_enabled"] is True
    finally:
        if original is None:
            monkeypatch.delenv("KNOWLEDGE_AUTO_RETRIEVAL_ENABLED", raising=False)
        else:
            monkeypatch.setenv("KNOWLEDGE_AUTO_RETRIEVAL_ENABLED", original)
        importlib.reload(config_module)
