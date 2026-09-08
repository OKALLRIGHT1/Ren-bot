from types import SimpleNamespace

from modules.gui.sedentary_popup import (
    SedentaryPopupOptions,
    build_frontend_show_payload,
    build_sedentary_popup_options,
    image_to_data_url,
    route_sedentary_popup,
)
from modules.gui.settings_pages.sedentary_page import select_sedentary_preview_image_path


def test_build_sedentary_popup_options_ignores_missing_image(tmp_path):
    missing_image = tmp_path / "missing.png"
    cfg = SimpleNamespace(
        SEDENTARY_POPUP_ENABLED=True,
        SEDENTARY_POPUP_IMAGE_PATH=str(missing_image),
        SEDENTARY_POPUP_MESSAGE="站起来活动一下",
        SEDENTARY_POPUP_SNOOZE_MINUTES=12,
    )

    options = build_sedentary_popup_options(cfg, app_name="Code", active_minutes=90)

    assert options.enabled is True
    assert options.image_path is None
    assert options.message == "站起来活动一下"
    assert options.snooze_minutes == 12
    assert options.auto_close_seconds == 20


def test_build_sedentary_popup_options_formats_default_message():
    cfg = SimpleNamespace()

    options = build_sedentary_popup_options(cfg, app_name="PyCharm", active_minutes=61)

    assert options.enabled is True
    assert "PyCharm" in options.message
    assert "61" in options.message
    assert options.snooze_minutes == 10
    assert options.auto_close_seconds == 20


def test_build_sedentary_popup_options_prefers_existing_image_override(tmp_path):
    override = tmp_path / "rest.png"
    fallback = tmp_path / "fallback.png"
    override.write_bytes(b"png")
    fallback.write_bytes(b"png")
    cfg = SimpleNamespace(SEDENTARY_POPUP_IMAGE_PATH=str(fallback))

    options = build_sedentary_popup_options(
        cfg,
        app_name="Code",
        active_minutes=60,
        image_path_override=str(override),
    )

    assert options.image_path == str(override)


def test_build_sedentary_popup_options_reads_auto_close_seconds():
    cfg = SimpleNamespace(SEDENTARY_POPUP_AUTO_CLOSE_SECONDS=5)

    options = build_sedentary_popup_options(cfg, app_name="Code", active_minutes=60)

    assert options.auto_close_seconds == 5


def test_select_sedentary_preview_image_path_uses_application_selector():
    calls = []

    class Done:
        def result(self, timeout=None):
            calls.append(("timeout", timeout))
            return "D:/memes/rest.png"

    app = SimpleNamespace(
        app=SimpleNamespace(
            select_sedentary_meme_image_path=lambda app_name, minutes: (
                calls.append((app_name, minutes)) or Done()
            )
        )
    )

    selected = select_sedentary_preview_image_path(app, "电脑", 25)

    assert selected == "D:/memes/rest.png"
    assert calls == [("电脑", 25), ("timeout", 8)]


def test_frontend_payload_uses_data_url_not_filesystem_path(tmp_path):
    image = tmp_path / "rest.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 16)
    options = SedentaryPopupOptions(
        enabled=True,
        title="起来",
        message="已经连续工作 60 分钟了",
        image_path=str(image),
        snooze_minutes=10,
        auto_close_seconds=20,
    )

    payload = build_frontend_show_payload(
        options, app_name="电脑", active_minutes=60
    )

    assert payload["type"] == "sedentary_popup"
    assert payload["image_path"] == ""
    assert payload["image_data_url"].startswith("data:image/png;base64,")
    assert str(image) not in payload["image_data_url"]
    assert "LLM" not in payload["message"]


def test_image_to_data_url_rejects_oversized_file(tmp_path):
    image = tmp_path / "huge.png"
    image.write_bytes(b"\x89PNG" + b"x" * 2_000_000)
    assert image_to_data_url(str(image), max_bytes=1024) == ""


def test_route_sedentary_popup_prefers_frontend_when_live2d_client():
    emitted = []
    qt_calls = []
    results = []
    options = SedentaryPopupOptions(
        enabled=True,
        title="起来",
        message="模板正文",
        image_path=None,
        snooze_minutes=8,
        auto_close_seconds=12,
    )

    routed = route_sedentary_popup(
        options,
        app_name="电脑",
        active_minutes=45,
        has_live2d_client=True,
        emit_frontend=emitted.append,
        show_qt=lambda: qt_calls.append("qt"),
        on_result=results.append,
    )

    assert routed == "frontend"
    assert qt_calls == []
    assert results == []
    assert emitted[0]["message"] == "模板正文"
    assert emitted[0]["active_minutes"] == 45


def test_route_sedentary_popup_falls_back_to_qt_without_frontend():
    qt_calls = []
    options = SedentaryPopupOptions(
        enabled=True,
        title="起来",
        message="模板正文",
        image_path=None,
        snooze_minutes=8,
        auto_close_seconds=12,
    )

    routed = route_sedentary_popup(
        options,
        app_name="电脑",
        active_minutes=45,
        has_live2d_client=False,
        emit_frontend=lambda payload: None,
        show_qt=lambda: qt_calls.append("qt"),
        on_result=None,
    )

    assert routed == "qt"
    assert qt_calls == ["qt"]
