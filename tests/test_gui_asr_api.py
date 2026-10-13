from __future__ import annotations

from typing import Any, Dict, List

from services.gui_api.asr_service import ASR_ENGINES, AsrGuiService

DEFAULTS: Dict[str, Any] = {
    "asr_engine": "qwen3",
    "asr_hotwords_from_wake": True,
    "asr_require_wake_word": True,
    "asr_use_character_wake_words": True,
    "asr_include_global_wake_words": True,
    "asr_global_wake_words": ["五十铃", "怜", "Suzu", "助手"],
    "asr_extra_wake_words": [],
    "asr_active_window_sec": 20,
    "asr_min_chars": 2,
}


def _install_store(monkeypatch, store: Dict[str, Any], wake_words: List[str] | None = None):
    monkeypatch.setattr(
        "services.gui_api.asr_service.load_asr_settings",
        lambda runtime=None: {**DEFAULTS, **dict(runtime or store)},
    )
    saved: list[Dict[str, Any]] = []

    def fake_save(patch: Dict[str, Any]) -> Dict[str, Any]:
        store.update(patch or {})
        saved.append(dict(store))
        return dict(store)

    monkeypatch.setattr("services.gui_api.asr_service.save_asr_settings", fake_save)
    monkeypatch.setattr(
        "services.gui_api.asr_service.resolve_wake_words",
        lambda settings=None: list(wake_words or ["五十铃", "怜"]),
    )
    return saved


def test_get_asr_settings_includes_engines_and_wake_words(monkeypatch):
    store = dict(DEFAULTS)
    store["asr_engine"] = "zipformer"
    _install_store(monkeypatch, store, ["五十铃"])
    result = AsrGuiService().get_settings()
    assert result["ok"] is True
    data = result["data"]
    assert data["settings"]["asr_engine"] == "zipformer"
    assert data["wake_words"] == ["五十铃"]
    assert data["engines"] == list(ASR_ENGINES)


def test_save_asr_settings_nested_payload_applies_hot_reload(monkeypatch):
    store = dict(DEFAULTS)
    saved = _install_store(monkeypatch, store)
    applied: list[Dict[str, Any]] = []

    def apply_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
        applied.append(dict(settings))
        return {"ok": True, "sensor_reloaded": True}

    result = AsrGuiService(apply_settings=apply_settings).save_settings(
        {"settings": {"asr_require_wake_word": False, "asr_min_chars": 4}}
    )
    assert result["ok"] is True
    assert saved
    assert applied[0]["asr_require_wake_word"] is False
    assert result["data"]["applied"]["sensor_reloaded"] is True


def test_save_asr_settings_flat_payload_and_apply_error(monkeypatch):
    store = dict(DEFAULTS)
    _install_store(monkeypatch, store)
    result = AsrGuiService(
        apply_settings=lambda _settings: {"ok": False, "error": "sensor down"}
    ).save_settings({"asr_engine": "zipformer"})
    assert result["ok"] is False
    assert "sensor down" in result["error"]


def test_preview_asr_wake_words_uses_draft(monkeypatch):
    store = dict(DEFAULTS)
    _install_store(monkeypatch, store, ["小铃"])
    result = AsrGuiService().preview(
        {"settings": {"asr_require_wake_word": False, "asr_extra_wake_words": ["小铃"]}}
    )
    assert result["ok"] is True
    assert result["data"]["wake_words"] == ["小铃"]
    assert result["data"]["require_wake_word"] is False
