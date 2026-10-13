import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

if not (Path(__file__).parents[1] / "plugins" / "Isuzu_news" / "magic_daily").is_dir():
    pytest.skip("local Isuzu_news plugin is not part of the public repository", allow_module_level=True)

from plugins.Isuzu_news.magic_daily.constants import EMPTY_HITOKOTO
from plugins.Isuzu_news.magic_daily.plugin_impl import Plugin
from plugins.Isuzu_news.magic_daily.api.bgm import BGMAPI


SAMPLE_ANIME = [{"title": "测试番", "image": "https://example.test/a.jpg"}]
SAMPLE_HOTWORDS = ["测试热点"]
SAMPLE_IT_NEWS = ["测试 IT"]


class _FakeBGMAPI:
    calls = 0

    def __init__(self, session=None):
        pass

    async def get_calendar_async(self):
        type(self).calls += 1
        return []

    def parse_today_anime(self, api_data, max_count=4):
        return SAMPLE_ANIME[:max_count]


class _FakeBilibiliAPI:
    calls = 0

    def __init__(self, session=None):
        pass

    async def get_hotwords_data_async(self):
        type(self).calls += 1
        return {"code": 0, "list": [{"show_name": "测试热点"}]}

    def parse_hotwords_data(self, api_data, max_count=4):
        return SAMPLE_HOTWORDS[:max_count]


class _FakeITHomeRSS:
    calls = 0

    def __init__(self, session=None):
        pass

    async def get_rss_async(self):
        type(self).calls += 1
        return object()

    def parse_news(self, rss_root, max_count=5):
        return SAMPLE_IT_NEWS[:max_count]


class _FakeSession:
    closed = False


def test_bgm_parse_does_not_invent_anime():
    api = BGMAPI()

    assert api.parse_today_anime([], max_count=2, use_fallback=False) == []
    assert api.parse_today_anime([], max_count=2) == []


class _CapturingInfoSourceService:
    init_kwargs = None
    calls = []

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs

    async def fetch_daily_bundle(
        self,
        max_anime_count,
        max_news_count,
        max_hotword_count,
        max_holiday_count,
    ):
        type(self).calls.append(
            (max_anime_count, max_news_count, max_hotword_count, max_holiday_count)
        )
        provider = type(self).init_kwargs.get("alapi_provider")
        assert provider is not None
        assert "zaobao" in provider.list_capabilities()
        assert "hitokoto" in provider.list_capabilities()
        assert "holiday" in provider.list_capabilities()
        fetchers = type(self).init_kwargs.get("builtin_fetchers") or {}
        assert set(fetchers) == {"today_anime", "bili_hot", "it_news"}
        return {
            "anime_list": [{"title": "番剧"}],
            "bili_hotwords": ["热点"],
            "hitokoto_data": {"hitokoto": "统一一言", "from": "信息源"},
            "moyu_list": [{"name": "假期", "days_left": 1}],
            "world_news": ["统一早报"],
            "it_news": ["IT"],
        }


def test_fetch_all_data_retries_only_defaulted_source():
    from services.info_sources.service import InfoSourceService

    calls = {"anime": 0, "world": 0}

    async def fetch_today_anime(limit=4):
        calls["anime"] += 1
        return SAMPLE_ANIME[:limit]

    async def fetch_world_news(limit=5):
        calls["world"] += 1
        if calls["world"] == 1:
            return []
        return ["重试后获取到的世界新闻"]

    service = InfoSourceService(
        token_getter=lambda: "",
        builtin_fetchers={
            "today_anime": fetch_today_anime,
            "world_news": fetch_world_news,
        },
        daily_fallbacks={
            "today_anime": [],
            "bili_hot": [],
            "hitokoto": dict(EMPTY_HITOKOTO),
            "moyu": [],
            "world_news": [],
            "it_news": [],
        },
    )

    bundle = asyncio.run(
        service.fetch_daily_bundle(
            max_anime_count=4,
            max_news_count=5,
            max_hotword_count=4,
            max_holiday_count=3,
        )
    )

    assert bundle["world_news"] == ["重试后获取到的世界新闻"]
    assert calls == {"anime": 1, "world": 2}


