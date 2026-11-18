import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from services.chat_support.group_reply_planner import (
    GroupReplyPlanner,
    GroupReplyPlannerConfig,
    build_planner_prompt,
    config_from_mapping,
    is_low_content,
    mention_names,
    parse_yes_no,
    talk_stance,
)


def _planner() -> GroupReplyPlanner:
    return GroupReplyPlanner(
        GroupReplyPlannerConfig(
            talk_value=0.35,
            backoff_base_sec=15,
            backoff_cap_sec=120,
            backoff_start_count=2,
            backoff_bypass_count=6,
        )
    )


def _group(text="今天吃什么", *, at=False, reply_user="", sender="小明"):
    meta = {
        "message_type": "group",
        "session_id": "group:100",
        "group_id": "100",
        "sender_name": sender,
        "self_id": "42",
        "targets_self": at,
    }
    if reply_user:
        meta["reply"] = {"user_id": reply_user, "text": "之前那句"}
    return {
        "source": "napcat_qq",
        "channel_meta": meta,
        "user_text": text,
    }


class _Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


class _Model:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.answers:
            return "NO"
        return self.answers.pop(0)


@pytest.mark.asyncio
async def test_private_and_desktop_skip_the_planner():
    planner = _planner()
    model = _Model(["YES"])
    private = _group()
    private["channel_meta"]["message_type"] = "private"
    desktop = {"source": "desktop", "channel_meta": {"message_type": "group"}}
    for ctx in (private, desktop):
        decision = await planner.decide("你好呀", ctx, chat_with_ai=model)
        assert decision.applicable is False
        assert decision.reply is True
    assert model.calls == []


@pytest.mark.asyncio
async def test_at_name_reply_and_command_skip_the_model():
    planner = _planner()
    model = _Model(["NO"])
    cases = [
        (_group(at=True), "at"),
        (_group("高松灯在吗"), "name"),
        (_group(reply_user="42"), "reply_to_self"),
        (_group("/画图 一只猫"), "command"),
        (_group("来一首歌"), "command"),
    ]
    for ctx, reason in cases:
        decision = await planner.decide(
            ctx["user_text"],
            ctx,
            mention_names=["高松灯", "小灯"],
            keywords=["来一首"],
            chat_with_ai=model,
        )
        assert decision.reply is True
        assert decision.reason == reason
        assert decision.used_model is False
    assert model.calls == []


@pytest.mark.asyncio
async def test_ordinary_group_line_uses_gatekeeper_model():
    planner = _planner()
    model = _Model(["NO"])
    decision = await planner.decide(
        "你们晚上开黑吗",
        _group("你们晚上开黑吗"),
        mention_names=["高松灯"],
        chat_with_ai=model,
    )
    assert decision.applicable is True
    assert decision.reply is False
    assert decision.reason == "model_no"
    assert decision.used_model is True
    call = model.calls[0]
    assert call["task_type"] == "gatekeeper"
    assert call["caller"] == "group_reply_planner"
    assert call["max_tokens"] == 24
    prompt = call["messages"][0]["content"]
    assert "很安静" in prompt
    assert "高松灯" in prompt
    assert "人设" not in prompt


@pytest.mark.asyncio
async def test_uncertain_and_model_error_stay_quiet():
    planner = _planner()
    model = _Model(["也许吧"])
    decision = await planner.decide("随便聊聊", _group("随便聊聊"), chat_with_ai=model)
    assert decision.reply is False
    assert decision.reason == "uncertain"

    def boom(messages, **kwargs):
        raise RuntimeError("down")

    decision = await planner.decide("再来一句", _group("再来一句"), chat_with_ai=boom)
    assert decision.reply is False
    assert decision.reason == "model_error"
    assert decision.used_model is False


