from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from services.gui_api.diary_service import DiaryGuiService


class FakeStore:
    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {
            "d1": {
                "id": "d1",
                "title": "2026-07-15 日记",
                "summary": "今天写代码",
                "status": "active",
                "tags": ["daily_log"],
            },
            "e1": {
                "id": "e1",
                "title": "普通片段",
                "summary": "不是日记",
                "status": "active",
                "tags": ["note"],
            },
        }

    def list_episodes(self, **kwargs: Any) -> List[Dict[str, Any]]:
        query = str(kwargs.get("query") or "").strip().lower()
        rows = list(self.rows.values())
        if query:
            rows = [
                row
                for row in rows
                if query in f"{row.get('title')} {row.get('summary')}".lower()
            ]
        return rows

    def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(episode_id)

    def upsert_episode(self, ep: Dict[str, Any]) -> str:
        episode_id = str(ep.get("id") or f"d{len(self.rows)+1}")
        row = dict(ep)
        row["id"] = episode_id
        self.rows[episode_id] = row
        return episode_id

    def delete_episode(self, episode_id: str) -> bool:
        return self.rows.pop(episode_id, None) is not None


def test_list_diaries_filters_daily_log():
    service = DiaryGuiService(store=FakeStore())
    data = service.list_diaries()
    assert data["ok"] is True
    assert data["data"]["count"] == 1
    assert data["data"]["diaries"][0]["id"] == "d1"


def test_upsert_and_delete_diary():
    service = DiaryGuiService(store=FakeStore())
    saved = service.upsert_diary(
        {"id": "d1", "title": "更新标题", "summary": "更新正文"}
    )
    assert saved["ok"] is True
    assert saved["data"]["title"] == "更新标题"
    deleted = service.delete_diary("d1")
    assert deleted["ok"] is True
    missing = service.get_diary("d1")
    assert missing["ok"] is False


def test_export_markdown(tmp_path: Path):
    service = DiaryGuiService(store=FakeStore(), export_root=tmp_path)
    result = service.export_markdown()
    assert result["ok"] is True
    path = Path(result["data"]["path"])
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "2026-07-15 日记" in text
    assert "今天写代码" in text
