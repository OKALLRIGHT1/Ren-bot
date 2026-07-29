import asyncio

import pytest

import services.chat_service as chat_service_module
from modules.tool_router import ToolRouteResult
from services.chat_service import ChatService


class _Brain:
    def __init__(self):
        self.short_term_memory = []
        self.sqlite_store = None
        self.last_build_kwargs = {}

    def build_prompt(self, user_text, **kwargs):
        self.last_build_kwargs = dict(kwargs)
        return [
            {"role": "system", "content": kwargs.get("system_persona", "")},
            {"role": "user", "content": user_text},
        ]

    def add_memory(
        self,
        role,
        content,
        session_id=None,
        meta=None,
        memory_session_id=None,
    ):
        item_meta = dict(meta or {})
        if session_id:
            item_meta["session_id"] = session_id
        if memory_session_id:
            item_meta["memory_session_id"] = memory_session_id
        self.short_term_memory.append({"role": role, "content": content, "meta": item_meta})


class _PluginManager:
    def __init__(self, *, direct_result=None, command_result=None, command_results=None):
        self.direct_result = direct_result
        self.command_result = command_result or (False, "", [], [])
        self.command_results = list(command_results or [])
        self.direct_calls = []
        self.command_calls = []

    async def execute_direct_commands(self, user_text, context):
        self.direct_calls.append((user_text, dict(context or {})))
        if self.direct_result is None:
            return False, None
        return True, self.direct_result

    async def execute_observe_commands(self, user_text, context):
        return False, None

    async def execute_commands(self, text, context, allow_tools=True, allowed_types=None):
        self.command_calls.append(
            {
                "text": text,
                "context": dict(context or {}),
                "allow_tools": allow_tools,
                "allowed_types": set(allowed_types or set()),
            }
        )
        if self.command_results:
            return self.command_results.pop(0)
        return self.command_result

    def get_tool_prompt_for_triggers(self, triggers, compact=False):
        return ""

    def get_delegate_prompt_for_triggers(self, triggers, compact=False):
        return ""

    def get_system_prompt_addition(self):
        return ""

    def should_use_deferred_tool_flow(self, user_text):
        return False


class _ToolRouter:
    def __init__(
        self,
        *,
        need_tools=False,
        triggers=None,
        reason="",
        capability_id="",
        capability_args=None,
    ):
        self.need_tools = need_tools
        self.triggers = list(triggers or [])
        self.reason = reason
        self.capability_id = capability_id
        self.capability_args = dict(capability_args or {})
        self.calls = []

    def route(self, text, last_tool_triggers=None):
        self.calls.append((text, list(last_tool_triggers or [])))
        return ToolRouteResult(
            need_tools=self.need_tools,
            tool_triggers=list(self.triggers),
            reason=self.reason,
            capability_id=self.capability_id,
            capability_args=dict(self.capability_args),
        )


class _Presenter:
    def __init__(self):
        self.presented = []

    async def present(self, text, emotion="neutral", **kwargs):
        self.presented.append({"text": text, "emotion": emotion, "kwargs": kwargs})


class _EventBus:
    def __init__(self):
        self.events = []

    async def emit(self, name, **kwargs):
        self.events.append((name, kwargs))


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Gateway:
    def __init__(self):
        self.sent_text = []

    async def send_text(self, adapter_name, session_id, text, **kwargs):
        self.sent_text.append(
            {
                "adapter_name": adapter_name,
                "session_id": session_id,
                "text": text,
                "kwargs": kwargs,
            }
        )
        return {"ok": True}

    async def fetch_message_by_id(self, adapter_name, session_id, message_id, **kwargs):
        return {
            "ok": True,
            "item": {
                "content": "被引用的历史消息",
                "meta": {"sender_name": "历史发送者", "message_id": message_id},
            },
        }


class _ScreenSensor:
    def get_recent_observations(self, limit=3):
        return [
            {
                "time": "14:31",
                "source": "vision",
                "app": "DeepSeek",
                "window_title": "DeepSeek - Google Chrome",
                "content": "页面上提到了《原神》这款游戏及其第16-30章。",
            }
        ]


class _Personality:
    def update_state(self):
        pass

    async def think_before_respond(self, user_text, show_thinking=None):
        pass

    def try_share(self):
        return ""


