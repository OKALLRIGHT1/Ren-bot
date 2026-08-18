from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.dialogs.knowledge_manager import (
    KnowledgeImportWorker,
    KnowledgeManagerDialog,
)
from plugins.local_knowledge.plugin import Plugin


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class FakeBrain:
    def __init__(self):
        self.calls = []

    def import_knowledge_from_file(self, path, progress_callback=None):
        self.calls.append(path)
        if progress_callback:
            progress_callback(
                {
                    "stage": "prepared",
                    "batch": 0,
                    "batches": 2,
                    "total": 2,
                    "added": 0,
                    "skipped": 0,
                }
            )
            progress_callback(
                {
                    "stage": "batch_done",
                    "batch": 2,
                    "batches": 2,
                    "total": 2,
                    "added": 1,
                    "skipped": 1,
                }
            )
        return {"ok": True, "added": 1, "skipped": 1}


def test_import_worker_emits_file_progress_and_summary():
    brain = FakeBrain()
    worker = KnowledgeImportWorker(brain, ["a.txt", "b.txt"])
    progress = []
    finished = []
    worker.progress.connect(progress.append)
    worker.finished.connect(finished.append)
    worker.run()

    assert len(progress) == 4
    assert progress[0]["file_index"] == 1
    assert progress[0]["file_count"] == 2
    assert progress[-1]["file_index"] == 2
    assert progress[-1]["stage"] == "batch_done"
    assert finished == [
        {
            "file_count": 2,
            "added": 2,
            "skipped": 2,
            "failed": 0,
            "results": [
                "a.txt: 新增 1 条，跳过 1 条。",
                "b.txt: 新增 1 条，跳过 1 条。",
            ],
        }
    ]


def test_learn_dirs_starts_worker_instead_of_blocking_ingest(tmp_path, monkeypatch):
    _app()
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    class PluginStub:
        def list_configured_learn_files(self):
            return [str(first), str(second)]

    class Manager:
        plugins = {"knowledge_base": PluginStub()}
        plugin_configs = {"knowledge_base": {"settings": {}}}

        def save_plugin_config(self, trigger, config):
            del trigger, config
            return True

    started = {}

    def fake_start(self, importer, paths, *, kind, title, ready_text):
        started.update(
            {
                "importer": importer,
                "paths": list(paths),
                "kind": kind,
                "title": title,
                "ready_text": ready_text,
            }
        )

    monkeypatch.setattr(
        KnowledgeManagerDialog, "_start_knowledge_import_job", fake_start
    )
    dialog = KnowledgeManagerDialog(
        main_app=SimpleNamespace(brain=FakeBrain(), plugin_manager=Manager())
    )
    dialog._learn_dirs()
    assert started["kind"] == "learn"
    assert started["title"] == "一键学习"
    assert started["paths"] == [str(first), str(second)]
    dialog.close()


def test_plugin_learn_callback_fires_once_per_file(tmp_path):
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    plugin = Plugin()
    plugin.settings = {}
    seen = []

    def on_progress(info):
        seen.append(str(info.get("file_path") or ""))

    brain = FakeBrain()
    plugin._import_one_file(
        brain, str(first), progress_callback=on_progress, file_index=1, file_count=2
    )
    plugin._import_one_file(
        brain, str(second), progress_callback=on_progress, file_index=2, file_count=2
    )
    assert str(first) in seen
    assert str(second) in seen
    assert brain.calls == [str(first), str(second)]


def test_knowledge_manager_hints_when_only_default_docs_exist():
    class Brain:
        def get_knowledge_stats(self):
            return {
                "chunk_count": 1,
                "embedding": {
                    "state": "ready",
                    "model": "bge-m3",
                    "dimension": 1024,
                    "calls": 1,
                    "failures": 0,
                },
            }

    _app()
    dialog = KnowledgeManagerDialog(
        main_app=SimpleNamespace(brain=Brain(), plugin_manager=None)
    )
    dialog.dir_table.setRowCount(0)
    dialog._append_dir_row(str(Path.cwd() / "knowledge_docs"), True)
    dialog._refresh_stats()
    assert "当前几乎没资料" in dialog.stats_label.text()
    dialog.close()
