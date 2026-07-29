from __future__ import annotations

from unittest.mock import Mock

import pytest

from core.logger import AppLogger
from modules.embeddings import EmbeddingUnavailableError
from modules.memory.retrieval import retrieve_knowledge_chunks


def test_explicit_knowledge_search_does_not_hide_vector_errors():
    class Collection:
        def query(self, **_kwargs):
            raise EmbeddingUnavailableError("ollama offline")

    with pytest.raises(EmbeddingUnavailableError, match="ollama offline"):
        retrieve_knowledge_chunks(Collection(), "查询", k=3)


def test_chat_knowledge_recall_degrades_when_embedding_is_unavailable():
    from modules.advanced_memory import AdvancedMemorySystem

    brain = AdvancedMemorySystem.__new__(AdvancedMemorySystem)

    def failed_search(_text, k=2):
        del k
        raise EmbeddingUnavailableError("ollama offline")

    brain.search_knowledge = failed_search
    brain._logger = AppLogger()
    brain._logger.logger = Mock()

    assert brain._retrieve_knowledge("普通聊天问题", k=2) == []
    warning_args = brain._logger.logger.warning.call_args.args
    assert warning_args[0] == "Knowledge recall unavailable: %s"
    assert isinstance(warning_args[1], EmbeddingUnavailableError)
    assert str(warning_args[1]) == "ollama offline"
