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


def test_deferred_catalog_lists_only_executable_schemas(tmp_path):
    manager = PluginManager(plugin_dir=str(tmp_path))

    class _Timer:
        name = "提醒"
        type = "react"
        plugin_trigger = "timer"
        llm_command = "timer"
        description = "到点提醒"

        def get_tool_schema(self):
            return {
                "name": "timer",
                "description": "到点提醒",
                "parameters": {"type": "object", "properties": {}},
            }

    class _NoSchema:
        name = "笔记"
        type = "react"
        plugin_trigger = "notes"
        llm_command = "notes"
        description = "没有函数参数"

    class _Delegate:
        name = "搜索"
        type = "delegate"
        plugin_trigger = "search_web"
        llm_command = "search"
        description = "联网搜索"

        def get_tool_schema(self):
            return {
                "name": "search",
                "description": "联网",
                "parameters": {"type": "object", "properties": {}},
            }

    class _Workspace:
        name = "工作区"
        type = "react"
        plugin_trigger = "workspace_ops"
        llm_command = "workspace_ops"
        description = "改项目文件"

        def get_tool_schema(self):
            return {
                "name": "workspace_ops",
                "description": "改文件",
                "parameters": {"type": "object", "properties": {}},
            }

    manager.plugins = {
        "timer": _Timer(),
        "notes": _NoSchema(),
        "search_web": _Delegate(),
        "workspace_ops": _Workspace(),
    }
    manager.disabled_plugins = set()
    prompt = manager.get_deferred_tool_prompt(invocation_mode="dual")
    assert "timer" in prompt
    assert "不是参数，也不是步骤" in prompt
    assert "notes" not in prompt
    assert "- search:" not in prompt
    assert "workspace_ops" not in prompt
    assert manager.should_use_deferred_tool_flow("在吗") is False
    assert manager.should_use_deferred_tool_flow("今天天气不错") is False
    assert manager.should_use_deferred_tool_flow("播放一首歌") is True

    class _Bare:
        name = "裸工具"
        type = "react"
        plugin_trigger = "bare_notes"
        llm_command = "bare_notes"
        description = "只能写命令，没有函数参数"

    manager.plugins = {"bare_notes": _Bare()}
    text = manager._format_tool_search_output(
        "裸工具",
        {},
        {"react"},
        invocation_mode="function",
    )
    assert "不能作为函数调用" in text


def test_casual_turn_does_not_attach_deferred_catalog():
    assert should_attach_deferred_tool_catalog(need_tools=False, deferred_tool_flow=False) is False
    assert should_attach_deferred_tool_catalog(need_tools=True, deferred_tool_flow=True) is False
    assert should_attach_deferred_tool_catalog(need_tools=False, deferred_tool_flow=True) is True
    assert should_rewrite_tool_rules("dual") is True
    assert should_rewrite_tool_rules("cmd") is False
