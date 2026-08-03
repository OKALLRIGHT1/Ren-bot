from modules.gui.widgets.assistant_badge_editor import (
    badge_scope_label,
    normalize_presentation,
)


def test_normalize_presentation_clamps_values():
    assert normalize_presentation(9, -7, 4) == (3.0, -1.0, 1.0)
    assert normalize_presentation("bad", None, None) == (1.0, 0.0, 0.0)


def test_badge_scope_label_describes_costume_inheritance():
    assert badge_scope_label("character", True) == "继承角色默认徽章"
    assert badge_scope_label("costume", True) == "使用服装独立徽章"
    assert badge_scope_label("none", False) == "尚未设置角色默认徽章"
