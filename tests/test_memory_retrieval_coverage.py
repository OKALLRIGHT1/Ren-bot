"""Recall must filter before limiting, including old and global memories."""
import pytest

from modules.memory_core.vector_index import MemoryVectorIndex
from tests.test_memory_vector_index import _core, _Embedding


@pytest.mark.parametrize("person", ["owner", "qq:42"])
def test_real_chroma_filters_scope_before_top_k(tmp_path, person):
    import chromadb

    core = _core(tmp_path)
    collection = chromadb.PersistentClient(path=str(tmp_path / "chroma")).get_or_create_collection(
        "scope_recall", metadata={"embedding_model": "bge-m3", "embedding_dimension": 2},
    )
    text = "预算"
    ids = [f"other_{i}" for i in range(20)] + ["local", "shared"]
    metadata = [
        {"subject_id": person, "session_id": "elsewhere"} for _ in range(10)
    ] + [
        {"subject_id": "other", "session_id": "here"} for _ in range(10)
    ] + [
        {"subject_id": person, "session_id": "here"},
        {"subject_id": "" if person == "owner" else person, "session_id": ""},
    ]
    collection.upsert(
        ids=ids, documents=[text] * len(ids), metadatas=metadata,
        embeddings=[[2.0, 1.0]] * 20 + [[1.0, 0.0], [0.0, 1.0]],
    )
    index = MemoryVectorIndex(repository=core.repository, collection=collection, embedding_service=_Embedding())
    rows = index.query(text, person_id=person, session_id="here", limit=2)
    assert {row["id"] for row in rows} == {"local", "shared"}


def add(core, key, content, **kwargs):
    return core.upsert_memory_record(
        kind="episode", key=key, content=content, subject_id="owner",
        session_id="here", source_type="test", **kwargs,
    )


def test_old_low_importance_memory_is_recalled_without_vectors(tmp_path):
    core = _core(tmp_path)
    target = add(core, "budget", "星河预算八万元", importance=0.1)
    for i in range(305):
        add(core, str(i), f"今天练习钢琴曲目{i}", importance=0.9)
    result = core.build_reply_context(
        "星河预算", person_id="owner", session_id="here", include_profile=False, use_llm=False,
    )
    assert target in result.selected_ids
    assert "八万元" in result.memory_text


def test_expired_records_do_not_consume_current_record_limit(tmp_path):
    core = _core(tmp_path)
    add(core, "expired", "过期的安排", importance=0.9, valid_until="2000-01-01T00:00:00Z")
    target = add(core, "current", "仍有效的安排", importance=0.1)
    rows = core.repository.list_current_records(subject_id="owner", session_id="here", limit=1)
    assert [row["id"] for row in rows] == [target]


@pytest.mark.parametrize("content,term", [
    ("优惠比例100%", "%"),
    ("项目代号a_b", "a_b"),
    ("SQLite stores metadata", "SQLITE"),
    ("用户说过 O'Reilly", "O'Reilly"),
    ("最喜欢乌龙茶", "乌龙"),
])
def test_keyword_search_treats_terms_as_literals(tmp_path, content, term):
    core = _core(tmp_path)
    target = add(core, "literal", content)
    add(core, "noise", "unrelated value axb")
    rows = core.repository.search_current_records(
        query_terms=[term], subject_id="owner", session_id="here",
    )
    assert [row["id"] for row in rows] == [target]
    assert core.repository.search_current_records(query_terms=[], subject_id="owner") == []


def test_keyword_filtering_happens_before_limit(tmp_path):
    core = _core(tmp_path)
    for i in range(5):
        add(core, f"old{i}", "星河预算八万元", importance=0.9,
            valid_until="2000-01-01T00:00:00Z")
    add(core, "future", "星河预算八万元", importance=0.9,
        valid_from="2099-01-01T00:00:00Z")
    foreign = add(core, "foreign", "星河预算八万元", importance=0.9)
    core.update_memory_record(foreign, session_id="other")
    stranger = add(core, "stranger", "星河预算八万元", importance=0.9)
    core.update_memory_record(stranger, subject_id="qq:99")
    target = add(core, "shared", "星河预算八万元", importance=0.1)
    core.update_memory_record(target, session_id="")
    rows = core.repository.search_current_records(
        query_terms=["星河", "预算"], subject_id="owner", session_id="here", limit=1,
    )
    assert [row["id"] for row in rows] == [target]


