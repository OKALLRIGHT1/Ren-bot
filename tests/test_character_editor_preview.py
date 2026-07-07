from types import SimpleNamespace
import json

from modules.gui.dialogs.character_editor import CharacterEditorWidget
from modules import live2d


class FakeCharacterManager:
    def __init__(self):
        self.runtime_config_calls = []
        self.saved_overrides = []

    def get_character(self, char_id):
        return {
            "costumes": {
                "casual": {
                    "path": "D:/models/tomori/casual/model.json",
                    "emotion_map": {},
                }
            }
        }

    def get_costume_runtime_config(self, char_id, costume_name):
        self.runtime_config_calls.append((char_id, costume_name))
        return {"emotion_map": {"idle": {"mtn": "idle01"}}}

    def set_costume_emotion_override(self, char_id, costume_name, emotion, cfg):
        self.saved_overrides.append((char_id, costume_name, emotion, cfg))


def make_widget(main_app):
    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)
    widget.current_char_id = "tomori"
    widget.current_costume_name = "casual"
    widget.main_app = main_app
    widget.mgr = FakeCharacterManager()
    return widget


def test_preview_does_not_reload_when_selected_costume_is_already_active():
    calls = []
    main_app = SimpleNamespace(
        _current_costume_path="D:/models/tomori/casual/model.json",
        on_costume_callback=lambda path, cfg: calls.append((path, cfg)),
    )
    widget = make_widget(main_app)

    reloaded = widget._load_selected_costume_for_preview()

    assert reloaded is False
    assert calls == []


def test_preview_reload_marks_config_as_preview_mode():
    calls = []
    main_app = SimpleNamespace(
        _current_costume_path="D:/models/other/model.json",
        on_costume_callback=lambda path, cfg: calls.append((path, cfg)),
    )
    widget = make_widget(main_app)

    reloaded = widget._load_selected_costume_for_preview()

    assert reloaded is True
    assert calls == [
        (
            "D:/models/tomori/casual/model.json",
            {
                "emotion_map": {"idle": {"mtn": "idle01"}},
                "preview_mode": True,
                "suppress_auto_idle": True,
            },
        )
    ]


def test_preview_retry_delays_repeat_after_model_reload():
    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)

    assert widget._preview_retry_delays(False) == (0,)
    assert widget._preview_retry_delays(True) == (700, 1400, 2200)


def test_legacy_cubism2_motion_preview_uses_group_token(tmp_path):
    model_path = tmp_path / "model.json"
    model_path.write_text(
        json.dumps(
            {
                "motions": {
                    "angry01": [{"file": "data/motions/angry01.mtn"}],
                    "idle01": [{"file": "data/motions/idle01.mtn"}],
                }
            }
        ),
        encoding="utf-8",
    )
    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)

    motions, expressions = widget._parse_model_meta(str(model_path))

    assert expressions == []
    assert motions[0]["name"] == "angry01"
    assert motions[0]["preview_mtn"] == "angry01:angry01"


def test_preview_options_include_model_default_pose_marker():
    class FakeCombo:
        def __init__(self):
            self.items = []

        def clear(self):
            self.items.clear()

        def addItem(self, label, data):
            self.items.append((label, data))

    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)
    widget.combo_motion = FakeCombo()
    widget.combo_expression = FakeCombo()

    widget._refresh_preview_options([], [])

    assert widget.combo_motion.items[0][1] == live2d.MODEL_DEFAULT_MOTION
    assert "默认" in widget.combo_motion.items[0][0]


def test_preview_options_include_stop_motion_marker():
    class FakeCombo:
        def __init__(self):
            self.items = []

        def clear(self):
            self.items.clear()

        def addItem(self, label, data):
            self.items.append((label, data))

    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)
    widget.combo_motion = FakeCombo()
    widget.combo_expression = FakeCombo()

    widget._refresh_preview_options([], [])

    assert widget.combo_motion.items[1][1] == live2d.STOP_MOTION
    assert "停止" in widget.combo_motion.items[1][0]


def test_resolve_emotion_row_prefers_costume_then_character_default():
    widget = CharacterEditorWidget.__new__(CharacterEditorWidget)

    mtn, exp, source = widget._resolve_emotion_row(
        "happy",
        {"happy": {"mtn": "derived", "type": 0, "exp": 1}},
        {"happy": {"mtn": "character-default", "type": 0, "exp": 2}},
        {"happy": {"mtn": "costume-override", "type": 1, "exp": 3}},
    )

    assert (mtn, exp, source) == ("costume-override", "3", "costume")

    mtn, exp, source = widget._resolve_emotion_row(
        "happy",
        {"happy": {"mtn": "derived", "type": 0, "exp": 1}},
        {"happy": {"mtn": "character-default", "type": 0, "exp": 2}},
        {},
    )

    assert (mtn, exp, source) == ("character-default", "2", "character")


def test_hot_reload_current_costume_mapping_updates_runtime_config(monkeypatch):
    calls = []
    main_app = SimpleNamespace(
        _current_costume_path="D:/models/tomori/casual/model.json",
    )
    widget = make_widget(main_app)
    monkeypatch.setattr(
        "modules.gui.dialogs.character_editor.update_current_costume_config",
        lambda cfg, model_path=None: calls.append((cfg, model_path)),
    )

    assert widget._hot_reload_current_costume_mapping() is True

    assert calls == [
        (
            {"emotion_map": {"idle": {"mtn": "idle01"}}},
            "D:/models/tomori/casual/model.json",
        )
    ]


def test_save_costume_mapping_hot_reloads_current_costume(monkeypatch):
    class FakeCombo:
        def __init__(self, value):
            self.value = value

        def currentData(self):
            return self.value

    class FakeTable:
        def currentRow(self):
            return 0

        def setCurrentCell(self, row, column):
            pass

        def item(self, row, column):
            return SimpleNamespace(text=lambda: "happy")

    hot_reload_calls = []
    widget = make_widget(SimpleNamespace())
    widget.combo_motion = FakeCombo("happy01")
    widget.combo_motion_type = FakeCombo(1)
    widget.combo_expression = FakeCombo(2)
    widget.emo_table = FakeTable()
    widget._selected_motion_candidates = []
    widget._refresh_costume_detail_ui = lambda: None
    widget._hot_reload_current_costume_mapping = lambda: hot_reload_calls.append(True)

    widget._apply_dropdown_to_selected_emotion()

    assert widget.mgr.saved_overrides == [
        (
            "tomori",
            "casual",
            "happy",
            {"mtn": "happy01", "type": 1, "exp": 2},
        )
    ]
    assert hot_reload_calls == [True]
