import modules.character_manager as character_manager_module
from modules.character_manager import CharacterManager


def test_character_manager_saves_multiple_motion_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(character_manager_module, "DATA_FILE", str(tmp_path / "characters.json"))
    mgr = CharacterManager()
    mgr.data = {
        "active_id": "tomori",
        "characters": {
            "tomori": {
                "name": "tomori",
                "prompt": "",
                "costumes": {"casual": {"path": "model.json", "emotion_map": {}}},
                "current_costume": "casual",
            }
        },
    }

    mgr.set_costume_emotion_override(
        "tomori",
        "casual",
        "sad",
        {
            "mtn": "cry01:cry01",
            "type": 0,
            "motions": [
                {"mtn": "cry01:cry01", "type": 0},
                {"mtn": "cry02:cry02", "type": 1},
            ],
            "exp": 3,
        },
    )

    saved = mgr.data["characters"]["tomori"]["costumes"]["casual"]["emotion_map"]["sad"]
    assert saved["mtn"] == "cry01:cry01"
    assert saved["type"] == 0
    assert saved["motions"] == [
        {"mtn": "cry01:cry01", "type": 0},
        {"mtn": "cry02:cry02", "type": 1},
    ]
    assert saved["exp"] == 3


def test_character_default_emotion_map_overrides_model_derivation(tmp_path, monkeypatch):
    monkeypatch.setattr(character_manager_module, "DATA_FILE", str(tmp_path / "characters.json"))
    monkeypatch.setattr(
        character_manager_module,
        "_derive_emotion_map_from_model",
        lambda path: {"happy": {"mtn": f"derived:{path}", "type": 0, "exp": 1}},
    )
    mgr = CharacterManager()
    mgr.data = {
        "active_id": "tomori",
        "characters": {
            "tomori": {
                "name": "tomori",
                "prompt": "",
                "default_emotion_map": {
                    "happy": {"mtn": "character-default", "type": 0, "exp": 2}
                },
                "costumes": {"stage": {"path": "stage.model3.json", "emotion_map": {}}},
                "current_costume": "stage",
            }
        },
    }

    runtime = mgr.get_costume_runtime_config("tomori", "stage")

    assert runtime["emotion_map"]["happy"] == {
        "mtn": "character-default",
        "type": 0,
        "exp": 2,
    }
    assert runtime["character_default_emotion_keys"] == ["happy"]


def test_costume_emotion_map_overrides_character_default(tmp_path, monkeypatch):
    monkeypatch.setattr(character_manager_module, "DATA_FILE", str(tmp_path / "characters.json"))
    monkeypatch.setattr(
        character_manager_module,
        "_derive_emotion_map_from_model",
        lambda path: {"happy": {"mtn": "derived", "type": 0, "exp": 1}},
    )
    mgr = CharacterManager()
    mgr.data = {
        "active_id": "tomori",
        "characters": {
            "tomori": {
                "name": "tomori",
                "prompt": "",
                "default_emotion_map": {
                    "happy": {"mtn": "character-default", "type": 0, "exp": 2}
                },
                "costumes": {
                    "stage": {
                        "path": "stage.model3.json",
                        "emotion_map": {
                            "happy": {"mtn": "costume-override", "type": 1, "exp": 3}
                        },
                    }
                },
                "current_costume": "stage",
            }
        },
    }

    runtime = mgr.get_costume_runtime_config("tomori", "stage")

    assert runtime["emotion_map"]["happy"] == {
        "mtn": "costume-override",
        "type": 1,
        "exp": 3,
    }


def test_normalize_schema_generates_character_default_from_current_costume(tmp_path, monkeypatch):
    monkeypatch.setattr(character_manager_module, "DATA_FILE", str(tmp_path / "characters.json"))
    calls = []

    def fake_derive(path):
        calls.append(path)
        return {"idle": {"mtn": f"derived:{path}", "type": 0}}

    monkeypatch.setattr(character_manager_module, "_derive_emotion_map_from_model", fake_derive)
    mgr = CharacterManager()
    calls.clear()
    mgr.data = {
        "active_id": "tomori",
        "characters": {
            "tomori": {
                "name": "tomori",
                "prompt": "",
                "costumes": {
                    "first": {"path": "first.model3.json", "emotion_map": {}},
                    "main": {
                        "path": "main.model3.json",
                        "emotion_map": {
                            "happy": {"mtn": "manual-main-happy", "type": 1, "exp": 3}
                        },
                    },
                },
                "current_costume": "main",
            }
        },
    }

    mgr._normalize_schema()

    char = mgr.data["characters"]["tomori"]
    assert calls == ["main.model3.json"]
    assert char["default_emotion_map"] == {
        "idle": {"mtn": "derived:main.model3.json", "type": 0},
        "happy": {"mtn": "manual-main-happy", "type": 1, "exp": 3},
    }
