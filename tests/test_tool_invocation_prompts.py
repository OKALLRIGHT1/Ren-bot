from config import SYSTEM_RULES_PROMPT, TOOL_INVOCATION_MODE
from modules.plugin_manager import PluginManager
from services.chat_support.tool_invocation import (
    get_tool_invocation_mode,
    rewrite_system_tool_rules,
    should_attach_deferred_tool_catalog,
    should_rewrite_tool_rules,
)


class _PromptPlugin:
    name = "Sample"
    type = "react"
    llm_command = "sample"
    description = "demo tool"
    aliases = ["sample"]
    example_arg = "args"


def _manager():
    manager = PluginManager(plugin_dir="plugins")
    plugin = _PromptPlugin()
    manager.plugins = {"sample": plugin}
    manager.react_map = {"sample": plugin}
    manager.delegate_map = {"sample": plugin}
    manager.llm_command_map = {"sample": "sample"}
    return manager


def test_repo_default_invocation_mode_is_dual(monkeypatch):
    monkeypatch.setattr(
        "modules.runtime_settings.load_runtime_settings",
        lambda: {},
    )
    assert TOOL_INVOCATION_MODE == "dual"
    assert get_tool_invocation_mode() == "dual"


def test_cmd_prompt_still_teaches_cmd_format():
    prompt = _manager().get_tool_prompt_for_triggers(
        ["sample"], compact=True, invocation_mode="cmd"
    )
    assert "[CMD:" in prompt


def _teaches_cmd_format(text: str) -> bool:
    blob = str(text or "")
    return any(
        token in blob
        for token in (
            "[CMD: 命令",
            "[CMD: trigger",
            "[CMD: tool_search",
            "格式严格：[CMD:",
        )
    )


def test_function_prompt_does_not_teach_cmd():
    prompt = _manager().get_tool_prompt_for_triggers(
        ["sample"], compact=True, invocation_mode="function"
    )
    assert not _teaches_cmd_format(prompt)
    assert "tools" in prompt


def test_dual_prompt_does_not_include_cmd_lesson():
    prompt = _manager().get_tool_prompt_for_triggers(
        ["sample"], compact=True, invocation_mode="dual"
    )
    assert not _teaches_cmd_format(prompt)
    deferred = _manager().get_deferred_tool_prompt(invocation_mode="dual")
    assert not _teaches_cmd_format(deferred)
    delegate = _manager().get_delegate_prompt_for_triggers(
        ["sample"], invocation_mode="function"
    )
    assert not _teaches_cmd_format(delegate)


def test_rewrite_system_tool_rules_strips_cmd_block():
    dual = rewrite_system_tool_rules(SYSTEM_RULES_PROMPT, "dual")
    function = rewrite_system_tool_rules(SYSTEM_RULES_PROMPT, "function")
    assert _teaches_cmd_format(SYSTEM_RULES_PROMPT)
    assert not _teaches_cmd_format(dual)
    assert not _teaches_cmd_format(function)
    assert rewrite_system_tool_rules(SYSTEM_RULES_PROMPT, "cmd") == SYSTEM_RULES_PROMPT


def test_casual_turn_does_not_attach_deferred_catalog():
    assert should_attach_deferred_tool_catalog(need_tools=False, deferred_tool_flow=False) is False
    assert should_attach_deferred_tool_catalog(need_tools=True, deferred_tool_flow=True) is False
    assert should_attach_deferred_tool_catalog(need_tools=False, deferred_tool_flow=True) is True
    assert should_rewrite_tool_rules("dual") is True
    assert should_rewrite_tool_rules("cmd") is False
