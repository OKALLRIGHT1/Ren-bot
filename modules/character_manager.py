import json
import os
import time
import uuid
from copy import deepcopy
from typing import Dict, Optional

DATA_FILE = "data/characters.json"

DEFAULT_EMOTION_KEYS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "flustered",
    "confused",
    "think",
    "idle",
    "music",
]


class CharacterManager:
    def __init__(self):
        self.data = {
            "active_id": None,
            "characters": {}
        }
        self.load()

    def load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"❌ 加载角色数据失败: {e}")

        if not self.data["characters"]:
            self._migrate_from_config()

        self._normalize_schema()

    def save(self):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
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
                        "emotion_map": cfg.get("emotion_map", {}) if isinstance(cfg.get("emotion_map", {}), dict) else {}
                    }
                elif isinstance(cfg, str):
                    costumes[name] = {"path": cfg, "emotion_map": {}}
            self.data["characters"][char_id] = {
                "name": "默认角色",
                "prompt": PERSONA_PROMPT,
                "costumes": costumes,
                "current_costume": next(iter(costumes.keys()), None)
            }
            self.data["active_id"] = char_id
            self.save()
        except:
            pass

    def _normalize_schema(self):
        changed = False
        characters = self.data.setdefault("characters", {})

        for _, char_data in characters.items():
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

        if changed:
            self.save()

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
            "prompt": prompt,
            "costumes": {}
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
                store.upsert_item({
                    "id": name_id,
                    "type": "agent_profile",
                    "text": name,
                    "tags": [f"role:{char_id}", "name"],  # 关键标签
                    "status": "active",
                    "updated_at": datetime.now().isoformat()
                })

                # 条目2: 默认性格占位符 (可选)
                trait_id = f"p_init_trait_{char_id}_{int(time.time())}"
                store.upsert_item({
                    "id": trait_id,
                    "type": "agent_profile",
                    "text": "温柔 / 冷静 (初始性格)",
                    "tags": [f"role:{char_id}", "traits"],  # 关键标签
                    "status": "active",
                    "updated_at": datetime.now().isoformat()
                })

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
        if not char: return False

        rel_path = model_path.replace("\\", "/")
        if "assets/" in rel_path:
            rel_path = "assets/" + rel_path.split("assets/", 1)[1]

        char["costumes"][costume_name] = {
            "path": rel_path,
            "emotion_map": {}
        }
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
        emotion_map = costume.get("emotion_map")
        if not isinstance(emotion_map, dict):
            emotion_map = {}
        return {
            "emotion_map": deepcopy(emotion_map)
        }

    def set_costume_emotion_override(self, char_id: str, costume_name: str, emotion: str, cfg: Optional[dict]):
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

        if not cfg:
            emotion_map.pop(emo, None)
            self.save()
            return True

        mtn = (cfg.get("mtn") or "").strip()
        exp = cfg.get("exp")
        type_val = cfg.get("type", 0)

        if not mtn:
            emotion_map.pop(emo, None)
            self.save()
            return True

        try:
            exp = int(exp) if exp is not None and str(exp).strip() != "" else None
        except Exception:
            exp = None

        try:
            type_val = int(type_val)
        except Exception:
            type_val = 0

        payload = {"mtn": mtn, "type": type_val}
        if exp is not None:
            payload["exp"] = exp

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