@pytest.fixture
def chat_env(monkeypatch):
    monkeypatch.setattr(chat_service_module, "GATEKEEPER_ENABLED", False)
    monkeypatch.setattr(chat_service_module, "CHARACTER_SHARING_ENABLED", False)
    monkeypatch.setattr(chat_service_module, "chat_with_ai_stream", None)

    async def no_followup(*args, **kwargs):
        return None

    async def yes_reply(*args, **kwargs):
        return True

    async def passthrough_polish(*args, **kwargs):
        return kwargs.get("draft_text") or (args[1] if len(args) > 1 else "")

    def passthrough_output(*args, **kwargs):
        if args and isinstance(args[0], ChatService):
            return args[1] if len(args) > 1 else ""
        return args[0] if args else ""

    async def neutral_emotion(*args, **kwargs):
        return None

    monkeypatch.setattr(ChatService, "_sync_qq_user_profile", no_followup)
    monkeypatch.setattr(ChatService, "_update_task_agent", no_followup)
    monkeypatch.setattr(ChatService, "_maybe_send_proactive_followup", no_followup)
    monkeypatch.setattr(ChatService, "_maybe_send_task_followup", no_followup)
    monkeypatch.setattr(ChatService, "_describe_external_images", no_followup)
    monkeypatch.setattr(ChatService, "_maybe_send_auto_meme_reply", no_followup)
    monkeypatch.setattr(ChatService, "_record_proactive_followup", no_followup)
    monkeypatch.setattr(ChatService, "_record_task_followup", no_followup)

    async def idle_noop(self, *args, **kwargs):
        return None

    monkeypatch.setattr(ChatService, "_emit_idle_status_when_safe", idle_noop)
    monkeypatch.setattr(ChatService, "_emit_idle_status", idle_noop)
    monkeypatch.setattr(ChatService, "_should_reply", yes_reply)
    monkeypatch.setattr(ChatService, "_get_current_live2d_emotion", lambda self: ("neutral", 0.4))
    monkeypatch.setattr(ChatService, "_build_current_emotion_context", lambda self, ctx=None: "")
    monkeypatch.setattr(ChatService, "_build_reply_style_context", lambda self, text, ctx=None: "")
    monkeypatch.setattr(ChatService, "_build_qq_reply_angle_context", lambda self, text, ctx=None: "")
    monkeypatch.setattr(ChatService, "_build_live2d_self_awareness_hint", lambda self, ctx=None: "")
    monkeypatch.setattr(ChatService, "_build_mcp_tool_prompt", lambda self: "")
    monkeypatch.setattr(ChatService, "_polish_natural_reply", passthrough_polish)
    monkeypatch.setattr(ChatService, "_apply_character_catchphrase", lambda self, text: text)
    monkeypatch.setattr(ChatService, "_prepare_reply_for_output", passthrough_output)
    monkeypatch.setattr(ChatService, "_infer_reply_emotion_with_llm", neutral_emotion)
    monkeypatch.setattr(
        ChatService,
        "_wants_detailed_answer",
        lambda self, text: True,
    )
    monkeypatch.setattr(ChatService, "_add_codex_session_event", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ChatService, "_set_codex_task_state", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ChatService, "_set_delegate_task_state", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ChatService, "_add_delegate_session_event", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ChatService, "_record_reply_effect", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ChatService, "_observe_reply_effect", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(ChatService, "_record_search_topic", lambda self, *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ChatService, "_remember_search_topic", lambda self, *args, **kwargs: None)

    def make_service(*, plugin_manager=None, tool_router=None, presenter=None, event_bus=None, gateway=None):
        service = ChatService(
            brain=_Brain(),
            plugin_manager=plugin_manager or _PluginManager(),
            tool_router=tool_router or _ToolRouter(),
            presenter=presenter or _Presenter(),
            event_bus=event_bus or _EventBus(),
            logger=_Logger(),
            chat_gateway=gateway,
        )
        service.personality = _Personality()
        service.learning = None
        return service

    return make_service


def test_final_reply_emotion_updates_personality_continuity(chat_env):
    class Personality(_Personality):
        def __init__(self):
            self.adjust_calls = []

        def adjust_emotion(self, emotion, intensity):
            self.adjust_calls.append((emotion, intensity))
            return emotion, intensity

    service = chat_env()
    personality = Personality()
    service.personality = personality

    service._observe_final_reply_emotion("happy")

    assert len(personality.adjust_calls) == 1
    emotion, intensity = personality.adjust_calls[0]
    assert emotion == "happy"
    assert 0 < intensity <= 0.5


@pytest.mark.asyncio
async def test_music_event_emotion_updates_personality_continuity(chat_env, monkeypatch):
    class Personality(_Personality):
        def __init__(self):
            self.adjust_calls = []

        def adjust_emotion(self, emotion, intensity):
            self.adjust_calls.append((emotion, intensity))
            return emotion, intensity

    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: "<emo=happy>这首歌很轻。",
    )
    presenter = _Presenter()
    service = chat_env(presenter=presenter)
    service.personality = Personality()
    service._sensor_min_reply_interval_sec = 0

    await service.handle_music_event("Song", "Artist")

    assert presenter.presented[-1]["emotion"] == "happy"
    assert service.personality.adjust_calls
    emotion, intensity = service.personality.adjust_calls[-1]
    assert emotion == "happy"
    assert 0 < intensity <= 0.5


