import json
from pathlib import Path
from typing import Any, Dict, List


COMMAND_PREFIXES = ("/egg_reload", "/egg")


class Plugin:
    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self._plugin_dir = Path(__file__).resolve().parent
        self._settings: Dict[str, Any] = {}
        self._egg_data: List[Dict[str, Any]] = []
        self.reload_config()

    def reload_config(self):
        try:
            config = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
        settings = config.get("settings") or {}
        self._settings = {
            "data_path": self._read_setting(settings, "data_path", "data/egg_data.json"),
            "result_limit": int(self._read_setting(settings, "result_limit", 50) or 50),
        }
        self._egg_data = self._load_egg_data()

    def _read_setting(self, settings: Dict[str, Any], key: str, default: Any) -> Any:
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return default if value is None else value

    def _data_path(self) -> Path:
        path = Path(str(self._settings.get("data_path") or "data/egg_data.json"))
        if not path.is_absolute():
            path = self._plugin_dir / path
        return path

    def _load_egg_data(self) -> List[Dict[str, Any]]:
        path = self._data_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []

        items: List[Dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            try:
                items.append(
                    {
                        "name": name,
                        "size_min": float(row.get("size_min")),
                        "size_max": float(row.get("size_max")),
                        "weight_min": float(row.get("weight_min")),
                        "weight_max": float(row.get("weight_max")),
                    }
                )
            except Exception:
                continue
        return items

    def should_handle_direct(
        self, user_text: str, context: dict, matched_alias: str
    ) -> bool:
        text = str(user_text or "").strip()
        return any(text.startswith(prefix) for prefix in COMMAND_PREFIXES)

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        text = str(args or "").strip()
        if text.startswith("/egg_reload"):
            self._egg_data = self._load_egg_data()
            return f"数据已重新加载，当前共 {len(self._egg_data)} 条精灵数据"
        return self._search(text)

    def _search(self, text: str) -> str:
        parts = str(text or "").split()
        if len(parts) < 3:
            return "参数格式错误，请使用：/egg 尺寸 重量"

        try:
            size = float(parts[1])
            weight = float(parts[2])
        except ValueError:
            return "请输入有效的数字"

        if not self._egg_data:
            return "数据文件不存在或无数据，请联系管理员"

        matched = [
            item
            for item in self._egg_data
            if self._matches(item, size=size, weight=weight)
        ]
        matched.sort(key=lambda item: item["name"])
        limit = max(1, int(self._settings.get("result_limit") or 50))
        visible = matched[:limit]

        size_str = f"{size:.3f}"
        weight_str = f"{weight:.3f}"
        if visible:
            names = "、".join(item["name"] for item in visible)
            suffix = ""
            if len(matched) > len(visible):
                suffix = f"\n结果过多，仅显示前 {len(visible)} 项。"
            return (
                f"【查询结果】\n"
                f"输入：尺寸 {size_str}，重量 {weight_str}\n"
                f"匹配精灵：{names}{suffix}"
            )
        return (
            f"【查询结果】\n"
            f"输入：尺寸 {size_str}，重量 {weight_str}\n"
            f"未找到匹配的精灵，请检查输入的尺寸和重量。"
        )

    def _matches(self, item: Dict[str, Any], *, size: float, weight: float) -> bool:
        return (
            item["size_min"] <= size <= item["size_max"]
            and item["weight_min"] <= weight <= item["weight_max"]
        )