def test_more_relevant_record_beats_generic_matches_before_limit(tmp_path):
    core = _core(tmp_path)
    for i in range(5):
        add(core, str(i), "讨论预算", importance=0.9)
    target = add(core, "specific", "星河预算八万元", importance=0.1)
    rows = core.repository.search_current_records(
        query_terms=["星河", "预算"], subject_id="owner", session_id="here", limit=1,
    )
    assert [row["id"] for row in rows] == [target]


@pytest.mark.parametrize("ineligible", ["expired", "future", "wrong_kind"])
def test_entity_recall_reaches_the_41st_linked_record(tmp_path, ineligible):
    core = _core(tmp_path)
    repo = core.repository
    entities = [repo.upsert_entity(name) for name in ("星河", "月亮")]
    target = add(core, "budget", "原定金额为八万元")
    repo.link_record_entities(target, entities)
    for i in range(40):
        kind = "summary" if ineligible == "wrong_kind" else "episode"
        rid, _ = repo.upsert_record(
            kind=kind, key=str(i), content=f"较新的记录 {i}", subject_id="owner",
            session_id="here",
            valid_until="2000-01-01" if ineligible == "expired" else None,
        )
        repo.link_record_entities(rid, entities)
    # Make the cutoff deterministic even on clocks with coarse resolution.
    conn = repo._connect()
    with conn:
        conn.execute("UPDATE memory_records SET last_confirmed_at='1990-01-01' WHERE id=?", (target,))
        if ineligible == "future":
            # Older databases can contain future-dated rows rejected by today's writer.
            conn.execute("UPDATE memory_records SET valid_from='2099-01-01' WHERE id<>?", (target,))
    rows = core._with_entity_hits(
        [], search_text="星河和月亮", person_id="owner", session_id="here", kinds=iter(["episode"]),
    )
    assert [row["id"] for row in rows] == [target]
    assert rows[0]["entity_hit"] is True


def test_linked_records_share_current_window_and_scope_semantics(tmp_path):
    core = _core(tmp_path)
    repo = core.repository
    entity = repo.upsert_entity("茶单")
    conn = repo._connect()
    # 2020-01-01 00:00 UTC, also 08:00 in +08:00.
    instant = 1577836800
    for key, start, end, subject, session in [
        ("opens_now", "2020-01-01T08:00:00+08:00", "", "owner", "here"),
        ("ends_now", "", "2020-01-01", "owner", "here"),
        ("legacy_global", "unknown", None, "", ""),
        ("another_group", "", "", "owner", "elsewhere"),
        ("visitor", "", "", "qq:42", "here"),
    ]:
        rid, _ = repo.upsert_record(kind="episode", key=key, content=key, subject_id=subject, session_id=session)
        repo.link_record_entities(rid, [entity])
        with conn:
            conn.execute("UPDATE memory_records SET valid_from=?, valid_until=? WHERE id=?", (start, end, rid))
    for person, expected in [("owner", {"opens_now", "legacy_global"}), ("qq:42", {"visitor"})]:
        rows = repo.records_for_entities([entity], subject_id=person, session_id="here",
                                         kinds=["episode"], current_at=instant)
        assert {row["key"] for row in rows} == expected
    assert repo.records_for_entities([entity], subject_id="owner", kinds=[], current_at=instant) == []


def test_chinese_short_terms_keep_short_only_matches_and_rank_mixed_hits_first(tmp_path):
    core = _core(tmp_path)
    both = add(core, "both", "乌龙茶预算", importance=0.1)
    short_only = add(core, "budget", "年度预算", importance=0.9)
    long_only = add(core, "tea", "买乌龙茶", importance=0.7)
    single_only = add(core, "leaves", "购买红茶", importance=0.9)

    def ids(*terms):
        return [row["id"] for row in core.repository.search_current_records(
            query_terms=terms, subject_id="owner", session_id="here",
        )]

    assert ids("茶") == [single_only, long_only, both]
    assert ids("预算") == [short_only, both]
    assert ids("乌龙茶", "预算") == [both, short_only, long_only]