@pytest.mark.asyncio
async def test_backoff_skips_model_until_messages_pile_up():
    planner = _planner()
    model = _Model(["NO", "NO", "YES"])
    clock = _Clock()
    ctx = _group()
    first = await planner.decide("第一句闲聊", ctx, chat_with_ai=model, now=clock)
    second = await planner.decide("第二句闲聊", ctx, chat_with_ai=model, now=clock)
    assert first.reason == "model_no"
    assert second.reason == "model_no"
    assert len(model.calls) == 2

    clock.now += 5
    skipped = await planner.decide("第三句闲聊", ctx, chat_with_ai=model, now=clock)
    assert skipped.reason == "backoff"
    assert skipped.reply is False
    assert len(model.calls) == 2

    for index in range(4):
        clock.now += 0.1
        held = await planner.decide(f"继续{index}", ctx, chat_with_ai=model, now=clock)
        assert held.reason == "backoff"
    clock.now += 0.1
    resumed = await planner.decide("堆够了", ctx, chat_with_ai=model, now=clock)
    assert resumed.reason == "model_yes"
    assert resumed.reply is True
    assert len(model.calls) == 3
    assert "【最近群聊】" in resumed.context_block


@pytest.mark.asyncio
async def test_her_reply_clears_backoff_and_context_is_for_the_prompt():
    planner = _planner()
    model = _Model(["NO", "NO", "YES"])
    clock = _Clock()
    ctx = _group()
    await planner.decide("闲聊一", ctx, chat_with_ai=model, now=clock)
    await planner.decide("闲聊二", ctx, chat_with_ai=model, now=clock)
    planner.note_assistant(ctx, "我在。")
    planner.note_assistant(ctx, "我在。")
    assert sum(1 for line in planner._state("group:100").lines if line.text == "我在。") == 1
    clock.now += 1
    decision = await planner.decide(
        "那你觉得呢",
        ctx,
        chat_with_ai=model,
        now=clock,
    )
    assert decision.reason == "model_yes"
    assert "她：我在。" in decision.context_block
    assert decision.context_block.startswith("【最近群聊】")


def test_quiet_stance_and_name_filter():
    assert "很安静" in talk_stance(0.35)
    assert "偏安静" in talk_stance(0.6)
    assert "可以正常接话" in talk_stance(0.9)
    assert parse_yes_no("YES") is True
    assert parse_yes_no("NO。") is False
    assert parse_yes_no("也许") is None
    assert is_low_content("嗯") is True
    assert is_low_content("。。。") is True
    assert is_low_content("今晚呢") is False
    assert mention_names({"name": "高松灯", "aliases": ["小灯", "灯"]}) == ["高松灯", "小灯"]
    prompt = build_planner_prompt(
        names=["高松灯"],
        talk_value=0.35,
        prior=[],
        sender="小明",
        text="在吗",
    )
    assert "拿不准就输出 NO" in prompt


def test_planner_config_stays_quiet_and_group_switch_stays_off():
    from config import (
        GROUP_REPLY_PLANNER_ENABLED,
        GROUP_REPLY_TALK_VALUE,
        NAPCAT_ALLOW_GROUP,
        NAPCAT_GROUP_REQUIRE_AT,
    )

    planner = GroupReplyPlanner.from_config()
    assert GROUP_REPLY_PLANNER_ENABLED is True
    assert GROUP_REPLY_TALK_VALUE == pytest.approx(0.35)
    assert planner.config.enabled is True
    assert planner.config.talk_value == pytest.approx(0.35)
    assert NAPCAT_ALLOW_GROUP is False
    assert NAPCAT_GROUP_REQUIRE_AT is True


def test_runtime_mapping_overrides_only_written_keys():
    config = config_from_mapping(
        {
            "group_reply_planner_enabled": False,
            "group_reply_talk_value": 0.9,
            "group_reply_backoff_base_sec": 30,
        }
    )
    assert config.enabled is False
    assert config.talk_value == pytest.approx(0.9)
    assert config.backoff_base_sec == pytest.approx(30)
    assert config.backoff_cap_sec == pytest.approx(120)
    assert config.context_lines == 8


@pytest.mark.asyncio
async def test_loader_applies_on_the_next_decision():
    holder = {"talk": 0.35}

    def loader():
        return GroupReplyPlannerConfig(talk_value=holder["talk"])

    planner = GroupReplyPlanner(load_config=loader)
    model = _Model(["NO", "YES"])

    async def ask(text):
        return await planner.decide(
            text,
            _group(text),
            chat_with_ai=model,
        )

    first = await ask("晚上吃什么")
    assert first.reply is False
    assert "很安静" in model.calls[0]["messages"][0]["content"]
    holder["talk"] = 0.9
    second = await ask("这个话题你怎么看")
    assert second.reply is True
    assert "可以正常接话" in model.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_non_group_service_paths_skip_group_preparation():
    from services.chat_service import ChatService

    service = Mock(spec=ChatService)
    service.gateway_sender = Mock(send_reply=AsyncMock(return_value=True))
    private = _group()
    private["channel_meta"]["message_type"] = "private"
    for ctx in (private, {"source": "desktop"}):
        assert not await ChatService._maybe_hold_group_chatter(service, "hi", ctx, {})
        assert await ChatService._send_gateway_reply(service, "hi", ctx)
    service._group_reply_planner.assert_not_called()
    service._get_active_character_profile.assert_not_called()
    service._plugin_group_keywords.assert_not_called()