@pytest.mark.asyncio
async def test_non_stream_reply_reaches_presenter_and_memory_once(chat_env, monkeypatch):
    monkeypatch.setattr(chat_service_module, "chat_with_ai", lambda *args, **kwargs: "非流式回复")
    presenter = _Presenter()
    event_bus = _EventBus()
    service = chat_env(presenter=presenter, event_bus=event_bus)

    await service.process("正常聊天", ctx={"source": "desktop"})

    assert [item["text"] for item in presenter.presented] == ["非流式回复"]
    assert [m["role"] for m in service.brain.short_term_memory] == ["user", "assistant"]
    assert service.brain.short_term_memory[1]["content"] == "非流式回复"
    assert len([e for e in event_bus.events if e[0] == "ui.append"]) == 1


@pytest.mark.asyncio
async def test_sensor_source_followup_injects_recent_observation_context(
    chat_env, monkeypatch
):
    captured = {}

    def fake_chat(messages, *args, **kwargs):
        captured["messages"] = messages
        return "是在刚才的 DeepSeek 页面视觉观察里看到的。"

    monkeypatch.setattr(chat_service_module, "chat_with_ai", fake_chat)
    service = chat_env()
    service.screen_sensor_ref = _ScreenSensor()

    await service.process("你从哪看到有原神", ctx={"source": "desktop"})

    system_text = captured["messages"][0]["content"]
    assert "最近屏幕/视觉观察证据" in system_text
    assert "DeepSeek" in system_text
    assert "原神" in system_text


@pytest.mark.asyncio
async def test_stream_reply_emits_feed_end_and_final_memory_once(chat_env, monkeypatch):
    async def fake_stream(*args, **kwargs):
        yield "流式"
        yield "回复"

    monkeypatch.setattr(chat_service_module, "chat_with_ai_stream", fake_stream)
    monkeypatch.setattr(chat_service_module, "chat_with_ai", lambda *args, **kwargs: "unused")
    event_bus = _EventBus()
    service = chat_env(event_bus=event_bus)

    await service.process("请流式回答", ctx={"source": "unknown"})

    assert [name for name, _ in event_bus.events].count("assistant.stream.start") == 1
    assert [name for name, _ in event_bus.events].count("assistant.stream.end") == 1
    feed_chunks = [kwargs["chunk"] for name, kwargs in event_bus.events if name == "assistant.stream.feed"]
    assert "".join(feed_chunks) == "流式回复"
    assert [m["role"] for m in service.brain.short_term_memory] == ["user", "assistant"]
    assert service.brain.short_term_memory[1]["content"] == "流式回复"


@pytest.mark.asyncio
async def test_search_flow_result_is_merged_without_duplicate_memory(chat_env, monkeypatch):
    replies = iter(["[CMD: search | query]", "search done", "\u641c\u7d22\u603b\u7ed3"])
    monkeypatch.setattr(chat_service_module, "chat_with_ai", lambda *args, **kwargs: next(replies))
    plugin_manager = _PluginManager(
        command_result=(True, "", ["【search 结果】猫是动物。"], ["search"])
    )
    service = chat_env(
        plugin_manager=plugin_manager,
        tool_router=_ToolRouter(need_tools=True, triggers=["search"], reason="search"),
    )

    await service.process("查一下猫", ctx={"source": "desktop"})

    assert len(plugin_manager.command_calls) == 1
    assert [m["role"] for m in service.brain.short_term_memory] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert service.brain.short_term_memory[0]["content"].startswith("[tool_use]")
    assert service.brain.short_term_memory[-1]["content"] == "搜索总结"


