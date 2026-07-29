from __future__ import annotations

from typing import Any, Dict

from services.gui_api.theme_service import ThemeGuiService


class FakeRuntime:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {
            "theme_name": "Indigo (靛蓝)",
            "ui_palette": {"accent": "#111111"},
        }

    def load(self) -> Dict[str, Any]:
        return dict(self.data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.data.update(patch or {})
        return dict(self.data)


def test_list_themes_and_get_palette():
    runtime = FakeRuntime()
    service = ThemeGuiService(
        load_runtime=runtime.load,
        update_runtime=runtime.update,
        themes={
            "Indigo (靛蓝)": {"accent": "#6366F1", "bg_app": "#F5F7FB"},
            "Frost (雾白玻璃)": {"accent": "#0A84FF", "bg_app": "#EBECEF"},
        },
        default_theme="Indigo (靛蓝)",
    )
    listed = service.list_themes()
    assert listed["ok"] is True
    assert listed["data"]["current"] == "Indigo (靛蓝)"
    assert any(item["name"] == "Frost (雾白玻璃)" for item in listed["data"]["themes"])
    palette = service.get_palette()
    assert palette["ok"] is True
    assert palette["data"]["palette"]["accent"] == "#111111"


def test_save_theme_and_palette():
    runtime = FakeRuntime()
    service = ThemeGuiService(
        load_runtime=runtime.load,
        update_runtime=runtime.update,
        themes={
            "Indigo (靛蓝)": {"accent": "#6366F1", "bg_app": "#F5F7FB"},
            "Frost (雾白玻璃)": {"accent": "#0A84FF", "bg_app": "#EBECEF"},
        },
        default_theme="Indigo (靛蓝)",
    )
    saved = service.save(
        {
            "theme_name": "Frost (雾白玻璃)",
            "ui_palette": {"accent": "#ABCDEF", "bg_app": "#112233"},
        }
    )
    assert saved["ok"] is True
    assert saved["data"]["theme_name"] == "Frost (雾白玻璃)"
    assert saved["data"]["palette"]["accent"].upper() == "#ABCDEF"
    assert runtime.data["theme_name"] == "Frost (雾白玻璃)"
