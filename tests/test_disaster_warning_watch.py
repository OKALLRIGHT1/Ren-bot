from plugins.disaster_warning.event import DisasterEvent
from plugins.disaster_warning.watch import (
    WatchRegion,
    fold_zh,
    should_push_alert,
    should_push_eew,
)


def _event(**kwargs):
    payload = {
        "source_id": "cea",
        "event_id": "E1",
        "updates": 1,
        "kind": "eew",
        "place_name": "上海市",
        "magnitude": 5.0,
        "lat": 31.23,
        "lng": 121.47,
    }
    payload.update(kwargs)
    return DisasterEvent(**payload)


def test_empty_regions_never_push_including_major():
    event = _event(magnitude=7.8)
    ok, reason, _ = should_push_eew(
        event, [], min_magnitude=4.0, major_override_mag=7.0
    )
    assert ok is False
    assert reason == "no_watch_regions"


def test_keyword_hit_and_below_min_magnitude():
    regions = [WatchRegion(id="sh", label="上海", keywords=["上海"])]
    ok, reason, extra = should_push_eew(
        _event(magnitude=5.1),
        regions,
        min_magnitude=4.0,
        major_override_mag=7.0,
    )
    assert ok is True
    assert reason == "region:sh"
    assert extra.get("label") == "上海"

    ok, reason, _ = should_push_eew(
        _event(magnitude=3.2),
        regions,
        min_magnitude=4.0,
        major_override_mag=7.0,
    )
    assert ok is False
    assert reason == "below_min_magnitude"


def test_major_override_only_when_regions_exist_and_miss():
    regions = [WatchRegion(id="sh", label="上海", keywords=["上海"])]
    ok, reason, _ = should_push_eew(
        _event(place_name="新疆阿克苏", magnitude=7.1, lat=41.0, lng=80.0),
        regions,
        min_magnitude=4.0,
        major_override_mag=7.0,
    )
    assert ok is True
    assert reason == "major_override"


def test_geo_requires_radius_and_intensity():
    regions = [
        WatchRegion(
            id="home",
            label="上海",
            keywords=[],
            lat=31.23,
            lng=121.47,
            radius_km=50,
            min_intensity=2.0,
        )
    ]
    far = _event(place_name="日本", magnitude=5.0, lat=35.0, lng=139.0)
    ok, reason, _ = should_push_eew(
        far, regions, min_magnitude=4.0, major_override_mag=0
    )
    assert ok is False
    assert reason == "no_region_hit"

    near = _event(place_name="附近", magnitude=5.5, lat=31.25, lng=121.50)
    ok, reason, extra = should_push_eew(
        near, regions, min_magnitude=4.0, major_override_mag=0
    )
    assert ok is True
    assert extra.get("dist_km") < 50


def test_fold_zh_maps_hualian():
    assert "花莲" in fold_zh("台灣花蓮")


def _alert(**kwargs):
    payload = {
        "source_id": "nmc",
        "event_id": "A1",
        "updates": 1,
        "kind": "weather",
        "place_name": "上海市发布暴雨橙色预警",
        "magnitude": 0.0,
        "raw_title": "上海市发布暴雨橙色预警",
        "color_level": "橙色",
        "location_desc": "上海市发布暴雨橙色预警",
    }
    payload.update(kwargs)
    return DisasterEvent(**payload)


def test_weather_blue_below_yellow_dropped():
    regions = [
        WatchRegion(
            id="sh",
            label="上海",
            keywords=["上海"],
            kinds=["eew", "typhoon", "weather"],
            min_color_level="黄色",
        )
    ]
    ok, reason, _ = should_push_alert(
        _alert(
            raw_title="上海市发布暴雨蓝色预警",
            place_name="上海市发布暴雨蓝色预警",
            location_desc="上海市发布暴雨蓝色预警",
            color_level="蓝色",
        ),
        regions,
    )
    assert ok is False
    assert reason == "below_color_level"


def test_weather_shanghai_orange_pushed():
    regions = [
        WatchRegion(
            id="sh",
            label="上海",
            keywords=["上海"],
            kinds=["eew", "typhoon", "weather"],
            min_color_level="黄色",
        )
    ]
    ok, reason, extra = should_push_alert(_alert(), regions)
    assert ok is True
    assert reason == "region:sh"
    assert extra.get("label") == "上海"


def test_alert_same_id_not_repeat_unless_color_up(tmp_path):
    from plugins.disaster_warning.state import WarningState

    state = WarningState(tmp_path / "seen.json")
    first = _alert(event_id="/alarm/sh", color_level="黄色")
    assert state.should_skip_duplicate(first) is False
    state.mark_seen(first)
    assert state.should_skip_duplicate(first) is True
    same = _alert(event_id="/alarm/sh", color_level="黄色")
    assert state.should_skip_duplicate(same) is True
    upgraded = _alert(event_id="/alarm/sh", color_level="橙色")
    assert state.should_skip_duplicate(upgraded) is False


def test_weather_other_province_blocked():
    regions = [
        WatchRegion(
            id="sh",
            label="上海",
            keywords=["上海"],
            kinds=["eew", "typhoon", "weather"],
            min_color_level="黄色",
        )
    ]
    ok, reason, _ = should_push_alert(
        _alert(
            raw_title="广东省发布暴雨橙色预警",
            place_name="广东省发布暴雨橙色预警",
            location_desc="广东省发布暴雨橙色预警",
            color_level="橙色",
        ),
        regions,
    )
    assert ok is False
    assert reason == "no_region_hit"
