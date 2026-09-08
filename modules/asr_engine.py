"""Compatibility shim — real implementation moved to :mod:`modules.asr.engine`.

Preserved so legacy ``from modules.asr_engine import X`` keeps working.
New code should import from ``modules.asr`` instead.
"""

from __future__ import annotations

from modules.asr.engine import (  # noqa: F401
    AsrEngine,
    HttpAsrEngine,
    KNOWN_ENGINES,
    MIN_ONNX_BYTES,
    QWEN3_ENGINE,
    SAMPLE_RATE,
    ZIPFORMER_ENGINE,
    ZIPFORMER_SAMPLES_PER_READ,
    SherpaQwen3Engine,
    SherpaZipformerEngine,
    _abs,
    _dir_ok,
    _file_ok,
    _first_file,
    _stream_text,
    create_asr_engine,
    format_hotwords,
    missing_qwen3_reason,
    qwen3_api_available,
    qwen3_paths,
    resolve_qwen3_model_dir,
    resolve_vad_path,
    sherpa_onnx,
)
