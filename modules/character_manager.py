import json
import os
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, Optional

DATA_FILE = "data/characters.json"

DEFAULT_EMOTION_KEYS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "shy",
    "flustered",
    "confused",
    "think",
    "idle",
    "idle_random",
    "music",
]

DEFAULT_CATCHPHRASE_CONFIG = {
    "enabled": False,
    "text": "",
    "probability": 0,
}

EMOTION_MOTION_PREFERENCES = {
    "neutral": ["nf03", "nf01", "idle01", "motion_001", "motion_100", "motion_000"],
    "happy": ["smile04", "smile03", "smile01", "motion_100"],
    "sad": ["sad01", "cry03", "cry01", "motion_100"],
    "angry": ["angry01", "angry03", "angry02", "motion_200"],
    "shy": ["shame01", "shame02", "odoodo01", "motion_300"],
    "flustered": ["shame01", "shame02", "odoodo01", "motion_300"],
    "confused": ["surprised02", "surprised01", "thinking01", "motion_400"],
    "think": ["thinking01", "thinking02", "motion_001"],
    "idle": ["idle01", "nf03", "motion_000", "motion_001"],
    "idle_random": ["idle02", "idle03", "nf01", "smile01", "motion_001"],
    "music": ["smile04", "smile03", "motion_100"],
}


def _resolve_model_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(raw)


def _pick_first_existing(candidates: list[str], available: dict[str, Any]) -> Any:
    if not available:
        return None
    lowered = {str(k).lower(): v for k, v in available.items()}
    for name in candidates:
        key = str(name).lower()
        if key in lowered:
            return lowered[key]
    for name in candidates:
        needle = str(name).lower()
        for key, value in lowered.items():
            if needle and needle in key:
                return value
    return None


def _read_model_motion_meta(model_path: str) -> tuple[dict[str, str], dict[str, int]]:
    """Return motion tokens and expression indices keyed by readable names."""
    abs_path = _resolve_model_path(model_path)
    if not abs_path or not os.path.exists(abs_path):
        return {}, {}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, {}

    motions: dict[str, str] = {}
    expressions: dict[str, int] = {}

    file_refs = data.get("FileReferences") if isinstance(data, dict) else None
    if isinstance(file_refs, dict):
        raw_motions = file_refs.get("Motions") or {}
        if isinstance(raw_motions, dict):
            for group, entries in raw_motions.items():
                if not isinstance(entries, list):
                    continue
                group_name = str(group or "").strip()
                for idx, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("Name") or "").strip()
                    if not name:
                        file_name = os.path.basename(str(entry.get("File") or ""))
                        name = file_name.split(".", 1)[0] if file_name else f"{group_name}_{idx}"
                    token = f"{group_name}:{name}" if group_name else name
                    motions[name] = token

        raw_expressions = file_refs.get("Expressions") or []
        if isinstance(raw_expressions, list):
            for idx, entry in enumerate(raw_expressions):
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("Name") or entry.get("File") or "").strip()
                if name:
                    base = os.path.basename(name).split(".", 1)[0]
                    expressions[base] = idx
                    expressions[name] = idx

        return motions, expressions

    raw_motions = data.get("motions") if isinstance(data, dict) else None
    if isinstance(raw_motions, dict):
        for name in raw_motions.keys():
            key = str(name or "").strip()
            if key:
                motions[key] = key

    raw_expressions = data.get("expressions") if isinstance(data, dict) else None
    if isinstance(raw_expressions, list):
        for idx, entry in enumerate(raw_expressions):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or entry.get("file") or "").strip()
            if name:
                base = os.path.basename(name).split(".", 1)[0]
                expressions[base] = idx
                expressions[name] = idx

    return motions, expressions


def _derive_emotion_map_from_model(model_path: str) -> dict[str, dict[str, Any]]:
    motions, expressions = _read_model_motion_meta(model_path)
    if not motions:
        return {}

    derived: dict[str, dict[str, Any]] = {}
    for emotion, candidates in EMOTION_MOTION_PREFERENCES.items():
        motion = _pick_first_existing(candidates, motions)
        if not motion:
            continue
        payload: dict[str, Any] = {"mtn": motion, "type": 0}
        exp = _pick_first_existing(candidates + ["default"], expressions)
        if exp is not None:
            payload["exp"] = int(exp)
        derived[emotion] = payload
    return derived


