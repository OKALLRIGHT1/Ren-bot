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


def test_ingest_knowledge_paths_counts_failures():
    from services.gui_api.knowledge_service import ingest_knowledge_paths

    def _import(path, progress_callback=None):
        if progress_callback:
            progress_callback({"stage": "prepared", "batch": 0, "batches": 1})
        if path.endswith("bad.md"):
            return {"ok": False, "error": "boom"}
        return {"ok": True, "added": 2, "skipped": 1}

    payload = ingest_knowledge_paths(
        ["good.md", "bad.md"],
        import_file=_import,
    )
    assert payload["file_count"] == 2
    assert payload["added"] == 2
    assert payload["skipped"] == 1
    assert payload["failed"] == 1
    assert "good.md" in payload["results"][0]
    assert "失败" in payload["results"][1]


def test_learn_configured_dirs_uses_shared_ingest(tmp_path: Path):
    class Plugin:
        def __init__(self, files):
            self.files = files

        def list_configured_learn_files(self):
            return list(self.files)

        def _slow_ingest_config(self):
            return False, 20, 0, False

    first = tmp_path / "a.md"
    first.write_text("a", encoding="utf-8")
    manager = FakeManager()
    manager.plugins["knowledge_base"] = Plugin([str(first)])
    brain = FakeBrain()
    service = KnowledgeGuiService(plugin_manager=manager, brain=brain)
    result = service.learn_configured_dirs()
    assert result["ok"] is True
    assert result["data"]["file_count"] == 1
    assert brain.imported == [str(first)]
    assert "学习完成" in str(result["data"]["result"])
