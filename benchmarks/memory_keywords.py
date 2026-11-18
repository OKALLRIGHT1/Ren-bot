"""Compare SQLite keyword candidates on synthetic memories. Run as a module."""

import math
import re
import sqlite3
import statistics
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from modules.memory_core.repository import MemoryCoreRepository
from modules.memory_core.service import MemoryCoreService
from modules.memory_sqlite import MemorySQLite


class SyntheticStore(MemorySQLite):
    def _bootstrap_from_legacy_if_empty(self):
        """Use the real schema without importing the user's transcript or profile."""


def sample_rows(count):
    phrases = ["星河预算和乌龙茶", "购买红茶", "年度预算", "追加预算", "星河预算", "买乌龙茶"]
    for i in range(count):
        slot = i % 100
        phrase = phrases[slot] if slot < len(phrases) else "今天整理会议笔记"
        yield (
            f"r{i:06}", "other" if i % 11 == 0 else "owner",
            "away" if i % 7 == 0 else "here", f"{phrase}，记录编号{i}",
            i % 13 / 13,
        )


def han_grams(text):
    tokens = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(run)
        tokens.update(a + b for a, b in zip(run, run[1:]))
    return " ".join(sorted(tokens))


def candidate_query(exact, groups):
    selections = []
    params = []
    for table, terms in groups:
        if terms:
            selections.append(f"SELECT rowid AS rid FROM {table} WHERE {table} MATCH ?")
            params.append(" OR ".join('"' + term.replace('"', '""') + '"' for term in terms))
    candidates = " UNION ".join(selections)
    return (
        f"WITH memory_records AS (SELECT r.* FROM ({candidates}) hits "
        "CROSS JOIN main.memory_records r WHERE r.rowid=hits.rid) " + exact, params,
    )


def compare(repo, conn, terms):
    # Capture the actual production ranking, scope, validity and row projection.
    queries = []
    conn.set_trace_callback(queries.append)
    try:
        reference = repo.search_current_records(
            query_terms=terms, subject_id="owner", session_id="here", kinds=["episode"], limit=20,
        )
    finally:
        conn.set_trace_callback(None)
    exact, limit, _ = queries[-1].rpartition(" LIMIT ")
    assert limit, "Update benchmark for changed production SQL"
    indexed = candidate_query(exact, [("keyword_trigram", terms)])
    short = [term for term in terms if len(term) < 3]
    long = [term for term in terms if len(term) >= 3]
    paths = {"exact": (exact, ()), "trigram": indexed, "safe": (exact, ()) if short else indexed}
    if all(re.fullmatch(r"[\u4e00-\u9fff]+", t) for t in short):
        paths["han+trigram"] = candidate_query(exact, [("keyword_short", short), ("keyword_trigram", long)])
    else:
        paths["han+trigram"] = (exact, ())
    if terms == ["乌龙茶", "预算"]:
        sql, params = paths["han+trigram"]
        for row in conn.execute("EXPLAIN QUERY PLAN " + sql + " LIMIT 20", params):
            print("plan:", row["detail"])
    truth = {row["id"] for row in conn.execute(exact)}
    assert truth
    top = [row["id"] for row in reference]
    timings = {name: [] for name in paths}

    def top_ids(sql, params):
        return [row["id"] for row in conn.execute(sql + " LIMIT 20", params)]

    for i in range(32):
        # Rotate order across rounds to distribute warm-cache and scheduling effects.
        names = list(paths)
        offset = i % len(names)
        for name in names[offset:] + names[:offset]:
            start = perf_counter()
            top_ids(*paths[name])
            if i >= 2:
                timings[name].append((perf_counter() - start) * 1000)
    for name, (sql, params) in paths.items():
        found = {row["id"] for row in conn.execute(sql, params)}
        order_matches = top_ids(sql, params) == top
        if name != "trigram":
            assert found == truth and order_matches
        ms = sorted(timings[name])
        print(f"{'+'.join(terms)} | {name} | {len(found & truth)}/{len(truth)} | "
              f"{order_matches} | {statistics.median(ms):.2f} | {ms[math.ceil(.95 * len(ms))-1]:.2f}")


def run(directory):
    path = directory / "memory" / "memory.sqlite"
    path.parent.mkdir()
    repo = MemoryCoreRepository(SyntheticStore(str(path)))
    conn = repo._connect()
    try:
        repo.initialize()
        with conn:
            conn.executemany(
                "INSERT INTO memory_records(id,subject_id,session_id,content,importance,"
                "kind,created_at,updated_at) VALUES(?,?,?,?,?,'episode','2026-01-01','2026-01-01')",
                sample_rows(50_000),
            )
        print(f"SQLite {sqlite3.sqlite_version}")
        conn.create_function("han_grams", 1, han_grams)
        for table, tokenizer, body in (
            ("keyword_trigram", "trigram", "content || ' ' || key"),
            ("keyword_short", "unicode61", "han_grams(content || ' ' || key)"),
        ):
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            start = perf_counter()
            with conn:
                conn.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(text,content='',tokenize='{tokenizer}')")
                conn.execute(f"INSERT INTO {table}(rowid,text) SELECT rowid,{body} FROM memory_records")
            elapsed = perf_counter() - start
            extra_pages = conn.execute("PRAGMA page_count").fetchone()[0] - pages
            size = extra_pages * conn.execute("PRAGMA page_size").fetchone()[0]
            print(f"{table}: build {elapsed:.2f} s; additional pages {size / 2**20:.2f} MiB")
        for terms in (
            ["茶"], ["预算"], ["乌龙茶"], ["乌龙茶", "预算"],
            sorted(MemoryCoreService._tokens("星河预算")), ["记录"],
        ):
            compare(repo, conn, terms)
    finally:
        conn.close()


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        run(Path(temp))
