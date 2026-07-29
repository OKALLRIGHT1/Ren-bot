from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from services.gui_api.characters_service import CharactersService


def _catalog() -> Dict[str, Any]:
    return {
        "active_id": "suzu",
        "characters": {
            "suzu": {
                "name": "Suzu",
                "description": "desk friend",
                "prompt": "you are suzu",
                "user_address": "主人",
                "catchphrase": {"enabled": True, "items": ["嗯"]},
                "current_costume": "default",
                "costumes": {
                    "default": {
                        "path": "models/suzu/default.model3.json",
                        "emotion_map": {"happy": ["idle"]},
                    }
                },
                "tts_config": {"enabled": True, "voice": "zh-CN"},
                "qq_profile": {"nickname": "铃"},
                "aliases": ["小铃"],
                "default_emotion_map": {"idle": ["idle"]},
            }
        },
    }


def test_list_characters_summary(tmp_path: Path):
    path = tmp_path / "characters.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = CharactersService(path)
    data = service.list_characters()
    assert data["active_id"] == "suzu"
    assert data["characters"][0]["id"] == "suzu"
    assert data["characters"][0]["name"] == "Suzu"
    assert data["characters"][0]["costume_count"] == 1
    assert data["characters"][0]["current_costume"] == "default"


def test_get_character_detail(tmp_path: Path):
    path = tmp_path / "characters.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = CharactersService(path)
    detail = service.get_character("suzu")
    assert detail["ok"] is True
    assert detail["data"]["prompt"] == "you are suzu"
    assert detail["data"]["costumes"][0]["name"] == "default"
    assert detail["data"]["tts"]["enabled"] is True


def test_upsert_and_activate_character(tmp_path: Path):
    path = tmp_path / "characters.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = CharactersService(path)
    created = service.upsert_character(
        {
            "id": "nova",
            "name": "Nova",
            "description": "new",
            "prompt": "hello",
            "user_address": "你",
        }
    )
    assert created["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "nova" in raw["characters"]
    activated = service.activate_character("nova")
    assert activated["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["active_id"] == "nova"


def test_delete_character_rejects_active(tmp_path: Path):
    path = tmp_path / "characters.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = CharactersService(path)
    result = service.delete_character("suzu")
    assert result["ok"] is False
    assert result["error"] == "cannot_delete_active"


def test_upsert_costume_and_emotion_map(tmp_path: Path):
    path = tmp_path / "characters.json"
    path.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    service = CharactersService(path)
    costume = service.upsert_costume(
        "suzu",
        {
            "name": "school",
            "path": "models/suzu/school.model3.json",
            "emotion_map": {"sad": ["cry"]},
        },
    )
    assert costume["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "school" in raw["characters"]["suzu"]["costumes"]
    wear = service.set_current_costume("suzu", "school")
    assert wear["ok"] is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["characters"]["suzu"]["current_costume"] == "school"