def _normalize_emotion_payload(cfg: Optional[dict]) -> Optional[dict[str, Any]]:
    if not isinstance(cfg, dict):
        return None

    raw_motions = cfg.get("motions")
    motions = []
    if isinstance(raw_motions, list):
        for item in raw_motions:
            if isinstance(item, dict):
                motion_mtn = str(item.get("mtn") or "").strip()
                if not motion_mtn:
                    continue
                try:
                    motion_type = int(item.get("type", cfg.get("type", 0)) or 0)
                except Exception:
                    motion_type = 0
                candidate = {"mtn": motion_mtn, "type": motion_type}
            else:
                motion_mtn = str(item or "").strip()
                if not motion_mtn:
                    continue
                try:
                    motion_type = int(cfg.get("type", 0) or 0)
                except Exception:
                    motion_type = 0
                candidate = {"mtn": motion_mtn, "type": motion_type}
            if candidate not in motions:
                motions.append(candidate)

    mtn = str(cfg.get("mtn") or "").strip()
    if not mtn and not motions:
        return None

    exp = cfg.get("exp")
    try:
        exp = int(exp) if exp is not None and str(exp).strip() != "" else None
    except Exception:
        exp = None

    try:
        type_val = int(cfg.get("type", 0) or 0)
    except Exception:
        type_val = 0

    if motions:
        primary = motions[0]
        payload: dict[str, Any] = {
            "mtn": primary["mtn"],
            "type": int(primary.get("type", type_val) or 0),
            "motions": motions,
        }
    else:
        payload = {"mtn": mtn, "type": type_val}
    if exp is not None:
        payload["exp"] = exp
    return payload


