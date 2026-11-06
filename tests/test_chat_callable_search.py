import pytest

from modules.llm_types import ChatCompletionResult, ToolCallRequest
from services.chat_support.chat_callable_tools import (
    chat_search_hint,
    chat_search_is_available,
    chat_search_openai_tools,
    chat_self_callable_available,
    run_chat_model_pass,
    search_query_from_completion,
)
from services.chat_support.flow_result import STATUS_CONTINUE, STATUS_REPLIED
from services.chat_support.natural_chat_pipeline import run_plain_chat
from tests.test_natural_chat_plain_pipeline import _hooks, _stub_thought_and_guard


class _SearchPlugin:
    type = "delegate"
    llm_command = "search"
    plugin_trigger = "search_web"
    self_callable = True

    def get_tool_schema(self):
        return {
            "name": "search",
            "description": "联网搜索当前事实。不要用来查天气。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }


class _Manager:
    def __init__(self, plugin=None):
        plugin = plugin or _SearchPlugin()
        self.delegate_map = {"search_web": plugin}
        self.react_map = {}
        self.direct_map = {}
        self.plugins = {"search_web": plugin}
        self.llm_command_map = {"search": "search_web"}


def test_plain_chat_without_search_plugin_has_no_search_tool():
    assert chat_search_is_available(object()) is False
    assert chat_search_openai_tools(object(), "dual") == []


def test_search_schema_is_the_only_chat_tool():
    tools = chat_search_openai_tools(_Manager(), "dual")
    names = [item["function"]["name"] for item in tools]
    assert names == ["search"]
    assert "天气" in chat_search_hint("dual")
    assert "[CMD:" in chat_search_hint("cmd")
    assert chat_search_openai_tools(_Manager(), "cmd") == []


def test_search_query_ignores_other_tools_and_reads_cmd():
    assert (
        search_query_from_completion(
            ChatCompletionResult(
                text="你好",
                tool_calls=(
                    ToolCallRequest(id="1", name="workspace_ops", arguments={"path": "a"}),
                ),
            )
        )
        == ""
    )
    assert (
        search_query_from_completion(
            ChatCompletionResult(
                text="",
                tool_calls=(
                    ToolCallRequest(id="2", name="search", arguments={"query": "今天油价"}),
                ),
            )
        )
        == "今天油价"
    )
    assert (
        search_query_from_completion(
            ChatCompletionResult(text="随便聊聊 [CMD: search | 金价] 呀")
        )
        == "金价"
    )


@pytest.mark.asyncio
async def test_chat_pass_answers_without_search_when_she_knows(monkeypatch):
    seen = {}

    async def chat(messages, **kwargs):
        seen["tools"] = kwargs.get("tools")
        seen["task_type"] = kwargs.get("task_type")
        return ChatCompletionResult(text="我记得，猫是一种动物。")

    async def explode(**kwargs):
        raise AssertionError("search should stay idle")

    monkeypatch.setattr(
        "services.chat_support.chat_callable_tools.run_search_delegate_query",
        explode,
    )
    result = await run_chat_model_pass(
        context_messages=[{"role": "user", "content": "猫是什么"}],
        ctx={},
        task_default="default",
        plugin_manager=_Manager(),
        chat_with_ai=chat,
        invocation_mode="dual",
    )
    assert result.triggered is False
    assert result.reply == "我记得，猫是一种动物。"
    assert seen["task_type"] == "default"
    assert seen["tools"][0]["function"]["name"] == "search"
    assert any("自己查" in str(item.get("content") or "") for item in result.context_messages)


@pytest.mark.asyncio
async def test_chat_pass_runs_search_then_hands_the_result_on(monkeypatch):
    calls = []

    async def chat(messages, **kwargs):
        calls.append(kwargs.get("caller"))
        return ChatCompletionResult(
            text="我猜是…",
            tool_calls=(
                ToolCallRequest(id="1", name="search", arguments={"query": "今天金价"}),
            ),
        )

    async def search(**kwargs):
        assert kwargs["query"] == "今天金价"
        return True, "", ["金价 560 元/克"], ["search_web"]

    monkeypatch.setattr(
        "services.chat_support.chat_callable_tools.run_search_delegate_query",
        search,
    )
    recorded = {}

    def record(**event):
        recorded.update(event)

    result = await run_chat_model_pass(
        context_messages=[{"role": "user", "content": "今天金价多少"}],
        ctx={"source": "desktop"},
        task_default="default",
        plugin_manager=_Manager(),
        chat_with_ai=chat,
        invocation_mode="function",
        record_tool_execution=record,
    )
    assert result.triggered is True
    assert result.clean_thought == ""
    assert result.tool_results == ["金价 560 元/克"]
    assert result.used_triggers == ["search_web"]
    assert result.fc_used is True
    assert recorded["used_triggers"] == ["search_web"]
    assert calls == ["chat_default_reply"]


@pytest.mark.asyncio
async def test_plain_chat_uses_search_pass_when_plugin_is_loaded(monkeypatch):
    _stub_thought_and_guard(monkeypatch)

    async def search_pass(**kwargs):
        from services.chat_support.tool_flow_service import ReactFirstPassResult

        return ReactFirstPassResult(
            reply="",
            triggered=True,
            tool_results=["搜到了"],
            used_triggers=["search_web"],
            context_messages=list(kwargs["context_messages"]),
        )

    async def react_pass(**kwargs):
        raise AssertionError("keyword router pass should not run")

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_chat_model_pass",
        search_pass,
    )
    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_react_first_pass",
        react_pass,
    )
    outcome = await run_plain_chat(
        user_text="最近有什么新闻",
        ctx={"source": "desktop"},
        hooks=_hooks(plugin_manager=_Manager()),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
        invocation_mode="dual",
    )
    assert outcome.flow.status == STATUS_CONTINUE
    assert outcome.react_first_pass.used_triggers == ["search_web"]


