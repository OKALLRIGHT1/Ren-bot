import asyncio
import json
from pathlib import Path

import pytest

from plugins.disaster_warning.plugin import Plugin
from plugins.disaster_warning.settings import (
    PluginSettings,
    normalize_session,
    parse_session_list,
)
from plugins.disaster_warning.sources.fanstudio_ws import build_auth_message
from plugins.disaster_warning.sources.parse_eew import extract_cea_payload, parse_cea_event
from plugins.disaster_warning.query import extract_place, looks_like_disaster_query
from plugins.disaster_warning.sources.cenc_eqlist import CencEqListClient, parse_eqlist
from plugins.disaster_warning.sources.nmc_alerts import (
    NmcClient,
    alert_event_id,
    parse_find_alarm,
    parse_typhoon_list,
)
from plugins.disaster_warning.sources.parse_wolfx import parse_wolfx_eew
from plugins.disaster_warning.sources.wolfx_ws import take_snapshot_flag


class HangClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._stop = asyncio.Event()
        self.started = True

    def request_stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.is_set()

    async def close_socket(self):
        return None

    async def run_forever(self):
        ready = self.kwargs.get("on_ready") or self.kwargs.get("on_auth_success")
        if ready is not None:
            await ready()
        await self._stop.wait()


WOLFX_CENC = {
    "type": "cenc_eew",
    "EventID": "CENC.20260825001",
    "ID": "wx-1",
    "ReportNum": 2,
    "OriginTime": "2026-08-25 12:01:03",
    "HypoCenter": "上海市嘉定区",
    "Latitude": 31.34,
    "Longitude": 121.24,
    "Magnitude": 5.2,
    "Depth": 10,
    "MaxIntensity": 5.1,
}

WOLFX_SC = {
    "type": "sc_eew",
    "EventID": "SC.20260825002",
    "ReportNum": 1,
    "OriginTime": "2026-08-25 12:02:00",
    "HypoCenter": "四川宜宾市长宁县",
    "Latitude": 28.33,
    "Longitude": 104.98,
    "Magnitude": 4.7,
    "Depth": 5,
    "MaxIntensity": 4.0,
}

EQLIST_FIXTURE = {
    "type": "cenc_eqlist",
    "No1": {
        "type": "reviewed",
        "time": "2026-08-25 08:26:20",
        "location": "四川宜宾市长宁县",
        "placeName": "四川宜宾市长宁县",
        "magnitude": "4.7",
        "depth": "5",
        "latitude": "28.33",
        "longitude": "104.98",
        "intensity": "4.0",
    },
    "No2": {
        "type": "reviewed",
        "time": "2026-08-24 22:10:17",
        "location": "上海市嘉定区",
        "placeName": "上海市嘉定区",
        "magnitude": "3.1",
        "depth": "10",
        "latitude": "31.34",
        "longitude": "121.24",
        "intensity": "2.0",
    },
}

CEA_DATA = {
    "eventId": "CEA.20260825001",
    "placeName": "上海市嘉定区",
    "magnitude": 5.2,
    "latitude": 31.34,
    "longitude": 121.24,
    "depth": 10,
    "epiIntensity": 5.1,
    "updates": 2,
    "shockTime": "2026-08-25 12:01:03",
}


class FakeGateway:
    def __init__(self, up=True):
        self.up = up
        self.sent = []

    def available(self):
        return self.up

    async def send_text(self, text, ctx, emotion="neutral"):
        self.sent.append({"text": text, "ctx": ctx, "emotion": emotion})


def _plugin(tmp_path, regions=None, **settings):
    plugin = Plugin()
    plugin.settings = {
        "enabled": True,
        "owner_session": "private:123456",
        "watch_regions_json": json.dumps(
            regions
            if regions is not None
            else [{"id": "sh", "label": "上海", "keywords": ["上海"]}]
        ),
        "min_magnitude": 4.0,
        "major_override_mag": 7.0,
        "silence_sec": 60,
        "fanstudio_api_key": "",
        "state_path": str(tmp_path / "disaster_warning_state.json"),
        "wolfx_eew_enabled": False,
        "alert_poll_enabled": False,
        **settings,
    }
    plugin.reload_config()
    plugin._gateway = FakeGateway()
    plugin._state.silence_until = 0
    plugin._state.silence_armed = True
    plugin._make_wolfx_client = HangClient
    plugin._make_fan_client = HangClient
    return plugin


