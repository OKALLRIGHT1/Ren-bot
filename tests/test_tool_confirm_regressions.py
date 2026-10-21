import time

import pytest

from modules.llm_types import ChatCompletionResult, ToolCallRequest
from modules.memory.short_term import ShortTermMemoryManager, _restored_event_id
from modules.memory_sqlite import MemorySQLite
from services.agent_runtime import AgentRuntime
from services.chat_support.tool_flow_service import run_tool_command_loop
from services.chat_support.tool_invocation import arguments_invalid, parse_arguments_blob
from services.chat_support.tool_schema import build_openai_tools
from services.memory_writeback import MemoryWritebackService, WritebackJob, _content_fingerprint
from services.security.pending_confirm import (
    get_pending_confirm_store,
    normalize_decision_word,
)


class _DelegatePlugin:
    type = "delegate"
    llm_command = "search"
    plugin_trigger = "search_web"
    name = "搜索"
    llm_commands = []

    def get_tool_schema(self):
        return {
            "name": "search",
            "description": "search",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }


class _ReactPlugin:
    type = "react"
    llm_command = "note"
    plugin_trigger = "note"
    name = "笔记"
    llm_commands = []

    def get_tool_schema(self):
        return {
            "name": "note",
            "description": "note",
            "parameters": {"type": "object", "properties": {}},
        }


class _SchemaManager:
    def __init__(self):
        search = _DelegatePlugin()
        note = _ReactPlugin()
        self.plugins = {"search_web": search, "note": note}
        self.react_map = {"note": note}
        self.delegate_map = {"search_web": search}
        self.llm_command_map = {"search": "search_web", "note": "note"}


def test_react_schema_hides_delegate_tools():
    tools = build_openai_tools(
        _SchemaManager(),
        ["search", "note"],
        allowed_types={"react"},
        include_tool_search=False,
    )
    names = [item["function"]["name"] for item in tools]
    assert names == ["note"]


def test_bad_json_arguments_are_not_a_query():
    arguments, raw = parse_arguments_blob("not-json")
    assert arguments == {}
    assert arguments_invalid(
        ToolCallRequest(id="c1", name="search", arguments={}, raw_arguments=raw)
    )


def test_confirm_word_ignores_trailing_punctuation():
    assert normalize_decision_word(" 确认。 ") == "确认"
    assert normalize_decision_word("确认！") == "确认"
    assert normalize_decision_word("确认发送") != "确认"
    assert normalize_decision_word("执行") != "确认"


def test_pending_confirm_is_per_session_and_expires():
    store = get_pending_confirm_store()
    store.clear()
    first, status = store.try_set(trigger="mail", summary="将发送给 a@b.c", session_key="qq:1")
    assert status == "stored"
    second, busy = store.try_set(trigger="other", summary="别的操作", session_key="qq:1")
    assert busy == "busy"
    assert second.summary == first.summary
    desktop, desktop_status = store.try_set(
        trigger="files", summary="将写入笔记", session_key="local"
    )
    assert desktop_status == "stored"
    assert store.peek("qq:1").trigger == "mail"
    assert store.peek("local").trigger == "files"
    desktop.expires_at = time.monotonic() - 1
    assert store.peek("local") is None
    store.clear()


class _InvocationManager:
    def __init__(self):
        self.calls = []

    async def execute_invocations(self, calls, ctx, allow_tools=True, allowed_types=None, invocation_mode="dual"):
        self.calls.append(list(calls))
        return True, "", ["请看结果\n回复 确认，或回复 取消。"], ["mail"]


@pytest.mark.asyncio
async def test_tool_loop_stops_when_confirmation_is_pending():
    replies = []

    async def chat_with_ai(messages, *, task_type, caller, tools=None, request_context=None):
        replies.append(caller)
        return ChatCompletionResult(
            text="",
            tool_calls=[ToolCallRequest(id="c1", name="mail", arguments={"to": "a"})],
        )

    result = await run_tool_command_loop(
        context_messages=[{"role": "user", "content": "发邮件"}],
        ctx={"source": "text_input"},
        task_type="reasoning",
        caller_prefix="confirm_loop",
        plugin_manager=_InvocationManager(),
        chat_with_ai=chat_with_ai,
        allowed_types={"react"},
        contains_cmd=lambda text: False,
        invocation_mode="function",
        max_iterations=3,
    )
    assert result.iterations == 1
    assert "回复 确认" in result.clean_thought
    assert len(replies) == 1


