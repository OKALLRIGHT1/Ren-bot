from core.message_source import (
    build_output_profile,
    is_hud_user_source,
    is_live2d_enabled_for_source,
    is_local_actor_source,
)


def test_hud_user_source_helper():
    assert is_hud_user_source("tauri_gui") is True
    assert is_hud_user_source("text_input") is True
    assert is_hud_user_source("screen_sensor") is False
    assert is_local_actor_source("tauri_gui") is True
    assert is_local_actor_source("hud") is True


def test_hud_and_gui_sources_drive_live2d():
    for source in ("text_input", "tauri_gui", "qt_gui", "gui", "local", "hud", "voice"):
        profile = build_output_profile(source)
        assert is_live2d_enabled_for_source(source) is True
        assert profile["show_bubble"] is True
        assert profile["speak"] is True
        assert profile["live2d_enabled"] is True


def test_qq_sources_do_not_drive_live2d():
    for source in ("qq_gateway", "napcat_qq"):
        profile = build_output_profile(source)
        assert profile["show_bubble"] is False
        assert profile["speak"] is False
        assert profile["live2d_enabled"] is False
