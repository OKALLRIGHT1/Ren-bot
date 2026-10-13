from pathlib import Path

from modules.asr.model_select import asr_model_root, load_selected, zipformer_cfg


def test_default_root_is_sherpa_model(tmp_path, monkeypatch):
    monkeypatch.delenv("SUZU_ASR_MODEL_DIR", raising=False)
    monkeypatch.delenv("SUZU_DATA_DIR", raising=False)
    monkeypatch.delenv("SUZU_USER_DIR", raising=False)
    root = asr_model_root(str(tmp_path))
    assert root == tmp_path / "sherpa_model"


def test_env_model_dir_wins(tmp_path, monkeypatch):
    overlay = tmp_path / "custom"
    overlay.mkdir()
    monkeypatch.setenv("SUZU_ASR_MODEL_DIR", str(overlay))
    assert asr_model_root(str(tmp_path / "unused")) == overlay


def test_user_overlay_and_selected_json(tmp_path, monkeypatch):
    monkeypatch.delenv("SUZU_ASR_MODEL_DIR", raising=False)
    user = tmp_path / "user"
    asr = user / "asr"
    asr.mkdir(parents=True)
    (asr / "selected.json").write_text('{"engine": "qwen3", "model_dir": "qwen3-0.6b-int8"}', encoding="utf-8")
    monkeypatch.setenv("SUZU_DATA_DIR", str(user))
    assert asr_model_root(str(tmp_path)) == asr
    selected = load_selected(str(tmp_path))
    assert selected["engine"] == "qwen3"
    assert selected["model_dir"] == "qwen3-0.6b-int8"


def test_zipformer_cfg_follows_root(tmp_path, monkeypatch):
    overlay = tmp_path / "asr"
    overlay.mkdir()
    monkeypatch.setenv("SUZU_ASR_MODEL_DIR", str(overlay))
    cfg = zipformer_cfg(str(tmp_path))
    assert cfg["encoder"] == str(overlay / "encoder-epoch-99-avg-1.onnx")
