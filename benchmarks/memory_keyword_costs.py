"""Measure transactional keyword maintenance and the scoped scan crossover."""

import math
import random
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from benchmarks.memory_keywords import SyntheticStore, han_grams
from modules.memory_core import keyword_index
from modules.memory_core.repository import MemoryCoreRepository


@contextmanager
def database(directory):
    path = directory / "memory" / "memory.sqlite"
    path.parent.mkdir(parents=True)
    repo = MemoryCoreRepository(SyntheticStore(str(path)))
    conn = repo._connect()
    try:
        repo.initialize()
        yield repo, conn
    finally:
        conn.close()


def install(conn, dual):
    conn.create_function("han_grams", 1, han_grams, deterministic=True)
    tables = [("keyword_short", "unicode61", "han_grams({}content || ' ' || {}key)")]
    if dual:
        tables.append(("keyword_trigram", "trigram", "({}content || ' ' || {}key)"))
    for table, tokenizer, expression in tables:
        new = expression.format("new.", "new.")
        old = expression.format("old.", "old.")
        insert = f"INSERT INTO {table}(rowid,text) VALUES(new.rowid,{new});"
        delete = f"INSERT INTO {table}({table},rowid,text) VALUES('delete',old.rowid,{old});"
        with conn:
            conn.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(text,content='',tokenize='{tokenizer}')")
            conn.execute(f"INSERT INTO {table}(rowid,text) SELECT rowid,{expression.format('', '')} FROM memory_records")
            for suffix, event, body in [
                ("ai", "INSERT", insert), ("ad", "DELETE", delete),
                ("au", "UPDATE OF key,content", delete + insert),
            ]:
                conn.execute(f"CREATE TRIGGER {table}_{suffix} AFTER {event} ON memory_records BEGIN {body} END")


def stats(ns):
    ordered = sorted(ns)
    return f"{median(ordered)/1e6:.3f}/{ordered[math.ceil(.95*len(ns))-1]/1e6:.3f}"


INSERT = ("INSERT INTO memory_records(id,key,content,session_id,subject_id,kind,created_at,updated_at) "
          "VALUES(?,?,?,?,'owner','episode','2026-01-01','2026-01-01')")
FREQUENCIES = [(1, "春茶"), (5, "茶叶"), (15, "午餐"), (30, "工作"), (50, "预算"),
               (70, "会议"), (90, "整理"), (100, "记录")]


def seed(conn):
    rng = random.Random(227)
    rows = []
    for i in range(12000):
        # Shuffle frequency independently of scope; the same corpus is used on all paths.
        p = rng.randrange(100)
        terms = "，".join(term for pct, term in FREQUENCIES if p < pct)
        session = "small" if i < 120 else "medium" if i < 1320 else "large"
        rows.append((f"r{i}", f"k{i}", f"今天更新{terms}，编号{i}", session))
    with conn:
        conn.executemany(INSERT, rows)


def write_costs(repos):
    rng = random.Random(891)
    alphabet = "天气晴朗公园散步工作进度饮食预算交通工具旅行读书运动银行季节假期校园"
    for length in (24, 256):
        content = ["".join(rng.choices(alphabet, k=length)) for _ in range(150)]
        edits = ["".join(rng.choices(alphabet, k=length)) for _ in content]
        ids = {name: [] for name in repos}
        for label in ("insert", "edit", "metadata", "delete"):
            elapsed = {name: [] for name in repos}
            for i, body in enumerate(content):
                names = list(repos)
                offset = i % len(names)
                for name in names[offset:] + names[:offset]:
                    repo = repos[name]
                    start = perf_counter_ns()
                    if label == "insert":
                        rid, created = repo.upsert_record(kind="episode", key=f"活动{i}", content=body,
                                                          subject_id="owner", session_id="large")
                        assert created
                        ids[name].append(rid)
                    elif label == "edit":
                        changed = repo.update_record(ids[name][i], {"key": f"修改{i}", "content": edits[i]})
                        assert changed
                    elif label == "metadata":
                        changed = repo.update_record_metadata(ids[name][i], {"label": "updated"})
                        assert changed
                    else:
                        deleted = repo.delete_record(ids[name][i])
                        assert deleted
                    if i >= 10:
                        elapsed[name].append(perf_counter_ns() - start)
            print("write", length, label, *(stats(elapsed[name]) for name in repos), flush=True)


def crossover(repo, conn):
    baseline = MemoryCoreRepository(repo.store)
    repo.initialize(keyword_index_enabled=True)
    for session in ("small", "medium", "large"):
        for pct, term in FREQUENCIES:
            kwargs = dict(query_terms=[term], subject_id="owner", session_id=session, limit=20)
            queries = []
            conn.set_trace_callback(queries.append)
            try:
                baseline.search_current_records(**kwargs)
                exact = queries[-1]
                queries.clear()
                repo.search_current_records(**kwargs)
            finally:
                conn.set_trace_callback(None)
            route = "index" if any(q.startswith("WITH memory_records") for q in queries) else "scan"
            indexed = (f"WITH memory_records AS (SELECT r.* FROM {keyword_index.TABLE} k "
                       "CROSS JOIN main.memory_records r ON r.rowid=k.rowid "
                       f"WHERE {keyword_index.TABLE} MATCH ?) " + exact)
            expression = keyword_index.match_expression([term])
            elapsed = [[], [], []]
            for i in range(22):
                results = {}
                for offset in range(3):
                    j = (i + offset) % 3
                    start = perf_counter_ns()
                    if j == 1:
                        rows = [repo._row_to_record(r) for r in conn.execute(indexed, (expression,))]
                    else:
                        reader = baseline if j == 0 else repo
                        rows = reader.search_current_records(**kwargs)
                    results[j] = [r["id"] for r in rows]
                    if i >= 2:
                        elapsed[j].append(perf_counter_ns() - start)
                assert results[0] == results[1] == results[2]
            hits = conn.execute(f"SELECT count(*) FROM {keyword_index.TABLE} WHERE {keyword_index.TABLE} MATCH ?",
                                (expression,)).fetchone()[0]
            print("read", session, pct, hits, route, *(stats(item) for item in elapsed), flush=True)


def run():
    print("SQLite", sqlite3.sqlite_version, "milliseconds p50/p95")
    with TemporaryDirectory() as temp, ExitStack() as stack:
        repos = {}
        for name in ("scan", "short", "dual", "native"):
            repo, conn = stack.enter_context(database(Path(temp) / name))
            seed(conn)
            if name == "native":
                pages = conn.execute("PRAGMA page_count").fetchone()[0]
                start = perf_counter_ns()
                if not keyword_index.configure(conn, True):
                    raise RuntimeError("This benchmark requires SQLite FTS5")
                seconds = (perf_counter_ns() - start) / 1e9
                extra = conn.execute("PRAGMA page_count").fetchone()[0] - pages
                size = conn.execute("PRAGMA page_size").fetchone()[0] * extra / 2**20
                print(f"native backfill {seconds:.3f}s, extra {size:.2f} MiB", flush=True)
            elif name != "scan":
                install(conn, dual=name == "dual")
            repos[name] = repo
        print("write: chars operation baseline short-prototype dual-prototype production")
        write_costs(repos)
        print("read: scope target-frequency-percent candidates route baseline forced-index adaptive")
        crossover(repo, conn)


if __name__ == "__main__":
    run()
