"""ASR settings for the control-center /gui HTTP API."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from modules.asr.settings import (
    ASR_ENGINE_QWEN3,
    ASR_ENGINE_ZIPFORMER,
    load_asr_settings,
    resolve_wake_words,
    save_asr_settings,
)

ASR_ENGINES = (
    {
        "id": ASR_ENGINE_QWEN3,
        "label": "Qwen3-ASR 0.6B（推荐，说完一句再出字）",
    },
    {
        "id": ASR_ENGINE_ZIPFORMER,
        "label": "Zipformer 流式（旧模型，边说边出字）",
    },
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


class AsrGuiService:
    def __init__(
        self,
        *,
        apply_settings: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._apply_settings = apply_settings

    def get_settings(self) -> Dict[str, Any]:
        settings = load_asr_settings()
        return {
            "ok": True,
            "data": {
                "settings": settings,
                "wake_words": resolve_wake_words(settings=settings),
                "engines": list(ASR_ENGINES),
            },
        }

    def save_settings(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        incoming = _as_dict(payload)
        patch = incoming.get("settings")
        if not isinstance(patch, dict):
            patch = incoming
        settings = save_asr_settings(patch)
        applied: Dict[str, Any] = {}
        if callable(self._apply_settings):
            try:
                applied = self._apply_settings(settings) or {}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        if isinstance(applied, dict) and applied.get("ok") is False:
            return {
                "ok": False,
                "error": str(applied.get("error") or "apply_asr_settings failed"),
            }
        return {
            "ok": True,
            "data": {
                "settings": settings,
                "wake_words": resolve_wake_words(settings=settings),
                "applied": applied,
            },
        }

    def preview(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        incoming = _as_dict(payload)
        patch = incoming.get("settings")
        if not isinstance(patch, dict):
            patch = incoming
        draft = load_asr_settings(patch)
        words: List[str] = resolve_wake_words(settings=draft)
        return {
            "ok": True,
            "data": {
                "wake_words": words,
                "require_wake_word": bool(draft.get("asr_require_wake_word", True)),
            },
        }
