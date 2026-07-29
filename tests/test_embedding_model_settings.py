from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules.gui.dialogs.settings import ModelEditDialog


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_model_dialog_round_trips_embedding_fields():
    _app()
    dialog = ModelEditDialog(
        model_id="local-bge-m3",
        model_data={
            "model": "bge-m3",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "ollama",
            "api_key_env": "EMBEDDING_API_KEY",
            "purposes": ["embedding"],
            "embedding_endpoint_path": "/embeddings",
            "embedding_dimension": 1024,
            "embedding_provider": "ollama",
            "embedding_timeout": 12,
        },
    )

    assert dialog.embedding_card.isHidden() is False
    assert dialog.inp_embedding_dimension.value() == 1024
    dialog._on_save()

    saved = dialog.result_data["config"]
    assert saved["embedding_endpoint_path"] == "/embeddings"
    assert saved["embedding_dimension"] == 1024
    assert saved["embedding_provider"] == "ollama"
    assert saved["embedding_timeout"] == 12
    assert saved["api_key_env"] == "EMBEDDING_API_KEY"
    dialog.close()


def test_model_dialog_accepts_full_embedding_api_url_without_base_url(monkeypatch):
    _app()
    dialog = ModelEditDialog(
        model_id="remote-embedding",
        model_data={
            "model": "remote-embed",
            "embedding_api_url": "https://embed.example/api/vectorize",
            "purposes": ["embedding"],
            "embedding_dimension": 768,
        },
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *_args: None)

    dialog._on_save()

    assert dialog.result_data is not None
    assert (
        dialog.result_data["config"]["embedding_api_url"]
        == "https://embed.example/api/vectorize"
    )
    dialog.close()
