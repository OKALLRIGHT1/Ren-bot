import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from modules.plugin_model_gateway import PluginModelCallResult
from plugins.meme_pack.plugin import Plugin


@dataclass
class _Asset:
    id: int = 1
    file_path: str = "plugins/meme_pack/assets/rest.png"
    tags: list[str] = None
    description: str = "久坐提醒，适合提醒休息"
    emotion: str = "提醒"
    usage_count: int = 0

    def __post_init__(self):
        if self.tags is None:
            self.tags = ["久坐", "休息"]


class _FakeStore:
    def __init__(self):
        self.asset = _Asset()
        self.mark_used_calls = 0

    def rank_assets(self, *, query, emotion, limit):
        return [(1.0, self.asset)]

    def pick(self, *, query, emotion, limit):
        return self.asset

    def mark_used(self, *args, **kwargs):
        self.mark_used_calls += 1


class _RankedStore(_FakeStore):
    def __init__(self):
        super().__init__()
        self.best = _Asset(
            id=6,
            file_path="plugins/meme_pack/assets/stretch.jpg",
            tags=["久坐", "休息", "提醒", "伸展", "护眼"],
            description="久坐提醒弹窗使用，适合提醒用户起来活动、伸展、休息一下",
            emotion="提醒",
        )
        self.other = _Asset(
            id=1,
            file_path="plugins/meme_pack/assets/confused.jpg",
            tags=["疑惑"],
            description="适合表达困惑",
            emotion="疑惑",
        )

    def rank_assets(self, *, query, emotion, limit):
        return [(32.0, self.best), (0.2, self.other)]

    def pick(self, *, query, emotion, limit):
        return self.other


class _FakeChatService:
    def __init__(self):
        self.sent = False

    async def _send_gateway_image_reply(self, image_path, ctx, caption=""):
        self.sent = True
        return True


class _FakeModelGateway:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def invoke_text(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return PluginModelCallResult(ok=True, text=self.text, model_id="chat-a")


def test_meme_pack_config_selects_main_models():
    config = json.loads(
        Path("plugins/meme_pack/config.json").read_text(encoding="utf-8")
    )
    setting = config["settings"]["model_queue"]

    assert setting["type"] == "model_queue"
    assert setting["purpose"] == ["chat"]


def test_meme_selector_uses_selected_main_model():
    plugin = Plugin()
    plugin.settings = {"model_queue": {"default": ["chat-a"]}}
    gateway = _FakeModelGateway(
        '{"send":true,"meme_id":1,"emotion":"提醒","reason":"fit"}'
    )

    asset, reason = asyncio.run(
        plugin._select_auto_meme_with_llm(
            candidates=[(1.0, _Asset())],
            user_text="坐太久了",
            reply_text="休息一下吧",
            inferred_emotion="提醒",
            ctx={"model_gateway": gateway},
        )
    )

    assert asset.id == 1
    assert reason == "llm:提醒:fit"
    assert gateway.calls[0][1]["selected_ids"] == ["chat-a"]


def test_select_meme_image_path_does_not_send_gateway_image():
    plugin = Plugin()
    store = _FakeStore()
    plugin._store = store
    plugin.settings = {
        "llm_selector_enabled": False,
        "auto_probability": 1.0,
        "max_candidates": 8,
    }
    chat_service = _FakeChatService()

    result = asyncio.run(
        plugin.select_meme_image_path(
            user_text="已经坐太久了",
            reply_text="起来活动一下吧",
            emotion="提醒",
            ctx={"source": "desktop"},
            mark_used=False,
        )
    )

    assert result["image_path"] == "plugins/meme_pack/assets/rest.png"
    assert result["reason"].startswith("auto:")
    assert chat_service.sent is False
    assert store.mark_used_calls == 0


def test_select_qq_meme_image_path_uses_local_tagged_database_without_sending():
    plugin = Plugin()
    store = _FakeStore()
    plugin._store = store
    plugin.settings = {
        "llm_selector_enabled": False,
        "auto_probability": 1.0,
        "max_candidates": 8,
    }

    result = asyncio.run(
        plugin.select_qq_meme_image_path(
            user_text="久坐提醒",
            reply_text="起来活动一下吧",
            emotion="提醒",
            ctx={"source": "desktop", "reason": "sedentary"},
            mark_used=False,
        )
    )

    assert result["image_path"] == "plugins/meme_pack/assets/rest.png"
    assert result["asset_id"] == 1
    assert store.mark_used_calls == 0


def test_select_qq_meme_image_path_force_picks_even_when_auto_probability_zero():
    plugin = Plugin()
    store = _FakeStore()
    plugin._store = store
    plugin.settings = {
        "llm_selector_enabled": True,
        "auto_probability": 0.0,
        "max_candidates": 8,
    }

    result = asyncio.run(
        plugin.select_qq_meme_image_path(
            user_text="久坐提醒",
            reply_text="起来活动一下吧",
            emotion="提醒",
            ctx={"source": "desktop", "reason": "sedentary"},
        )
    )

    assert result["image_path"] == "plugins/meme_pack/assets/rest.png"
    assert result["asset_id"] == 1


def test_select_qq_meme_image_path_force_pick_uses_highest_ranked_asset():
    plugin = Plugin()
    store = _RankedStore()
    plugin._store = store
    plugin.settings = {
        "llm_selector_enabled": True,
        "auto_probability": 1.0,
        "max_candidates": 8,
    }

    result = asyncio.run(
        plugin.select_qq_meme_image_path(
            user_text="久坐提醒：连续使用电脑 60 分钟",
            reply_text="起来活动一下吧",
            emotion="提醒",
            ctx={"source": "desktop", "reason": "sedentary"},
        )
    )

    assert result["image_path"] == "plugins/meme_pack/assets/stretch.jpg"
    assert result["asset_id"] == 6
    assert result["reason"] == "force_pick:提醒"
