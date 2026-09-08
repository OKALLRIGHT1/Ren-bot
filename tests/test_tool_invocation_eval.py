import json
from pathlib import Path

from modules.tool_router import ToolRouter
from plugins.info_gateway.plugin import Plugin as InfoGateway
from plugins.moegirl_wiki.plugin import Plugin as MoegirlWiki
from plugins.search.plugin import Plugin as SearchPlugin
from plugins.web_reader.plugin import Plugin as WebReader
from plugins.workspace_ops.plugin import Plugin as WorkspaceOps

CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "tool_invocation_cases.json"


def _load_cases():
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _router():
    info = InfoGateway()
    search = SearchPlugin()
    search.aliases = ["搜索", "查一下", "联网"]
    search.llm_command = "search"
    web = WebReader()
    workspace = WorkspaceOps()
    moegirl = MoegirlWiki()
    return ToolRouter(
        react_map={"info_gateway": info},
        direct_map={},
        delegate_map={
            "search_web": search,
            "web_reader": web,
            "workspace_ops": workspace,
            "moegirl_wiki": moegirl,
        },
    )


def test_fixture_layers_are_valid():
    cases = _load_cases()
    assert cases
    layers = {str(case.get("layer") or "") for case in cases}
    assert layers <= {"route", "invoke", "gate"}
    for case in cases:
        assert case.get("id")
        assert case.get("text")
        assert isinstance(case.get("expect"), dict)


def test_route_layer_cases():
    router = _router()
    cases = [case for case in _load_cases() if case.get("layer") == "route"]
    failures = []
    for case in cases:
        route = router.route(case["text"])
        expect = case["expect"]
        want_tools = bool(expect.get("need_tools"))
        if route.need_tools != want_tools:
            failures.append(
                f"{case['id']}: need_tools={route.need_tools} want={want_tools} reason={route.reason}"
            )
            continue
        plugin = str(expect.get("plugin") or "")
        if plugin and plugin not in set(route.tool_triggers or []):
            failures.append(
                f"{case['id']}: triggers={route.tool_triggers} want plugin={plugin}"
            )
        capability = str(expect.get("capability") or "")
        if capability and str(route.capability_id or "") != capability:
            failures.append(
                f"{case['id']}: capability={route.capability_id} want={capability}"
            )
    assert failures == []


def test_weather_umbrella_is_a_weather_route():
    plugin = InfoGateway()
    assert plugin._looks_like_weather_query("明天出门要不要带伞")
    assert not plugin._extract_city("明天出门要不要带伞")
    match = plugin._match_weather_now("明天出门要不要带伞", {})
    assert match is not None
    assert match.capability_id == "info.weather_now"


def test_weather_mood_is_not_a_weather_query():
    plugin = InfoGateway()
    assert not plugin._looks_like_weather_query("这天气真好，好想出去玩")
    assert not plugin._looks_like_weather_query("今天天气真好")
    assert plugin._looks_like_weather_query("上海今天天气怎么样")
    assert plugin._looks_like_weather_query("现在多少度")
    assert plugin._looks_like_weather_query("外面温度怎么样")
    assert plugin._match_weather_now("现在多少度", {}) is not None
    assert not plugin._extract_city("现在多少度")
    schema = plugin.get_tool_schema()
    assert "气温" in schema["description"]
    assert "不要改用搜索" in schema["description"]
