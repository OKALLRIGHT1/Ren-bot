from __future__ import annotations

from pathlib import Path

from services.gui_api.app_rules_service import AppRulesGuiService


def test_list_save_and_test_rules(tmp_path: Path):
    path = tmp_path / "app_category_rules.json"
    service = AppRulesGuiService(rules_path=path)
    listed = service.list_rules()
    assert listed["ok"] is True
    assert listed["data"]["count"] >= 0

    saved = service.save_rules(
        [
            {
                "name": "Endfield",
                "category": "gaming",
                "display_name": "Endfield",
                "app_patterns": ["Endfield.exe"],
                "title_patterns": [],
                "domain_patterns": [],
                "note": "test",
            }
        ]
    )
    assert saved["ok"] is True
    assert path.exists()
    tested = service.test_match({"app": "Endfield.exe", "title": "", "domain": ""})
    assert tested["ok"] is True
    assert tested["data"]["matched"] is True
    assert tested["data"]["category"] == "gaming"
