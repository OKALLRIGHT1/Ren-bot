import pytest

from services.chat_support.flow_result import STATUS_CONTINUE, STATUS_REPLIED
from services.chat_support.natural_chat_pipeline import (
    NaturalChatConfig,
    PlainChatHooks,
    apply_forbidden_phrase_guard,
    assemble_chat_context_messages,
    chat_message_type,
    looks_like_chat_command,
    run_plain_chat,
    should_run_plain_chat,
)
from services.chat_support.tool_flow_service import ReactFirstPassResult


def test_should_run_plain_chat_requires_clean_route():
    assert should_run_plain_chat(
        plain_direct_chat_candidate=True,
        deferred_tool_flow=False,
        delegate_triggers=[],
        search_delegate_requested=False,
    )
    assert not should_run_plain_chat(
        plain_direct_chat_candidate=True,
        deferred_tool_flow=True,
        delegate_triggers=[],
        search_delegate_requested=False,
    )
    assert not should_run_plain_chat(
        plain_direct_chat_candidate=True,
        deferred_tool_flow=False,
        delegate_triggers=["search_web"],
        search_delegate_requested=False,
    )
    assert not should_run_plain_chat(
        plain_direct_chat_candidate=False,
        deferred_tool_flow=False,
        delegate_triggers=[],
        search_delegate_requested=False,
    )


class _Brain:
    def __init__(self):
        self.last_build_kwargs = {}

    def build_prompt(self, user_text, **kwargs):
        self.last_build_kwargs = dict(kwargs)
        parts = kwargs.get("prompt_parts")
        if parts is not None and hasattr(parts, "to_legacy_system_text"):
            system = parts.to_legacy_system_text()
        else:
            system = str(kwargs.get("system_persona") or "")
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]


def _hooks(**overrides):
    async def no_emotion(*args, **kwargs):
        return None

    values = dict(
        brain=_Brain(),
        plugin_manager=object(),
        chat_with_ai=lambda messages, **kwargs: "闲聊回复",
        thought_chat_fn=lambda messages, **kwargs: "{}",
        natural_config=NaturalChatConfig(),
        get_active_character_context=lambda: ("角色", "id", ""),
        consume_character_switch=lambda: False,
        wants_detailed_answer=lambda text: False,
        looks_like_command=looks_like_chat_command,
        clean_retry_text=lambda text: text,
        normalize_qq_reply=lambda text: text,
        build_expression=lambda *args, **kwargs: ("", []),
        get_short_term_messages=lambda ctx=None: [],
        build_user_address_context=lambda ctx=None: "",
        build_external_sender_context=lambda ctx=None: "",
        build_live2d_self_awareness_hint=lambda ctx=None: "",
        build_current_emotion_context=lambda ctx=None: "",
        build_reply_style_context=lambda text, ctx=None: "",
        get_memory_person_id=lambda ctx=None: "owner",
        extract_emo_tag=lambda text: ("", text),
        clean_text_for_tts=lambda text: text,
        strip_internal_tags=lambda text: text,
        strip_cmd_anywhere=lambda text: text,
        strip_emo_tags_anywhere=lambda text: text,
        should_suppress_followup_preface=lambda text: False,
        merge_preface_texts=lambda *parts: "".join(str(p or "") for p in parts if p),
        apply_character_catchphrase=lambda text: text,
        prepare_reply_for_output=lambda text, ctx=None, scene="chat": text,
        infer_reply_emotion_with_llm=no_emotion,
        contains_cmd=lambda text: False,
        looks_like_uncertain_answer=lambda text: False,
        is_searchworthy_question=lambda text: False,
    )
    values.update(overrides)
    return PlainChatHooks(**values)


def _stub_thought_and_guard(monkeypatch):
    async def no_thought(**kwargs):
        return "", {"character_thought_used": False}

    async def passthrough_guard(*, text, user_text, ctx, **kwargs):
        return text, {
            "forbidden_phrase_hit": False,
            "forbidden_phrases": [],
            "forbidden_retries": 0,
        }

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.build_character_thought_block",
        no_thought,
    )
    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.apply_forbidden_phrase_guard",
        passthrough_guard,
    )


def test_looks_like_chat_command_and_message_type():
    assert looks_like_chat_command("/help")
    assert looks_like_chat_command("！切换")
    assert not looks_like_chat_command("今天天气不错")
    assert chat_message_type({"channel_meta": {"message_type": "group"}}) == "group"
    assert chat_message_type(None) == "private"


