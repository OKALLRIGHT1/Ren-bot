import pytest

from services.agent_runtime import AgentRuntime


class FakePluginManager:
    def __init__(self):
        self.calls = []
        self.plugins = {}

    async def execute_direct_commands(self, text, ctx):
        self.calls.append((text, ctx))
        if "邮件" in text:
            return True, "mail-ok"
        return False, None


@pytest.mark.asyncio
async def test_agent_runtime_passes_through_direct_commands():
    manager = FakePluginManager()
    runtime = AgentRuntime(plugin_manager=manager)

    result = await runtime.handle_direct_text("查邮件", {"source": "text_input"})

    assert result.handled is True
    assert result.reply == "mail-ok"
    assert manager.calls == [("查邮件", {"source": "text_input"})]


@pytest.mark.asyncio
async def test_agent_runtime_ignores_non_tool_text():
    manager = FakePluginManager()
    runtime = AgentRuntime(plugin_manager=manager)

    result = await runtime.handle_direct_text("你好", {"source": "text_input"})

    assert result.handled is False
    assert result.reply is None


def test_chat_service_can_import_agent_runtime():
    from services.agent_runtime import AgentRuntime

    assert AgentRuntime is not None


def test_runtime_lists_tool_catalog():
    class CatalogManager(FakePluginManager):
        def __init__(self):
            super().__init__()
            self.plugins = {
                "agently_mail": type(
                    "P",
                    (),
                    {
                        "name": "Agent Mail",
                        "type": "direct",
                        "description": "邮件工具",
                        "aliases": ["邮件"],
                        "tool_examples": ["最近邮件"],
                    },
                )()
            }

    runtime = AgentRuntime(plugin_manager=CatalogManager())
    catalog = runtime.list_tools()

    assert catalog[0]["trigger"] == "agently_mail"
    assert catalog[0]["type"] == "direct"
    assert catalog[0]["examples"] == ["最近邮件"]


class ConfirmPlugin:
    name = "Confirm Tool"
    type = "direct"

    def __init__(self):
        self.confirmed = []

    async def confirm_agent_action(self, payload, ctx):
        self.confirmed.append((payload, ctx))
        return "confirmed-ok"


class ConfirmPluginManager(FakePluginManager):
    def __init__(self, plugin):
        super().__init__()
        self.plugin = plugin
        self.plugins = {"confirm_tool": plugin}

    async def execute_direct_commands(self, text, ctx):
        if text == "start":
            return True, {
                "__agent_result__": "confirmation_required",
                "trigger": "confirm_tool",
                "summary": "确认执行测试操作",
                "payload": {"token": "ctk_1"},
                "expires_in": 300,
            }
        return False, None


@pytest.mark.asyncio
async def test_runtime_stores_and_confirms_pending_action():
    plugin = ConfirmPlugin()
    runtime = AgentRuntime(plugin_manager=ConfirmPluginManager(plugin))

    first = await runtime.handle_direct_text("start", {"source": "text_input"})
    assert first.handled is True
    assert "确认执行测试操作" in first.reply

    second = await runtime.handle_direct_text("确认", {"source": "text_input"})
    assert second.handled is True
    assert second.reply == "confirmed-ok"
    assert plugin.confirmed == [({"token": "ctk_1"}, {"source": "text_input"})]


@pytest.mark.asyncio
async def test_runtime_can_cancel_pending_action():
    plugin = ConfirmPlugin()
    runtime = AgentRuntime(plugin_manager=ConfirmPluginManager(plugin))

    await runtime.handle_direct_text("start", {"source": "text_input"})
    result = await runtime.handle_direct_text("取消", {"source": "text_input"})

    assert result.handled is True
    assert "已取消" in result.reply
    assert plugin.confirmed == []


@pytest.mark.asyncio
async def test_runtime_handles_mail_confirmation_result():
    from tests.test_agently_mail_plugin import plugin_with_runner

    plugin, runner = plugin_with_runner(
        [
            {
                "returncode": 0,
                "stdout": '{"ok": true, "queued": true}',
                "stderr": "",
            }
        ]
    )

    class MailManager(FakePluginManager):
        def __init__(self):
            super().__init__()
            self.plugins = {"agently_mail": plugin}

        async def execute_direct_commands(self, text, ctx):
            return True, {
                "__agent_result__": "confirmation_required",
                "trigger": "agently_mail",
                "summary": "发送给 bob@example.com，主题 Hi",
                "payload": {
                    "action": "send",
                    "argv": [
                        "agently-cli",
                        "message",
                        "+send",
                        "--to",
                        "bob@example.com",
                        "--subject",
                        "Hi",
                        "--body",
                        "Hello",
                    ],
                    "confirmation_token": "ctk_123",
                },
                "expires_in": 300,
            }

    runtime = AgentRuntime(plugin_manager=MailManager())
    first = await runtime.handle_direct_text("发邮件", {"source": "text_input"})
    second = await runtime.handle_direct_text("确认", {"source": "text_input"})

    assert "确认" in first.reply
    assert "已提交发送" in second.reply
    assert "--confirmation-token" in runner.calls[0][0]


@pytest.mark.asyncio
async def test_runtime_run_steps_executes_direct_tools_until_final():
    manager = FakePluginManager()
    runtime = AgentRuntime(plugin_manager=manager)
    planner_calls = []

    async def planner(state):
        planner_calls.append(dict(state))
        if not state["observations"]:
            return {"tool_text": "查邮件"}
        return {"final": "完成：" + state["observations"][0]["reply"]}

    result = await runtime.run_steps(
        "帮我查邮件",
        {"source": "text_input"},
        planner=planner,
        max_steps=3,
    )

    assert result.handled is True
    assert result.reply == "完成：mail-ok"
    assert result.meta["path"] == "agent_steps"
    assert result.meta["steps"] == 1
    assert manager.calls == [("查邮件", {"source": "text_input"})]
    assert planner_calls[1]["observations"][0]["handled"] is True


@pytest.mark.asyncio
async def test_runtime_run_steps_stops_on_confirmation_required():
    plugin = ConfirmPlugin()
    runtime = AgentRuntime(plugin_manager=ConfirmPluginManager(plugin))

    async def planner(state):
        return {"tool_text": "start"}

    result = await runtime.run_steps(
        "开始测试操作",
        {"source": "text_input"},
        planner=planner,
        max_steps=3,
    )

    assert result.handled is True
    assert "确认执行测试操作" in result.reply
    assert result.meta["path"] == "agent_confirm"