@pytest.mark.asyncio
async def test_plain_chat_keeps_a_normal_reply_when_she_does_not_search(monkeypatch):
    _stub_thought_and_guard(monkeypatch)

    async def chat(messages, **kwargs):
        return "在的，刚在发呆。"

    async def explode(**kwargs):
        raise AssertionError("no search")

    monkeypatch.setattr(
        "services.chat_support.chat_callable_tools.run_search_delegate_query",
        explode,
    )
    outcome = await run_plain_chat(
        user_text="在吗",
        ctx={"source": "desktop"},
        hooks=_hooks(plugin_manager=_Manager(), chat_with_ai=chat),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
        invocation_mode="dual",
    )
    assert outcome.flow.status == STATUS_REPLIED
    assert outcome.flow.reply_text == "在的，刚在发呆。"


def test_self_callable_flag_gates_the_chat_schema():
    plugin = _SearchPlugin()
    plugin.self_callable = False
    manager = _Manager(plugin)
    assert chat_self_callable_available(manager) is False
    assert chat_search_is_available(manager) is False
    assert chat_search_openai_tools(manager, "dual") == []


@pytest.mark.asyncio
async def test_chat_pass_ignores_untagged_commands(monkeypatch):
    async def chat(messages, **kwargs):
        return ChatCompletionResult(text="[CMD: workspace_ops | read_file notes.txt]")

    async def explode(**kwargs):
        raise AssertionError("untagged command must not run")

    monkeypatch.setattr(
        "services.chat_support.chat_callable_tools.run_search_delegate_query",
        explode,
    )
    result = await run_chat_model_pass(
        context_messages=[{"role": "user", "content": "读一下笔记"}],
        ctx={},
        task_default="default",
        plugin_manager=_Manager(),
        chat_with_ai=chat,
        invocation_mode="cmd",
    )
    assert result.triggered is False
    assert "workspace_ops" in result.reply


@pytest.mark.asyncio
async def test_empty_search_query_does_not_search(monkeypatch):
    async def chat(messages, **kwargs):
        return ChatCompletionResult(
            text="",
            tool_calls=(ToolCallRequest(id="1", name="search", arguments={"query": "  "}),),
        )

    async def explode(**kwargs):
        raise AssertionError("empty query must not search")

    monkeypatch.setattr(
        "services.chat_support.chat_callable_tools.run_search_delegate_query",
        explode,
    )
    result = await run_chat_model_pass(
        context_messages=[{"role": "user", "content": "嗯"}],
        ctx={},
        task_default="default",
        plugin_manager=_Manager(),
        chat_with_ai=chat,
        invocation_mode="function",
    )
    assert result.triggered is False


@pytest.mark.asyncio
async def test_plain_chat_without_flag_stays_on_the_react_pass(monkeypatch):
    _stub_thought_and_guard(monkeypatch)
    plugin = _SearchPlugin()
    plugin.self_callable = False

    async def react_pass(**kwargs):
        from services.chat_support.tool_flow_service import ReactFirstPassResult

        return ReactFirstPassResult(reply="普通回复", context_messages=list(kwargs["context_messages"]))

    async def chat_pass(**kwargs):
        raise AssertionError("untagged search must not open the chat tool pass")

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_react_first_pass",
        react_pass,
    )
    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_chat_model_pass",
        chat_pass,
    )
    outcome = await run_plain_chat(
        user_text="在吗",
        ctx={"source": "desktop"},
        hooks=_hooks(plugin_manager=_Manager(plugin)),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
        invocation_mode="dual",
    )
    assert outcome.flow.status == STATUS_REPLIED
    assert outcome.flow.reply_text == "普通回复"


@pytest.mark.asyncio
async def test_plain_chat_greeting_does_not_open_the_tool_pass(monkeypatch):
    _stub_thought_and_guard(monkeypatch)

    async def react_pass(**kwargs):
        from services.chat_support.tool_flow_service import ReactFirstPassResult

        return ReactFirstPassResult(
            reply="在的",
            context_messages=list(kwargs["context_messages"]),
        )

    async def chat_pass(**kwargs):
        raise AssertionError("greetings must not carry chat tools")

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_react_first_pass",
        react_pass,
    )
    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_chat_model_pass",
        chat_pass,
    )
    outcome = await run_plain_chat(
        user_text="在吗",
        ctx={"source": "desktop"},
        hooks=_hooks(plugin_manager=_Manager()),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
        invocation_mode="dual",
    )
    assert outcome.flow.status == STATUS_REPLIED
    assert outcome.flow.reply_text == "在的"
