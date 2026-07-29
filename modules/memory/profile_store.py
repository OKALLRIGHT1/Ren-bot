import json
import os
import re
from datetime import datetime, timezone

try:
    from modules.llm import chat_with_ai
except Exception:
    chat_with_ai = None


class ProfileStore:
    def __init__(self, path: str):
        self.path = path
        self.data = {
            "user": {
                "name": "Master",
                "likes": {"general": []},
                "dislikes": [],
                "status": [],
                "notes": [],
            },
            "agent": {
                "name": "Suzu",
                "likes": {"general": []},
                "dislikes": [],
                "traits": [],
            },
            "updated_at": None,
        }
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for role in ["user", "agent"]:
                    if role in loaded:
                        if "likes" in loaded[role] and isinstance(
                            loaded[role]["likes"], list
                        ):
                            loaded[role]["likes"] = {"general": loaded[role]["likes"]}
                        self.data[role].update(loaded[role])
                self.data["updated_at"] = loaded.get("updated_at")
        except Exception as e:
            print(f"⚠️ [Profile] 加载失败: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [Profile] 保存失败: {e}")

    def extract_and_update(self, role: str, text: str, meta: dict | None = None):
        if not chat_with_ai:
            return
        t = (text or "").strip()
        if len(t) < 4:
            return
        safe_meta = meta or {}
        path = str(safe_meta.get("path") or "").strip().lower()
        if safe_meta.get("hidden") or path in {
            "direct",
            "tool_use",
            "summary",
            "summary_makeup",
        }:
            return
        if t.startswith("[") or t.startswith("System:") or t.startswith("/"):
            return

        target_role_desc = "用户(User)" if role == "user" else "五十铃怜(Assistant)"
        prompt = f'''
Analyze the following conversation snippet.
Speaker Role: {target_role_desc}
Speaker's Words: "{t}"

Task: Extract facts about the Speaker into a STRUCTURED JSON.
Structure Requirement:
- "likes": A dictionary with sub-categories:
    - "music": Songs, artists, bands, genres
    - "games": Game titles, platforms, types
    - "food": Food, drinks, flavors
    - "general": Hobbies, habits, colors, or anything else
- "dislikes": List of things hated
- "status": List of current activities
- "name": String
- "traits": List of personality traits (only if Speaker is Assistant)

Rules:
1. Ignore one-off command prompts, generated image prompts, file paths, and operational instructions.
2. Output JSON ONLY.
'''
        try:
            response = chat_with_ai(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="profile_extract",
            )
            data = self._parse_profile_json(response)
            if not data:
                return

            target_key = "user" if role == "user" else "agent"
            has_update = False

            if "name" in data and data["name"]:
                new_name = str(data["name"]).strip()
                current_name = self.data[target_key].get("name")
                bad_names = [
                    "user",
                    "User",
                    "USER",
                    "用户",
                    "unknown",
                    "Unknown",
                    "None",
                    "我",
                    "自己",
                ]
                is_bad = new_name in bad_names or len(new_name) < 2
                if not is_bad:
                    if (not current_name) or (current_name in ["user", "User", "用户"]):
                        self.data[target_key]["name"] = new_name
                        has_update = True
                        print(f"📝 [Profile] 自动捕获名字: {new_name}")
                    elif current_name != new_name:
                        print(
                            f"🛡️ [Profile] 拦截名字覆盖: {current_name} -> {new_name} (已忽略)"
                        )

            if "likes" in data and isinstance(data["likes"], dict):
                if not isinstance(self.data[target_key].get("likes"), dict):
                    self.data[target_key]["likes"] = {
                        "music": [],
                        "games": [],
                        "food": [],
                        "general": [],
                    }
                for category, items in data["likes"].items():
                    if category not in ["music", "games", "food", "general"]:
                        category = "general"
                    if isinstance(items, list):
                        current_list = self.data[target_key]["likes"].get(category, [])
                        for item in items:
                            if item not in current_list and len(item) < 20:
                                current_list.append(item)
                                limit = 50 if category in ["music", "games"] else 30
                                if len(current_list) > limit:
                                    current_list.pop(0)
                                self.data[target_key]["likes"][category] = current_list
                                has_update = True
                                print(
                                    f"📝 [Profile] 新增档案 ({target_key}.likes.{category}): {item}"
                                )

            for field in ["dislikes", "status", "traits"]:
                if field in data and isinstance(data[field], list):
                    current_list = self.data[target_key].get(field, [])
                    for item in data[field]:
                        if item not in current_list and len(item) < 20:
                            current_list.append(item)
                            if len(current_list) > 20:
                                current_list.pop(0)
                            self.data[target_key][field] = current_list
                            has_update = True
                            print(
                                f"📝 [Profile] 新增档案 ({target_key}.{field}): {item}"
                            )

            if has_update:
                self.save()
        except Exception as e:
            print(f"⚠️ [Profile] 提取失败: {e}")

    def _parse_profile_json(self, response: str):
        text = str(response or "").strip()
        if not text:
            return None

        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        raw = m.group(0)

        candidates = [raw]
        candidates.append(re.sub(r",\s*([}\]])", r"\1", raw))
        candidates.append(re.sub(r"\n\s*//.*", "", raw))
        candidates.append(re.sub(r"\n\s*#.*", "", raw))

        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

        fixed = re.sub(r",\s*([}\]])", r"\1", raw)
        fixed = re.sub(r"\n\s*//.*", "", fixed)
        fixed = re.sub(r"\n\s*#.*", "", fixed)
        return json.loads(fixed)

    def format_for_prompt(self) -> str:
        out = []

        def _format_one_role(role_key, display_name):
            data = self.data.get(role_key, {})
            lines = []
            name = data.get("name")
            if name:
                if role_key == "user":
                    lines.append(f"【称呼指引】你必须称呼对方为：{name}")
                else:
                    lines.append(f"- {display_name}称呼/名字：{name}")
            status = data.get("status")
            if isinstance(status, list) and status:
                lines.append(f"- {display_name}当前状态：{'、'.join(status[-3:])}")
            likes = data.get("likes")
            if isinstance(likes, dict):
                for category, items in likes.items():
                    if isinstance(items, list) and items:
                        lines.append(
                            f"- {display_name}喜好({category})：{'、'.join(items[-5:])}"
                        )
            for field, label in (
                ("dislikes", "雷点"),
                ("traits", "性格"),
                ("notes", "备注"),
            ):
                items = data.get(field)
                if isinstance(items, list) and items:
                    lines.append(f"- {display_name}{label}：{'、'.join(items[-5:])}")
            return lines

        user_lines = _format_one_role("user", "用户")
        agent_lines = _format_one_role("agent", "你")
        if user_lines:
            out.append("【用户档案】")
            out.extend(user_lines)
        if agent_lines:
            out.append("\n【自我认知 (你)】")
            out.extend(agent_lines)
        return "\n".join(out)