def test_content_fingerprint_ignores_punctuation_and_spaces():
    assert _content_fingerprint("我还喜欢，猫！") == _content_fingerprint("我还喜欢猫")
    assert _content_fingerprint("我喜欢 A") != _content_fingerprint("我喜欢 B")


def test_duplicate_writeback_fingerprint_is_dropped(tmp_path):
    core = type("Core", (), {"store": MemorySQLite(str(tmp_path / "memory.sqlite")), "llm_call": lambda *a, **k: "", "settings": {}})()
    service = MemoryWritebackService(core, llm_call=lambda *a, **k: '{"items":[]}', settings={"memory_writeback_inline": True})
    service.process_job = lambda job: None
    job = WritebackJob(
        session_id="s",
        person_id="owner",
        trigger_role="user",
        trigger_text="我还喜欢 猫",
        reason="user_statement",
    )
    assert service.enqueue(job) is True
    again = WritebackJob(
        session_id="s",
        person_id="owner",
        trigger_role="user",
        trigger_text="我还喜欢，猫！",
        reason="user_statement",
    )
    assert service.enqueue(again) is False


def test_full_writeback_queue_spills_to_sqlite(tmp_path):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = type("Core", (), {"store": store, "llm_call": None, "settings": {}})()
    service = MemoryWritebackService(
        core,
        llm_call=lambda *a, **k: "{}",
        settings={"memory_writeback_queue_maxsize": 8, "memory_writeback_inline": False},
    )
    service.start = lambda: None
    for index in range(8):
        assert service.enqueue(
            WritebackJob(
                session_id="s",
                person_id="owner",
                trigger_role="user",
                trigger_text=f"事实 {index}",
                reason="user_statement",
            )
        )
    spilled = service.enqueue(
        WritebackJob(
            session_id="s",
            person_id="owner",
            trigger_role="user",
            trigger_text="多出来的事实",
            reason="user_statement",
        )
    )
    assert spilled is True
    rows = store.pop_writeback_jobs(limit=5)
    assert rows[0]["trigger_text"] == "多出来的事实"


def test_restored_short_term_keeps_legacy_event_id(tmp_path):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    transcript_id = store.add_transcript("user", "旧对话", meta={})
    assert _restored_event_id({"id": transcript_id, "meta": {}}) == f"legacy:{transcript_id}"
    manager = ShortTermMemoryManager(store, max_short_term=4)
    manager.restore_global()
    assert manager.short_term_memory[-1]["event_id"] == f"legacy:{transcript_id}"


@pytest.mark.asyncio
async def test_runtime_confirm_accepts_punctuation_and_blocks_other_tools():
    class Plugin:
        confirmed = []

        async def confirm_agent_action(self, payload, ctx):
            self.confirmed.append(payload)
            return "ok"

    plugin = Plugin()

    class Manager:
        plugins = {"mail": plugin}

        async def execute_direct_commands(self, text, ctx, dry_run=False):
            if text == "start":
                return True, {
                    "__agent_result__": "confirmation_required",
                    "trigger": "mail",
                    "summary": "将发送给 a@b.c：你好",
                    "payload": {"token": "1"},
                }
            if "打开" in text:
                return True, "不应执行"
            return False, None

        async def _run_with_timeout(self, plugin, args, context):
            return "rerun"

    runtime = AgentRuntime(plugin_manager=Manager())
    first = await runtime.handle_direct_text("start", {"source": "text_input", "session_id": "desk"})
    assert "将发送给 a@b.c" in first.reply
    assert "回复 确认" in first.reply
    blocked = await runtime.handle_direct_text("打开计算器", {"source": "text_input", "session_id": "desk"})
    assert blocked.handled is True
    assert "等确认" in blocked.reply
    chat = await runtime.handle_direct_text("今天天气真好", {"source": "text_input", "session_id": "desk"})
    assert chat.handled is False
    done = await runtime.handle_direct_text("确认！", {"source": "text_input", "session_id": "desk"})
    assert done.reply == "ok"
