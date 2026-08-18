from __future__ import annotations

from pathlib import Path

import pytest

from plugins.local_knowledge.plugin import Plugin
from services.plugin_ports.knowledge import BrainKnowledgePort


class FakeBrain:
    def __init__(self) -> None:
        self.imported = []
        self.stats_calls = 0

    def import_knowledge_from_file(self, path, progress_callback=None):
        self.imported.append(path)
        if progress_callback:
            progress_callback({"stage": "prepared", "batch": 0, "batches": 1})
        return {"ok": True, "added": 1, "skipped": 0}

    def search_knowledge(self, search_text, k=3):
        return [f"hit:{search_text}:{k}"]

    def get_knowledge_stats(self):
        self.stats_calls += 1
        return {"chunk_count": 2, "rate_limit_hits": 0}


def test_port_forwards_three_methods():
    brain = FakeBrain()
    port = BrainKnowledgePort(brain)
    assert port.import_knowledge_from_file("a.md")["added"] == 1
    assert brain.imported == ["a.md"]
    assert port.search_knowledge("设定", k=5) == ["hit:设定:5"]
    assert port.get_knowledge_stats()["chunk_count"] == 2


@pytest.mark.asyncio
async def test_plugin_search_uses_knowledge_without_brain():
    plugin = Plugin()
    port = BrainKnowledgePort(FakeBrain())
    result = await plugin.run("search ||| 设定", {"knowledge": port})
    assert "hit:设定:3" in result


@pytest.mark.asyncio
async def test_plugin_learn_falls_back_to_brain(tmp_path: Path):
    target = tmp_path / "note.md"
    target.write_text("设定", encoding="utf-8")
    plugin = Plugin()
    plugin.settings = {
        "slow_ingest_enabled": {"default": False},
        "slow_ingest_sleep_ms": {"default": 0},
    }
    brain = FakeBrain()
    result = await plugin.run(f"learn ||| {tmp_path}", {"brain": brain})
    assert "学习完成" in result
    assert str(target) in brain.imported
