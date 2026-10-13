from types import SimpleNamespace

from modules.gui.dialogs.character_editor import CharacterEditorWidget


def test_char_list_label_keeps_star():
    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)
    widget.mgr = SimpleNamespace(data={"active_id": "a"})
    assert widget._char_list_label("a", "怜").startswith("⭐ ")
    assert widget._char_list_label("b", "别人") == "别人"
