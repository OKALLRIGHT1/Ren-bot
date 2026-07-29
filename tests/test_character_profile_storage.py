from __future__ import annotations


def test_add_character_does_not_write_legacy_memory_items(tmp_path, monkeypatch):
    import modules.character_manager as character_manager_module
    import modules.memory_sqlite as memory_sqlite

    manager = character_manager_module.CharacterManager.__new__(
        character_manager_module.CharacterManager
    )
    manager.data = {"active_id": None, "characters": {}}
    monkeypatch.setattr(character_manager_module, "DATA_FILE", str(tmp_path / "characters.json"))

    calls = []
    monkeypatch.setattr(
        memory_sqlite,
        "get_memory_store",
        lambda: calls.append("legacy-write"),
    )

    assert manager.add_character("char_test", "测试角色", "角色设定") is True
    assert calls == []
    assert manager.get_character("char_test")["name"] == "测试角色"


def test_normalize_schema_preserves_character_and_costume_badges():
    from modules.character_manager import CharacterManager

    manager = CharacterManager.__new__(CharacterManager)
    manager.data = {
        "active_id": "suzu",
        "characters": {
            "suzu": {
                "name": "Suzu",
                "user_address": "Master",
                "aliases": [],
                "assistant_badge": {"path": "data/assistant_badges/suzu.png"},
                "current_costume": "winter",
                "default_emotion_map": {},
                "costumes": {
                    "winter": {
                        "path": "winter/model.model3.json",
                        "emotion_map": {},
                        "assistant_badge": {
                            "path": "data/assistant_badges/winter.png"
                        },
                    }
                },
            }
        },
    }
    manager.save = lambda: None

    manager._normalize_schema()

    character = manager.data["characters"]["suzu"]
    assert character["assistant_badge"]["path"].endswith("suzu.png")
    assert character["costumes"]["winter"]["assistant_badge"]["path"].endswith(
        "winter.png"
    )