@pytest.mark.asyncio
async def test_search_delegate_acknowledges_then_returns_result_without_reasoning_command(
    chat_env, monkeypatch
):
    timeline = []
    model_callers = []

    class SearchPlugin:
        plugin_trigger = "search_web"

    class TimelinePluginManager(_PluginManager):
        def __init__(self):
            super().__init__(
                command_result=(
                    True,
                    "",
                    ["【search 结果】宝可梦风波搜索结果"],
                    ["search_web"],
                )
            )
            self.delegate_map = {"search_web": SearchPlugin()}

        def is_delegate_trigger(self, trigger):
            return trigger in self.delegate_map

        async def execute_commands(
            self, text, context, allow_tools=True, allowed_types=None
        ):
            timeline.append(("tool", text))
            return await super().execute_commands(
                text,
                context,
                allow_tools=allow_tools,
                allowed_types=allowed_types,
            )

    class TimelinePresenter(_Presenter):
        async def present(self, text, emotion="neutral", **kwargs):
            timeline.append(("present", text))
            await super().present(text, emotion, **kwargs)

    async def chat_with_ai(messages, *, task_type, caller):
        model_callers.append(caller)
        if caller == "chat_tool_finalize":
            return "宝可梦风波搜索结果"
        return "好，我查一下"

    monkeypatch.setattr(chat_service_module, "chat_with_ai", chat_with_ai)
    presenter = TimelinePresenter()
    event_bus = _EventBus()
    service = chat_env(
        plugin_manager=TimelinePluginManager(),
        tool_router=_ToolRouter(
            need_tools=True,
            triggers=["search_web"],
            reason="intent_keyword_matched",
        ),
        presenter=presenter,
        event_bus=event_bus,
    )

    await service.process(
        "查一下宝可梦风波的最新信息",
        ctx={"source": "desktop"},
    )

    acknowledgement = timeline[0][1]
    assert timeline[0][0] == "present"
    assert "宝可梦风波" in acknowledgement
    assert acknowledgement != "好，我查一下"
    assert len(acknowledgement) <= 36
    assert timeline[1] == (
        "tool",
        "[CMD: search | 查一下宝可梦风波的最新信息]",
    )
    assert timeline[-1] == ("present", "宝可梦风波搜索结果")
    assert model_callers == ["chat_tool_finalize"]
    assistant_log_texts = [
        payload.get("content")
        for name, payload in event_bus.events
        if name == "chat.log" and payload.get("role") == "assistant"
    ]
    assert acknowledgement not in assistant_log_texts
    assert "宝可梦风波搜索结果" in assistant_log_texts


@pytest.mark.asyncio
async def test_info_gateway_route_executes_even_when_reasoning_omits_cmd(
    chat_env, monkeypatch
):
    replies = iter(["上海天气我这边看不了", "上海天气：多云"])
    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: next(replies),
    )
    plugin_manager = _PluginManager(
        command_result=(True, "", ["上海天气：多云"], ["info_gateway"])
    )
    service = chat_env(
        plugin_manager=plugin_manager,
        tool_router=_ToolRouter(
            need_tools=True,
            triggers=["info_gateway"],
            reason="capability:info.weather_now",
        ),
    )

    await service.process("上海今天天气怎么样", ctx={"source": "desktop"})

    assert len(plugin_manager.command_calls) == 1
    assert plugin_manager.command_calls[0]["text"] == "[CMD: info_gateway | 上海今天天气怎么样]"
    assert service.brain.short_term_memory[-1]["content"] == "上海天气：多云"


@pytest.mark.asyncio
async def test_info_gateway_capability_gatekeeper_refines_weather_args(
    chat_env, monkeypatch
):
    replies = iter(
        [
            "我帮你查一下",
            '{"capability_id":"info.weather_now","args":{"city":"长春"},"confidence":0.92}',
            "长春天气：晴",
        ]
    )
    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: next(replies),
    )
    plugin_manager = _PluginManager(
        command_result=(True, "", ["长春天气：晴"], ["info_gateway"])
    )
    service = chat_env(
        plugin_manager=plugin_manager,
        tool_router=_ToolRouter(
            need_tools=True,
            triggers=["info_gateway"],
            reason="capability:info.weather_now",
            capability_id="info.weather_now",
            capability_args={"city": "看一下长春"},
        ),
    )

    await service.process("帮我看一下长春今天的天气", ctx={"source": "desktop"})

    assert plugin_manager.command_calls[0]["text"] == (
        "[CMD: info_gateway | weather_now city=长春]"
    )
    assert service.brain.short_term_memory[-1]["content"] == "长春天气：晴"


@pytest.mark.asyncio
async def test_info_gateway_capability_gatekeeper_bad_json_falls_back_to_match_args(
    chat_env, monkeypatch
):
    replies = iter(["我帮你查一下", "not json", "上海天气：多云"])
    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: next(replies),
    )
    plugin_manager = _PluginManager(
        command_result=(True, "", ["上海天气：多云"], ["info_gateway"])
    )
    service = chat_env(
        plugin_manager=plugin_manager,
        tool_router=_ToolRouter(
            need_tools=True,
            triggers=["info_gateway"],
            reason="capability:info.weather_now",
            capability_id="info.weather_now",
            capability_args={"city": "上海"},
        ),
    )

    await service.process("上海今天的天气怎么样", ctx={"source": "desktop"})

    assert plugin_manager.command_calls[0]["text"] == (
        "[CMD: info_gateway | weather_now city=上海]"
    )


