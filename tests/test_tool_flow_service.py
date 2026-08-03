import pytest

from services.chat_support.tool_flow_service import (
    finalize_tool_reply,
    run_react_first_pass,
    run_tool_command_loop,
)


class _LoopPluginManager:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def execute_commands(self, text, ctx, allow_tools=True, allowed_types=None):
        self.calls.append(
            {
                "text": text,
                "ctx": dict(ctx or {}),
                "allow_tools": allow_tools,
                "allowed_types": set(allowed_types or set()),
            }
        )
        if not self.results:
            return False, text, [], []
        return self.results.pop(0)


def _extract_emo_tag(text):
    return "", text


def _contains_cmd(text):
    return "[CMD:" in str(text or "")


async def _chat_from_replies(replies, captured):
    async def chat_with_ai(messages, *, task_type, caller):
        captured.append(
            {
                "messages": [dict(message) for message in messages],
                "task_type": task_type,
                "caller": caller,
            }
        )
        return replies.pop(0)

    return chat_with_ai


@pytest.mark.asyncio
async def test_tool_command_loop_runs_until_no_cmd_conclusion():
    captured = []
    chat_with_ai = await _chat_from_replies(
        ["[CMD: sample | first]", "[CMD: sample | second]", "finished"],
        captured,
    )
    plugin_manager = _LoopPluginManager(
        [
            (True, "", ["first-result"], ["sample"]),
            (True, "", ["second-result"], ["sample"]),
        ]
    )

    result = await run_tool_command_loop(
        context_messages=[{"role": "user", "content": "do task"}],
        ctx={"source": "text_input"},
        task_type="reasoning",
        caller_prefix="test_loop",
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
        allowed_types={"delegate"},
        contains_cmd=_contains_cmd,
    )

    assert result.triggered is True
    assert result.clean_thought == "finished"
    assert result.tool_results == ["first-result", "second-result"]
    assert result.used_triggers == ["sample", "sample"]
    assert len(plugin_manager.calls) == 2
    assert all(call["allowed_types"] == {"delegate"} for call in plugin_manager.calls)
    assert len(captured) == 3
    assert "first-result" in captured[1]["messages"][-1]["content"]
    assert "second-result" in captured[2]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_tool_command_loop_stops_on_duplicate_command_signature():
    captured = []
    chat_with_ai = await _chat_from_replies(
        ["[CMD: sample | same   args]", "[CMD: sample | same args]"],
        captured,
    )
    plugin_manager = _LoopPluginManager(
        [
            (True, "", ["first-error"], ["sample"]),
            (True, "", ["second-error"], ["sample"]),
        ]
    )

    result = await run_tool_command_loop(
        context_messages=[],
        ctx={},
        task_type="reasoning",
        caller_prefix="test_loop",
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
        allowed_types={"delegate"},
        contains_cmd=_contains_cmd,
    )

    assert result.triggered is True
    assert result.tool_results == ["first-error"]
    assert result.used_triggers == ["sample"]
    assert len(plugin_manager.calls) == 1
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_tool_command_loop_stops_at_max_iterations():
    captured = []
    chat_with_ai = await _chat_from_replies(
        ["[CMD: sample | one]", "[CMD: sample | two]"],
        captured,
    )
    plugin_manager = _LoopPluginManager(
        [
            (True, "", ["one-result"], ["sample"]),
            (True, "", ["two-result"], ["sample"]),
        ]
    )

    result = await run_tool_command_loop(
        context_messages=[],
        ctx={},
        task_type="reasoning",
        caller_prefix="test_loop",
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
        allowed_types={"delegate"},
        contains_cmd=_contains_cmd,
        max_iterations=2,
    )

    assert result.triggered is True
    assert result.tool_results == ["one-result", "two-result"]
    assert len(plugin_manager.calls) == 2
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_react_first_pass_replaces_tool_search_special_case_with_loop():
    captured = []
    chat_with_ai = await _chat_from_replies(
        [
            "[CMD: tool_search | filesystem]",
            "[CMD: workspace_ops | read_file ||| notes.txt]",
            "read complete",
        ],
        captured,
    )
    plugin_manager = _LoopPluginManager(
        [
            (True, "", ["matched workspace_ops"], ["tool_search"]),
            (True, "", ["file contents"], ["workspace_ops"]),
        ]
    )

    result = await run_react_first_pass(
        context_messages=[{"role": "user", "content": "read notes"}],
        ctx={"source": "text_input"},
        need_tools=True,
        deferred_tool_flow=False,
        task_reasoning="reasoning",
        task_default="default",
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
        contains_cmd=_contains_cmd,
        strip_cmd_anywhere=lambda text: text.replace("[CMD:", ""),
    )

    assert result.triggered is True
    assert result.clean_thought == "read complete"
    assert result.tool_results == ["matched workspace_ops", "file contents"]
    assert result.used_triggers == ["tool_search", "workspace_ops"]
    assert len(plugin_manager.calls) == 2
    assert len(captured) == 3


@pytest.mark.asyncio
async def test_finalize_tool_reply_limits_feedback_size():
    captured = {}

    async def chat_with_ai(messages, *, task_type, caller):
        captured["messages"] = messages
        return "summary"

    await finalize_tool_reply(
        clean_thought="",
        tool_results=["a" * 2000, "b" * 2000, "c" * 2000],
        used_triggers=["workspace_ops"],
        context_messages=[],
        route_reason="",
        task_default="default",
        start_emo="neutral",
        chat_with_ai=chat_with_ai,
        extract_emo_tag=_extract_emo_tag,
        character_sharing_enabled=False,
        try_share=lambda: "",
    )

    feedback_message = captured["messages"][0]["content"]
    assert len(feedback_message) < 5000
    assert "...[truncated]" in feedback_message


@pytest.mark.asyncio
async def test_finalize_tool_reply_tells_info_gateway_not_to_guess_empty_data():
    captured = {}

    async def chat_with_ai(messages, *, task_type, caller):
        captured["messages"] = messages
        return "查不到"

    await finalize_tool_reply(
        clean_thought="",
        tool_results=["weather_now 没有返回可用数据"],
        used_triggers=["info_gateway"],
        context_messages=[],
        route_reason="",
        task_default="default",
        start_emo="neutral",
        chat_with_ai=chat_with_ai,
        extract_emo_tag=_extract_emo_tag,
        character_sharing_enabled=False,
        try_share=lambda: "",
    )

    feedback_message = captured["messages"][0]["content"]
    assert "不要猜测" in feedback_message
    assert "只基于工具结果" in feedback_message
    assert "不要说自己没有这个功能" in feedback_message


@pytest.mark.asyncio
async def test_finalize_search_reply_falls_back_to_tool_result_on_model_error():
    async def chat_with_ai(messages, *, task_type, caller):
        return "❌ 系统繁忙，无法连接 AI。"

    result = await finalize_tool_reply(
        clean_thought="我查一下",
        tool_results=[
            "[search_meta] provider=GrokChat; query=最近登陆中国的台风\n"
            "最近登陆中国的台风是台风示例，登陆时间为7月。"
        ],
        used_triggers=["search_web"],
        context_messages=[],
        route_reason="",
        task_default="default",
        start_emo="think",
        chat_with_ai=chat_with_ai,
        extract_emo_tag=_extract_emo_tag,
        character_sharing_enabled=False,
        try_share=lambda: "",
        is_model_error_reply=lambda text: "系统繁忙" in str(text),
    )

    assert "最近登陆中国的台风是台风示例" in result.final_reply
    assert "系统繁忙" not in result.final_reply
    assert result.final_emo == "think"
    assert result.model_emo_seen is True
