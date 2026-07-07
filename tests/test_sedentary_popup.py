from types import SimpleNamespace

from modules.gui.sedentary_popup import build_sedentary_popup_options
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
