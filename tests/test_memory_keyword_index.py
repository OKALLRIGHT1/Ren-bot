import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from modules.memory_core import MemoryCoreService, keyword_index
from modules.memory_core.repository import MemoryCoreRepository
from modules.memory_sqlite import MemorySQLite


@pytest.fixture
def memory(tmp_path):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    core = MemoryCoreService(store, settings={"memory_writeback_enabled": False})
    core.initialize()
    conn = store._connect()
    with conn:
        conn.executemany(
            "INSERT INTO memory_records(id,kind,subject_id,session_id,content,created_at,updated_at) "
            "VALUES(?,'episode','owner','here','常见背景','','')", [(str(i),) for i in range(600)],
        )
    # Existing rows must be backfilled when an existing database opts in.
    core.settings["memory_keyword_index_enabled"] = True
    core.initialize()
    yield core, conn
    conn.close()


def compare(core, conn, terms, indexed, *, session="here", kinds=("episode",)):
    kwargs = dict(query_terms=terms, subject_id="owner", session_id=session, kinds=kinds, limit=300)
    expected = MemoryCoreRepository(core.store).search_current_records(**kwargs)
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        actual = core.repository.search_current_records(**kwargs)
    finally:
        conn.set_trace_callback(None)
    assert actual == expected
    assert any(s.startswith("WITH memory_records AS") for s in statements) == indexed
    return [r["id"] for r in actual]


def test_updates_and_rollback_work_from_an_independent_sqlite_connection(memory):
    core, conn = memory
    table = keyword_index.TABLE

    def postings(term):
        return [r[0] for r in conn.execute(f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (term,))]

    with closing(sqlite3.connect(core.store.db_path)) as writer:
        with writer:
            cursor = writer.execute(
                "INSERT INTO memory_records(id,key,kind,content,subject_id,created_at,updated_at) "
                "VALUES('target','茶单','episode','秋雨','owner','','')"
            )
            rowid = cursor.lastrowid
        assert postings("秋雨") == postings("茶单") == [rowid]
        assert compare(core, conn, ["秋雨"], True) == ["target"]
        writer.execute("UPDATE memory_records SET content='春茶', key='饮料' WHERE id='target'")
        writer.rollback()
        assert postings("秋雨") == [rowid] and not postings("春茶")
        with writer:
            writer.execute("UPDATE memory_records SET content='春茶', key='饮料' WHERE id='target'")
        assert not postings("秋雨") and not postings("茶单")
        assert postings("春茶") == postings("饮料") == [rowid]
        assert compare(core, conn, ["春茶", "茶"], True) == ["target"]
        with writer:
            writer.execute("DELETE FROM memory_records WHERE id='target'")
        assert not postings("春茶") and not postings("饮料")
        assert compare(core, conn, ["春茶"], True) == []


def test_routing_preserves_scope_validity_literals_and_order(memory):
    core, conn = memory
    for rid, body, key, subject, session, end in (
        ("both", "春茶预算", "茶具", "owner", "here", ""),
        ("long", "春茶" + "字" * 500 + "饮料", "a_b", "", "", ""),
        ("nul", "甲\0春茶", "", "owner", "here", ""),
        ("edge", "龿茶", "sale%", "owner", "here", ""),
        ("hidden", "春茶", "", "other", "here", ""),
        ("away", "春茶", "", "owner", "away", ""),
        ("old", "春茶", "", "owner", "here", "2000-01-01"),
    ):
        with conn:
            conn.execute(
                "INSERT INTO memory_records(id,content,key,subject_id,session_id,valid_until,kind,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'episode','','')", (rid, body, key, subject, session, end),
            )
    assert set(compare(core, conn, ["春茶", "预算"], True)) == {"both", "long", "nul"}
    assert compare(core, conn, ["字饮料"], True) == ["long"]
    assert compare(core, conn, ["龿茶"], True) == ["edge"]
    assert compare(core, conn, ["算 茶具"], True) == ["both"]
    assert compare(core, conn, ["%", "预算"], False) == ["both", "edge"]
    assert compare(core, conn, ["春茶"], False, session="away")
    assert compare(core, conn, ["常见"], False)
    assert compare(core, conn, ["春茶"], False, kinds=("rule",)) == []


def test_budget_counts_out_of_scope_candidates_and_exact_boundaries(memory):
    core, conn = memory
    def set_matches(n):
        with conn:
            conn.execute("UPDATE memory_records SET content='门槛' WHERE CAST(id AS INTEGER) < ?", (n,))

    set_matches(450)  # 75% of the 600 visible rows.
    compare(core, conn, ["门槛"], True)
    with conn:
        conn.execute("INSERT INTO memory_records(id,kind,subject_id,session_id,content,created_at,updated_at) "
                     "VALUES('foreign','episode','other','away','门槛','','')")
    compare(core, conn, ["门槛"], False)
    with conn:
        conn.execute("DELETE FROM memory_records WHERE CAST(id AS INTEGER) >= 511")
    compare(core, conn, ["从未命中"], False)


def test_disable_reopen_and_atomic_rebuild(memory):
    core, conn = memory
    other = MemoryCoreRepository(core.store)
    other.initialize(keyword_index_enabled=False)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name GLOB 'memory_han_keywords_v1*'").fetchall()
    assert compare(core, conn, ["春茶"], False) == []
    with conn:
        conn.execute("UPDATE memory_records SET content='春茶' WHERE id='1'")

    def deny_trigger(action, *args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK

    conn.set_authorizer(deny_trigger)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            keyword_index.configure(conn, True)
    finally:
        conn.set_authorizer(None)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name=?", (keyword_index.TABLE,)).fetchone()
    core.initialize()
    core.initialize()
    assert compare(core, conn, ["春茶"], True) == ["1"]


def test_concurrent_initializers_publish_one_complete_index(memory):
    core, conn = memory
    keyword_index.configure(conn, False)
    barrier = threading.Barrier(2)

    def enable():
        with closing(sqlite3.connect(core.store.db_path)) as other:
            barrier.wait(timeout=5)
            return keyword_index.configure(other, True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(enable) for _ in range(2)]
        assert all(f.result(timeout=10) for f in futures)
    assert conn.execute(f"SELECT count(*) FROM {keyword_index.TABLE}").fetchone()[0] == 600
    with conn:
        conn.execute("UPDATE memory_records SET key='红茶' WHERE id='2'")
    assert compare(core, conn, ["红茶"], True) == ["2"]
