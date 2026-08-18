from __future__ import annotations

from unittest.mock import Mock

from core.logger import AppLogger
from modules.memory.retrieval import (
    KnowledgeHit,
    format_knowledge_hits_for_display,
    format_knowledge_hits_for_prompt,
    retrieve_knowledge_chunks,
    trim_knowledge_body,
)
from services.gui_api.knowledge_service import KnowledgeGuiService


def test_retrieve_knowledge_chunks_returns_hits_with_source():
    class Collection:
        def query(self, **_kwargs):
            return {
                "ids": [["know_1"]],
                "documents": [["皮卡丘是电属性宝可梦。"]],
                "metadatas": [
                    [
                        {
                            "source": "pika.md",
                            "source_path": "D:/docs/pika.md",
                            "chunk_index": 0,
                        }
                    ]
                ],
                "distances": [[0.12]],
            }

    hits = retrieve_knowledge_chunks(Collection(), "设定里皮卡丘", k=2)
    assert len(hits) == 1
    assert isinstance(hits[0], KnowledgeHit)
    assert hits[0].id == "know_1"
    assert hits[0].source == "pika.md"
    assert hits[0].source_path.endswith("pika.md")
    assert hits[0].content == "皮卡丘是电属性宝可梦。"
    assert hits[0].score == 0.12


def test_prompt_inject_includes_source_and_constraints():
    hits = [
        KnowledgeHit(
            id="1",
            content="皮卡丘拥有静电特性。",
            source="pika.md",
            source_path="pika.md",
        )
    ]
    text = format_knowledge_hits_for_prompt(hits)
    assert "只来自资料文件" in text
    assert "不要说成「你说过 / 我记得你」" in text
    assert "以用户为准" in text
    assert "· 《pika.md》: 皮卡丘拥有静电特性。" in text
    assert "<knowledge_data>" in text
    assert "</knowledge_data>" in text
    assert text.index("只来自资料文件") < text.index("<knowledge_data>")


def test_prompt_inject_budget_keeps_two_items_and_sentence_bounds():
    first = KnowledgeHit(
        id="a",
        content=("这是第一段完整资料。" * 30) + "后面还有一句不该被半截切断。",
        source="a.md",
    )
    second = KnowledgeHit(
        id="b",
        content=("这是第二段完整资料。" * 30),
        source="b.md",
    )
    third = KnowledgeHit(
        id="c",
        content="第三段不应该进入自动注入。",
        source="c.md",
    )
    text = format_knowledge_hits_for_prompt([first, second, third])
    assert "《a.md》" in text
    assert "《b.md》" in text
    assert "《c.md》" not in text
    bodies = []
    for line in text.splitlines():
        if line.startswith("· 《"):
            body = line.split(": ", 1)[1]
            bodies.append(body)
            assert len(body) <= 400
            assert body.endswith("。")
    assert len(bodies) == 2
    assert sum(len(body) for body in bodies) <= 800


def test_trim_knowledge_body_cuts_on_sentence():
    text = "第一句。" + ("补充说明" * 80) + "。结尾句。"
    trimmed = trim_knowledge_body(text, 40)
    assert trimmed == "第一句。"
    assert "结尾句" not in trimmed
    assert len(trimmed) <= 40


def test_display_formatter_keeps_source_for_gui_and_plugin():
    hits = [
        KnowledgeHit(id="1", content="房间里有灯。", source="room.md"),
        "纯字符串兜底",
    ]
    rows = format_knowledge_hits_for_display(hits)
    assert rows[0] == "《room.md》\n房间里有灯。"
    assert rows[1] == "《资料》\n纯字符串兜底"


def test_gui_search_exposes_source_hits():
    class Brain:
        def search_knowledge(self, query: str, k: int = 5):
            assert query == "设定"
            return [
                KnowledgeHit(
                    id="know_1",
                    content="房间布置靠窗。",
                    source="room.md",
                    source_path="docs/room.md",
                    score=0.2,
                )
            ]

    found = KnowledgeGuiService(brain=Brain()).search("设定")
    assert found["ok"] is True
    assert found["data"]["count"] == 1
    assert found["data"]["results"][0].startswith("《room.md》")
    assert found["data"]["hits"][0]["source"] == "room.md"
    assert found["data"]["hits"][0]["content"] == "房间布置靠窗。"


def test_build_prompt_injects_bounded_knowledge_block(monkeypatch):
    import modules.advanced_memory as advanced_memory
    from modules.memory_core.models import ReplyMemoryContext

    class FakeCore:
        profile_max_items = 6

        def build_reply_context(self, *args, **kwargs):
            return ReplyMemoryContext(intent="none")

    brain = advanced_memory.AdvancedMemorySystem.__new__(
        advanced_memory.AdvancedMemorySystem
    )
    brain.memory_core = FakeCore()
    brain.sqlite_store = None
    brain.short_term_memory = []
    brain.session_short_term_memory = {}
    brain.max_short_term = 12
    brain.tool_history = []
    brain.tool_context_max_chars = 500
    brain.knowledge_auto_retrieval_enabled = True
    brain.context_assembler = None
    brain._logger = AppLogger()
    brain._logger.logger = Mock()
    brain._retrieve_knowledge = lambda *args, **kwargs: [
        KnowledgeHit(
            id="know_1",
            content="皮卡丘拥有静电特性。",
            source="pika.md",
        )
    ]
    monkeypatch.setattr(
        advanced_memory,
        "character_manager",
        type("CM", (), {"data": {}, "get_active_character": staticmethod(lambda: None)})(),
    )

    messages = brain.build_prompt("设定里皮卡丘的特性是什么", "系统设定")
    content = messages[0]["content"]
    assert "【相关知识库】" in content
    assert "只来自资料文件" in content
    assert "· 《pika.md》: 皮卡丘拥有静电特性。" in content
    assert "<knowledge_data>" in content
