from types import SimpleNamespace

from modules.memory_core.models import ReplyMemoryContext
from modules.prompt_parts import PromptParts, join_prompt_sections
from services.chat_support.prompt_assembly import make_chat_prompt_parts


def _brain(monkeypatch):
    import modules.advanced_memory as advanced_memory

    class FakeCore:
        profile_max_items = 6
        enabled = True

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="none")

        def get_person_profile(self, *args, **kwargs):
            return ReplyMemoryContext(intent="none")

        def get_character_profile(self, *args, **kwargs):
            return SimpleNamespace(text="")

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
    brain._retrieve_knowledge = lambda *args, **kwargs: []
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        SimpleNamespace(
            data={},
            get_active_character=lambda: {"prompt": "【人设】核心角色"},
        ),
    )
    return brain


def test_join_prompt_sections_skips_empty():
    assert join_prompt_sections(" a ", "", None, "b") == "a\n\nb"


def test_build_prompt_prefers_prompt_parts_over_string_surgery(monkeypatch):
    brain = _brain(monkeypatch)
    parts = PromptParts(
        time_header="【当前时间】2026-09-07 12:00",
        time_rule="日历规则",
        runtime_context="【运行时】来源=desktop",
        tool_prompt="【可用工具能力】search",
        system_rules="【系统规则】structured",
    )
    messages = brain.build_prompt(
        "你好",
        system_persona="IGNORE【可用工具能力】wrong-tool",
        prompt_parts=parts,
    )
    content = messages[0]["content"]
    assert "【当前时间】2026-09-07 12:00" in content
    assert "日历规则" in content
    assert "【人设】核心角色" in content
    assert "【系统规则】structured" in content
    assert "【运行时】来源=desktop" in content
    assert "【可用工具能力】search" in content
    assert "IGNORE" not in content
    assert "wrong-tool" not in content


def test_legacy_system_persona_still_extracts_tools(monkeypatch):
    brain = _brain(monkeypatch)
    persona = (
        "【当前时间】2026-01-01 00:00\n"
        "【人设】核心角色\n"
        "运行时补充\n"
        "【可用工具能力】legacy-search"
    )
    messages = brain.build_prompt("你好", persona)
    content = messages[0]["content"]
    assert "运行时补充" in content
    assert "【可用工具能力】legacy-search" in content


def test_make_chat_prompt_parts_does_not_embed_persona():
    parts = make_chat_prompt_parts(
        runtime_sections=("情绪=calm",),
        tool_sections=("【可用工具能力】search",),
        invocation_mode="cmd",
    )
    legacy = parts.to_legacy_system_text()
    assert "情绪=calm" in legacy
    assert "【可用工具能力】search" in legacy
    assert "【当前时间】" in legacy
    assert "五十铃怜" not in legacy
    assert "【输出格式规范】" not in legacy
    assert parts.system_rules
