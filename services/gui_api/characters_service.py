from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.gui_api.assistant_badge_service import AssistantBadgeStore, normalize_badge


def _load_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def _save_json(path: Path, data: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        return False


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _costume_list(costumes: Any) -> List[Dict[str, Any]]:
    if isinstance(costumes, dict):
        rows = []
        for name, cfg in costumes.items():
            row = _as_dict(cfg)
            rows.append(
                {
                    "name": str(name),
                    "path": str(row.get("path") or ""),
                    "emotion_map": _as_dict(row.get("emotion_map")),
                    "assistant_badge": normalize_badge(row.get("assistant_badge")),
                }
            )
        return sorted(rows, key=lambda item: item["name"])
    if isinstance(costumes, list):
        rows = []
        for item in costumes:
            row = _as_dict(item)
            rows.append(
                {
                    "name": str(row.get("name") or ""),
                    "path": str(row.get("path") or ""),
                    "emotion_map": _as_dict(row.get("emotion_map")),
                    "assistant_badge": normalize_badge(row.get("assistant_badge")),
                }
            )
        return [row for row in rows if row["name"]]
    return []


class CharactersService:
    def __init__(self, characters_path: Path) -> None:
        self.path = Path(characters_path)
        self.badges = AssistantBadgeStore(self.path)

    def _empty(self) -> Dict[str, Any]:
        return {"active_id": "", "characters": {}}

    def _load(self) -> Dict[str, Any]:
        data = _load_json(self.path, self._empty())
        characters = data.get("characters") if isinstance(data.get("characters"), dict) else {}
        return {
            "active_id": str(data.get("active_id") or ""),
            "characters": characters,
        }

    def _write(self, data: Dict[str, Any]) -> bool:
        return _save_json(self.path, data)

    def list_characters(self) -> Dict[str, Any]:
        data = self._load()
        rows = []
        for character_id, cfg in sorted(data["characters"].items(), key=lambda item: str(item[0])):
            row = _as_dict(cfg)
            costumes = _costume_list(row.get("costumes"))
            rows.append(
                {
                    "id": str(character_id),
                    "name": str(row.get("name") or character_id),
                    "description": str(row.get("description") or ""),
                    "current_costume": str(row.get("current_costume") or ""),
                    "costume_count": len(costumes),
                    "is_active": str(character_id) == data["active_id"],
                    "user_address": str(row.get("user_address") or ""),
                    "aliases": list(row.get("aliases") or [])
                    if isinstance(row.get("aliases"), list)
                    else [],
                }
            )
        return {"active_id": data["active_id"], "characters": rows}

    def get_character(self, character_id: str) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        row = _as_dict(data["characters"][character_id])
        tts = _as_dict(row.get("tts_config"))
        return {
            "ok": True,
            "data": {
                "id": character_id,
                "name": str(row.get("name") or character_id),
                "description": str(row.get("description") or ""),
                "prompt": str(row.get("prompt") or ""),
                "user_address": str(row.get("user_address") or ""),
                "catchphrase": row.get("catchphrase") if isinstance(row.get("catchphrase"), dict) else {},
                "aliases": list(row.get("aliases") or [])
                if isinstance(row.get("aliases"), list)
                else [],
                "current_costume": str(row.get("current_costume") or ""),
                "costumes": _costume_list(row.get("costumes")),
                "default_emotion_map": _as_dict(row.get("default_emotion_map")),
                "assistant_badge": normalize_badge(row.get("assistant_badge")),
                "tts": {
                    "enabled": bool(tts.get("enabled")),
                    "voice": str(tts.get("voice") or ""),
                    "gpt_w": str(tts.get("gpt_w") or ""),
                    "sov_w": str(tts.get("sov_w") or ""),
                    "ref_wav": str(
                        tts.get("ref_wav")
                        or tts.get("ref_audio")
                        or tts.get("refer_wav_path")
                        or ""
                    ),
                    "prompt_lang": str(tts.get("prompt_lang") or "ja"),
                    "prompt_text": str(tts.get("prompt_text") or ""),
                    "has_ref_audio": bool(
                        str(
                            tts.get("ref_wav")
                            or tts.get("ref_audio")
                            or tts.get("refer_wav_path")
                            or ""
                        ).strip()
                    ),
                },
                "qq_profile": _as_dict(row.get("qq_profile")),
                "is_active": character_id == data["active_id"],
            },
        }

    def upsert_character(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        character_id = str(payload.get("id") or payload.get("character_id") or "").strip()
        if not character_id:
            character_id = f"char_{uuid.uuid4().hex[:8]}"
        data = self._load()
        current = _as_dict(data["characters"].get(character_id))
        for key in (
            "name",
            "description",
            "prompt",
            "user_address",
            "avatar",
            "current_costume",
        ):
            if key in payload:
                current[key] = payload.get(key)
        if "catchphrase" in payload and isinstance(payload.get("catchphrase"), dict):
            current["catchphrase"] = payload.get("catchphrase")
        if "aliases" in payload and isinstance(payload.get("aliases"), list):
            current["aliases"] = [str(item) for item in payload.get("aliases") or []]
        if "default_emotion_map" in payload and isinstance(payload.get("default_emotion_map"), dict):
            current["default_emotion_map"] = payload.get("default_emotion_map")
        if "tts_config" in payload and isinstance(payload.get("tts_config"), dict):
            tts = _as_dict(current.get("tts_config"))
            incoming = _as_dict(payload.get("tts_config"))
            for key in (
                "enabled",
                "voice",
                "gpt_w",
                "sov_w",
                "prompt_lang",
                "prompt_text",
            ):
                if key in incoming:
                    tts[key] = incoming.get(key)
            ref_value = None
            for key in ("ref_wav", "ref_audio", "refer_wav_path"):
                if key in incoming:
                    ref_value = incoming.get(key)
                    break
            if ref_value is not None:
                tts["ref_wav"] = str(ref_value or "")
                tts["ref_audio"] = str(ref_value or "")
            current["tts_config"] = tts
        if "qq_profile" in payload and isinstance(payload.get("qq_profile"), dict):
            current["qq_profile"] = payload.get("qq_profile")
        if "costumes" not in current or not isinstance(current.get("costumes"), dict):
            current["costumes"] = _as_dict(current.get("costumes"))
        if not current.get("name"):
            current["name"] = character_id
        data["characters"][character_id] = current
        if not data["active_id"]:
            data["active_id"] = character_id
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        detail = self.get_character(character_id)
        return {"ok": True, "data": detail.get("data"), "list": self.list_characters()}

    def delete_character(self, character_id: str) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        if character_id == data["active_id"]:
            return {"ok": False, "error": "cannot_delete_active"}
        data["characters"].pop(character_id, None)
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_characters()}

    def activate_character(self, character_id: str) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        data["active_id"] = character_id
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return {"ok": True, "data": self.list_characters()}

    def upsert_costume(self, character_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        name = str(payload.get("name") or "").strip()
        if not character_id or not name:
            return {"ok": False, "error": "invalid_costume"}
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        costumes = character.get("costumes")
        if not isinstance(costumes, dict):
            costumes = {}
            for row in _costume_list(costumes):
                costumes[row["name"]] = {
                    "path": row["path"],
                    "emotion_map": row["emotion_map"],
                }
        current = _as_dict(costumes.get(name))
        if "path" in payload:
            current["path"] = str(payload.get("path") or "")
        if "emotion_map" in payload and isinstance(payload.get("emotion_map"), dict):
            current["emotion_map"] = payload.get("emotion_map")
        costumes[name] = current
        character["costumes"] = costumes
        if not character.get("current_costume"):
            character["current_costume"] = name
        data["characters"][character_id] = character
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return self.get_character(character_id)

    def delete_costume(self, character_id: str, costume_name: str) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        costume_name = str(costume_name or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        costumes = character.get("costumes")
        if not isinstance(costumes, dict) or costume_name not in costumes:
            return {"ok": False, "error": "costume_not_found"}
        if len(costumes) <= 1:
            return {"ok": False, "error": "cannot_delete_last_costume"}
        costumes.pop(costume_name, None)
        if character.get("current_costume") == costume_name:
            character["current_costume"] = next(iter(costumes.keys()), "")
        character["costumes"] = costumes
        data["characters"][character_id] = character
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return self.get_character(character_id)

    def set_current_costume(self, character_id: str, costume_name: str) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        costume_name = str(costume_name or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        costumes = character.get("costumes")
        if not isinstance(costumes, dict) or costume_name not in costumes:
            return {"ok": False, "error": "costume_not_found"}
        character["current_costume"] = costume_name
        data["characters"][character_id] = character
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return self.get_character(character_id)

    def import_badge(
        self,
        character_id: str,
        source_path: str,
        *,
        costume_name: str = "",
        scale: Any = 1.0,
        offset_x: Any = 0.0,
        offset_y: Any = 0.0,
    ) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        costume_name = str(costume_name or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        costumes = _as_dict(character.get("costumes"))
        if costume_name and costume_name not in costumes:
            return {"ok": False, "error": "costume_not_found"}
        copied = self.badges.import_image(character_id, source_path, costume_name)
        if not copied.get("ok"):
            return copied
        badge = self.badges.record(
            str(copied["path"]), scale, offset_x, offset_y
        )
        if costume_name:
            costume = _as_dict(costumes[costume_name])
            costume["assistant_badge"] = badge
            costumes[costume_name] = costume
            character["costumes"] = costumes
        else:
            character["assistant_badge"] = badge
        data["characters"][character_id] = character
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return self.get_badge(character_id, costume_name)

    def update_badge(
        self,
        character_id: str,
        *,
        costume_name: str = "",
        scale: Any = 1.0,
        offset_x: Any = 0.0,
        offset_y: Any = 0.0,
    ) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        costume_name = str(costume_name or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        owner = character
        if costume_name:
            costumes = _as_dict(character.get("costumes"))
            if costume_name not in costumes:
                return {"ok": False, "error": "costume_not_found"}
            owner = _as_dict(costumes[costume_name])
        current = normalize_badge(owner.get("assistant_badge"))
        if current is None:
            return {"ok": False, "error": "badge_not_found"}
        owner["assistant_badge"] = self.badges.record(
            current["path"], scale, offset_x, offset_y
        )
        if costume_name:
            costumes[costume_name] = owner
            character["costumes"] = costumes
        data["characters"][character_id] = character
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return self.get_badge(character_id, costume_name)

    def clear_badge(
        self, character_id: str, *, costume_name: str = ""
    ) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        costume_name = str(costume_name or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        if costume_name:
            costumes = _as_dict(character.get("costumes"))
            if costume_name not in costumes:
                return {"ok": False, "error": "costume_not_found"}
            costume = _as_dict(costumes[costume_name])
            costume.pop("assistant_badge", None)
            costumes[costume_name] = costume
            character["costumes"] = costumes
        else:
            character.pop("assistant_badge", None)
        data["characters"][character_id] = character
        if not self._write(data):
            return {"ok": False, "error": "write_failed"}
        return self.get_badge(character_id, costume_name)

    def get_badge(
        self, character_id: str, costume_name: str = ""
    ) -> Dict[str, Any]:
        character_id = str(character_id or "").strip()
        costume_name = str(costume_name or "").strip()
        data = self._load()
        if character_id not in data["characters"]:
            return {"ok": False, "error": "not_found"}
        character = _as_dict(data["characters"][character_id])
        costumes = _as_dict(character.get("costumes"))
        if costume_name and costume_name not in costumes:
            return {"ok": False, "error": "costume_not_found"}
        costume_badge = normalize_badge(
            _as_dict(costumes.get(costume_name)).get("assistant_badge")
        )
        character_badge = normalize_badge(character.get("assistant_badge"))
        badge = costume_badge or character_badge
        source = "costume" if costume_badge else "character" if character_badge else "none"
        image_data_url = self.badges.image_data_url(badge["path"]) if badge else ""
        if badge and not image_data_url:
            badge = None
            source = "none"
        return {
            "ok": True,
            "data": {
                "character_id": character_id,
                "costume_name": costume_name,
                "source": source,
                "badge": badge,
                "image_data_url": image_data_url,
            },
        }

    def get_current_badge(self) -> Dict[str, Any]:
        data = self._load()
        character_id = str(data.get("active_id") or "").strip()
        if not character_id or character_id not in data["characters"]:
            return {
                "ok": True,
                "data": {
                    "character_id": "",
                    "costume_name": "",
                    "source": "none",
                    "badge": None,
                    "image_data_url": "",
                },
            }
        character = _as_dict(data["characters"][character_id])
        costume_name = str(character.get("current_costume") or "").strip()
        return self.get_badge(character_id, costume_name)
