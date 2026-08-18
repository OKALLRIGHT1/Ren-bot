from __future__ import annotations

from modules.memory.knowledge_gate import should_retrieve_knowledge
from modules.memory.retrieval import (
    KnowledgeHit,
    dedup_knowledge_hits,
    format_knowledge_hits_for_prompt,
    knowledge_alias_boost,
    retrieve_knowledge_chunks,
)


def test_alias_boost_only_uses_query_terms():
    meta = {"entity_name": "皮卡丘", "aliases": "Pikachu|ピカチュウ"}
    assert knowledge_alias_boost("设定里皮卡丘的特性是什么", meta) == 1
    assert knowledge_alias_boost("设定里 Pikachu 的特性", meta) == 1
    assert knowledge_alias_boost("设定里喷火龙的特性", meta) == 0


def test_named_entity_outranks_closer_semantic_neighbor():
    class Collection:
        def query(self, **kwargs):
            assert kwargs["n_results"] == 6
            return {
                "ids": [["noise", "pika"]],
                "documents": [
                    [
                        "这是一段语义很近但讲别的电系宝可梦的噪音。",
                        "皮卡丘拥有静电特性。",
                    ]
                ],
                "metadatas": [
                    [
                        {"source": "noise.md"},
                        {
                            "source": "pika.json",
                            "entity_name": "皮卡丘",
                            "aliases": "Pikachu|ピカチュウ",
                        },
                    ]
                ],
                "distances": [[0.01, 0.40]],
            }

    hits = retrieve_knowledge_chunks(Collection(), "设定里皮卡丘的特性是什么", k=2)
    assert [item.id for item in hits] == ["pika", "noise"]
    assert hits[0].source == "pika.json"


def test_fetch_size_caps_at_eight():
    seen = {}

    class Collection:
        def query(self, **kwargs):
            seen["n_results"] = kwargs["n_results"]
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

    retrieve_knowledge_chunks(Collection(), "设定", k=5)
    assert seen["n_results"] == 8


def test_dedup_drops_same_id_and_contained_shorter_text():
    hits = dedup_knowledge_hits(
        [
            KnowledgeHit(id="a", content="皮卡丘拥有静电特性。补充一句。", source="long.md"),
            KnowledgeHit(id="a", content="重复 id 应丢掉", source="dup.md"),
            KnowledgeHit(id="b", content="皮卡丘拥有静电特性。", source="short.md"),
            KnowledgeHit(id="c", content="喷火龙是火属性。", source="other.md"),
        ]
    )
    assert [item.id for item in hits] == ["a", "c"]


def test_plugin_search_can_keep_five_after_rerank():
    class Collection:
        def query(self, **kwargs):
            assert kwargs["n_results"] == 8
            ids = [f"id{i}" for i in range(6)]
            return {
                "ids": [ids],
                "documents": [[f"片段 {i} 的内容。" for i in range(6)]],
                "metadatas": [[{"source": f"{i}.md"} for i in range(6)]],
                "distances": [[0.1 * i for i in range(6)]],
            }

    hits = retrieve_knowledge_chunks(Collection(), "设定", k=5)
    assert len(hits) == 5
    prompt = format_knowledge_hits_for_prompt(hits)
    assert prompt.count("· 《") == 2


def test_gate_still_ignores_bare_entity_names():
    assert should_retrieve_knowledge("皮卡丘有什么特性") is False
    assert should_retrieve_knowledge("设定里皮卡丘的特性是什么") is True
