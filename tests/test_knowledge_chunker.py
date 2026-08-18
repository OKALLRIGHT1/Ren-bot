from __future__ import annotations

from modules.memory.knowledge_store import (
    _chunk_plain_text,
    import_knowledge_from_file,
)


def test_short_note_is_one_chunk():
    text = "这是一段很短的设定说明，只有几句话。角色喜欢安静的夜晚。"
    chunks = _chunk_plain_text(text, "notes.md")
    assert len(chunks) == 1
    body, meta = chunks[0]
    assert "安静的夜晚" in body
    assert meta["source"] == "notes.md"
    assert meta["chunk_index"] == 0


def test_headed_long_doc_keeps_multi_sentence_chunks():
    paragraphs = []
    for index in range(1, 6):
        paragraphs.append(f"## 章节 {index}")
        paragraphs.append(
            "这是完整的一段说明。"
            "它包含两句以上，不能再按行切碎。"
            f"这一节用来描述第 {index} 个主题的背景与结论。"
            + ("补充细节。" * 40)
        )
    text = "\n\n".join(paragraphs)
    chunks = _chunk_plain_text(text, "lore.md")
    bodies = [body for body, _meta in chunks]
    assert len(chunks) > 1
    assert len(chunks) < text.count("\n")
    assert all("。" in body for body in bodies)
    assert all(meta["chunk_index"] == index for index, (_body, meta) in enumerate(chunks))


def test_list_and_term_definitions_stay_together():
    text = (
        "常用术语\n\n"
        "- 技能A：造成火焰伤害\n"
        "- 技能B：提高防御\n"
        "- 技能C：回复少量生命\n\n"
        "暴击：攻击时有概率造成额外伤害\n"
        "命中：决定技能是否生效"
    )
    chunks = _chunk_plain_text(text, "terms.md")
    bodies = "\n".join(body for body, _meta in chunks)
    assert len(chunks) == 1
    assert "技能A：造成火焰伤害" in bodies
    assert "技能B：提高防御" in bodies
    assert "暴击：攻击时有概率造成额外伤害" in bodies


def test_oversize_paragraph_splits_on_sentences():
    sentence = "这是一句完整的背景说明，用来撑开长度。"
    text = sentence * 80
    assert len(text) > 1200
    chunks = _chunk_plain_text(text, "long.md")
    bodies = [body for body, _meta in chunks]
    assert len(chunks) > 1
    assert all(body.endswith("。") for body in bodies)
    assert all(len(body) <= 1200 for body in bodies)
    assert "完整的背景说明" in bodies[0]


def test_import_plain_text_uses_paragraph_chunks(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text(
        "# 设定\n\n"
        "第一段有两句。它描述角色的日常作息。\n\n"
        "第二段也有两句。它补充了房间里的摆设。\n",
        encoding="utf-8",
    )

    class Collection:
        def __init__(self):
            self.ids = []
            self.docs = []
            self.metas = []

        def get(self, ids=None, include=None):
            del include
            if ids is None:
                return {"ids": list(self.ids), "metadatas": list(self.metas)}
            existing = [item for item in ids if item in self.ids]
            return {"ids": existing}

        def add(self, documents, metadatas, ids):
            self.docs.extend(documents)
            self.ids.extend(ids)
            self.metas.extend(metadatas)
            assert all("chunk_index" in meta for meta in metadatas)
            assert all(meta.get("source") == "guide.md" for meta in metadatas)

        def delete(self, ids=None):
            keep = [
                (item_id, doc, meta)
                for item_id, doc, meta in zip(self.ids, self.docs, self.metas)
                if item_id not in set(ids or [])
            ]
            self.ids = [item[0] for item in keep]
            self.docs = [item[1] for item in keep]
            self.metas = [item[2] for item in keep]

    result = import_knowledge_from_file(
        Collection(),
        lambda text: text[:16],
        str(path),
        manifest_path=str(tmp_path / "manifest.json"),
    )
    assert result["added"] == 1
    assert result["skipped"] == 0
    assert result["total"] == 1