@pytest.mark.asyncio
async def test_info_gateway_route_executes_when_tool_search_did_not_run_gateway(
    chat_env, monkeypatch
):
    replies = iter(
        [
            "[CMD: tool_search | 天气]",
            "找到 info_gateway，但还没执行。",
            "上海天气：多云",
        ]
    )
    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: next(replies),
    )
    plugin_manager = _PluginManager(
        command_results=[
            (True, "", ["tool_search matched info_gateway"], ["tool_search"]),
            (True, "", ["上海天气：多云"], ["info_gateway"]),
        ]
    )
    service = chat_env(
        plugin_manager=plugin_manager,
        tool_router=_ToolRouter(
            need_tools=True,
            triggers=["info_gateway"],
            reason="capability:info.weather_now",
        ),
    )

    await service.process("上海今天的天气怎么样", ctx={"source": "desktop"})

    assert [call["text"] for call in plugin_manager.command_calls] == [
        "[CMD: tool_search | 天气]",
        "[CMD: info_gateway | 上海今天的天气怎么样]",
    ]
    assert service.brain.short_term_memory[-1]["content"] == "上海天气：多云"


@pytest.mark.asyncio
async def test_direct_plugin_output_does_not_send_generic_fallback(chat_env):
    plugin_manager = _PluginManager(
        direct_result={
            "__type__": "gateway_image",
            "image_path": "D:/tmp/smoke.png",
            "caption": "图片说明",
            "success_text": "图片已发送",
            "fallback_text": "图片失败",
            "cleanup": False,
        }
    )
    service = chat_env(plugin_manager=plugin_manager)
    image_calls = []
    text_calls = []

    async def fake_image(image_path, ctx, caption=""):
        image_calls.append((image_path, caption, dict(ctx or {})))
        return True

    async def fake_text(text, ctx, emotion=None):
        text_calls.append((text, emotion, dict(ctx or {})))

    service._send_gateway_image_reply = fake_image
    service._send_gateway_reply = fake_text

    await service.process("发图", ctx={"source": "qq_gateway", "channel_meta": {"session_id": "private:1"}})

    assert len(image_calls) == 1
    assert image_calls[0][0] == "D:/tmp/smoke.png"
    assert image_calls[0][1] == ""
    assert image_calls[0][2]["source"] == "qq_gateway"
    assert text_calls == []
    assert service.brain.short_term_memory[-1]["content"] == "图片说明"


@pytest.mark.asyncio
async def test_direct_gateway_image_can_send_post_text(chat_env):
    plugin_manager = _PluginManager(
        direct_result={
            "__type__": "gateway_image",
            "image_path": "D:/tmp/mail-card.png",
            "caption": "最近邮件卡片",
            "post_send_text": "最近邮件：共 2 封，未读 1 封。详情见图片。",
            "success_text": "图片已发送",
            "fallback_text": "图片失败",
            "cleanup": False,
        }
    )
    service = chat_env(plugin_manager=plugin_manager)
    image_calls = []
    text_calls = []

    async def fake_image(image_path, ctx, caption=""):
        image_calls.append((image_path, caption))
        return True

    async def fake_text(text, ctx, emotion=None):
        text_calls.append((text, emotion))

    service._send_gateway_image_reply = fake_image
    service._send_gateway_reply = fake_text

    await service.process(
        "查邮件",
        ctx={"source": "qq_gateway", "channel_meta": {"session_id": "private:1"}},
    )

    assert image_calls == [("D:/tmp/mail-card.png", "")]
    assert text_calls == [("最近邮件：共 2 封，未读 1 封。详情见图片。", "neutral")]
    assert service.brain.short_term_memory[-1]["content"] == "最近邮件卡片"


@pytest.mark.asyncio
async def test_direct_app_restart_replies_before_scheduling_restart(chat_env):
    plugin_manager = _PluginManager(
        direct_result={
            "__type__": "app_restart",
            "message": "收到，正在重启主程序。",
            "delay_sec": 0,
        }
    )
    gateway = _Gateway()
    service = chat_env(plugin_manager=plugin_manager, gateway=gateway)
    restart_calls = []

    class _App:
        def restart_app(self):
            restart_calls.append("restart")

    service.app = _App()

    await service.process(
        "/重启",
        ctx={
            "source": "qq_gateway",
            "channel_meta": {
                "adapter": "napcat_qq",
                "session_id": "private:10001",
                "message_type": "private",
                "is_owner": True,
            },
        },
    )
    await asyncio.sleep(0.01)

    assert gateway.sent_text
    assert gateway.sent_text[0]["text"] == "收到，正在重启主程序。"
    assert restart_calls == ["restart"]


