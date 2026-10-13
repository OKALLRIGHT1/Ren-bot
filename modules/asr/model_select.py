"""Resolve which ASR model files to load.

Replaceable layout (install or %APPDATA%\\Suzu\\asr):

    sherpa_model/
      selected.json          # {"engine": "zipformer"} or {"engine": "qwen3"}
      encoder-epoch-99-avg-1.onnx
      decoder-epoch-99-avg-1.onnx
      joiner-epoch-99-avg-1.onnx
      tokens.txt
      silero_vad.onnx
      qwen3-0.6b-int8/       # optional drop-in

Env wins: SUZU_ASR_MODEL_DIR, SUZU_ASR_ENGINE.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

ZIPFORMER_FILES = (
    "tokens.txt",
    "encoder-epoch-99-avg-1.onnx",
    "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.onnx",
)


def _user_asr_dir() -> Optional[Path]:
    try:
        from modules.paths import user_dir

        root = user_dir()
    except Exception:
        root = ""
    env = os.environ.get("SUZU_DATA_DIR", "").strip() or os.environ.get("SUZU_USER_DIR", "").strip()
    if not root and env:
        root = env
    if not root:
        return None
    return Path(str(root)) / "asr"


def asr_model_root(base_dir: str) -> Path:
    """Directory that actually holds onnx/tokens. Overlay > program sherpa_model."""
    env = os.environ.get("SUZU_ASR_MODEL_DIR", "").strip()
    if env:
        return Path(env)
    overlay = _user_asr_dir()
    if overlay is not None and _looks_like_asr_root(overlay):
        return overlay
    return Path(base_dir) / "sherpa_model"


def _looks_like_asr_root(root: Path) -> bool:
    if (root / "selected.json").is_file():
        return True
    if (root / ZIPFORMER_FILES[1]).is_file():
        return True
    if (root / "qwen3-0.6b-int8").is_dir():
        return True
    if (root / "silero_vad.onnx").is_file():
        return True
    return False


def load_selected(base_dir: str) -> Dict[str, Any]:
    root = asr_model_root(base_dir)
    payload: Dict[str, Any] = {"root": str(root), "engine": "", "model_dir": ""}
    selected = root / "selected.json"
    if selected.is_file():
        try:
            data = json.loads(selected.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            payload["engine"] = str(data.get("engine") or "").strip().lower()
            payload["model_dir"] = str(data.get("model_dir") or "").strip()
    env_engine = os.environ.get("SUZU_ASR_ENGINE", "").strip().lower()
    if env_engine:
        payload["engine"] = env_engine
    return payload


def zipformer_cfg(base_dir: str) -> Dict[str, str]:
    root = asr_model_root(base_dir)
    return {
        "tokens": str(root / "tokens.txt"),
        "encoder": str(root / "encoder-epoch-99-avg-1.onnx"),
        "decoder": str(root / "decoder-epoch-99-avg-1.onnx"),
        "joiner": str(root / "joiner-epoch-99-avg-1.onnx"),
    }


def resolve_vad_path(base_dir: str) -> str:
    root = asr_model_root(base_dir)
    return str(root / "silero_vad.onnx")


def write_pack_selection(sherpa_root: Path) -> None:
    sherpa_root.mkdir(parents=True, exist_ok=True)
    (sherpa_root / "selected.json").write_text(
        json.dumps({"engine": "zipformer"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (sherpa_root / "README-models.txt").write_text(
        "默认 Zipformer（本目录 encoder/decoder/joiner/tokens）。\n"
        "换成 Qwen3：把 qwen3-0.6b-int8 解压到本目录或 %APPDATA%\\Suzu\\asr\\，\n"
        "并把 selected.json 的 engine 改成 \"qwen3\"（或设置页选 Qwen3）。\n"
        "整套模型也可直接放到 %APPDATA%\\Suzu\\asr\\ 覆盖安装目录。\n"
        "环境变量 SUZU_ASR_ENGINE / SUZU_ASR_MODEL_DIR 优先。\n",
        encoding="utf-8",
    )
