from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from services.gui_api.knowledge_service import KnowledgeGuiService


class FakeManager:
    def __init__(self) -> None:
        self.plugin_configs = {
            "knowledge_base": {
                "settings": {
                    "knowledge_source_dirs": {
                        "default": [{"path": "knowledge_docs", "enabled": True}]
                    }
                }
            }
        }
        self.plugins = {}

    def save_plugin_config(self, trigger: str, config: Dict[str, Any]) -> bool:
        self.plugin_configs[trigger] = config
        return True


class FakeBrain:
    def __init__(self) -> None:
        self.chunks = ["chunk-a", "chunk-b"]
        self.imported: List[str] = []

    def get_knowledge_stats(self) -> Dict[str, Any]:
        return {
            "chunk_count": len(self.chunks),
            "rebuild_required": False,
            "embedding": {"state": "ready", "model": "bge", "dimension": 1024},
        }

    def search_knowledge(self, query: str, k: int = 3):
        return [item for item in self.chunks if query in item][:k]

    def import_knowledge_from_file(self, path: str, progress_callback=None):
        self.imported.append(path)
        self.chunks.append(f"imported:{Path(path).name}")
        return f"ok:{path}"

    def rebuild_knowledge_collection(self) -> bool:
        self.chunks = []
        return True

    def delete_knowledge_by_dirs(self, dirs) -> int:
        removed = len(self.chunks)
        self.chunks = []
        return removed


def test_list_and_save_dirs():
    service = KnowledgeGuiService(plugin_manager=FakeManager(), brain=FakeBrain())
    listed = service.list_dirs()
    assert listed["ok"] is True
    assert listed["data"]["dirs"][0]["path"] == "knowledge_docs"
    saved = service.save_dirs([{"path": "docs", "enabled": False}])
    assert saved["ok"] is True
    assert saved["data"]["dirs"][0]["enabled"] is False


def test_search_and_stats():
    service = KnowledgeGuiService(plugin_manager=FakeManager(), brain=FakeBrain())
    stats = service.stats()
    assert stats["data"]["chunk_count"] == 2
    found = service.search("chunk-a")
    assert found["ok"] is True
    assert found["data"]["count"] == 1


def test_create_doc_and_import(tmp_path: Path):
    brain = FakeBrain()
    service = KnowledgeGuiService(
        plugin_manager=FakeManager(),
        brain=brain,
        write_root=tmp_path,
    )
    created = service.create_doc(
        {
            "title": "测试知识",
            "lines": ["第一条", "第二条"],
            "target_dir": str(tmp_path),
            "ingest_now": True,
        }
    )
    assert created["ok"] is True
    path = Path(created["data"]["path"])
    assert path.exists()
    assert brain.imported


def test_learn_configured_dirs_supports_async_in_running_loop():
    import asyncio

    class AsyncPlugin:
        async def gui_ingest_configured_dirs(self, context):
            await asyncio.sleep(0)
            assert context.get("brain") is not None
            return "学习完成: 1 文件"

    manager = FakeManager()
    manager.plugins["knowledge_base"] = AsyncPlugin()
    service = KnowledgeGuiService(plugin_manager=manager, brain=FakeBrain())

    async def _call_from_loop():
        return service.learn_configured_dirs()

    result = asyncio.run(_call_from_loop())
    assert result["ok"] is True
    assert "学习完成" in str(result["data"]["result"])