def test_parse_cea_and_drop_non_cea():
    event = parse_cea_event(CEA_DATA)
    assert event is not None
    assert event.source_id == "cea"
    assert event.place_name.startswith("上海")
    assert extract_cea_payload({"type": "update", "source": "cenc", "Data": CEA_DATA}) is None
    assert extract_cea_payload({"type": "update", "source": "cea-pr", "Data": CEA_DATA}) is None
    assert (
        extract_cea_payload(
            {"type": "update", "source": "cea", "Data": {**CEA_DATA, "province": "上海"}}
        )
        is None
    )
    missing = dict(CEA_DATA)
    missing.pop("epiIntensity")
    assert extract_cea_payload({"type": "update", "source": "cea", "Data": missing}) is None


def test_group_session_normalization():
    assert normalize_session("123456") == "group:123456"
    assert normalize_session("group:123456") == "group:123456"
    assert normalize_session("1132824061", default_kind="private") == "private:1132824061"
    assert parse_session_list("111, 222，333") == [
        "group:111",
        "group:222",
        "group:333",
    ]


def test_auth_message_shape():
    msg = build_auth_message("app-id", "sk-secret")
    assert msg == {"type": "auth", "appId": "app-id", "key": "sk-secret"}


def test_default_settings_push_disabled():
    cfg = PluginSettings.from_raw({})
    assert cfg.enabled is False
    assert cfg.wolfx_eew_enabled is False
    assert cfg.alert_poll_enabled is False
    assert "wolfx.jp" in cfg.wolfx_ws_url
    assert cfg.fanstudio_api_key == ""


@pytest.mark.asyncio
async def test_initial_all_never_sends(tmp_path):
    plugin = _plugin(tmp_path)
    await plugin.handle_fan_message(
        {"type": "initial_all", "cea": {"Data": CEA_DATA, "md5": "x"}, "fssn": {"Data": {}}}
    )
    assert plugin._gateway.sent == []
    assert any("CEA.20260825001" in key for key in plugin._state.seen)


@pytest.mark.asyncio
async def test_enabled_false_blocks_push(tmp_path):
    plugin = _plugin(tmp_path, enabled=False)
    await plugin.handle_fan_message({"type": "update", "source": "cea", "Data": CEA_DATA})
    assert plugin._gateway.sent == []
    await plugin.handle_wolfx_message(WOLFX_CENC, snapshot=False)
    assert plugin._gateway.sent == []


@pytest.mark.asyncio
async def test_update_sends_once_with_gateway_ctx(tmp_path):
    plugin = _plugin(tmp_path)
    text = await plugin.handle_fan_message(
        {"type": "update", "source": "cea", "Data": CEA_DATA}
    )
    assert text and "上海" in text and "粗估烈度" not in text
    assert len(plugin._gateway.sent) == 1
    ctx = plugin._gateway.sent[0]["ctx"]
    assert ctx["source"] == "qq_gateway"
    assert ctx["channel_meta"]["adapter"] == "napcat_qq"
    assert ctx["channel_meta"]["session_id"] == "private:123456"

    await plugin.handle_fan_message({"type": "update", "source": "cea", "Data": CEA_DATA})
    assert len(plugin._gateway.sent) == 1


@pytest.mark.asyncio
async def test_update_sends_private_and_group(tmp_path):
    plugin = _plugin(tmp_path, group_sessions="999001")
    plugin._state.seen = {}
    await plugin.handle_fan_message({"type": "update", "source": "cea", "Data": CEA_DATA})
    sessions = [item["ctx"]["channel_meta"]["session_id"] for item in plugin._gateway.sent]
    assert sessions == ["private:123456", "group:999001"]


@pytest.mark.asyncio
async def test_empty_regions_drop_update(tmp_path):
    plugin = _plugin(tmp_path, regions=[])
    await plugin.handle_fan_message({"type": "update", "source": "cea", "Data": CEA_DATA})
    assert plugin._gateway.sent == []


