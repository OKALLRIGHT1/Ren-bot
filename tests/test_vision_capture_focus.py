from modules.vision import capture as capture_module


def test_find_monitor_for_point_and_rect():
    displays = [
        {
            "index": 1,
            "left": 0,
            "top": 0,
            "right": 1920,
            "bottom": 1080,
            "width": 1920,
            "height": 1080,
            "is_primary": True,
        },
        {
            "index": 2,
            "left": 1920,
            "top": 0,
            "right": 3840,
            "bottom": 1080,
            "width": 1920,
            "height": 1080,
            "is_primary": False,
        },
    ]

    secondary = capture_module.find_monitor_for_point(2000, 100, displays=displays)
    assert secondary is not None
    assert secondary["index"] == 2

    by_rect = capture_module.find_monitor_for_rect(
        2000, 100, 2800, 800, displays=displays
    )
    assert by_rect is not None
    assert by_rect["index"] == 2


def test_resolve_screenshot_selection_active_monitor(monkeypatch):
    displays = [
        {
            "index": 1,
            "left": 0,
            "top": 0,
            "right": 1920,
            "bottom": 1080,
            "width": 1920,
            "height": 1080,
            "is_primary": True,
        },
        {
            "index": 2,
            "left": 1920,
            "top": 0,
            "right": 3840,
            "bottom": 1080,
            "width": 1920,
            "height": 1080,
            "is_primary": False,
        },
    ]
    monkeypatch.setattr(capture_module, "get_display_regions", lambda: displays)
    active_info = {
        "title": "Docs - Google Chrome",
        "left": 2100,
        "top": 120,
        "right": 3000,
        "bottom": 900,
    }

    selected, window_bbox, regions = capture_module._resolve_screenshot_selection(
        target="active_monitor",
        active_info=active_info,
    )
    assert len(regions) == 2
    assert window_bbox is None
    assert selected is not None
    assert selected["index"] == 2


def test_resolve_screenshot_selection_active_window_bbox(monkeypatch):
    displays = [
        {
            "index": 1,
            "left": 0,
            "top": 0,
            "right": 1920,
            "bottom": 1080,
            "width": 1920,
            "height": 1080,
            "is_primary": True,
        }
    ]
    monkeypatch.setattr(capture_module, "get_display_regions", lambda: displays)
    active_info = {
        "title": "Codex",
        "left": 100,
        "top": 80,
        "right": 900,
        "bottom": 700,
    }

    selected, window_bbox, _regions = capture_module._resolve_screenshot_selection(
        target="active_window",
        active_info=active_info,
    )
    assert selected is not None
    assert selected["index"] == 1
    assert window_bbox == (100, 80, 900, 700)


def test_take_screenshot_base64_forwards_target(monkeypatch):
    calls = []

    class DummyImage:
        size = (100, 100)

    def fake_grab(target="primary", monitor_index=1):
        calls.append((target, monitor_index))
        return DummyImage(), {"index": 2}, []

    monkeypatch.setattr(capture_module, "_grab_screenshot_image", fake_grab)
    monkeypatch.setattr(capture_module, "_resize_if_needed", lambda image, max_size=1024: image)
    monkeypatch.setattr(capture_module, "encode_image_to_base64", lambda image: "b64")

    result = capture_module.take_screenshot_base64(target="active_monitor")
    assert result == "b64"
    assert calls == [("active_monitor", 1)]
