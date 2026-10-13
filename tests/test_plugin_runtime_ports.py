from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.plugin_ports import (
    AppPort,
    ChatGatewayPort,
    TtsPort,
    resolve_app,
    resolve_gateway,
    resolve_tts,
)


class FakeChat:
    def __init__(self):
        self.chat_gateway = object()
        self.replies = []
        self.tts = None
        self.music = []
        self.presenter = self

    async def _send_gateway_reply(self, text, ctx, emotion=None):
        self.replies.append(("text", text, emotion, ctx))
        return True

    async def _send_gateway_image_reply(self, path, ctx, caption=""):
        self.replies.append(("image", path, caption, ctx))
        return True

    def set_tts_enabled(self, enabled):
        self.tts = bool(enabled)

    async def handle_music_event(self, title="", artist=""):
        self.music.append((title, artist))


@pytest.mark.asyncio
async def test_gateway_port_forwards_text_and_image():
    chat = FakeChat()
    port = ChatGatewayPort.from_chat_service(chat)
    assert not hasattr(port, "_chat_service")
    assert port.available() is True
    await port.send_text("hi", {"source": "qq_gateway"}, emotion="happy")
    await port.send_image("a.png", {"source": "qq_gateway"}, caption="")
    assert chat.replies[0][0] == "text"
    assert chat.replies[1][0] == "image"


def test_resolve_gateway_prefers_ctx_port():
    sentinel = object()
    assert resolve_gateway({"gateway": sentinel}) is sentinel
    chat = FakeChat()
    fallback = resolve_gateway({"chat_service": chat})
    assert isinstance(fallback, ChatGatewayPort)
    assert not hasattr(fallback, "_chat_service")
    assert resolve_gateway({}) is None


def test_tts_port_and_resolve():
    chat = FakeChat()
    port = TtsPort.from_chat_service(chat)
    assert not hasattr(port, "_chat_service")
    assert port.set_enabled(False) is True
    assert chat.tts is False
    assert resolve_tts({"tts": port}) is port
    assert isinstance(resolve_tts({"chat_service": chat}), TtsPort)


def test_second_wave_schemas_encode_old_args():
    from plugins.timer.plugin import Plugin as Timer
    from plugins.task_manager.plugin import Plugin as Tasks
    from plugins.qq_draw.plugin import Plugin as Draw

    timer = Timer()
    assert timer.get_tool_schema()["name"] == "timer"
    assert timer.format_invocation({"minutes": 25}) == "25"
    tasks = Tasks()
    assert tasks.get_tool_schema()["name"] == "task_manager"
    assert tasks.format_invocation({"action": "add_task", "query": "买牛奶"}) == "add_task 买牛奶"
    draw = Draw()
    assert draw.get_tool_schema()["name"] == "qq_draw"
    assert draw.format_invocation({"prompt": "一只猫"}).startswith("/画图")


def test_third_wave_schemas_encode_old_args():
    from plugins.music_player.plugin import Plugin as Music
    from plugins.local_knowledge.plugin import Plugin as Knowledge
    from plugins.diary_export.plugin import Plugin as Diary

    music = Music()
    assert music.get_tool_schema()["name"] == "play_music"
    assert music.format_invocation({"query": "春日影"}) == "春日影"
    assert music.format_invocation({"query": "随机"}) == ""
    knowledge = Knowledge()
    assert knowledge.get_tool_schema()["name"] == "knowledge_base"
    assert knowledge.format_invocation({"action": "search", "query": "人设"}) == "search ||| 人设"
    diary = Diary()
    assert diary.get_tool_schema()["name"] == "diary_manager"
    assert diary.format_invocation({"query": "昨天"}) == "昨天"


def test_plugins_do_not_read_ctx_chat_service():
    pattern = re.compile(
        r"""ctx(?:\s*\.\s*get\(\s*['\"]chat_service['\"]|\s*\[\s*['\"]chat_service['\"])"""
    )
    hits = []
    for path in Path("plugins").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(path.as_posix())
    assert hits == []


def test_skill_runtime_reads_ctx_skills_without_chat_service():
    from plugins.skill_runtime.plugin import Plugin as Skills

    plugin = Skills()
    manager = object()
    assert plugin._get_manager({"skills": manager}) is manager
    assert plugin._get_manager({}) is None


@pytest.mark.asyncio
async def test_meme_auto_send_uses_gateway_port():
    from plugins.meme_pack.plugin import Plugin as Meme

    plugin = Meme()
    chat = FakeChat()
    asset = type("Asset", (), {"file_path": "a.png"})()

    async def fake_select(**kwargs):
        return {
            "asset": asset,
            "ctx": kwargs.get("ctx") or {},
            "session_id": "s1",
            "now": 1,
            "reason": "test",
        }

    plugin._select_auto_meme_asset = fake_select
    plugin._last_auto_sent = {}
    plugin._store_obj = lambda: type("Store", (), {"mark_used": staticmethod(lambda *a, **k: None)})()
    sent = await plugin.maybe_send_auto_meme(
        user_text="hi",
        reply_text="hey",
        ctx={"gateway": ChatGatewayPort.from_chat_service(chat), "source": "qq_gateway"},
    )
    assert sent is True
    assert chat.replies[0][0] == "image"


@pytest.mark.asyncio
async def test_app_port_music_event_and_resolve():
    chat = FakeChat()
    port = AppPort.from_chat_service(chat)
    assert not hasattr(port, "_chat_service")
    assert port.handle_music_event(title="song", artist="a") is True
    assert resolve_app({"app": port}) is port
    assert isinstance(resolve_app({"chat_service": chat}), AppPort)
