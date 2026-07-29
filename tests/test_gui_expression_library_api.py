from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.gui_api.expression_library_service import ExpressionLibraryGuiService


class FakeStore:
    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {
            "xp1": {
                "id": "xp1",
                "character_id": "",
                "character_name": "Suzu",
                "scene": "chat",
                "situation": "问候",
                "style": "轻松",
                "example": "早呀~",
                "content_list": ["早呀~", "早上好！"],
                "source": "manual",
                "quality_score": 8.0,
                "use_count": 2,
                "enabled": True,
                "meta": {},
                "updated_at": "2026-07-15",
            }
        }

    def list_expression_patterns(self, **kwargs: Any) -> List[Dict[str, Any]]:
        query = str(kwargs.get("query") or "").strip().lower()
        scene = str(kwargs.get("scene") or "").strip().lower()
        enabled_only = bool(kwargs.get("enabled_only"))
        rows = list(self.rows.values())
        if scene:
            rows = [row for row in rows if str(row.get("scene") or "").lower() == scene]
        if enabled_only:
            rows = [row for row in rows if row.get("enabled")]
        if query:
            rows = [
                row
                for row in rows
                if query in f"{row.get('style')} {row.get('example')} {row.get('situation')}".lower()
            ]
        return rows

    def get_expression_pattern(self, pattern_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(pattern_id)

    def upsert_expression_pattern(self, pattern: Dict[str, Any]) -> str:
        pattern_id = str(pattern.get("id") or f"xp{len(self.rows)+1}")
        row = dict(pattern)
        row["id"] = pattern_id
        content = row.get("content_list") if isinstance(row.get("content_list"), list) else []
        if not content and row.get("example"):
            content = [str(row.get("example"))]
        row["content_list"] = [str(item) for item in content if str(item).strip()]
        row["example"] = row["content_list"][0] if row["content_list"] else str(row.get("example") or "")
        self.rows[pattern_id] = row
        return pattern_id

    def delete_expression_pattern(self, pattern_id: str) -> bool:
        return self.rows.pop(pattern_id, None) is not None


class FakeRuntime:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {
            "expression_library_enabled": True,
            "expression_library_use_in_chat": True,
            "expression_library_use_in_screen": False,
            "expression_library_max_prompt_items": 4,
        }

    def load(self) -> Dict[str, Any]:
        return dict(self.data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.data.update(patch or {})
        return dict(self.data)


def test_list_and_get_patterns():
    service = ExpressionLibraryGuiService(store=FakeStore(), load_runtime=FakeRuntime().load)
    listed = service.list_patterns()
    assert listed["ok"] is True
    assert listed["data"]["count"] == 1
    assert listed["data"]["patterns"][0]["id"] == "xp1"
    got = service.get_pattern("xp1")
    assert got["ok"] is True
    assert got["data"]["style"] == "轻松"


def test_upsert_delete_and_runtime():
    runtime = FakeRuntime()
    store = FakeStore()
    service = ExpressionLibraryGuiService(
        store=store,
        load_runtime=runtime.load,
        update_runtime=runtime.update,
    )
    saved = service.upsert_pattern(
        {
            "id": "xp1",
            "character_name": "Suzu",
            "scene": "sensor",
            "style": "吐槽",
            "content_list": ["又在摸鱼？"],
            "enabled": True,
        }
    )
    assert saved["ok"] is True
    assert saved["data"]["scene"] == "sensor"
    deleted = service.delete_pattern("xp1")
    assert deleted["ok"] is True
    settings = service.save_runtime(
        {
            "expression_library_enabled": False,
            "expression_library_max_prompt_items": 6,
        }
    )
    assert settings["ok"] is True
    assert settings["data"]["expression_library_enabled"] is False
    assert settings["data"]["expression_library_max_prompt_items"] == 6