@pytest.mark.asyncio
async def test_silence_window_drops_update(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._state.silence_until = 10**12
    await plugin.handle_fan_message({"type": "update", "source": "cea", "Data": CEA_DATA})
    assert plugin._gateway.sent == []
    assert plugin._state.stats["drop_silence"] >= 1


@pytest.mark.asyncio
async def test_secret_comes_from_injected_settings(tmp_path):
    plugin = _plugin(tmp_path, fanstudio_api_key="sk-from-store")
    assert plugin._cfg.fanstudio_api_key == "sk-from-store"


@pytest.mark.asyncio
async def test_missing_key_starts_wolfx_not_fan(tmp_path):
    plugin = _plugin(tmp_path, fanstudio_api_key="", wolfx_eew_enabled=True)
    await plugin._ensure_loop()
    assert plugin._wolfx_task is not None and not plugin._wolfx_task.done()
    assert plugin._fan_task is None
    await plugin.stop()
    assert plugin._wolfx_task is None


@pytest.mark.asyncio
async def test_fan_key_starts_both_sources(tmp_path):
    plugin = _plugin(tmp_path, fanstudio_api_key="sk-x", wolfx_eew_enabled=True)
    await plugin._ensure_loop()
    assert plugin._wolfx_task is not None and not plugin._wolfx_task.done()
    assert plugin._fan_task is not None and not plugin._fan_task.done()
    await plugin.stop()


@pytest.mark.asyncio
async def test_disabled_starts_neither_source(tmp_path):
    plugin = _plugin(tmp_path, fanstudio_api_key="sk-x", wolfx_eew_enabled=False)
    plugin.settings["enabled"] = False
    plugin.reload_config()
    plugin._make_wolfx_client = HangClient
    plugin._make_fan_client = HangClient
    await plugin._ensure_loop()
    assert plugin._wolfx_task is None
    assert plugin._fan_task is None


@pytest.mark.asyncio
async def test_wolfx_snapshot_never_sends(tmp_path):
    plugin = _plugin(tmp_path)
    text = await plugin.handle_wolfx_message(WOLFX_CENC, snapshot=True)
    assert text is None
    assert plugin._gateway.sent == []
    assert any("CENC.20260825001" in key for key in plugin._state.seen)


@pytest.mark.asyncio
async def test_wolfx_cenc_sends_once(tmp_path):
    plugin = _plugin(tmp_path)
    text = await plugin.handle_wolfx_message(WOLFX_CENC, snapshot=False)
    assert text and "上海" in text and "中国地震台网" in text
    assert len(plugin._gateway.sent) == 1
    ctx = plugin._gateway.sent[0]["ctx"]
    assert ctx["channel_meta"]["adapter"] == "napcat_qq"
    await plugin.handle_wolfx_message(WOLFX_CENC, snapshot=False)
    assert len(plugin._gateway.sent) == 1


@pytest.mark.asyncio
async def test_wolfx_drops_jma_and_training(tmp_path):
    plugin = _plugin(tmp_path)
    assert (
        await plugin.handle_wolfx_message(
            {"type": "jma_eew", "EventID": "x", "Magnitude": 7, "HypoCenter": "東京"},
            snapshot=False,
        )
        is None
    )
    assert (
        await plugin.handle_wolfx_message(
            {**WOLFX_CENC, "isTraining": True}, snapshot=False
        )
        is None
    )
    assert plugin._gateway.sent == []


def test_parse_wolfx_cenc_and_sc():
    cenc = parse_wolfx_eew(WOLFX_CENC)
    assert cenc is not None
    assert cenc.source_id == "cea_wolfx"
    assert cenc.place_name.startswith("上海")
    assert cenc.magnitude == 5.2
    sc = parse_wolfx_eew(WOLFX_SC)
    assert sc is not None
    assert sc.source_id == "sc_wolfx"
    assert sc.display_name == "四川地震局"
    assert parse_wolfx_eew({"type": "cenc_eqlist"}) is None
    assert parse_wolfx_eew({"type": "cenc_eew", "EventID": "x"}) is None


def test_wolfx_snapshot_flag_first_of_type_in_grace():
    seeded = set()
    assert take_snapshot_flag("cenc_eew", seeded, grace_until=10.0, now=1.0) is True
    assert take_snapshot_flag("cenc_eew", seeded, grace_until=10.0, now=2.0) is True
    assert take_snapshot_flag("sc_eew", seeded, grace_until=10.0, now=2.0) is True
    late = set()
    assert take_snapshot_flag("cenc_eew", late, grace_until=1.0, now=5.0) is True
    assert take_snapshot_flag("cenc_eew", late, grace_until=1.0, now=6.0) is False
    assert take_snapshot_flag("jma_eew", set(), grace_until=10.0, now=1.0) is False


@pytest.mark.asyncio
async def test_status_and_watch_commands(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._config_path = tmp_path / "config.json"
    plugin._config_path.write_text(
        json.dumps(
            {
                "settings": {
                    "watch_regions_json": {
                        "type": "text",
                        "default": json.dumps(
                            [{"id": "sh", "label": "上海", "keywords": ["上海"]}],
                            ensure_ascii=False,
                        ),
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx = {"source": "local"}
    status = await plugin.run("/灾害预警 状态", ctx)
    assert "Fan：未配置" in status
    assert "Wolfx" in status
    listed = await plugin.run("/灾害预警 关注", ctx)
    assert "上海" in listed
    added = await plugin.run("/灾害预警 关注 添加 广州", ctx)
    assert "广州" in added


def test_should_not_steal_casual_chat():
    plugin = Plugin()
    ctx = {"source": "qq_gateway", "channel_meta": {"is_owner": True}}
    assert plugin.should_handle_direct("刚看到地震预警新闻", ctx, "地震预警") is False
    assert plugin.should_handle_direct("上海今天天气怎么样", ctx, "灾害预警") is False
    assert plugin.should_handle_direct("/灾害预警 状态", ctx, "/灾害预警") is True
    match = plugin.get_capabilities()[0].match("刚看到地震预警新闻", ctx)
    assert match is None
    assert plugin.get_capabilities()[0].match("/灾害预警 状态", ctx) is not None


def _eq_list_cap(plugin):
    return next(cap for cap in plugin.get_capabilities() if cap.id == "disaster.eq_list")


def test_natural_query_match_and_non_match():
    plugin = Plugin()
    ctx = {}
    match = _eq_list_cap(plugin).match
    assert match("上海今天天气怎么样", ctx) is None
    assert match("刚看到地震预警新闻", ctx) is None
    assert match("这部剧好地震感", ctx) is None
    hit = match("最近上海有地震吗", ctx)
    assert hit is not None
    assert hit.capability_id == "disaster.eq_list"
    assert (hit.args or {}).get("action") == "eq_list"
    assert (hit.args or {}).get("place") == "上海"
    assert looks_like_disaster_query("最近有没有地震") is True
    assert extract_place("最近上海有地震吗") == "上海"
    typhoon = match("现在有没有台风", ctx)
    assert typhoon is not None
    assert (typhoon.args or {}).get("action") == "typhoon"
    rain = match("上海有暴雨预警吗", ctx)
    assert rain is not None
    assert (rain.args or {}).get("action") == "weather_alert"


def test_plugin_is_react_with_slash_direct_aliases():
    plugin = Plugin()
    assert plugin.type == "react"
    assert "/灾害预警" in plugin.direct_command_aliases
    assert plugin.allow_natural_language_direct is False


def test_tool_schema_and_format_invocation():
    plugin = Plugin()
    schema = plugin.get_tool_schema()
    assert schema["name"] == "disaster_warning"
    assert "eq_list" in schema["parameters"]["properties"]["action"]["enum"]
    assert plugin.format_invocation({"action": "eq_list", "place": "上海"}) == "eq_list place=上海"
    args = plugin.collect_invocation_args("最近上海有地震吗", capability_id="disaster.eq_list")
    assert args["action"] == "eq_list"
    assert args["place"] == "上海"


@pytest.mark.asyncio
async def test_eq_list_uses_fixture_without_fan_key(tmp_path):
    plugin = _plugin(tmp_path, fanstudio_api_key="")
    calls = []

    async def fake_fetch(url):
        calls.append(url)
        return EQLIST_FIXTURE

    plugin._eq_client = CencEqListClient(fetch_json=fake_fetch, cache_ttl_sec=60)
    text = await plugin.run("eq_list place=上海", {"source": "local"})
    assert "上海" in text and "3.1" in text
    assert "四川" not in text
    assert plugin._gateway.sent == []
    assert plugin._cfg.fanstudio_api_key == ""
    slash = await plugin.run("/灾害预警 最近地震", {"source": "local"})
    assert "上海" in slash
    assert len(calls) == 1
    missing = await plugin.run("eq_list place=吉林", {"source": "local"})
    assert "没有找到" in missing


@pytest.mark.asyncio
async def test_eq_list_failure_does_not_invent(tmp_path):
    plugin = _plugin(tmp_path)

    async def boom(url):
        del url
        raise RuntimeError("network")

    plugin._eq_client = CencEqListClient(fetch_json=boom)
    text = await plugin.run("eq_list", {"source": "local"})
    assert "查不到" in text
    assert plugin._gateway.sent == []


def test_parse_eqlist_skips_other_types():
    items = parse_eqlist(EQLIST_FIXTURE)
    assert len(items) == 2
    assert items[0].location.startswith("四川")
    assert parse_eqlist({"type": "cenc_eew", "No1": EQLIST_FIXTURE["No1"]}) == []


FIND_ALARM_FIXTURE = {
    "data": {
        "page": {
            "list": [
                {
                    "title": "上海市发布暴雨橙色预警",
                    "url": "/alarm/sh-orange",
                    "issuetime": "2026-08-25 10:00:00",
                },
                {
                    "title": "广东省发布暴雨蓝色预警",
                    "url": "/alarm/gd-blue",
                    "issuetime": "2026-08-25 09:00:00",
                },
                {
                    "title": "中央气象台发布台风橙色预警",
                    "url": "/alarm/ty-orange",
                    "issuetime": "2026-08-25 08:00:00",
                },
            ]
        }
    }
}

TYPHOON_FIXTURE = {
    "typhoonList": [
        [1, "ZITAN", "紫檀", "2411", "2411", 20260011, "", "start"],
        [2, "OLD", "停编", "2400", "2400", 0, "", "stop"],
    ]
}


def test_alert_event_id_ignores_clock_and_continue():
    a = alert_event_id("中央气象台8月25日10时发布台风橙色预警", "/alarm/a_20260825100000")
    b = alert_event_id("中央气象台8月25日11时继续发布台风橙色预警", "/alarm/a_20260825110000")
    assert a == b
    assert a.startswith("typhoon:")


def test_parse_nmc_fixtures():
    alerts = parse_find_alarm(FIND_ALARM_FIXTURE)
    assert [item.kind for item in alerts] == ["weather", "weather", "typhoon"]
    storms = parse_typhoon_list(TYPHOON_FIXTURE)
    assert [item.name_cn for item in storms if item.active] == ["紫檀"]
    wrapped = parse_typhoon_list(
        'typhoon_jsons_list_default({"typhoonList":[[9,"A","阿","1","1",1,"","start"]]})'
    )
    assert wrapped[0].name_cn == "阿"


@pytest.mark.asyncio
async def test_typhoon_and_weather_query_use_fixture(tmp_path):
    plugin = _plugin(tmp_path, fanstudio_api_key="")

    async def fake(url):
        if "findAlarm" in url or "Alarm" in url:
            return FIND_ALARM_FIXTURE
        return TYPHOON_FIXTURE

    plugin._nmc_client = NmcClient(fetch_json=fake, fetch_text=fake)
    storms = await plugin.run("typhoon", {"source": "local"})
    assert "紫檀" in storms
    assert "停编" not in storms
    assert plugin._gateway.sent == []
    weather = await plugin.run("weather_alert place=上海", {"source": "local"})
    assert "上海" in weather and "橙色" in weather
    assert "广东" not in weather
    slash = await plugin.run("/灾害预警 台风", {"source": "local"})
    assert "紫檀" in slash


@pytest.mark.asyncio
async def test_alert_poll_seeds_then_pushes(tmp_path):
    plugin = _plugin(
        tmp_path,
        regions=[{"id": "sh", "label": "上海", "keywords": ["上海"], "kinds": ["weather"]}],
    )

    payloads = [{"data": {"page": {"list": []}}}, FIND_ALARM_FIXTURE]

    async def fake(url):
        del url
        return payloads.pop(0) if payloads else FIND_ALARM_FIXTURE

    plugin._nmc_client = NmcClient(fetch_json=fake)
    seeded = await plugin._poll_alerts(seed=True)
    assert seeded == 0
    assert plugin._gateway.sent == []
    plugin._nmc_client.clear_cache()
    pushed = await plugin._poll_alerts(seed=False)
    assert pushed == 1
    assert "气象预警" in plugin._gateway.sent[0]["text"]
    assert "上海" in plugin._gateway.sent[0]["text"]
    plugin._nmc_client.clear_cache()
    again = await plugin._poll_alerts(seed=False)
    assert again == 0
    assert len(plugin._gateway.sent) == 1
    plugin._nmc_client.clear_cache()
    refreshed = {
        "data": {
            "page": {
                "list": [
                    {
                        "title": "上海市8月25日11时继续发布暴雨橙色预警",
                        "url": "/alarm/sh-orange-1100",
                        "issuetime": "2026-08-25 11:00:00",
                    }
                ]
            }
        }
    }

    async def fake_refresh(url):
        del url
        return refreshed

    plugin._nmc_client.fetch_json = fake_refresh
    still = await plugin._poll_alerts(seed=False)
    assert still == 0
    assert len(plugin._gateway.sent) == 1


@pytest.mark.asyncio
async def test_execute_direct_ignores_news_sentence():
    from modules.plugin_manager import PluginManager

    plugin = Plugin()
    plugin.access_control = {
        "allow_local": True,
        "allow_remote_qq": True,
        "allow_qq_owner": True,
        "allow_qq_others": False,
        "allow_group_without_at": False,
    }
    manager = PluginManager(plugin_dir="plugins")
    manager.plugins = {"disaster_warning": plugin}
    manager.direct_map = {alias: plugin for alias in list(plugin.aliases) + ["disaster_warning"]}
    handled, _ = await manager.execute_direct_commands(
        "刚看到地震预警新闻",
        {"source": "qq_gateway", "channel_meta": {"is_owner": True, "session_id": "private:1"}},
    )
    assert handled is False


def test_public_status_hides_api_key(tmp_path):
    from plugins.disaster_warning.settings import clamp_ui_host
    from plugins.disaster_warning.status_server import public_status

    plugin = _plugin(tmp_path, fanstudio_api_key="sk-secret-value")
    data = public_status(plugin)
    blob = json.dumps(data)
    assert "sk-secret-value" not in blob
    assert data["status"]["has_api_key"] is True
    assert clamp_ui_host("0.0.0.0") == "127.0.0.1"
    assert clamp_ui_host("127.0.0.1") == "127.0.0.1"
    assert "sk-secret-value" not in json.dumps(data["status"])
    assert data["status"]["apply_url"].startswith("https://api.fanstudio.tech")


def test_replace_regions_roundtrip(tmp_path):
    plugin = _plugin(tmp_path, regions=[])
    plugin._config_path = tmp_path / "config.json"
    plugin._config_path.write_text(
        json.dumps({"settings": {"watch_regions_json": {"type": "text", "default": "[]"}}}),
        encoding="utf-8",
    )
    from plugins.disaster_warning.watch import WatchRegion

    assert plugin.replace_regions(
        [WatchRegion(id="gz", label="广州", keywords=["广州", "广东"])]
    )
    assert plugin._cfg.regions[0].label == "广州"


@pytest.mark.asyncio
async def test_hot_reload_disables_watch_without_restart(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._config_path = tmp_path / "config.json"
    plugin._config_path.write_text(
        json.dumps({"settings": {"enabled": {"type": "bool", "default": True}}}),
        encoding="utf-8",
    )
    plugin._wolfx_task = asyncio.create_task(asyncio.sleep(30))
    plugin._task = plugin._wolfx_task
    assert plugin.patch_settings({"enabled": False}) is True
    await asyncio.sleep(0.2)
    assert plugin._cfg.enabled is False
    assert plugin._wolfx_task is None or plugin._wolfx_task.done()


def test_plugin_tree_has_no_forbidden_imports():
    root = Path("plugins/disaster_warning")
    forbidden = (
        "astrbot",
        "plugin_logger",
        "plugincenter.aloys23.link",
        "TelemetryManager",
        "track_error_safely",
        'ctx["chat_service"]',
        "set_exception_handler",
    )
    blob = []
    for path in root.rglob("*.py"):
        blob.append(path.read_text(encoding="utf-8"))
    text = "\n".join(blob)
    for item in forbidden:
        assert item not in text, item
