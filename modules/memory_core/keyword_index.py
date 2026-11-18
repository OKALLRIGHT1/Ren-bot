"""Optional FTS5 candidates for literal searches containing Han characters."""

import re
import sqlite3


TABLE = "memory_han_keywords_v1"
MIN_SCOPE = 512
SCOPE_PROBE_LIMIT = 8192
MAX_CHARS = 384
_PAIR = re.compile(r"[\u4e00-\u9fff]{2}")
_CHAR = re.compile(r"[\u4e00-\u9fff]")


def _tokens_sql(body):
    # Keep triggers usable from any SQLite connection, including older clients.
    # Long and NUL-containing strings always participate in exact rechecking.
    return f"""(SELECT CASE WHEN length(s)>{MAX_CHARS} OR instr(s,char(0))>0
        THEN 'oversize' ELSE (
            WITH RECURSIVE pos(i) AS (
                SELECT 1 UNION ALL SELECT i+1 FROM pos WHERE i<length(s)
            )
            SELECT group_concat(substr(s,i,1)||' '||substr(s,i,2),' ') FROM pos
        ) END FROM (SELECT {body} AS s))"""


def configure(conn: sqlite3.Connection, enabled: bool | None) -> bool:
    """Publish the table, old rows and maintenance triggers in one transaction."""
    def installed():
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone() is not None

    present = installed()
    if enabled is None or enabled == present:
        return present
    # Reserve the writer before checking again, so concurrent initializers cannot
    # both backfill the same rows. Called after the schema transaction has ended.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not enabled:
            for suffix in ("ai", "ad", "au"):
                conn.execute(f"DROP TRIGGER IF EXISTS {TABLE}_{suffix}")
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        elif not installed():
            conn.execute(f"CREATE VIRTUAL TABLE {TABLE} USING fts5(tokens,tokenize='ascii',detail=none,columnsize=0)")
            body = _tokens_sql("content || ' ' || key")
            conn.execute(f"INSERT INTO {TABLE}(rowid,tokens) SELECT rowid,{body} FROM memory_records")
            body = _tokens_sql("new.content || ' ' || new.key")
            insert = f"INSERT INTO {TABLE}(rowid,tokens) VALUES(new.rowid,{body});"
            delete = f"DELETE FROM {TABLE} WHERE rowid=old.rowid;"
            for suffix, event, action in (
                ("ai", "INSERT ON memory_records", insert),
                ("ad", "DELETE ON memory_records", delete),
                ("au", "UPDATE ON memory_records WHEN old.rowid<>new.rowid OR "
                       "old.content<>new.content OR old.key<>new.key", delete + insert),
            ):
                conn.execute(f"CREATE TRIGGER {TABLE}_{suffix} AFTER {event} BEGIN {action} END")
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if "no such module: fts5" in str(exc).lower():
            return False
        raise
    except BaseException:
        conn.rollback()
        raise
    return enabled


def match_expression(terms):
    anchors = set()
    for term in terms:
        match = _PAIR.search(term) or _CHAR.search(term)
        if match is None:
            return None
        anchors.add(match.group())
    # Every literal match contains its anchor; false positives are re-ranked by SQL.
    return " OR ".join(sorted(anchors | {"oversize"}))


def indexed_cursor(conn, sql, params, terms, scope_filters, scope_args):
    expression = match_expression(terms)
    if not expression:
        return None
    where = " AND ".join(scope_filters) or "1"
    visible = conn.execute(
        f"SELECT count(*) FROM (SELECT 1 FROM memory_records WHERE {where} LIMIT ?)",
        [*scope_args, SCOPE_PROBE_LIMIT],
    ).fetchone()[0]
    if visible < MIN_SCOPE:
        return None
    budget = visible * 3 // 4
    candidates = conn.execute(
        f"SELECT count(*) FROM (SELECT rowid FROM {TABLE} WHERE {TABLE} MATCH ? LIMIT ?)",
        (expression, budget + 1),
    ).fetchone()[0]
    if candidates > budget:
        return None
    prefix = (
        f"WITH memory_records AS (SELECT r.* FROM {TABLE} "
        f"CROSS JOIN main.memory_records r ON r.rowid={TABLE}.rowid "
        f"WHERE {TABLE} MATCH ?) "
    )
    return conn.execute(prefix + sql, [expression, *params])