class CharacterManager:
    def __init__(self):
        self.data = {"active_id": None, "characters": {}}
        self.load()

    def load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"❌ 加载角色数据失败: {e}")

        if not self.data["characters"]:
            self._migrate_from_config()

        self._normalize_schema()

    def save(self):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ 保存角色数据失败: {e}")

    def _migrate_from_config(self):
        try:
            from config import COSTUME_MAP, PERSONA_PROMPT

            char_id = "default_char"
            costumes = {}
            for name, cfg in (COSTUME_MAP or {}).items():
                if isinstance(cfg, dict):
                    costumes[name] = {
                        "path": cfg.get("path", ""),
                        "emotion_map": cfg.get("emotion_map", {})
                        if isinstance(cfg.get("emotion_map", {}), dict)
                        else {},
                    }
                elif isinstance(cfg, str):
                    costumes[name] = {"path": cfg, "emotion_map": {}}
            self.data["characters"][char_id] = {
                "name": "默认角色",
                "prompt": PERSONA_PROMPT,
                "costumes": costumes,
                "current_costume": next(iter(costumes.keys()), None),
            }
            self.data["active_id"] = char_id
            self.save()
        except:
            pass

    def _normalize_schema(self):
        changed = False
        characters = self.data.setdefault("characters", {})

        for _, char_data in characters.items():
            aliases = char_data.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [line.strip() for line in aliases.splitlines() if line.strip()]
                changed = True
            elif not isinstance(aliases, list):
                aliases = []
                changed = True
            normalized_aliases = []
            for alias in aliases:
                value = str(alias or "").strip()
                if value and value not in normalized_aliases:
                    normalized_aliases.append(value)
            if char_data.get("aliases") != normalized_aliases:
                char_data["aliases"] = normalized_aliases
                changed = True

            costumes = char_data.get("costumes") or {}
            normalized = {}

            for costume_name, raw_cfg in costumes.items():
                if isinstance(raw_cfg, dict):
                    path = raw_cfg.get("path", "")
                    emotion_map = raw_cfg.get("emotion_map", {})
                    if not isinstance(emotion_map, dict):
                        emotion_map = {}
                        changed = True
                else:
                    path = str(raw_cfg)
                    emotion_map = {}
                    changed = True

                normalized[costume_name] = {
                    "path": path,
                    "emotion_map": emotion_map,
                }

            if normalized != costumes:
                char_data["costumes"] = normalized
                changed = True

            current_costume = char_data.get("current_costume")
            if current_costume not in normalized:
                char_data["current_costume"] = next(iter(normalized.keys()), None)
                changed = True

            default_emotion_map = char_data.get("default_emotion_map")
            if not isinstance(default_emotion_map, dict):
                source_costume = (char_data.get("current_costume") or next(iter(normalized.keys()), None))
                source_cfg = normalized.get(source_costume) if source_costume else None
                default_emotion_map = self._build_default_emotion_map_from_costume(
                    source_cfg
                )
                char_data["default_emotion_map"] = default_emotion_map
                changed = True

            tts_cfg = char_data.get("tts_config") or {}
            if not isinstance(tts_cfg, dict):
                tts_cfg = {}
            normalized_tts = {
                "enabled": bool(tts_cfg.get("enabled", False)),
                "gpt_w": str(tts_cfg.get("gpt_w", "") or ""),
                "sov_w": str(tts_cfg.get("sov_w", "") or ""),
                "ref_wav": str(tts_cfg.get("ref_wav", "") or ""),
                "prompt_lang": str(tts_cfg.get("prompt_lang", "ja") or "ja"),
                "prompt_text": str(tts_cfg.get("prompt_text", "") or ""),
            }
            if char_data.get("tts_config") != normalized_tts:
                char_data["tts_config"] = normalized_tts
                changed = True

            catchphrase_cfg = char_data.get("catchphrase")
            if catchphrase_cfg is None and str(char_data.get("name") or "") == "五十铃怜":
                catchphrase_cfg = {
                    "enabled": True,
                    "text": "……はい。",
                    "probability": 18,
                }
                changed = True
            normalized_catchphrase = self._normalize_catchphrase_config(catchphrase_cfg)
            if char_data.get("catchphrase") != normalized_catchphrase:
                char_data["catchphrase"] = normalized_catchphrase
                changed = True

        if changed:
            self.save()

    def _normalize_catchphrase_config(self, cfg) -> dict:
        if isinstance(cfg, str):
            text = cfg.strip()
            return {
                "enabled": bool(text),
                "text": text,
                "probability": 18 if text else 0,
            }
        if not isinstance(cfg, dict):
            return deepcopy(DEFAULT_CATCHPHRASE_CONFIG)

        text = str(cfg.get("text", "") or "").strip()
        try:
            probability = int(cfg.get("probability", 0))
        except Exception:
            probability = 0
        probability = max(0, min(100, probability))
        return {
            "enabled": bool(cfg.get("enabled", False)) and bool(text) and probability > 0,
            "text": text,
            "probability": probability,
        }

    # --- CRUD ---
    def get_all_characters(self) -> Dict:
        return self.data.get("characters", {})

    def get_character(self, char_id: str) -> Optional[dict]:
        return self.data["characters"].get(char_id)

    # 🔥 核心修改：新建角色时，同步写入 SQLite 档案
    def add_character(self, char_id: str, name: str, prompt: str):
        # 1. 先存入 JSON (形象管理)
        if char_id in self.data["characters"]:
            return False

        self.data["characters"][char_id] = {
            "name": name,
            "aliases": [],
            "prompt": prompt,
            "default_emotion_map": {},
            "catchphrase": deepcopy(DEFAULT_CATCHPHRASE_CONFIG),
            "costumes": {},
            "tts_config": {
                "enabled": False,
                "gpt_w": "",
                "sov_w": "",
                "ref_wav": "",
                "prompt_lang": "ja",
                "prompt_text": "",
            },
            "qq_profile": {
                "enabled": False,
                "nickname": "",
                "avatar_path": "",
            },
        }
        self.save()

        # 2. 同步写入 SQLite (记忆管理)
        try:
            from modules.memory_sqlite import get_memory_store

            store = get_memory_store()
            if store:
                from datetime import datetime

                # 构造初始档案条目
                # 条目1: 名字
                name_id = f"p_init_name_{char_id}_{int(time.time())}"
                store.upsert_item(
                    {
                        "id": name_id,
                        "type": "agent_profile",
                        "text": name,
                        "tags": [f"role:{char_id}", "name"],  # 关键标签
                        "status": "active",
                        "updated_at": datetime.now().isoformat(),
                    }
                )

                # 条目2: 默认性格占位符 (可选)
                trait_id = f"p_init_trait_{char_id}_{int(time.time())}"
                store.upsert_item(
                    {
                        "id": trait_id,
                        "type": "agent_profile",
                        "text": "温柔 / 冷静 (初始性格)",
                        "tags": [f"role:{char_id}", "traits"],  # 关键标签
                        "status": "active",
                        "updated_at": datetime.now().isoformat(),
                    }
                )

                print(f"✅ [Sync] 已同步创建角色档案: {name} (ID: {char_id})")
        except Exception as e:
            print(f"⚠️ [Sync] 档案同步失败 (不影响角色创建): {e}")

        return True

    def delete_character(self, char_id: str):
        if char_id in self.data["characters"]:
            del self.data["characters"][char_id]
            if self.data["active_id"] == char_id:
                self.data["active_id"] = None
            self.save()

            # 可选：删除角色时，是否归档对应的记忆？
            # 为了数据安全，这里暂时不动数据库，保留记忆。

            return True
        return False

    def add_costume(self, char_id: str, costume_name: str, model_path: str):
        char = self.get_character(char_id)
        if not char:
            return False

        rel_path = model_path.replace("\\", "/")
        if "assets/" in rel_path:
            rel_path = "assets/" + rel_path.split("assets/", 1)[1]

        char["costumes"][costume_name] = {"path": rel_path, "emotion_map": {}}
        if not char.get("current_costume"):
            char["current_costume"] = costume_name
        self.save()
        return True

    def get_costume_runtime_config(self, char_id: str, costume_name: str) -> dict:
        """返回给 Live2D 的服装配置（含每服装情绪映射）。"""
        char = self.get_character(char_id)
        if not char:
            return {}
        costume = (char.get("costumes") or {}).get(costume_name) or {}
        character_map = char.get("default_emotion_map")
        if not isinstance(character_map, dict):
            character_map = {}
        emotion_map = costume.get("emotion_map")
        if not isinstance(emotion_map, dict):
            emotion_map = {}
        model_path = str(costume.get("path") or "").strip()
        derived_map = _derive_emotion_map_from_model(model_path)
        runtime_map = deepcopy(derived_map)
        runtime_map.update(deepcopy(character_map))
        runtime_map.update(deepcopy(emotion_map))
        return {
            "emotion_map": runtime_map,
            "derived_emotion_keys": sorted(derived_map.keys()),
            "character_default_emotion_keys": sorted(character_map.keys()),
            "costume_override_emotion_keys": sorted(emotion_map.keys()),
        }

    def _build_default_emotion_map_from_costume(self, costume_cfg: Any) -> dict[str, Any]:
        if not isinstance(costume_cfg, dict):
            return {}
        result = _derive_emotion_map_from_model(str(costume_cfg.get("path") or ""))
        overrides = costume_cfg.get("emotion_map")
        if isinstance(overrides, dict):
            result.update(deepcopy(overrides))
        return result

    def set_character_emotion_default(
        self, char_id: str, emotion: str, cfg: Optional[dict]
    ) -> bool:
        char = self.get_character(char_id)
        if not char:
            return False
        emo = (emotion or "").strip().lower()
        if not emo:
            return False

        emotion_map = char.setdefault("default_emotion_map", {})
        if not isinstance(emotion_map, dict):
            emotion_map = {}
            char["default_emotion_map"] = emotion_map

        payload = _normalize_emotion_payload(cfg)
        if payload is None:
            emotion_map.pop(emo, None)
        else:
            emotion_map[emo] = payload
        self.save()
        return True

    def generate_character_default_emotion_map(
        self, char_id: str, costume_name: Optional[str] = None
    ) -> bool:
        char = self.get_character(char_id)
        if not char:
            return False
        costumes = char.get("costumes") or {}
        if not isinstance(costumes, dict) or not costumes:
            char["default_emotion_map"] = {}
            self.save()
            return True

        source_name = costume_name if costume_name in costumes else char.get("current_costume")
        if source_name not in costumes:
            source_name = next(iter(costumes.keys()), None)
        source_cfg = costumes.get(source_name) if source_name else None
        if not isinstance(source_cfg, dict):
            return False

        char["default_emotion_map"] = self._build_default_emotion_map_from_costume(
            source_cfg
        )
        self.save()
        return True

    def set_costume_emotion_override(
        self, char_id: str, costume_name: str, emotion: str, cfg: Optional[dict]
    ):
        char = self.get_character(char_id)
        if not char:
            return False
        costumes = char.setdefault("costumes", {})
        costume = costumes.get(costume_name)
        if not isinstance(costume, dict):
            return False

        emo = (emotion or "").strip().lower()
        if not emo:
            return False

        emotion_map = costume.setdefault("emotion_map", {})
        if not isinstance(emotion_map, dict):
            emotion_map = {}
            costume["emotion_map"] = emotion_map

        payload = _normalize_emotion_payload(cfg)
        if payload is None:
            emotion_map.pop(emo, None)
            self.save()
            return True

        emotion_map[emo] = payload
        self.save()
        return True

    def delete_costume(self, char_id: str, costume_name: str):
        char = self.get_character(char_id)
        if char and costume_name in char["costumes"]:
            del char["costumes"][costume_name]
            if char.get("current_costume") == costume_name:
                char["current_costume"] = next(iter(char["costumes"].keys()), None)
            self.save()
            return True
        return False

    def get_current_costume_name(self, char_id: Optional[str] = None) -> Optional[str]:
        if not char_id:
            char_id = self.data.get("active_id")
        if not char_id:
            return None
        char = self.get_character(char_id)
        if not char:
            return None
        current = char.get("current_costume")
        if current in (char.get("costumes") or {}):
            return current
        costumes = char.get("costumes") or {}
        return next(iter(costumes.keys()), None)

    def set_current_costume_name(self, char_id: str, costume_name: str) -> bool:
        char = self.get_character(char_id)
        if not char:
            return False
        costumes = char.get("costumes") or {}
        if costume_name not in costumes:
            return False
        char["current_costume"] = costume_name
        self.save()
        return True

    def set_active_character(self, char_id: str):
        if char_id in self.data["characters"]:
            self.data["active_id"] = char_id
            self.save()
            return self.data["characters"][char_id]
        return None

    def get_tts_config(self, char_id: Optional[str] = None) -> dict:
        if not char_id:
            char_id = self.data.get("active_id")
        if not char_id:
            return {}
        char = self.get_character(char_id)
        if not char:
            return {}
        cfg = char.get("tts_config") or {}
        return cfg if isinstance(cfg, dict) else {}

    def get_catchphrase_config(self, char_id: Optional[str] = None) -> dict:
        if not char_id:
            char_id = self.data.get("active_id")
        if not char_id:
            return deepcopy(DEFAULT_CATCHPHRASE_CONFIG)
        char = self.get_character(char_id)
        if not char:
            return deepcopy(DEFAULT_CATCHPHRASE_CONFIG)
        cfg = self._normalize_catchphrase_config(char.get("catchphrase"))
        if char.get("catchphrase") != cfg:
            char["catchphrase"] = cfg
            self.save()
        return cfg

    def get_active_character(self):
        aid = self.data.get("active_id")
        if aid:
            return self.get_character(aid)
        chars = self.get_all_characters()
        if chars:
            first_id = list(chars.keys())[0]
            self.set_active_character(first_id)
            return chars[first_id]
        return None


# 全局单例
character_manager = CharacterManager()
