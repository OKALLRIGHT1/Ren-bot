from __future__ import annotations

from typing import Any, Callable, Optional


class BrainKnowledgePort:
    """Narrow knowledge facade for plugins. Forwards to AdvancedMemorySystem."""

    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def import_knowledge_from_file(
        self,
        file_path,
        progress_callback: Optional[Callable[..., Any]] = None,
        **kwargs,
    ):
        brain = self._brain
        if brain is None or not hasattr(brain, "import_knowledge_from_file"):
            raise RuntimeError("knowledge_unavailable")
        if kwargs:
            return brain.import_knowledge_from_file(
                file_path, progress_callback=progress_callback, **kwargs
            )
        return brain.import_knowledge_from_file(
            file_path, progress_callback=progress_callback
        )

    def search_knowledge(self, search_text: str, k: int = 3):
        brain = self._brain
        if brain is None or not hasattr(brain, "search_knowledge"):
            raise RuntimeError("knowledge_unavailable")
        return brain.search_knowledge(search_text, k=k)

    def get_knowledge_stats(self) -> dict:
        brain = self._brain
        if brain is None or not hasattr(brain, "get_knowledge_stats"):
            return {}
        return dict(brain.get_knowledge_stats() or {})