@pytest.mark.asyncio
async def test_direct_code_agent_request_is_handled_before_llm(chat_env, monkeypatch):
    llm_calls = []
    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: llm_calls.append(args) or "LLM",
    )
    plugin_manager = _PluginManager(direct_result="代码代理完成")
    service = chat_env(plugin_manager=plugin_manager)

    await service.process("让 Codex 分析这个项目为什么启动失败", ctx={"source": "text_input"})

    assert plugin_manager.direct_calls
    assert plugin_manager.direct_calls[0][0] == "让 Codex 分析这个项目为什么启动失败"
    assert llm_calls == []
    assert service.brain.short_term_memory[-1]["content"] == "代码代理完成"


@pytest.mark.asyncio
async def test_explicit_code_agent_request_keeps_exec_permission_outside_codex_mode(
    chat_env, monkeypatch
):
    monkeypatch.setattr(
        chat_service_module,
        "chat_with_ai",
        lambda *args, **kwargs: "LLM",
    )
    plugin_manager = _PluginManager(direct_result="代码代理完成")
    service = chat_env(plugin_manager=plugin_manager)

    await service.process(
        "让 Codex 画一张丰川祥子的图",
        ctx={"source": "text_input", "allow_exec": True},
    )

    assert plugin_manager.direct_calls
    assert plugin_manager.direct_calls[0][1]["codex_mode"] is False
    assert plugin_manager.direct_calls[0][1]["allow_exec"] is True


def test_owner_sender_context_uses_character_user_address(chat_env):
    service = chat_env()
    ctx = {
        "source": "napcat_qq",
        "channel_meta": {
            "is_owner": True,
            "owner_label": "主人",
            "sender_name": "ExampleUser",
            "user_id": "10001",
            "message_type": "private",
        },
    }

    user_address = service._get_active_user_address(ctx)
    assert user_address
    assert f"当前角色称呼用户为「{user_address}」" in service._build_user_address_context(ctx)
    external = service._build_external_sender_context(ctx)
    assert f"The current sender is {user_address}." in external
    assert "The current sender is 主人." not in external


@pytest.mark.asyncio
async def test_direct_plain_text_result_uses_persona_polish(chat_env, monkeypatch):
    async def fake_polish(self, *, user_text, draft_text, ctx=None, scene="chat"):
        assert scene == "direct_tool"
        return "……那个，" + draft_text

    monkeypatch.setattr(ChatService, "_polish_natural_reply", fake_polish)
    plugin_manager = _PluginManager(direct_result="工具处理完成")
    service = chat_env(plugin_manager=plugin_manager)

    await service.process("agent 工具列表", ctx={"source": "text_input"})

    assert service.brain.short_term_memory[-1]["content"] == "……那个，工具处理完成"


@pytest.mark.asyncio
async def test_direct_file_content_is_not_persona_rewritten(chat_env, monkeypatch):
    async def fail_polish(self, *args, **kwargs):
        raise AssertionError("file content should not be polished")

    monkeypatch.setattr(ChatService, "_polish_natural_reply", fail_polish)
    raw_file = "# D:\\Downloads\\a.txt\nsecret=value"
    plugin_manager = _PluginManager(direct_result=raw_file)
    service = chat_env(plugin_manager=plugin_manager)

    await service.process("帮我看看下载目录里的 a.txt", ctx={"source": "text_input"})

    assert service.brain.short_term_memory[-1]["content"] == raw_file


@pytest.mark.asyncio
async def test_direct_error_is_softened_without_losing_exact_error(chat_env):
    plugin_manager = _PluginManager(
        direct_result="文件不存在: D:\\Downloads\\missing.txt"
    )
    service = chat_env(plugin_manager=plugin_manager)

    await service.process("帮我看看下载目录里的 missing.txt", ctx={"source": "text_input"})

    reply = service.brain.short_term_memory[-1]["content"]
    assert "……那个" in reply
    assert "文件不存在: D:\\Downloads\\missing.txt" in reply


@pytest.mark.asyncio
async def test_direct_user_files_read_gets_read_permission_outside_codex(chat_env):
    class ReadPluginManager(_PluginManager):
        async def execute_direct_commands(self, user_text, context):
            self.direct_calls.append((user_text, dict(context or {})))
            assert context["allow_read"] is True
            assert context["allow_write"] is False
            return True, "用户文件读取完成"

    plugin_manager = ReadPluginManager()
    service = chat_env(plugin_manager=plugin_manager)

    await service.process("帮我看看下载目录里的 a.txt", ctx={"source": "text_input"})

    assert plugin_manager.direct_calls
    assert service.brain.short_term_memory[-1]["content"] == "用户文件读取完成"


