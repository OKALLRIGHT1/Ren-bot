from __future__ import annotations

import asyncio

from modules.memory_sqlite import MemorySQLite
from plugins.diary_export import plugin as diary_plugin


def test_diary_plugin_queries_and_exports_from_store_api(tmp_path, monkeypatch):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    store.upsert_episode(
        {
            "id": "daily",
            "title": "2026-07-09 日记",
            "summary": "当天的日记正文",
            "tags": ["daily_log", "date:2026-07-09"],
        }
    )
    monkeypatch.setattr(diary_plugin, "get_memory_store", lambda: store)
    plugin = diary_plugin.Plugin()
    plugin.output_dir = str(tmp_path / "output")

    result = asyncio.run(plugin.run("查询 2026-07-09 的日记", {}))
    exported = asyncio.run(plugin.run("导出日记", {}))

    assert "当天的日记正文" in result
    assert "导出成功" in exported
    assert list((tmp_path / "output").glob("Diary_Export_*.md"))
