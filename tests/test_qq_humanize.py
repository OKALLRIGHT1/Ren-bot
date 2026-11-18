import random
from types import SimpleNamespace

import pytest

from integrations.chat_gateway.napcat import NapCatOneBotAdapter
from services.chat_support.gateway_sender import GatewaySender
from services.chat_support.qq_humanize import (
    DEFAULT_PART_GAP_SEC,
    QqHumanizeConfig,
    part_gap_sec,
    typing_delay_sec,
)


def _sender(gateway, **kwargs):
    sleeps = kwargs.pop("sleeps", None)
    if sleeps is None:
        sleeps = []

    async def sleep_fn(delay):
        sleeps.append(delay)

    logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    return GatewaySender(
        chat_gateway_getter=lambda: gateway,
        logger=logger,
        prepare_reply_for_output=lambda text, *a, **kw: text,
        strip_emo_tags=lambda text: text,
        strip_cmd=lambda text: text,
        clean_text_for_tts=lambda text: text,
        sleep_fn=sleep_fn,
        rng=random.Random(0),
        **kwargs,
    ), sleeps


def test_typing_delay_is_clamped_and_scales_with_length():
    cfg = QqHumanizeConfig(
        enabled=True, ms_per_char=100.0, min_delay_sec=0.4, max_delay_sec=2.0
    )
    rng = random.Random(0)
    short = typing_delay_sec("嗯", cfg, rng)
    long = typing_delay_sec("这是一句比较长的回复内容用来撑满上限。", cfg, rng)
    assert 0.4 <= short <= 2.0
    assert 0.4 <= long <= 2.0
    assert long >= short
    assert typing_delay_sec("x", QqHumanizeConfig(enabled=False), rng) == 0.0


def test_part_gap_only_humanizes_private_sessions():
    cfg = QqHumanizeConfig(
        enabled=True, part_gap_min_sec=0.5, part_gap_max_sec=0.5
    )
    rng = random.Random(0)
    assert part_gap_sec(cfg, "private:10001", rng) == 0.5
    assert part_gap_sec(cfg, "group:123", rng) == DEFAULT_PART_GAP_SEC
    assert part_gap_sec(None, "private:10001", rng) == DEFAULT_PART_GAP_SEC


@pytest.mark.asyncio
async def test_send_reply_marks_read_then_types_then_sends():
    calls = []

    class Gateway:
        async def mark_private_read(self, adapter, session_id, message_id=""):
            calls.append(("read", adapter, session_id, message_id))
            return {"ok": True}

        async def set_input_status(self, adapter, session_id, event_type=1):
            calls.append(("typing", adapter, session_id, event_type))
            return {"ok": True}

        async def send_text(self, adapter, session_id, text, **kwargs):
            calls.append(("send", text))
            return {"ok": True}

    sender, sleeps = _sender(
        Gateway(),
        humanize_config=QqHumanizeConfig(
            enabled=True,
            mark_read=True,
            typing=True,
            ms_per_char=80.0,
            min_delay_sec=0.5,
            max_delay_sec=0.5,
        ),
    )
    ctx = {
        "source": "napcat_qq",
        "channel_meta": {
            "adapter": "napcat_qq",
            "session_id": "private:10001",
            "message_id": "42",
        },
    }
    assert await sender.send_reply("你好呀", ctx) is True
    assert calls[0] == ("read", "napcat_qq", "private:10001", "42")
    assert calls[1] == ("typing", "napcat_qq", "private:10001", 1)
    assert calls[2] == ("send", "你好呀")
    assert sleeps and sleeps[0] == 0.5


@pytest.mark.asyncio
async def test_send_reply_skips_humanize_for_group_and_when_disabled():
    calls = []

    class Gateway:
        async def mark_private_read(self, *args, **kwargs):
            calls.append("read")
            return {"ok": True}

        async def set_input_status(self, *args, **kwargs):
            calls.append("typing")
            return {"ok": True}

        async def send_text(self, adapter, session_id, text, **kwargs):
            calls.append("send")
            return {"ok": True}

    sender, sleeps = _sender(
        Gateway(),
        humanize_config=QqHumanizeConfig(enabled=True),
    )
    ctx = {
        "source": "napcat_qq",
        "channel_meta": {"adapter": "napcat_qq", "session_id": "group:99"},
    }
    assert await sender.send_reply("群回复", ctx) is True
    assert calls == ["send"]
    assert sleeps == []

    calls.clear()
    sender, sleeps = _sender(Gateway())
    ctx["channel_meta"]["session_id"] = "private:10001"
    assert await sender.send_reply("私聊", ctx) is True
    assert calls == ["send"]
    assert sleeps == []


@pytest.mark.asyncio
async def test_napcat_humanize_actions_use_onebot_payloads():
    calls = []

    async def sender(action, params, timeout):
        calls.append((action, params))
        return {"ok": True, "transport": "websocket"}

    adapter = NapCatOneBotAdapter(ws_action_sender=sender, api_base="")
    typed = await adapter.set_input_status("private:10001", event_type=1)
    read = await adapter.mark_private_read("private:10001", message_id="88")
    online = await adapter.set_online_status()
    group = await adapter.set_input_status("group:123")

    assert typed["ok"] is True
    assert read["ok"] is True
    assert online["ok"] is True
    assert group["ok"] is False
    assert calls[0] == ("set_input_status", {"user_id": 10001, "event_type": 1})
    assert calls[1] == ("mark_msg_as_read", {"message_id": "88"})
    assert calls[2] == (
        "set_online_status",
        {"status": 10, "ext_status": 0, "battery_status": 0},
    )


@pytest.mark.asyncio
async def test_mark_private_read_falls_back_when_message_id_fails():
    calls = []

    async def sender(action, params, timeout):
        calls.append(action)
        if action == "mark_msg_as_read":
            return {"ok": False, "reason": "bad_id", "transport": "websocket"}
        return {"ok": True, "transport": "websocket"}

    adapter = NapCatOneBotAdapter(ws_action_sender=sender, api_base="")
    result = await adapter.mark_private_read("private:10001", message_id="88")
    assert result["ok"] is True
    assert calls == ["mark_msg_as_read", "mark_private_msg_as_read"]


@pytest.mark.asyncio
async def test_ensure_qq_online_status_sets_once(monkeypatch):
    from core import application as app_mod

    monkeypatch.setattr(app_mod, "NAPCAT_HUMANIZE_SET_ONLINE", True)
    app = app_mod.Live2DApplication.__new__(app_mod.Live2DApplication)
    app.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    app._napcat_enabled = True
    app._qq_online_status_applied = False
    app._napcat_reverse_ws_connected = lambda: True
    calls = []

    async def call_action(action, params=None, timeout=8.0):
        calls.append((action, params))
        return {"ok": True}

    app._call_napcat_action = call_action
    assert await app._ensure_qq_online_status("startup") is True
    assert await app._ensure_qq_online_status("watchdog") is True
    assert calls == [
        ("set_online_status", {"status": 10, "ext_status": 0, "battery_status": 0})
    ]
    assert app._qq_online_status_applied is True