@pytest.mark.asyncio
async def test_forbidden_guard_skips_outside_scope():
    cfg = NaturalChatConfig(character_thought_scope="desktop_only")

    async def fail_retry(**kwargs):
        raise AssertionError("retry should not run outside scope")

    text, trace = await apply_forbidden_phrase_guard(
        text="切换得好硬",
        user_text="hi",
        ctx={"source": "napcat_qq", "channel_meta": {"message_type": "group"}},
        config=cfg,
        retry_reply=fail_retry,
    )
    assert text == "切换得好硬"
    assert trace["forbidden_skipped"] == "scope_miss"


@pytest.mark.asyncio
async def test_run_plain_chat_replies_without_tools(monkeypatch):
    _stub_thought_and_guard(monkeypatch)

    async def fake_first_pass(**kwargs):
        return ReactFirstPassResult(
            reply="闲聊回复",
            context_messages=list(kwargs.get("context_messages") or []),
        )

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_react_first_pass",
        fake_first_pass,
    )
    brain = _Brain()
    outcome = await run_plain_chat(
        user_text="今天天气不错",
        ctx={"source": "desktop"},
        hooks=_hooks(brain=brain),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
        invocation_mode="cmd",
    )
    assert outcome.flow.status == STATUS_REPLIED
    assert outcome.flow.reply_text == "闲聊回复"
    assert outcome.flow.handled is True
    assert brain.last_build_kwargs["tool_intent"] == []


@pytest.mark.asyncio
async def test_run_plain_chat_continues_when_first_pass_triggers_tools(monkeypatch):
    _stub_thought_and_guard(monkeypatch)

    async def fake_first_pass(**kwargs):
        return ReactFirstPassResult(
            reply="[CMD: search | cat]",
            triggered=True,
            tool_results=["猫是动物"],
            used_triggers=["search"],
            context_messages=list(kwargs.get("context_messages") or []),
        )

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_react_first_pass",
        fake_first_pass,
    )
    outcome = await run_plain_chat(
        user_text="猫是什么",
        ctx={"source": "desktop"},
        hooks=_hooks(),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
    )
    assert outcome.flow.status == STATUS_CONTINUE
    assert outcome.react_first_pass.triggered is True
    assert outcome.react_first_pass.used_triggers == ["search"]


@pytest.mark.asyncio
async def test_run_plain_chat_continues_when_forced_search_needed(monkeypatch):
    _stub_thought_and_guard(monkeypatch)

    async def fake_first_pass(**kwargs):
        return ReactFirstPassResult(
            reply="我不太确定",
            context_messages=list(kwargs.get("context_messages") or []),
        )

    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.run_react_first_pass",
        fake_first_pass,
    )
    monkeypatch.setattr(
        "services.chat_support.natural_chat_pipeline.choose_forced_search_query",
        lambda **kwargs: "今天油价",
    )
    outcome = await run_plain_chat(
        user_text="今天油价多少",
        ctx={"source": "desktop"},
        hooks=_hooks(),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        source_key="desktop",
    )
    assert outcome.flow.status == STATUS_CONTINUE
    assert outcome.react_first_pass.triggered is False


@pytest.mark.asyncio
async def test_assemble_chat_context_messages_keeps_tool_and_codex_sections(
    monkeypatch,
):
    _stub_thought_and_guard(monkeypatch)
    brain = _Brain()
    assembled = await assemble_chat_context_messages(
        user_text="查一下猫",
        ctx={"source": "desktop"},
        hooks=_hooks(brain=brain),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        extra_runtime_sections=("【代码助手模式】测试",),
        tool_sections=("【可用工具能力】search",),
        tool_intent=["search"],
        invocation_mode="dual",
    )
    system = assembled.context_messages[0]["content"]
    assert "【可用工具能力】search" in system
    assert "【代码助手模式】测试" in system
    assert brain.last_build_kwargs["tool_intent"] == ["search"]
    assert brain.last_build_kwargs["session_id"] == "desktop"
    assert brain.last_build_kwargs["memory_session_id"] == "owner_shared"


@pytest.mark.asyncio
async def test_assemble_chat_context_messages_adds_capability_unavailable(
    monkeypatch,
):
    _stub_thought_and_guard(monkeypatch)
    assembled = await assemble_chat_context_messages(
        user_text="查天气",
        ctx={"source": "desktop"},
        hooks=_hooks(),
        conversation_session_id="desktop",
        memory_session_id="owner_shared",
        route_reason="capability_unavailable:weather",
        capability_match_reason="plugin disabled",
    )
    system = assembled.context_messages[0]["content"]
    assert "capability_unavailable:weather" in system
    assert "plugin disabled" in system
    assert "不要假装已经查询成功" in system
