from modules.screen_app_registry import ScreenAppRegistry
from modules.screen_sensor import ScreenSensor


class DummyChatService:
    async def send_active_alert(self, app_name, active_minutes):
        return None


def test_process_name_classifies_common_apps_without_title():
    reg = ScreenAppRegistry()
    cases = [
        ("chrome.exe", "browser"),
        ("Code.exe", "coding"),
        ("ZCode.exe", "coding"),
        ("QQ.exe", "social"),
        ("WINWORD.EXE", "work"),
        ("live2d-enhanced.exe", "self"),
        ("steamwebhelper.exe", "gaming"),
    ]
    for app, expected in cases:
        match = reg.match(app=app, title="", domain="")
        assert match is not None, app
        assert match.rule.category == expected, (app, match.rule.name, match.rule.category)


def test_browser_video_domain_outranks_generic_browser_app():
    reg = ScreenAppRegistry()
    match = reg.match(
        app="chrome.exe",
        title="某个视频 - Google Chrome",
        domain="www.bilibili.com",
    )
    assert match is not None
    assert match.rule.category == "video"


def test_browser_docs_do_not_become_coding_from_title_keywords():
    reg = ScreenAppRegistry()
    match = reg.match(
        app="chrome.exe",
        title="Cursor docs - Google Chrome",
        domain="cursor.com",
    )
    assert match is not None
    assert match.rule.category == "browser"


def test_analyze_window_context_keeps_real_app_name():
    sensor = ScreenSensor(DummyChatService())
    # Simulate polluted historical cache labels from older builds.
    sensor.app_cache[
        "app=Code.exe|title=main.py - Visual Studio Code|domain="
    ] = ["coding", "coding"]
    cat, app_name = sensor._analyze_window_context(
        app="Code.exe",
        title="main.py - Visual Studio Code",
        domain="",
    )
    assert cat == "coding"
    assert app_name == "Code.exe"


def test_self_window_titles_are_split_entries():
    import config

    titles = list(getattr(config, "SELF_WINDOW_TITLES", []) or [])
    assert "L2D" in titles
    assert "🧠 记忆与档案管理中心" in titles
    assert "L2D🧠 记忆与档案管理中心" not in titles