@pytest.mark.asyncio
async def test_group_snapshot_reused_until_successful_save(tmp_path, monkeypatch):
    from modules import runtime_settings as settings

    path = tmp_path / "runtime_settings.json"
    monkeypatch.setattr(settings, "RUNTIME_SETTINGS_PATH", path)
    assert settings.save_runtime_settings({"group_reply_planner_enabled": True})
    read = Mock(wraps=settings.load_runtime_settings_strict)
    monkeypatch.setattr(settings, "load_runtime_settings_strict", read)
    planner = GroupReplyPlanner.from_config()
    ctx = _group(at=True)
    for _ in range(5):
        assert (await planner.decide("hi", ctx)).reason == "at"
        planner.note_assistant(ctx, "yes")
    planner._load_config.next_check = 0
    assert (await planner.decide("hi", ctx)).reason == "at"
    assert read.call_count == 1
    assert settings.save_runtime_settings({"group_reply_planner_enabled": False})
    assert (await planner.decide("hi", ctx)).reason == "disabled"
    with monkeypatch.context() as patch:
        patch.setattr(settings.os, "replace", Mock(side_effect=OSError))
        assert not settings.save_runtime_settings({"group_reply_planner_enabled": True})
    assert (await planner.decide("hi", ctx)).reason == "disabled"
    assert read.call_count == 2

    # Simulate the next external-file poll without waiting on the wall clock.
    planner._load_config.next_check = 0
    path.write_text("{", encoding="utf-8")
    assert (await planner.decide("hi", ctx)).reason == "disabled"
    planner._load_config.next_check = 0
    path.write_text('{"group_reply_planner_enabled":true}', encoding="utf-8")
    assert (await planner.decide("hi", ctx)).reason == "at"


def _named_group(name):
    ctx = _group()
    ctx["channel_meta"]["session_id"] = name
    return ctx


def test_idle_cache_evicts_least_recent_group():
    planner = GroupReplyPlanner(max_sessions=2)
    for name in ("a", "b", "a", "c"):
        planner.note_assistant(_named_group(name), name)
    assert list(planner._sessions) == ["a", "c"]
    for i in range(20):
        planner.note_assistant(_named_group(str(i)), "hello")
    assert list(planner._sessions) == ["18", "19"]


@pytest.mark.asyncio
async def test_idle_timer_releases_quiet_group_without_more_traffic():
    config = GroupReplyPlannerConfig(backoff_base_sec=0, backoff_cap_sec=0)
    planner = GroupReplyPlanner(config, idle_ttl_sec=0.01)
    planner.note_assistant(_group(), "hello")
    await asyncio.sleep(0.05)
    assert not planner._sessions
    assert planner._expiry_timer is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_busy_groups_survive_cache_pressure_and_release_on_exit(cancel):
    planner = GroupReplyPlanner(max_sessions=1)
    ready = asyncio.Queue()

    async def ask(messages, **kwargs):
        gate = asyncio.Event()
        ready.put_nowait(gate)
        await gate.wait()
        return "NO"

    tasks = []
    gates = []
    for name in ("a", "b"):
        task = asyncio.create_task(planner.decide("闲聊", _named_group(name), chat_with_ai=ask))
        tasks.append(task)
        gates.append(await ready.get())
    planner.note_assistant(_named_group("c"), "hello")
    assert (await planner.decide("又一句", _named_group("a"))).reason == "in_flight"
    if cancel:
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
    else:
        gates[0].set()
        assert (await tasks[0]).reason == "model_no"
    gates[1].set()
    await tasks[1]
    assert not planner._in_flight
    assert list(planner._sessions) == ["b"]
    assert planner._sessions["b"].silence_streak == 1