@pytest.mark.asyncio
async def test_chat_service_injects_app_root_and_cwd_for_direct_tools(chat_env):
    class InspectPluginManager(_PluginManager):
        async def execute_direct_commands(self, user_text, context):
            self.direct_calls.append((user_text, dict(context or {})))
            assert context["app_root"]
            assert context["cwd"]
            assert context["code_path"] == context["app_root"]
            return True, "位置已注入"

    plugin_manager = InspectPluginManager()
    service = chat_env(plugin_manager=plugin_manager)

    await service.process("agent 位置", ctx={"source": "text_input"})

    assert plugin_manager.direct_calls
    assert service.brain.short_term_memory[-1]["content"] == "位置已注入"


@pytest.mark.asyncio
async def test_qq_reply_uses_gateway_without_local_presenter(chat_env, monkeypatch):
    monkeypatch.setattr(chat_service_module, "chat_with_ai", lambda *args, **kwargs: "QQ回复")
    presenter = _Presenter()
    gateway = _Gateway()
    service = chat_env(presenter=presenter, gateway=gateway)

    await service.process(
        "QQ消息",
        ctx={
            "source": "qq_gateway",
            "channel_meta": {
                "adapter": "napcat_qq",
                "session_id": "private:10001",
                "message_type": "private",
                "is_owner": True,
            },
        },
    )

    assert presenter.presented
    assert presenter.presented[0]["kwargs"]["speak"] is False
    assert presenter.presented[0]["kwargs"]["show_bubble"] is False
    assert gateway.sent_text
    assert gateway.sent_text[0]["session_id"] == "private:10001"
    assert gateway.sent_text[0]["text"] == "QQ回复"
    assert service.brain.last_build_kwargs["session_id"] == "private:10001"
    assert service.brain.last_build_kwargs["memory_session_id"] == "owner_shared"
    assert all(
        item["meta"].get("session_id") == "private:10001"
        for item in service.brain.short_term_memory
    )
    assert all(
        item["meta"].get("memory_session_id") == "owner_shared"
        for item in service.brain.short_term_memory
    )


@pytest.mark.asyncio
async def test_qq_private_messages_are_debounced_into_one_llm_call(chat_env, monkeypatch):
    captured = []

    def fake_chat(messages, *args, **kwargs):
        captured.append(messages[-1]["content"])
        return "合并回复"

    monkeypatch.setattr(chat_service_module, "chat_with_ai", fake_chat)
    gateway = _Gateway()
    service = chat_env(gateway=gateway)
    service.qq_private_message_buffer.debounce_sec = 0.03
    service.qq_private_message_buffer.short_debounce_sec = 0.03

    ctx1 = {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "session_id": "private:10001",
            "message_type": "private",
            "message_id": "m1",
            "is_owner": True,
        },
    }
    ctx2 = {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "session_id": "private:10001",
            "message_type": "private",
            "message_id": "m2",
            "is_owner": True,
        },
    }

    first = asyncio.create_task(service.process("第一句", ctx=ctx1))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(service.process("第二句", ctx=ctx2))
    await asyncio.gather(first, second)

    assert len(captured) == 1
    assert "第一句\n第二句" in captured[0]
    assert len(gateway.sent_text) == 1
    assert gateway.sent_text[0]["text"] == "合并回复"


@pytest.mark.asyncio
async def test_qq_reply_fetches_missing_quoted_text_before_merge(chat_env, monkeypatch):
    captured = []

    def fake_chat(messages, *args, **kwargs):
        captured.append(messages[-1]["content"])
        return "知道了"

    monkeypatch.setattr(chat_service_module, "chat_with_ai", fake_chat)
    gateway = _Gateway()
    service = chat_env(gateway=gateway)
    service.qq_private_message_buffer.debounce_sec = 0.03
    service.qq_private_message_buffer.short_debounce_sec = 0.03

    await service.process(
        "这句话是什么意思",
        ctx={
            "source": "qq_gateway",
            "channel_meta": {
                "adapter": "napcat_qq",
                "session_id": "private:10001",
                "message_type": "private",
                "message_id": "m1",
                "reply": {"message_id": "r1"},
            },
        },
    )

    assert len(captured) == 1
    assert '<quoted_message sender="历史发送者">被引用的历史消息</quoted_message>' in captured[0]


