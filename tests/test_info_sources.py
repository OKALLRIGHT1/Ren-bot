import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_alapi_provider_loads_endpoint_json_and_adds_shared_token(tmp_path):
    endpoint_dir = tmp_path / "alapi"
    endpoint_dir.mkdir()
    (endpoint_dir / "weather_7d.json").write_text(
        json.dumps(
            {
                "id": "weather_7d",
                "name": "7天天气查询",
                "method": "POST",
                "path": "/api/tianqi/seven",
                "params": {
                    "city": {"type": "string", "required": False},
                    "format": {"type": "string", "required": False, "default": "json"},
                },
                "cache_ttl_sec": 60,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    async def fake_request(method, url, params, timeout_sec):
        calls.append((method, url, dict(params), timeout_sec))
        return {"code": 200, "data": {"city": "上海", "weather": "多云"}}

    from services.info_sources.providers.alapi import AlapiProvider

    provider = AlapiProvider(
        endpoint_dir=endpoint_dir,
        token_getter=lambda: "tok_123",
        request_func=fake_request,
    )

    result = await provider.fetch("weather_7d", city="上海")

    assert result.ok is True
    assert result.provider == "alapi"
    assert result.capability == "weather_7d"
    assert result.data["city"] == "上海"
    assert calls == [
        (
            "POST",
            "https://v3.alapi.cn/api/tianqi/seven",
            {"format": "json", "token": "tok_123", "city": "上海"},
            10.0,
        )
    ]


@pytest.mark.asyncio
async def test_alapi_provider_summarizes_nested_current_weather(tmp_path):
    endpoint_dir = tmp_path / "alapi"
    endpoint_dir.mkdir()
    (endpoint_dir / "weather_now.json").write_text(
        json.dumps(
            {
                "id": "weather_now",
                "name": "ALAPI current weather",
                "method": "POST",
                "path": "/api/tianqi",
                "params": {"city": {"type": "string", "required": False}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def fake_request(method, url, params, timeout_sec):
        return {
            "code": 200,
            "data": {
                "city": "上海",
                "data": {
                    "weather": "多云",
                    "temperature": "28",
                    "humidity": "61",
                    "winddirection": "东南",
                    "windpower": "3级",
                },
            },
        }

    from services.info_sources.providers.alapi import AlapiProvider

    provider = AlapiProvider(
        endpoint_dir=endpoint_dir,
        token_getter=lambda: "",
        request_func=fake_request,
    )

    result = await provider.fetch("weather_now", city="上海")

    assert result.summary != "ALAPI current weather"
    assert "上海天气：多云" in result.summary
    assert "28" in result.summary
    assert "湿度61" in result.summary
    assert "东南风3级" in result.summary


@pytest.mark.asyncio
async def test_info_source_service_fetches_daily_bundle_with_fallbacks():
    from services.info_sources.service import InfoSourceService

    service = InfoSourceService(
        token_getter=lambda: "",
        alapi_provider=None,
        daily_fallbacks={
            "hitokoto": {"hitokoto": "fallback", "from": "local"},
            "moyu": [{"name": "休息", "days_left": 1}],
            "world_news": ["World"],
        },
        builtin_fetchers={
            "today_anime": lambda limit=4: [{"title": "A", "image": "img"}],
            "bili_hot": lambda limit=4: ["热点"],
            "it_news": lambda limit=5: ["IT"],
        },
    )

    bundle = await service.fetch_daily_bundle(
        max_anime_count=1,
        max_news_count=1,
        max_hotword_count=1,
        max_holiday_count=1,
    )

    assert set(bundle) == {
        "anime_list",
        "bili_hotwords",
        "hitokoto_data",
        "moyu_list",
        "world_news",
        "it_news",
    }
    assert bundle["anime_list"] == [{"title": "A", "image": "img"}]
    assert bundle["bili_hotwords"] == ["热点"]
    assert bundle["hitokoto_data"] == {"hitokoto": "fallback", "from": "local"}
    assert bundle["moyu_list"] == [{"name": "休息", "days_left": 1}]
    assert bundle["world_news"] == ["World"]
    assert bundle["it_news"] == ["IT"]


@pytest.mark.asyncio
async def test_info_source_service_fetches_daily_alapi_aliases_without_internal_limits(tmp_path):
    endpoint_dir = tmp_path / "alapi"
    endpoint_dir.mkdir()
    for endpoint_id, path in {
        "zaobao": "/api/zaobao",
        "hitokoto": "/api/hitokoto",
        "holiday": "/api/holiday",
    }.items():
        (endpoint_dir / f"{endpoint_id}.json").write_text(
            json.dumps(
                {
                    "id": endpoint_id,
                    "name": endpoint_id,
                    "method": "GET",
                    "path": path,
                    "params": {"format": {"type": "string", "default": "json"}}
                    if endpoint_id == "zaobao"
                    else {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    calls = []

    async def fake_request(method, url, params, timeout_sec):
        calls.append((method, url, dict(params), timeout_sec))
        if url.endswith("/api/zaobao"):
            return {"code": 200, "data": {"news": ["1. 世界新闻", "2. 第二条"]}}
        if url.endswith("/api/hitokoto"):
            return {"code": 200, "data": {"hitokoto": "一句话", "from_who": "作者"}}
        if url.endswith("/api/holiday"):
            return {
                "code": 200,
                "data": [
                    {"name": "未来节日", "date": "2999-01-01", "is_off_day": 1},
                    {"name": "工作日", "date": "2999-01-02", "is_off_day": 0},
                ],
            }
        return {"code": 404}

    from services.info_sources.providers.alapi import AlapiProvider
    from services.info_sources.service import InfoSourceService

    provider = AlapiProvider(
        endpoint_dir=endpoint_dir,
        token_getter=lambda: "tok",
        request_func=fake_request,
    )
    service = InfoSourceService(token_getter=lambda: "tok", alapi_provider=provider)

    bundle = await service.fetch_daily_bundle(
        max_anime_count=1,
        max_news_count=1,
        max_hotword_count=1,
        max_holiday_count=1,
    )

    assert bundle["world_news"] == ["世界新闻"]
    assert bundle["hitokoto_data"] == {"hitokoto": "一句话", "from": "作者"}
    assert bundle["moyu_list"][0]["name"] == "未来节日"
    assert isinstance(bundle["moyu_list"][0]["days_left"], int)
    assert bundle["moyu_list"][0]["days_left"] > 0
    params_by_url = {call[1]: call[2] for call in calls}
    assert params_by_url["https://v3.alapi.cn/api/hitokoto"] == {"token": "tok"}
    assert params_by_url["https://v3.alapi.cn/api/holiday"] == {"token": "tok"}
    assert params_by_url["https://v3.alapi.cn/api/zaobao"] == {
        "format": "json",
        "token": "tok",
    }
    assert all("limit" not in call[2] and "max_count" not in call[2] for call in calls)


@pytest.mark.asyncio
async def test_info_source_service_loads_multiple_provider_categories(tmp_path):
    root = tmp_path / "info_sources"
    alapi_dir = root / "alapi"
    weather_dir = root / "weatherapi"
    alapi_dir.mkdir(parents=True)
    weather_dir.mkdir()
    (alapi_dir / "provider.json").write_text(
        json.dumps(
            {"id": "alapi", "name": "ALAPI", "base_url": "https://v3.alapi.cn"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (weather_dir / "provider.json").write_text(
        json.dumps(
            {
                "id": "weatherapi",
                "name": "天气 API",
                "base_url": "https://weather.example",
                "token_param": "key",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (weather_dir / "now.json").write_text(
        json.dumps(
            {
                "id": "now",
                "name": "实况天气",
                "method": "GET",
                "path": "/now",
                "params": {"city": {"type": "string", "required": False}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    calls = []

    async def fake_request(method, url, params, timeout_sec):
        calls.append((method, url, dict(params), timeout_sec))
        return {"code": 200, "data": {"city": "上海", "weather": "晴"}}

    from services.info_sources.providers.alapi import AlapiProvider
    from services.info_sources.service import InfoSourceService

    provider = AlapiProvider(
        endpoint_dir=weather_dir,
        token_getter=lambda: "tok",
        request_func=fake_request,
        base_url="https://weather.example",
        name="weatherapi",
        token_param="key",
    )
    service = InfoSourceService(token_getter=lambda: "tok", providers=[provider])

    result = await service.fetch("now", city="上海")

    assert result.ok is True
    assert result.provider == "weatherapi"
    assert calls == [
        (
            "GET",
            "https://weather.example/now",
            {"key": "tok", "city": "上海"},
            10.0,
        )
    ]


@pytest.mark.asyncio
async def test_info_gateway_plugin_calls_service():
    from plugins.info_gateway.plugin import Plugin

    class Service:
        def list_capabilities(self):
            return ["weather_7d"]

        async def fetch(self, capability, **params):
            assert capability == "weather_7d"
            assert params == {"city": "上海"}
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                data={"city": "上海"},
                summary="上海天气：多云",
            )

    plugin = Plugin(service_getter=lambda _ctx: Service())

    result = await plugin.run("weather_7d city=上海", {"source": "text_input"})

    assert "上海天气：多云" in result


@pytest.mark.asyncio
async def test_info_gateway_plugin_strips_command_alias_before_fetching():
    from plugins.info_gateway.plugin import Plugin

    class Service:
        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            assert capability == "weather_now"
            assert params == {"city": "上海"}
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                summary="上海天气：多云",
            )

    plugin = Plugin(service_getter=lambda _ctx: Service())

    result = await plugin.run("/api weather_now city=上海", {"source": "text_input"})

    assert "上海天气：多云" in result


@pytest.mark.asyncio
async def test_info_gateway_plugin_handles_natural_weather_query():
    from plugins.info_gateway.plugin import Plugin

    calls = []

    class Service:
        def list_capabilities(self):
            return ["weather_now", "weather_7d"]

        async def fetch(self, capability, **params):
            calls.append((capability, params))
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                summary="上海天气：多云",
            )

    plugin = Plugin(service_getter=lambda _ctx: Service())

    result = await plugin.run("上海天气怎么样", {"source": "text_input"})

    assert result == "上海天气：多云"
    assert calls == [("weather_now", {"city": "上海"})]


@pytest.mark.asyncio
async def test_info_gateway_plugin_uses_runtime_secret_settings(monkeypatch):
    from plugins.info_gateway import plugin as info_gateway_plugin
    from plugins.info_gateway.plugin import Plugin
    from services.info_sources.models import InfoSourceResult

    tokens = []

    class Service:
        def __init__(self, token_getter, **_kwargs):
            self._token_getter = token_getter

        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            tokens.append(self._token_getter())
            assert capability == "weather_now"
            assert params == {"city": "上海"}
            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                summary="上海天气：多云",
            )

    monkeypatch.setattr(info_gateway_plugin, "InfoSourceService", Service)
    plugin = Plugin()
    plugin.settings = {"api_token": {"type": "secret", "default": "runtime-token"}}

    result = await plugin.run("上海天气怎么样", {"source": "text_input"})

    assert result == "上海天气：多云"
    assert tokens == ["runtime-token"]


@pytest.mark.asyncio
async def test_info_gateway_plugin_uses_shared_alapi_secret_store(monkeypatch):
    from plugins.info_gateway import plugin as info_gateway_plugin
    from plugins.info_gateway.plugin import Plugin
    from services.info_sources.models import InfoSourceResult

    tokens = []

    class SecretStore:
        def get_secret(self, plugin_trigger, secret_key):
            assert (plugin_trigger, secret_key) == ("magic_daily", "api_token")
            return "shared-token"

    class Service:
        def __init__(self, token_getter, **_kwargs):
            self._token_getter = token_getter

        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            tokens.append(self._token_getter())
            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                summary="上海天气：多云",
            )

    monkeypatch.setattr(info_gateway_plugin, "InfoSourceService", Service)
    plugin = Plugin(alapi_secret_store=SecretStore())

    result = await plugin.run("上海天气怎么样", {"source": "text_input"})

    assert result == "上海天气：多云"
    assert tokens == ["shared-token"]


@pytest.mark.asyncio
async def test_info_gateway_plugin_handles_forecast_weather_query():
    from plugins.info_gateway.plugin import Plugin

    calls = []

    class Service:
        def list_capabilities(self):
            return ["weather_now", "weather_7d"]

        async def fetch(self, capability, **params):
            calls.append((capability, params))
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                summary="上海未来天气：多云转晴",
            )

    plugin = Plugin(service_getter=lambda _ctx: Service())

    result = await plugin.run("上海未来7天天气", {"source": "text_input"})

    assert result == "上海未来天气：多云转晴"
    assert calls == [("weather_7d", {"city": "上海"})]


@pytest.mark.asyncio
async def test_info_gateway_plugin_explains_empty_source_data():
    from plugins.info_gateway.plugin import Plugin

    class Service:
        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                data=None,
                summary="",
            )

    plugin = Plugin(service_getter=lambda _ctx: Service())

    result = await plugin.run("上海天气怎么样", {"source": "text_input"})

    assert result
    assert result != "null"
    assert "没有返回可用数据" in result


def test_info_gateway_is_react_tool_for_natural_weather_query():
    from modules.tool_router import ToolRouter
    from plugins.info_gateway.plugin import Plugin

    plugin = Plugin()
    plugin.settings = {"api_token": {"type": "secret", "default": "test-token"}}
    assert plugin.type == "react"

    route = ToolRouter(
        react_map={"info_gateway": plugin},
        direct_map={},
        delegate_map={},
    ).route("上海天气怎么样")

    assert route.need_tools is True
    assert route.tool_triggers == ["info_gateway"]
    assert route.reason == "capability:info.weather_now"


def test_info_gateway_capability_does_not_match_weather_config_question():
    from modules.tool_router import ToolRouter
    from plugins.info_gateway.plugin import Plugin

    plugin = Plugin()
    plugin.settings = {"api_token": {"type": "secret", "default": "test-token"}}

    route = ToolRouter(
        react_map={"info_gateway": plugin},
        direct_map={},
        delegate_map={},
    ).route("天气接口怎么配置")

    assert route.need_tools is False


def test_info_gateway_capability_prefers_forecast_for_week_weather():
    from modules.tool_router import ToolRouter
    from plugins.info_gateway.plugin import Plugin

    plugin = Plugin()
    plugin.settings = {"api_token": {"type": "secret", "default": "test-token"}}

    route = ToolRouter(
        react_map={"info_gateway": plugin},
        direct_map={},
        delegate_map={},
    ).route("上海这周天气怎么样")

    assert route.need_tools is True
    assert route.tool_triggers == ["info_gateway"]
    assert route.reason == "capability:info.weather_7d"


def test_info_gateway_weather_city_extraction_strips_helper_phrase():
    from plugins.info_gateway.plugin import Plugin

    plugin = Plugin()

    assert plugin._extract_city("帮我看一下长春今天的天气") == "长春"
    assert plugin._extract_city("请帮我查询一下上海今天的天气") == "上海"


def test_info_gateway_weather_capability_reports_missing_token(monkeypatch):
    from pathlib import Path

    from plugins.info_gateway.plugin import Plugin
    from services.capability_manager import ToolCapabilityManager

    plugin = Plugin()
    monkeypatch.setattr(plugin, "_read_alapi_token", lambda root, ctx=None: "")
    manager = ToolCapabilityManager.from_plugin_maps(react_map={"info_gateway": plugin})

    result = manager.match("长春今天的天气怎么样", {})

    assert result.selected is None
    assert result.reason == "unavailable"
    assert result.candidates[0].capability_id == "info.weather_now"
    assert result.candidates[0].unavailable_reason == "missing_secret: alapi.api_token"


def test_info_gateway_weather_router_reports_unavailable_token(monkeypatch):
    from modules.tool_router import ToolRouter
    from plugins.info_gateway.plugin import Plugin

    plugin = Plugin()
    monkeypatch.setattr(plugin, "_read_alapi_token", lambda root, ctx=None: "")

    route = ToolRouter(
        react_map={"info_gateway": plugin},
        direct_map={},
        delegate_map={},
    ).route("长春今天的天气怎么样")

    assert route.need_tools is False
    assert route.reason == "capability_unavailable:info.weather_now"
    assert route.capability_id == "info.weather_now"
    assert route.capability_match_reason == "missing_secret: alapi.api_token"


@pytest.mark.asyncio
async def test_info_gateway_direct_manager_ignores_natural_weather_query():
    from modules.plugin_manager import PluginManager
    from plugins.info_gateway.plugin import Plugin

    class Service:
        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            raise AssertionError("natural weather query must not run as direct command")

    plugin = Plugin(service_getter=lambda _ctx: Service())
    plugin.access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
        "allow_group_without_at": True,
    }
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"info_gateway": plugin}
    manager.direct_map = {}

    handled, result = await manager.execute_direct_commands(
        "上海天气怎么样",
        {"source": "text_input"},
    )

    assert handled is False
    assert result is None


@pytest.mark.asyncio
async def test_info_gateway_direct_manager_keeps_api_command_alias():
    from modules.plugin_manager import PluginManager
    from plugins.info_gateway.plugin import Plugin

    calls = []

    class Service:
        def list_capabilities(self):
            return ["weather_now"]

        async def fetch(self, capability, **params):
            calls.append((capability, params))
            from services.info_sources.models import InfoSourceResult

            return InfoSourceResult(
                ok=True,
                capability=capability,
                provider="alapi",
                summary="上海天气：多云",
            )

    plugin = Plugin(service_getter=lambda _ctx: Service())
    plugin.access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
        "allow_group_without_at": True,
    }
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"info_gateway": plugin}
    manager._rebuild_plugin_maps()

    handled, result = await manager.execute_direct_commands(
        "/api weather_now city=上海",
        {"source": "text_input"},
    )

    assert handled is True
    assert result == "上海天气：多云"
    assert calls == [("weather_now", {"city": "上海"})]
