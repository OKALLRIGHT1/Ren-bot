from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from modules import runtime_settings


def test_embedding_selection_saves_only_model_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime_settings,
        "RUNTIME_SETTINGS_PATH",
        tmp_path / "runtime.json",
    )

    saved = runtime_settings.save_embedding_model_selection("local-bge-m3")

    assert saved["embedding_model_id"] == "local-bge-m3"
    assert saved["embedding_model_ids"] == ["local-bge-m3"]
    assert "api_key" not in saved
    assert "api_url" not in saved
    assert runtime_settings.load_runtime_settings() == saved


def test_embedding_selection_saves_ordered_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime_settings,
        "RUNTIME_SETTINGS_PATH",
        tmp_path / "runtime.json",
    )

    saved = runtime_settings.save_embedding_model_selection(
        model_ids=["local-bge-m3", "remote-bge", "local-bge-m3"]
    )

    assert saved["embedding_model_ids"] == ["local-bge-m3", "remote-bge"]
    assert saved["embedding_model_id"] == "local-bge-m3"


def test_embedding_selection_can_return_to_legacy_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime_settings,
        "RUNTIME_SETTINGS_PATH",
        tmp_path / "runtime.json",
    )
    runtime_settings.update_runtime_settings(
        {
            "embedding_model_id": "local-bge-m3",
            "embedding_model_ids": ["local-bge-m3"],
            "other": True,
        }
    )

    saved = runtime_settings.save_embedding_model_selection("")

    assert "embedding_model_id" not in saved
    assert "embedding_model_ids" not in saved
    assert saved["other"] is True


def test_strict_runtime_settings_rejects_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "RUNTIME_SETTINGS_PATH", path)

    with pytest.raises(runtime_settings.RuntimeSettingsError, match="无法读取"):
        runtime_settings.load_runtime_settings_strict()


def test_embedding_selection_does_not_overwrite_corrupt_runtime_settings(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "runtime.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "RUNTIME_SETTINGS_PATH", path)

    with pytest.raises(runtime_settings.RuntimeSettingsError, match="无法读取"):
        runtime_settings.save_embedding_model_selection("local-bge-m3")

    assert path.read_text(encoding="utf-8") == "{broken"


def test_memory_editor_lists_only_embedding_models(tmp_path, monkeypatch):
    import config
    from modules.gui.dialogs import memory_editor
    from modules.memory_sqlite import MemorySQLite

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(
        runtime_settings,
        "RUNTIME_SETTINGS_PATH",
        tmp_path / "runtime.json",
    )
    runtime_settings.save_embedding_model_selection("local-bge")
    monkeypatch.setattr(
        config,
        "MODELS",
        {
            "chat": {"model": "chat", "purposes": ["chat"]},
            "local-bge": {
                "model": "bge-m3",
                "purposes": ["embedding"],
            },
        },
    )
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(memory_editor, "get_memory_store", lambda: store)

    dialog = memory_editor.MemoryEditorDialog(embedded=True)

    assert dialog.embedding_model_combo.findData("local-bge") >= 0
    assert dialog.embedding_model_combo.findData("chat") == -1
    assert dialog.embedding_model_combo.currentData() == "local-bge"
    dialog.close()
    assert app is not None