@pytest.mark.asyncio
async def test_qq_reply_merges_quoted_images_into_current_context(chat_env):
    class ImageGateway(_Gateway):
        async def fetch_message_by_id(
            self, adapter_name, session_id, message_id, **kwargs
        ):
            return {
                "ok": True,
                "item": {
                    "content": "图片说明",
                    "meta": {
                        "sender_name": "历史发送者",
                        "message_id": message_id,
                        "images": [{"url": "https://example.test/image.jpg"}],
                    },
                },
            }

    service = chat_env(gateway=ImageGateway())
    ctx = {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "session_id": "group:100",
            "message_type": "group",
            "reply": {"message_id": "r1"},
            "images": [],
        },
    }

    await service._enrich_qq_reply_context(ctx)

    meta = ctx["channel_meta"]
    assert meta["images"] == [{"url": "https://example.test/image.jpg"}]
    assert meta["has_image"] is True
    assert meta["image_count"] == 1
    assert meta["reply"]["text"] == "图片说明"


def test_recent_chat_tone_uses_only_current_group_session(chat_env):
    class ShortTermManager:
        def get_context(self, session_id=None, **kwargs):
            assert session_id == "group:200"
            return [{"role": "user", "content": "第二个群的内容"}]

    service = chat_env()
    service.brain.short_term_memory = [
        {"role": "user", "content": "第一个群不该出现"}
    ]
    service.brain.short_term_manager = ShortTermManager()
    ctx = {
        "source": "qq_gateway",
        "channel_meta": {
            "is_owner": True,
            "message_type": "group",
            "session_id": "group:200",
        },
    }

    result = service._build_recent_chat_tone_context(ctx=ctx)

    assert "第二个群的内容" in result
    assert "第一个群不该出现" not in result


@pytest.mark.asyncio
async def test_qq_image_reference_without_image_does_not_reach_llm(
    chat_env, monkeypatch
):
    def fail_chat(*args, **kwargs):
        raise AssertionError("missing-image request must not reach the LLM")

    monkeypatch.setattr(chat_service_module, "chat_with_ai", fail_chat)
    gateway = _Gateway()
    service = chat_env(gateway=gateway)

    await service.process(
        "帮我总结一下图上的效果",
        ctx={
            "source": "qq_gateway",
            "channel_meta": {
                "adapter": "napcat_qq",
                "session_id": "group:100",
                "message_type": "group",
                "is_owner": True,
                "images": [],
                "has_image": False,
                "reply": {},
            },
        },
    )

    assert len(gateway.sent_text) == 1
    assert "没收到" in gateway.sent_text[0]["text"]
    assert "引用" in gateway.sent_text[0]["text"]


@pytest.mark.asyncio
async def test_chat_service_recall_notice_updates_pending_qq_buffer(chat_env, monkeypatch):
    captured = []

    def fake_chat(messages, *args, **kwargs):
        captured.append(messages[-1]["content"])
        return "收到"

    monkeypatch.setattr(chat_service_module, "chat_with_ai", fake_chat)
    service = chat_env(gateway=_Gateway())
    service.qq_private_message_buffer.debounce_sec = 0.03
    service.qq_private_message_buffer.short_debounce_sec = 0.03

    first = asyncio.create_task(
        service.process(
            "要撤回",
            ctx={
                "source": "qq_gateway",
                "channel_meta": {
                    "adapter": "napcat_qq",
                    "session_id": "private:10001",
                    "message_type": "private",
                    "message_id": "m1",
                },
            },
        )
    )
    await asyncio.sleep(0.01)
    second = asyncio.create_task(
        service.process(
            "保留",
            ctx={
                "source": "qq_gateway",
                "channel_meta": {
                    "adapter": "napcat_qq",
                    "session_id": "private:10001",
                    "message_type": "private",
                    "message_id": "m2",
                },
            },
        )
    )
    await asyncio.sleep(0.01)
    await service.handle_external_chat_notice(
        {
            "event_type": "qq_private_recall",
            "session_id": "private:10001",
            "metadata": {"message_id": "m1"},
        }
    )
    await asyncio.gather(first, second)

    assert len(captured) == 1
    assert "保留" in captured[0]
    assert "要撤回" not in captured[0]


@pytest.mark.asyncio
async def test_qq_group_messages_do_not_use_private_buffer(chat_env, monkeypatch):
    captured = []

    def fake_chat(messages, *args, **kwargs):
        captured.append(messages[-1]["content"])
        return "群聊回复"

    monkeypatch.setattr(chat_service_module, "chat_with_ai", fake_chat)
    gateway = _Gateway()
    service = chat_env(gateway=gateway)
    service.qq_private_message_buffer.debounce_sec = 0.2
    service.qq_private_message_buffer.short_debounce_sec = 0.2

    await service.process(
        "群聊消息",
        ctx={
            "source": "qq_gateway",
            "channel_meta": {
                "adapter": "napcat_qq",
                "session_id": "group:20001",
                "message_type": "group",
                "message_id": "m1",
            },
        },
    )

    assert captured == ["群聊消息"]
    assert gateway.sent_text[0]["text"] == "群聊回复"
