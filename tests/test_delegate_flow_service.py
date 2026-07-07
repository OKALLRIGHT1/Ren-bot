import pytest

from services.chat_support.delegate_flow_service import (
    DELEGATE_MODE_WORKSPACE_READ,
    choose_delegate_execution,
    run_delegate_round,
    should_use_background_delegate,
)


class _DelegatePlugin:
    name = "Delegate Tool"


class _DelegatePluginManager:
    def __init__(self, command_results):
        self.command_results = list(command_results)
        self.command_calls = []
        self.direct_fallback_calls = []
        self.delegate_map = {"sample": _DelegatePlugin()}

    def get_delegate_prompt_for_triggers(self, triggers, compact=True):
        return "\n[CMD: sample | args]"

    async def execute_commands(self, text, ctx, allow_tools=True, allowed_types=None):
        self.command_calls.append(
            {
                "text": text,
                "ctx": dict(ctx or {}),
                "allow_tools": allow_tools,
                "allowed_types": set(allowed_types or set()),
            }
        )
        if not self.command_results:
            return False, text, [], []
        return self.command_results.pop(0)

    def _build_delegate_runtime_context(self, ctx):
        return dict(ctx or {})

    async def _run_with_timeout(self, plugin, args, ctx):
        self.direct_fallback_calls.append((plugin, args, dict(ctx or {})))
        return "fallback-result"


async def _chat_from_replies(replies):
    async def chat_with_ai(messages, *, task_type, caller):
        return replies.pop(0)

    return chat_with_ai


@pytest.mark.asyncio
async def test_delegate_round_runs_multiple_tool_steps():
    plugin_manager = _DelegatePluginManager(
        [
            (True, "", ["first-result"], ["sample"]),
            (True, "", ["second-result"], ["sample"]),
        ]
    )
    chat_with_ai = await _chat_from_replies(
        ["[CMD: sample | first]", "[CMD: sample | second]", "done"]
    )

    result = await run_delegate_round(
        user_text="do complex task",
        ctx={"source": "text_input"},
        context_messages=[],
        delegate_triggers=["sample"],
        task_reasoning="reasoning",
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
    )

    assert result.triggered is True
    assert result.clean == "done"
    assert result.results == ["first-result", "second-result"]
    assert result.used == ["sample", "sample"]
    assert len(plugin_manager.command_calls) == 2
    assert plugin_manager.direct_fallback_calls == []


@pytest.mark.asyncio
async def test_delegate_round_no_cmd_does_not_direct_fallback():
    plugin_manager = _DelegatePluginManager([])
    chat_with_ai = await _chat_from_replies(["no tool needed"])

    result = await run_delegate_round(
        user_text="do simple task",
        ctx={"source": "text_input"},
        context_messages=[],
        delegate_triggers=["sample"],
        task_reasoning="reasoning",
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
    )

    assert result.triggered is False
    assert result.clean == "no tool needed"
    assert result.results == []
    assert result.used == []
    assert plugin_manager.command_calls == []
    assert plugin_manager.direct_fallback_calls == []


def test_workspace_read_capability_uses_workspace_shortcut():
    decision = choose_delegate_execution(
        route_reason="capability:workspace.read",
        delegate_triggers=["workspace_ops"],
        ctx={"source": "text_input"},
        user_text="帮我看看 README.md",
        followup_search_query="",
        is_search_delegate=lambda triggers, text: False,
    )

    assert decision.mode == DELEGATE_MODE_WORKSPACE_READ
    assert decision.background_delegate is False


def test_workspace_read_capability_is_not_background_delegate():
    assert (
        should_use_background_delegate(
            route_reason="capability:workspace.read",
            delegate_triggers=["workspace_ops"],
            ctx={"source": "text_input"},
            is_search_delegate=lambda triggers, text: False,
        )
        is False
    )