def test_fetch_all_data_uses_configured_info_sources_for_alapi_daily_sources(tmp_path):
    _CapturingInfoSourceService.init_kwargs = None
    _CapturingInfoSourceService.calls = []

    endpoint_dir = tmp_path / "data" / "info_sources" / "alapi"
    endpoint_dir.mkdir(parents=True)
    for endpoint_id, path in {
        "zaobao": "/api/zaobao",
        "hitokoto": "/api/hitokoto",
        "holiday": "/api/holiday",
    }.items():
        (endpoint_dir / f"{endpoint_id}.json").write_text(
            (
                "{"
                f"\"id\":\"{endpoint_id}\","
                f"\"name\":\"{endpoint_id}\","
                "\"method\":\"GET\","
                f"\"path\":\"{path}\","
                "\"params\":{}"
                "}"
            ),
            encoding="utf-8",
        )

    plugin = Plugin()
    plugin._root_dir = tmp_path
    plugin._http_session = _FakeSession()
    plugin._settings["api_token"] = "token"

    with (
        patch("plugins.Isuzu_news.magic_daily.plugin_impl.aiohttp", object()),
        patch("plugins.Isuzu_news.magic_daily.plugin_impl.InfoSourceService", _CapturingInfoSourceService),
    ):
        result = asyncio.run(
            plugin._fetch_all_data(
                max_anime_count=1,
                max_news_count=2,
                max_hotword_count=3,
                max_holiday_count=4,
            )
        )

    assert result == (
        [{"title": "番剧"}],
        ["热点"],
        {"hitokoto": "统一一言", "from": "信息源"},
        [{"name": "假期", "days_left": 1}],
        ["统一早报"],
        ["IT"],
    )
    assert _CapturingInfoSourceService.calls == [(1, 2, 3, 4)]
    assert Path(_CapturingInfoSourceService.init_kwargs["endpoint_dir"]) == endpoint_dir
    assert _CapturingInfoSourceService.init_kwargs["daily_fallbacks"]["today_anime"] == []


def test_build_report_data_filters_distant_holidays(monkeypatch):
    plugin = Plugin()

    async def fake_fetch_all_data(
        max_anime_count,
        max_news_count,
        max_hotword_count,
        max_holiday_count,
    ):
        return (
            [],
            [],
            dict(EMPTY_HITOKOTO),
            [
                {"name": "周末", "days_left": 2},
                {"name": "中秋节", "days_left": 80},
                {"name": "国庆节", "days_left": "85"},
                {"name": "无效节日", "days_left": "unknown"},
            ],
            [],
            [],
        )

    monkeypatch.setattr(plugin, "_fetch_all_data", fake_fetch_all_data)

    report_data = asyncio.run(plugin._build_report_data())

    assert report_data["moyu_list"] == [{"name": "周末", "days_left": 2}]
    assert "presentation" in report_data


def test_presentation_follows_character_and_skips_empty_sections(monkeypatch):
    plugin = Plugin()
    plugin._settings["title"] = ""
    plugin._settings["greeting_style"] = "character"
    plugin._settings["enable_ai_greeting"] = False
    plugin._settings["sections"] = ["moyu", "world_news"]

    monkeypatch.setattr(
        plugin,
        "_active_character",
        lambda: {"name": "高松灯", "prompt": "你是高松灯"},
    )
    monkeypatch.setattr(plugin, "_character_face_path", lambda _character: "")

    report = {
        "date_info": {"date_str": "2026-10-13", "week_cn": "星期一"},
        "anime_list": [{"title": "不该出现"}],
        "bili_hotwords": ["不该出现"],
        "hitokoto_data": dict(EMPTY_HITOKOTO),
        "moyu_list": [{"name": "周末", "days_left": 2}],
        "world_news": ["一条新闻"],
        "it_news": ["不该出现"],
        "presentation": plugin._resolve_presentation(),
    }

    text = plugin._format_text_report(report)
    assert "高松灯日报" in text
    assert "摸鱼日历" in text
    assert "一条新闻" in text
    assert "今日新番" not in text
    assert "B站热点" not in text
    assert "魔法" not in text

    greeting = plugin._get_default_greeting(report)
    assert greeting.startswith("📰 高松灯：")
    assert "魔法" not in greeting


def test_custom_greeting_uses_placeholders():
    plugin = Plugin()
    plugin._settings["greeting_style"] = "custom"
    plugin._settings["custom_greeting"] = "{name}送来了{title}"
    plugin._settings["title"] = "晚刊"

    presentation = {
        "character_name": "高松灯",
        "title": "晚刊",
        "greeting_style": "custom",
        "custom_greeting": "{name}送来了{title}",
    }
    greeting = asyncio.run(
        plugin._generate_greeting_text({"presentation": presentation, "moyu_list": []})
    )
    assert greeting == "📰 高松灯送来了晚刊"


def test_scheduled_push_targets_default_empty():
    plugin = Plugin()
    assert plugin._settings["scheduled_push_targets"] == []
