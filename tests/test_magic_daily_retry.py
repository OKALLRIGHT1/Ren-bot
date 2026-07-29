import asyncio
from pathlib import Path
from unittest.mock import patch

from plugins.Isuzu_news.magic_daily.constants import (
    DEFAULT_ANIME,
    DEFAULT_HITOKOTO,
    DEFAULT_HOLIDAYS,
    DEFAULT_HOTWORDS,
    DEFAULT_IT_NEWS,
    DEFAULT_WORLD_NEWS,
)
from plugins.Isuzu_news.magic_daily.plugin_impl import Plugin
from plugins.Isuzu_news.magic_daily.api.bgm import BGMAPI


class _FakeBGMAPI:
    calls = 0

    def __init__(self, session=None):
        pass

    async def get_calendar_async(self):
        type(self).calls += 1
        return []

    def parse_today_anime(self, api_data, max_count=4):
        return DEFAULT_ANIME[:max_count]


class _FakeBilibiliAPI:
    calls = 0

    def __init__(self, session=None):
        pass

    async def get_hotwords_data_async(self):
        type(self).calls += 1
        return {"code": 0, "list": [{"show_name": "测试热点"}]}

    def parse_hotwords_data(self, api_data, max_count=4):
        return DEFAULT_HOTWORDS[:max_count]


class _FakeITHomeRSS:
    calls = 0

    def __init__(self, session=None):
        pass

    async def get_rss_async(self):
        type(self).calls += 1
        return object()

    def parse_news(self, rss_root, max_count=5):
        return DEFAULT_IT_NEWS[:max_count]


class _FakeSession:
    closed = False


def test_bgm_parse_can_disable_default_fallback():
    api = BGMAPI()

    assert api.parse_today_anime([], max_count=2, use_fallback=False) == []
    assert api.parse_today_anime([], max_count=2)


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
        return DEFAULT_ANIME[:limit]

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
            "today_anime": DEFAULT_ANIME,
            "bili_hot": DEFAULT_HOTWORDS,
            "hitokoto": DEFAULT_HITOKOTO,
            "moyu": DEFAULT_HOLIDAYS,
            "world_news": DEFAULT_WORLD_NEWS,
            "it_news": DEFAULT_IT_NEWS,
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
            DEFAULT_HITOKOTO,
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
