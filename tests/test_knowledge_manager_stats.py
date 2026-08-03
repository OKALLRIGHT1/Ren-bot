from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.dialogs.knowledge_manager import KnowledgeManagerDialog


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_knowledge_manager_shows_shared_embedding_status() -> None:
    class Brain:
        def get_knowledge_stats(self):
            return {
                "chunk_count": 25,
                "embedding": {
                    "state": "ready",
                    "model": "bge-m3",
                    "dimension": 1024,
                    "calls": 7,
                    "failures": 1,
                },
            }

    _app()
    dialog = KnowledgeManagerDialog(
        main_app=SimpleNamespace(brain=Brain(), plugin_manager=None)
    )

    text = dialog.stats_label.text()
    assert "知识片段数：25" in text
    assert "bge-m3" in text
    assert "1024" in text
    assert "调用 7" in text
    dialog.close()


def test_knowledge_manager_warns_when_embedding_index_requires_rebuild() -> None:
    class Brain:
        def get_knowledge_stats(self):
            return {
                "chunk_count": 25,
                "rebuild_required": True,
                "embedding": {
                    "state": "ready",
                    "model": "new-embedding",
                    "dimension": 768,
                },
            }

    _app()
    dialog = KnowledgeManagerDialog(
        main_app=SimpleNamespace(brain=Brain(), plugin_manager=None)
    )

    assert "需要重建" in dialog.stats_label.text()
    dialog.close()
