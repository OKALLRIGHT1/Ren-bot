# modules/
"""
SQLite memory store (source of truth)

- ./memory/memory.sqlite is the primary store for:
  - transcript (all chat messages)
  - memory_items (manual long-term notes: rules/preferences/facts/assistant_said)
  - episodes (episodic summaries)
  - profile (stable user profile)
  - expression_patterns (reply-style learning library)
  - proposals (optional: LLM write proposals awaiting approval)
  - audit_log (change history)

This module is intentionally lightweight and safe:
- WAL enabled
- single process OK; multi-thread safe via per-call connections + a simple lock
- provides FTS5 for fast search on transcript/memory_items/episodes
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_MEMORY_DIR = os.getenv("MEMORY_DIR", "./memory")
DEFAULT_DB_PATH = os.getenv(
    "MEMORY_SQLITE_PATH", os.path.join(DEFAULT_MEMORY_DIR, "memory.sqlite")
)

LEGACY_PROFILE_JSON = os.getenv("LEGACY_PROFILE_JSON", "./memory_db/profile.json")
LEGACY_EVENTS_DB = os.getenv("LEGACY_EVENTS_DB", "./data/events.sqlite")

# Historical transcript mojibake repair (UTF-8 mis-decoded costume notices).
# Keep until old rows may still exist in user DBs; repair_known_transcript_mojibake uses these.
_BROKEN_COSTUME_MESSAGE_PREFIX = "鐢ㄦ埛涓轰綘鏇存崲浜嗘湇瑁"
_FIXED_COSTUME_MESSAGE_PREFIX = "用户为你更换了服装，文件路径为: "


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _pj(s: Optional[str], default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


@dataclass
class MemoryItem:
    id: str
    type: str
    status: str
    pin: int
    confidence: float
    tags: List[str]
    text: str
    source: str
    created_at: str
    updated_at: str


class MemorySQLite:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        _ensure_dir(db_path)
        self._lock = threading.Lock()
        # ✅ 性能优化：使用线程本地存储的连接池
        self._local = threading.local()
        self._init_db()
        self._bootstrap_from_legacy_if_empty()
        self.repair_known_transcript_mojibake()

    def _connect(self) -> sqlite3.Connection:
        """获取连接（线程本地复用，性能优化）"""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                self.db_path, timeout=30, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn.execute("PRAGMA foreign_keys=ON;")
            # ✅ 性能优化：启用查询计划缓存
            self._local.conn.execute("PRAGMA cache_size=-10000;")
        return self._local.conn

    def _table_columns(self, table_name: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows if row and row["name"]}

    def _ensure_column(
        self, conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str
    ) -> None:
        try:
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                if row and row["name"]
            }
            if column_name not in columns:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")
        except Exception:
            pass

    def _init_db(self) -> None:
        with self._connect() as conn:
            # transcript
            conn.execute("""
            CREATE TABLE IF NOT EXISTS transcript (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              ts_iso TEXT NOT NULL,
              session_id TEXT,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              meta_json TEXT
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_ts ON transcript(ts)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_role ON transcript(role)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_transcript_session ON transcript(session_id)"
            )

            # memory items
            conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_items (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              status TEXT NOT NULL,
              pin INTEGER NOT NULL DEFAULT 0,
              confidence REAL NOT NULL DEFAULT 1.0,
              tags_json TEXT NOT NULL DEFAULT '[]',
              text TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'manual',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_status_pin_time ON memory_items(status, pin, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_type_status ON memory_items(type, status)"
            )

            # episodes
            conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              title TEXT,
              summary TEXT,
              tags_json TEXT NOT NULL DEFAULT '[]',
              assistant_said_json TEXT NOT NULL DEFAULT '[]',
              range_start INTEGER,
              range_end INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_status_time ON episodes(status, updated_at)"
            )

            # profile KV
            conn.execute("""
            CREATE TABLE IF NOT EXISTS profile (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """)

            # qq user profiles
            conn.execute("""
            CREATE TABLE IF NOT EXISTS qq_user_profiles (
              user_id TEXT PRIMARY KEY,
              nickname TEXT,
              remark_name TEXT,
              identity_summary TEXT,
              relationship_to_owner TEXT,
              reply_style TEXT,
              preferences_json TEXT NOT NULL DEFAULT '{}',
              taboos_json TEXT NOT NULL DEFAULT '[]',
              permission_level TEXT,
              memory_scope TEXT,
              voice_reply_probability INTEGER NOT NULL DEFAULT 0,
              is_owner INTEGER NOT NULL DEFAULT 0,
              notes TEXT,
              updated_at TEXT NOT NULL
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qq_profiles_owner ON qq_user_profiles(is_owner, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qq_profiles_scope ON qq_user_profiles(memory_scope, updated_at)"
            )

            # proposals
            conn.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              source_message_id TEXT,
              created_at TEXT NOT NULL,
              resolved_at TEXT
            )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_proposals_status_time ON proposals(status, created_at)"
            )

            # audit log
            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_iso TEXT NOT NULL,
              action TEXT NOT NULL,
              entity TEXT NOT NULL,
              entity_id TEXT,
              before_json TEXT,
              after_json TEXT,
              note TEXT
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts_iso)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id)"
            )

            # FTS (best-effort)
            try:
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts
                USING fts5(content, role, ts_iso, content='transcript', content_rowid='id')
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS transcript_ai AFTER INSERT ON transcript BEGIN
                  INSERT INTO transcript_fts(rowid, content, role, ts_iso) VALUES (new.id, new.content, new.role, new.ts_iso);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS transcript_ad AFTER DELETE ON transcript BEGIN
                  INSERT INTO transcript_fts(transcript_fts, rowid, content, role, ts_iso) VALUES('delete', old.id, old.content, old.role, old.ts_iso);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS transcript_au AFTER UPDATE ON transcript BEGIN
                  INSERT INTO transcript_fts(transcript_fts, rowid, content, role, ts_iso) VALUES('delete', old.id, old.content, old.role, old.ts_iso);
                  INSERT INTO transcript_fts(rowid, content, role, ts_iso) VALUES (new.id, new.content, new.role, new.ts_iso);
                END;
                """)
            except Exception:
                pass

            try:
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts
                USING fts5(text, type, status, content='memory_items', content_rowid='rowid')
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON memory_items BEGIN
                  INSERT INTO memory_items_fts(rowid, text, type, status) VALUES (new.rowid, new.text, new.type, new.status);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON memory_items BEGIN
                  INSERT INTO memory_items_fts(memory_items_fts, rowid, text, type, status) VALUES('delete', old.rowid, old.text, old.type, old.status);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON memory_items BEGIN
                  INSERT INTO memory_items_fts(memory_items_fts, rowid, text, type, status) VALUES('delete', old.rowid, old.text, old.type, old.status);
                  INSERT INTO memory_items_fts(rowid, text, type, status) VALUES (new.rowid, new.text, new.type, new.status);
                END;
                """)
            except Exception:
                pass

            try:
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
                USING fts5(title, summary, status, content='episodes', content_rowid='rowid')
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
                  INSERT INTO episodes_fts(rowid, title, summary, status) VALUES (new.rowid, new.title, new.summary, new.status);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
                  INSERT INTO episodes_fts(episodes_fts, rowid, title, summary, status) VALUES('delete', old.rowid, old.title, old.summary, old.status);
                END;
                """)
                conn.execute("""
                CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
                  INSERT INTO episodes_fts(episodes_fts, rowid, title, summary, status) VALUES('delete', old.rowid, old.title, old.summary, old.status);
                  INSERT INTO episodes_fts(rowid, title, summary, status) VALUES (new.rowid, new.title, new.summary, new.status);
                END;
                """)
            except Exception:
                pass

            # 每日屏幕活动统计表
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_screen_stats (
                    date TEXT PRIMARY KEY,
                    summary_json TEXT,
                    total_hours REAL,
                    updated_at TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_events (
                    event_id TEXT PRIMARY KEY,
                    ts_iso TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    presence TEXT,
                    app_id TEXT,
                    app_name TEXT,
                    pid INTEGER,
                    window_title TEXT,
                    browser_family TEXT,
                    browser_name TEXT,
                    page_title TEXT,
                    url TEXT,
                    domain TEXT,
                    source TEXT,
                    raw_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_events_ts ON activity_events(ts_iso)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_events_domain ON activity_events(domain, ts_iso)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_events_app ON activity_events(app_name, ts_iso)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_latest (
                    source TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    ts_iso TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    presence TEXT,
                    app_id TEXT,
                    app_name TEXT,
                    pid INTEGER,
                    window_title TEXT,
                    browser_family TEXT,
                    browser_name TEXT,
                    page_title TEXT,
                    url TEXT,
                    domain TEXT,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expression_patterns (
                    id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL DEFAULT '',
                    character_name TEXT NOT NULL DEFAULT '',
                    scene TEXT NOT NULL DEFAULT 'chat',
                    situation TEXT NOT NULL DEFAULT '',
                    style TEXT NOT NULL DEFAULT '',
                    example TEXT NOT NULL DEFAULT '',
                    content_list_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'manual',
                    quality_score REAL NOT NULL DEFAULT 0,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expression_patterns_char_scene ON expression_patterns(character_name, scene, enabled)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expression_patterns_updated ON expression_patterns(enabled, updated_at)"
            )
            self._ensure_column(
                conn,
                "expression_patterns",
                "content_list_json",
                "content_list_json TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "expression_patterns",
                "character_id",
                "character_id TEXT NOT NULL DEFAULT ''",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expression_patterns_char_id_scene "
                "ON expression_patterns(character_id, scene, enabled)"
            )
            conn.commit()

    # ---------- audit ----------
    def _audit(
        self,
        action: str,
        entity: str,
        entity_id: Optional[str],
        before: Any,
        after: Any,
        note: str = "",
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO audit_log(ts_iso, action, entity, entity_id, before_json, after_json, note) VALUES(?,?,?,?,?,?,?)",
                    (
                        _now_iso(),
                        action,
                        entity,
                        entity_id,
                        _j(before) if before is not None else None,
                        _j(after) if after is not None else None,
                        note,
                    ),
                )
                conn.commit()
        except Exception:
            pass

    # ---------- bootstrap ----------
    def _table_count(self, table: str) -> int:
        with self._connect() as conn:
            r = conn.execute(f"SELECT COUNT(1) AS c FROM {table}").fetchone()
            return int(r["c"]) if r else 0

    def _bootstrap_from_legacy_if_empty(self) -> None:
        # Only bootstrap if empty
        if self._table_count("profile") == 0:
            self._import_legacy_profile_json()
        if self._table_count("transcript") == 0:
            self._import_legacy_events_sqlite(max_rows=50000)

    def _import_legacy_profile_json(self) -> None:
        if not os.path.exists(LEGACY_PROFILE_JSON):
            return
        try:
            obj = json.loads(open(LEGACY_PROFILE_JSON, "r", encoding="utf-8").read())
            if not isinstance(obj, dict):
                return
            # expected keys: name/likes/dislikes/notes
            for k in ("name", "likes", "dislikes", "notes"):
                if k in obj:
                    self.set_profile_value(k, obj.get(k))
        except Exception:
            return

    def _import_legacy_events_sqlite(self, max_rows: int = 50000) -> None:
        if not os.path.exists(LEGACY_EVENTS_DB):
            return
        try:
            conn = sqlite3.connect(LEGACY_EVENTS_DB)
            conn.row_factory = sqlite3.Row
            # table name chat_messages in main.py logger
            rows = conn.execute(
                "SELECT id, ts, role, content, meta FROM chat_messages ORDER BY id ASC LIMIT ?",
                (int(max_rows),),
            ).fetchall()
            conn.close()
            for r in rows:
                ts = int(r["ts"] or int(time.time()))
                role = str(r["role"] or "unknown")
                content = str(r["content"] or "")
                meta = _pj(r["meta"], {})
                self.add_transcript(role, content, meta=meta, ts=ts)
        except Exception:
            return

    # ---------- transcript ----------
    def add_transcript(
        self,
        role: str,
        content: str,
        meta: Optional[dict] = None,
        ts: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Optional[int]:
        content = (content or "").strip()
        if not content:
            return None
        role = (role or "unknown").strip() or "unknown"
        ts_i = int(ts if ts is not None else time.time())
        ts_iso = _now_iso()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO transcript(ts, ts_iso, session_id, role, content, meta_json) VALUES(?,?,?,?,?,?)",
                    (ts_i, ts_iso, session_id, role, content, _j(meta or {})),
                )
                conn.commit()
                return int(cursor.lastrowid)

    def repair_known_transcript_mojibake(self) -> int:
        """Repair the known costume-change message that was stored from a bad literal."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,content FROM transcript WHERE role='system' AND content LIKE ?",
                (f"{_BROKEN_COSTUME_MESSAGE_PREFIX}%",),
            ).fetchall()
        updates = []
        for row in rows:
            content = str(row["content"] or "")
            path_match = re.search(r"[A-Za-z]:[/\\\\].*$", content)
            if path_match is None:
                continue
            repaired = _FIXED_COSTUME_MESSAGE_PREFIX + path_match.group(0)
            if repaired != content:
                updates.append((repaired, int(row["id"])))
        if not updates:
            return 0
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    "UPDATE transcript SET content=? WHERE id=?",
                    updates,
                )
                conn.commit()
        return len(updates)

    def _activity_event_values(self, event: Dict[str, Any]) -> Tuple[Any, ...]:
        event_id = str(event.get("event_id") or uuid.uuid4().hex).strip()
        ts_iso = str(event.get("ts") or _now_iso()).strip()
        kind = str(event.get("kind") or "activity_sample").strip()
        presence = str(event.get("presence") or "").strip()
        app = event.get("app") if isinstance(event.get("app"), dict) else {}
        browser = event.get("browser") if isinstance(event.get("browser"), dict) else {}
        return (
            event_id,
            ts_iso,
            kind,
            presence,
            str(app.get("id") or ""),
            str(app.get("name") or ""),
            int(app.get("pid") or 0),
            str(event.get("window_title") or ""),
            str(browser.get("family") or ""),
            str(browser.get("name") or ""),
            str(browser.get("page_title") or ""),
            str(browser.get("url") or ""),
            str(browser.get("domain") or ""),
            str(event.get("source") or ""),
            _j(event),
        )

    def add_activity_event(self, event: Dict[str, Any]) -> None:
        values = self._activity_event_values(event)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO activity_events(
                      event_id, ts_iso, kind, presence, app_id, app_name, pid,
                      window_title, browser_family, browser_name, page_title,
                      url, domain, source, raw_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values,
                )
                conn.commit()

    def upsert_activity_latest(self, event: Dict[str, Any]) -> None:
        values = self._activity_event_values(event)
        source = str(event.get("source") or "").strip()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO activity_latest(
                      source, event_id, ts_iso, kind, presence, app_id, app_name, pid,
                      window_title, browser_family, browser_name, page_title,
                      url, domain, raw_json, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        source,
                        values[0],
                        values[1],
                        values[2],
                        values[3],
                        values[4],
                        values[5],
                        values[6],
                        values[7],
                        values[8],
                        values[9],
                        values[10],
                        values[11],
                        values[12],
                        values[14],
                        _now_iso(),
                    ),
                )
                conn.commit()

    def ingest_activity_event(self, event: Dict[str, Any]) -> Dict[str, bool]:
        self.upsert_activity_latest(event)
        kind = str(event.get("kind") or "activity_sample").strip().lower()
        historized = kind != "activity_sample"
        if historized:
            self.add_activity_event(event)
        return {"latest": True, "historized": historized}

    def _activity_row_to_dict(self, r: sqlite3.Row) -> Dict[str, Any]:
        raw_payload = _pj(r["raw_json"], {})
        item = {
            "event_id": r["event_id"],
            "ts": r["ts_iso"],
            "kind": r["kind"],
            "presence": r["presence"],
            "app": {
                "id": r["app_id"],
                "name": r["app_name"],
                "pid": r["pid"],
            },
            "window_title": r["window_title"],
            "browser": {
                "family": r["browser_family"],
                "name": r["browser_name"],
                "page_title": r["page_title"],
                "url": r["url"],
                "domain": r["domain"],
            },
            "source": r["source"],
        }
        if isinstance(raw_payload, dict) and isinstance(
            raw_payload.get("sedentary"), dict
        ):
            item["sedentary"] = raw_payload["sedentary"]
        return item

    def get_latest_activity_event(self, *, source: str = "") -> Optional[Dict[str, Any]]:
        source = str(source or "").strip()
        sql = "SELECT * FROM activity_latest"
        args: List[Any] = []
        if source:
            sql += " WHERE source=?"
            args.append(source)
        sql += " ORDER BY ts_iso DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, args).fetchone()
        return self._activity_row_to_dict(row) if row else None

    def list_activity_events(
        self, *, limit: int = 200, date_str: str = "", source: str = ""
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(5000, int(limit)))
        sql = "SELECT * FROM activity_events"
        args: List[Any] = []
        where_parts: List[str] = []
        if date_str:
            where_parts.append("substr(ts_iso,1,10)=?")
            args.append(str(date_str))
        source = str(source or "").strip()
        if source:
            where_parts.append("source=?")
            args.append(source)
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += " ORDER BY rowid DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._activity_row_to_dict(r) for r in rows]

    def list_transcript(
        self,
        *,
        role: Optional[str] = None,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        session_id: Optional[str] = None,
        session_scope: str = "all",
        context_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
        query = (query or "").strip()
        role = (role or "").strip() or None
        session_scope = str(session_scope or "all").strip().lower() or "all"
        session_id = (session_id or "").strip() or None
        context_session_id = (context_session_id or "").strip() or None

        def _append_session_filters(
            sql: str, args: List[Any], *, table_alias: str = ""
        ) -> tuple[str, List[Any]]:
            prefix = f"{table_alias}." if table_alias else ""
            if context_session_id:
                sql += f" AND json_extract({prefix}meta_json, '$.context_session_id')=?"
                args.append(context_session_id)
                return sql, args
            if session_scope == "specific":
                if not session_id:
                    return sql + " AND 1=0", args
                sql += f" AND {prefix}session_id=?"
                args.append(session_id)
            elif session_scope == "global":
                sql += f" AND ({prefix}session_id IS NULL OR {prefix}session_id='')"
            return sql, args

        with self._connect() as conn:
            if query:
                # FTS if available
                try:
                    sql = "SELECT t.* FROM transcript_fts f JOIN transcript t ON t.id=f.rowid WHERE transcript_fts MATCH ?"
                    args = [query]
                    if role:
                        sql += " AND t.role=?"
                        args.append(role)
                    sql, args = _append_session_filters(sql, args, table_alias="t")
                    sql += " ORDER BY t.id DESC LIMIT ? OFFSET ?"
                    args += [limit, offset]
                    rows = conn.execute(sql, args).fetchall()
                except Exception:
                    like = f"%{query}%"
                    sql = "SELECT * FROM transcript WHERE content LIKE ?"
                    args = [like]
                    if role:
                        sql += " AND role=?"
                        args.append(role)
                    sql, args = _append_session_filters(sql, args)
                    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
                    args += [limit, offset]
                    rows = conn.execute(sql, args).fetchall()
            else:
                sql = "SELECT * FROM transcript WHERE 1=1"
                args = []
                if role:
                    sql += " AND role=?"
                    args.append(role)
                sql, args = _append_session_filters(sql, args)
                sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
                args += [limit, offset]
                rows = conn.execute(sql, args).fetchall()

        out = []
        for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "ts": int(r["ts"]),
                    "ts_iso": r["ts_iso"],
                    "session_id": r["session_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "meta": _pj(r["meta_json"], {}),
                }
            )
        return out

    def list_transcript_sessions(self, *, limit: int = 80) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  COALESCE(NULLIF(session_id, ''), '(global)') AS session_key,
                  COUNT(1) AS message_count,
                  MAX(ts) AS last_ts,
                  MAX(ts_iso) AS last_ts_iso
                FROM transcript
                GROUP BY session_key
                ORDER BY last_ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            session_key = str(row["session_key"] or "").strip()
            out.append(
                {
                    "session_id": "" if session_key == "(global)" else session_key,
                    "label": session_key,
                    "message_count": int(row["message_count"] or 0),
                    "last_ts": int(row["last_ts"] or 0),
                    "last_ts_iso": row["last_ts_iso"] or "",
                }
            )
        return out

    def delete_transcript(self, transcript_id: int) -> bool:
        """删除指定的 transcript 记录"""
        with self._lock:
            before = None
            try:
                with self._connect() as conn:
                    r = conn.execute(
                        "SELECT * FROM transcript WHERE id=?", (transcript_id,)
                    ).fetchone()
                    if r:
                        before = {
                            "id": int(r["id"]),
                            "ts": int(r["ts"]),
                            "ts_iso": r["ts_iso"],
                            "role": r["role"],
                            "content": r["content"],
                            "meta": _pj(r["meta_json"], {}),
                        }
                        conn.execute(
                            "DELETE FROM transcript WHERE id=?", (transcript_id,)
                        )
                        conn.commit()
                        self._audit(
                            "delete",
                            "transcript",
                            str(transcript_id),
                            before,
                            None,
                            note="delete transcript",
                        )
                        return True
            except Exception as e:
                print(f"[MemorySQLite] 删除 transcript 失败: {e}")
                return False
        return False

    def clear_transcript(self) -> int:
        """删除全部 transcript 记录，返回删除数量。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    count_row = conn.execute(
                        "SELECT COUNT(1) AS c FROM transcript"
                    ).fetchone()
                    count = int(count_row["c"] if count_row else 0)
                    conn.execute("DELETE FROM transcript")
                    conn.commit()
                self._audit(
                    "clear",
                    "transcript",
                    None,
                    {"count": count},
                    None,
                    note="clear transcript",
                )
                return count
            except Exception as e:
                print(f"[MemorySQLite] 清空 transcript 失败: {e}")
                return -1

    # ---------- profile ----------
    def get_profile(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value_json FROM profile").fetchall()
        out = {"name": None, "likes": [], "dislikes": [], "notes": []}
        for r in rows:
            k = str(r["key"])
            out[k] = _pj(r["value_json"], None)
        # normalize
        out["likes"] = out.get("likes") if isinstance(out.get("likes"), list) else []
        out["dislikes"] = (
            out.get("dislikes") if isinstance(out.get("dislikes"), list) else []
        )
        out["notes"] = out.get("notes") if isinstance(out.get("notes"), list) else []
        return out

    def set_profile_value(self, key: str, value: Any) -> None:
        key = (key or "").strip()
        if not key:
            return
        with self._lock:
            before = None
            try:
                before = self.get_profile().get(key)
            except Exception:
                pass
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO profile(key, value_json, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, _j(value), _now_iso()),
                )
                conn.commit()
            self._audit(
                "set_profile", "profile", key, before, value, note="profile update"
            )

    # ---------- qq user profiles ----------
    def get_qq_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        user_key = str(user_id or "").strip()
        if not user_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM qq_user_profiles WHERE user_id=?", (user_key,)
            ).fetchone()
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "nickname": row["nickname"] or "",
            "remark_name": row["remark_name"] or "",
            "identity_summary": row["identity_summary"] or "",
            "relationship_to_owner": row["relationship_to_owner"] or "",
            "reply_style": row["reply_style"] or "",
            "preferences": _pj(row["preferences_json"], {}),
            "taboos": _pj(row["taboos_json"], []),
            "permission_level": row["permission_level"] or "",
            "memory_scope": row["memory_scope"] or "",
            "voice_reply_probability": int(row["voice_reply_probability"] or 0),
            "is_owner": bool(row["is_owner"]),
            "notes": row["notes"] or "",
            "updated_at": row["updated_at"],
        }

    def upsert_qq_user_profile(
        self, profile: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(profile, dict):
            return None
        user_key = str(profile.get("user_id") or "").strip()
        if not user_key:
            return None
        now = _now_iso()
        before = self.get_qq_user_profile(user_key)
        merged = dict(before or {})
        merged.update({"user_id": user_key})

        def _clean_text(value: Any) -> str:
            return str(value or "").strip()

        text_fields = (
            "nickname",
            "remark_name",
            "identity_summary",
            "relationship_to_owner",
            "reply_style",
            "permission_level",
            "memory_scope",
            "notes",
        )
        for field in text_fields:
            if field in profile and profile.get(field) is not None:
                value = _clean_text(profile.get(field))
                if value or field not in merged:
                    merged[field] = value

        if "preferences" in profile and isinstance(profile.get("preferences"), dict):
            merged["preferences"] = dict(profile.get("preferences") or {})
        else:
            merged.setdefault("preferences", {})

        if "taboos" in profile and isinstance(profile.get("taboos"), list):
            merged["taboos"] = list(profile.get("taboos") or [])
        else:
            merged.setdefault("taboos", [])

        if "voice_reply_probability" in profile:
            try:
                merged["voice_reply_probability"] = max(
                    0, min(100, int(profile.get("voice_reply_probability") or 0))
                )
            except Exception:
                merged["voice_reply_probability"] = int(
                    merged.get("voice_reply_probability") or 0
                )
        else:
            merged["voice_reply_probability"] = int(
                merged.get("voice_reply_probability") or 0
            )

        if "is_owner" in profile:
            merged["is_owner"] = bool(profile.get("is_owner"))
        else:
            merged["is_owner"] = bool(merged.get("is_owner"))

        merged["updated_at"] = now

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO qq_user_profiles(
                      user_id, nickname, remark_name, identity_summary, relationship_to_owner,
                      reply_style, preferences_json, taboos_json, permission_level, memory_scope,
                      voice_reply_probability, is_owner, notes, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET
                      nickname=excluded.nickname,
                      remark_name=excluded.remark_name,
                      identity_summary=excluded.identity_summary,
                      relationship_to_owner=excluded.relationship_to_owner,
                      reply_style=excluded.reply_style,
                      preferences_json=excluded.preferences_json,
                      taboos_json=excluded.taboos_json,
                      permission_level=excluded.permission_level,
                      memory_scope=excluded.memory_scope,
                      voice_reply_probability=excluded.voice_reply_probability,
                      is_owner=excluded.is_owner,
                      notes=excluded.notes,
                      updated_at=excluded.updated_at
                    """,
                    (
                        merged["user_id"],
                        _clean_text(merged.get("nickname")),
                        _clean_text(merged.get("remark_name")),
                        _clean_text(merged.get("identity_summary")),
                        _clean_text(merged.get("relationship_to_owner")),
                        _clean_text(merged.get("reply_style")),
                        _j(merged.get("preferences") or {}),
                        _j(merged.get("taboos") or []),
                        _clean_text(merged.get("permission_level")),
                        _clean_text(merged.get("memory_scope")),
                        int(merged.get("voice_reply_probability") or 0),
                        1 if bool(merged.get("is_owner")) else 0,
                        _clean_text(merged.get("notes")),
                        now,
                    ),
                )
                conn.commit()
        after = self.get_qq_user_profile(user_key)
        self._audit(
            "upsert",
            "qq_user_profiles",
            user_key,
            before,
            after,
            note="upsert qq user profile",
        )
        return after

    def list_qq_user_profiles(
        self, *, query: str = "", limit: int = 100, owner_only: bool = False
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        query = str(query or "").strip()
        with self._connect() as conn:
            sql = "SELECT user_id FROM qq_user_profiles WHERE 1=1"
            args: List[Any] = []
            if owner_only:
                sql += " AND is_owner=1"
            if query:
                like = f"%{query}%"
                sql += " AND (user_id LIKE ? OR nickname LIKE ? OR remark_name LIKE ? OR identity_summary LIKE ?)"
                args.extend([like, like, like, like])
            sql += " ORDER BY is_owner DESC, updated_at DESC LIMIT ?"
            args.append(limit)
            rows = conn.execute(sql, args).fetchall()
        result = []
        for row in rows:
            profile = self.get_qq_user_profile(row["user_id"])
            if profile:
                result.append(profile)
        return result

    def delete_qq_user_profile(self, user_id: str) -> bool:
        user_key = str(user_id or "").strip()
        if not user_key:
            return False
        with self._lock:
            before = self.get_qq_user_profile(user_key)
            if before is None:
                return False
            try:
                with self._connect() as conn:
                    conn.execute(
                        "DELETE FROM qq_user_profiles WHERE user_id=?", (user_key,)
                    )
                    conn.commit()
                self._audit(
                    "delete",
                    "qq_user_profiles",
                    user_key,
                    before,
                    None,
                    note="delete qq user profile",
                )
                return True
            except Exception as e:
                print(f"[MemorySQLite] 删除 qq_user_profiles 失败: {e}")
                return False

    # ---------- memory items ----------
    def upsert_item(self, item: Dict[str, Any]) -> str:
        # Normalize
        _id = str(item.get("id") or "").strip() or f"n_{uuid.uuid4().hex[:10]}"
        tp = str(item.get("type") or "other").strip()
        st = str(item.get("status") or "active").strip().lower()
        pin = 1 if bool(item.get("pin")) else 0
        conf = item.get("confidence", 1.0)
        try:
            conf = float(conf)
        except Exception:
            conf = 1.0
        conf = max(0.0, min(1.0, conf))
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        tags = [str(t).strip() for t in tags if str(t).strip()]
        text = str(item.get("text") or "").strip()
        if not text:
            raise ValueError("memory item text is empty")
        source = str(item.get("source") or "manual").strip() or "manual"
        now = _now_iso()

        with self._lock:
            before = self.get_item(_id)
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO memory_items(id,type,status,pin,confidence,tags_json,text,source,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "type=excluded.type,status=excluded.status,pin=excluded.pin,confidence=excluded.confidence,"
                    "tags_json=excluded.tags_json,text=excluded.text,source=excluded.source,updated_at=excluded.updated_at",
                    (
                        _id,
                        tp,
                        st,
                        pin,
                        conf,
                        _j(tags),
                        text,
                        source,
                        now if before is None else (before.get("created_at") or now),
                        now,
                    ),
                )
                conn.commit()
            self._audit(
                "upsert",
                "memory_items",
                _id,
                before,
                {
                    "id": _id,
                    "type": tp,
                    "status": st,
                    "pin": pin,
                    "confidence": conf,
                    "tags": tags,
                    "text": text,
                    "source": source,
                    "updated_at": now,
                },
                note="upsert memory item",
            )
        return _id

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        item_id = (item_id or "").strip()
        if not item_id:
            return None
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM memory_items WHERE id=?", (item_id,)
            ).fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "type": r["type"],
            "status": r["status"],
            "pin": int(r["pin"]),
            "confidence": float(r["confidence"]),
            "tags": _pj(r["tags_json"], []),
            "text": r["text"],
            "source": r["source"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }

    def delete_item(self, item_id: str) -> bool:
        item_id = (item_id or "").strip()
        if not item_id:
            return False
        before = self.get_item(item_id)
        if before is None:
            return False
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM memory_items WHERE id=?", (item_id,))
                    conn.commit()
            except Exception as e:
                print(f"[MemorySQLite] 删除 memory_items 失败: {e}")
                return False
        self._audit(
            "delete",
            "memory_items",
            item_id,
            before,
            None,
            note="delete memory item",
        )
        return True

    def list_items(
        self,
        *,
        status: str = "active",
        type_: str = "",
        query: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        status = (status or "").strip().lower()
        type_ = (type_ or "").strip()
        query = (query or "").strip()
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))

        where = []
        args: List[Any] = []
        if status:
            where.append("status=?")
            args.append(status)
        if type_:
            where.append("type=?")
            args.append(type_)

        with self._connect() as conn:
            if query:
                # FTS try
                try:
                    sql = "SELECT m.* FROM memory_items_fts f JOIN memory_items m ON m.rowid=f.rowid WHERE memory_items_fts MATCH ?"
                    args2 = [query] + args
                    if where:
                        sql += " AND " + " AND ".join(where)
                    sql += " ORDER BY m.pin DESC, m.updated_at DESC LIMIT ? OFFSET ?"
                    args2 += [limit, offset]
                    rows = conn.execute(sql, args2).fetchall()
                except Exception:
                    like = f"%{query}%"
                    sql = "SELECT * FROM memory_items"
                    if where:
                        sql += " WHERE " + " AND ".join(where) + " AND text LIKE ?"
                        args2 = args + [like]
                    else:
                        sql += " WHERE text LIKE ?"
                        args2 = [like]
                    sql += " ORDER BY pin DESC, updated_at DESC LIMIT ? OFFSET ?"
                    args2 += [limit, offset]
                    rows = conn.execute(sql, args2).fetchall()
            else:
                sql = "SELECT * FROM memory_items"
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY pin DESC, updated_at DESC LIMIT ? OFFSET ?"
                rows = conn.execute(sql, args + [limit, offset]).fetchall()

        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "type": r["type"],
                    "status": r["status"],
                    "pin": int(r["pin"]),
                    "confidence": float(r["confidence"]),
                    "tags": _pj(r["tags_json"], []),
                    "text": r["text"],
                    "source": r["source"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
        return out

    def set_item_status(self, item_id: str, status: str) -> None:
        item_id = (item_id or "").strip()
        status = (status or "active").strip().lower()
        if not item_id:
            return
        before = self.get_item(item_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE memory_items SET status=?, updated_at=? WHERE id=?",
                    (status, _now_iso(), item_id),
                )
                conn.commit()
        self._audit(
            "set_status",
            "memory_items",
            item_id,
            before,
            {"status": status},
            note="status change",
        )

    # ---------- expression patterns ----------
    def _normalize_expression_content_list(
        self, pattern: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        pattern = pattern if isinstance(pattern, dict) else {}
        raw_list = pattern.get("content_list")
        items: List[str] = []
        if isinstance(raw_list, list):
            items = [str(item).strip() for item in raw_list if str(item).strip()]
        elif isinstance(raw_list, str):
            raw = raw_list.strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                except Exception:
                    items = [raw]
                else:
                    if isinstance(parsed, list):
                        items = [
                            str(item).strip() for item in parsed if str(item).strip()
                        ]
        if not items:
            meta = pattern.get("meta") if isinstance(pattern.get("meta"), dict) else {}
            for key in ("content_list", "examples", "maibot_content_list"):
                value = meta.get(key)
                if isinstance(value, list):
                    items = [str(item).strip() for item in value if str(item).strip()]
                    if items:
                        break
        example = str(pattern.get("example") or "").strip()
        if example and example not in items:
            items.insert(0, example)
        deduped: List[str] = []
        for item in items:
            if item and item not in deduped:
                deduped.append(item)
        return deduped[:12]

    def _deserialize_expression_content_list(
        self, row: sqlite3.Row, meta: Dict[str, Any]
    ) -> List[str]:
        items: List[str] = []
        raw = None
        try:
            raw = row["content_list_json"]
        except Exception:
            raw = None
        if isinstance(raw, str) and raw.strip():
            items = _pj(raw, [])
            if not isinstance(items, list):
                items = []
        if not items:
            for key in ("content_list", "examples", "maibot_content_list"):
                value = meta.get(key)
                if isinstance(value, list):
                    items = [str(item).strip() for item in value if str(item).strip()]
                    if items:
                        break
        example = str(row["example"] or "").strip() if row["example"] else ""
        if example and example not in items:
            items.insert(0, example)
        deduped: List[str] = []
        for item in items:
            text = str(item).strip()
            if text and text not in deduped:
                deduped.append(text)
        return deduped[:12]

    def upsert_expression_pattern(self, pattern: Dict[str, Any]) -> str:
        _id = (
            str(pattern.get("id") or "").strip()
            or f"xp_{uuid.uuid4().hex[:10]}"
        )
        character_id = str(pattern.get("character_id") or "").strip()
        character_name = str(pattern.get("character_name") or "").strip()
        scene = str(pattern.get("scene") or "chat").strip().lower() or "chat"
        situation = str(pattern.get("situation") or "").strip()
        style = str(pattern.get("style") or "").strip()
        content_list = self._normalize_expression_content_list(pattern)
        example = content_list[0] if content_list else str(pattern.get("example") or "").strip()
        source = str(pattern.get("source") or "manual").strip() or "manual"
        if not style and not example:
            raise ValueError("expression pattern style/example cannot both be empty")
        try:
            quality_score = float(pattern.get("quality_score", 0))
        except Exception:
            quality_score = 0.0
        quality_score = max(0.0, min(10.0, quality_score))
        try:
            use_count = max(0, int(pattern.get("use_count", 0)))
        except Exception:
            use_count = 0
        enabled = 1 if bool(pattern.get("enabled", True)) else 0
        meta = pattern.get("meta") if isinstance(pattern.get("meta"), dict) else {}
        now = _now_iso()
        before = self.get_expression_pattern(_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO expression_patterns("
                    "id, character_id, character_name, scene, situation, style, example, content_list_json, source, "
                    "quality_score, use_count, enabled, meta_json, created_at, updated_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "character_id=excluded.character_id, character_name=excluded.character_name, scene=excluded.scene, "
                    "situation=excluded.situation, style=excluded.style, "
                    "example=excluded.example, content_list_json=excluded.content_list_json, source=excluded.source, "
                    "quality_score=excluded.quality_score, use_count=excluded.use_count, "
                    "enabled=excluded.enabled, meta_json=excluded.meta_json, "
                    "updated_at=excluded.updated_at",
                    (
                        _id,
                        character_id,
                        character_name,
                        scene,
                        situation,
                        style,
                        example,
                        _j(content_list),
                        source,
                        quality_score,
                        use_count,
                        enabled,
                        _j(meta),
                        now if before is None else (before.get("created_at") or now),
                        now,
                    ),
                )
                conn.commit()
        self._audit(
            "upsert",
            "expression_patterns",
            _id,
            before,
            {
                "id": _id,
                "character_id": character_id,
                "character_name": character_name,
                "scene": scene,
                "situation": situation,
                "style": style,
                "example": example,
                "content_list": content_list,
                "source": source,
                "quality_score": quality_score,
                "use_count": use_count,
                "enabled": enabled,
                "meta": meta,
            },
            note="upsert expression pattern",
        )
        return _id

    def get_expression_pattern(self, pattern_id: str) -> Optional[Dict[str, Any]]:
        pattern_id = str(pattern_id or "").strip()
        if not pattern_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM expression_patterns WHERE id=?",
                (pattern_id,),
            ).fetchone()
        if not row:
            return None
        meta = _pj(row["meta_json"], {})
        content_list = self._deserialize_expression_content_list(row, meta)
        return {
            "id": row["id"],
            "character_id": row["character_id"] or "",
            "character_name": row["character_name"] or "",
            "scene": row["scene"] or "chat",
            "situation": row["situation"] or "",
            "style": row["style"] or "",
            "example": content_list[0] if content_list else (row["example"] or ""),
            "content_list": content_list,
            "source": row["source"] or "manual",
            "quality_score": float(row["quality_score"] or 0),
            "use_count": int(row["use_count"] or 0),
            "enabled": bool(row["enabled"]),
            "meta": meta,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_expression_patterns(
        self,
        *,
        character_id: str = "",
        character_name: str = "",
        scene: str = "",
        person_id: str = "",
        enabled_only: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        character_id = str(character_id or "").strip()
        character_name = str(character_name or "").strip()
        scene = str(scene or "").strip().lower()
        person_id = str(person_id or "").strip()
        query = str(query or "").strip()
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
        where: List[str] = []
        args: List[Any] = []
        if character_id:
            if character_name:
                where.append(
                    "(character_id=? OR (character_id='' AND (character_name=? OR character_name='')))"
                )
                args.extend([character_id, character_name])
            else:
                where.append("(character_id=? OR (character_id='' AND character_name=''))")
                args.append(character_id)
        elif character_name:
            where.append("character_name=?")
            args.append(character_name)
        if scene:
            where.append("scene=?")
            args.append(scene)
        if person_id:
            where.append("(person_id=? OR person_id='')")
            args.append(person_id)
        if enabled_only:
            where.append("enabled=1")
        if query:
            like = f"%{query}%"
            where.append(
                "(character_name LIKE ? OR situation LIKE ? OR style LIKE ? OR example LIKE ? OR content_list_json LIKE ? OR source LIKE ?)"
            )
            args.extend([like, like, like, like, like, like])
        sql = "SELECT * FROM expression_patterns"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY enabled DESC, quality_score DESC, use_count DESC, updated_at DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            meta = _pj(row["meta_json"], {})
            content_list = self._deserialize_expression_content_list(row, meta)
            out.append(
                {
                    "id": row["id"],
                    "character_id": row["character_id"] or "",
                    "character_name": row["character_name"] or "",
                    "scene": row["scene"] or "chat",
                    "situation": row["situation"] or "",
                    "style": row["style"] or "",
                    "example": content_list[0] if content_list else (row["example"] or ""),
                    "content_list": content_list,
                    "source": row["source"] or "manual",
                    "quality_score": float(row["quality_score"] or 0),
                    "use_count": int(row["use_count"] or 0),
                    "enabled": bool(row["enabled"]),
                    "person_id": row["person_id"] if "person_id" in row.keys() else "",
                    "session_id": row["session_id"] if "session_id" in row.keys() else "",
                    "meta": meta,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def delete_expression_pattern(self, pattern_id: str) -> bool:
        pattern_id = str(pattern_id or "").strip()
        if not pattern_id:
            return False
        before = self.get_expression_pattern(pattern_id)
        if before is None:
            return False
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "DELETE FROM expression_patterns WHERE id=?",
                        (pattern_id,),
                    )
                    conn.commit()
            except Exception as e:
                print(f"[MemorySQLite] 删除 expression_patterns 失败: {e}")
                return False
        self._audit(
            "delete",
            "expression_patterns",
            pattern_id,
            before,
            None,
            note="delete expression pattern",
        )
        return True

    def import_expression_patterns(
        self, rows: List[Dict[str, Any]], *, replace: bool = False
    ) -> Dict[str, int]:
        stats = {"inserted": 0, "skipped": 0}
        if not isinstance(rows, list):
            return stats
        for row in rows:
            if not isinstance(row, dict):
                stats["skipped"] += 1
                continue
            payload = dict(row)
            row_id = str(payload.get("id") or "").strip()
            if row_id and self.get_expression_pattern(row_id) and not replace:
                stats["skipped"] += 1
                continue
            try:
                self.upsert_expression_pattern(payload)
                stats["inserted"] += 1
            except Exception:
                stats["skipped"] += 1
        return stats

    def select_expression_patterns_for_prompt(
        self,
        *,
        character_name: str = "",
        scene: str = "chat",
        limit: int = 4,
    ) -> List[Dict[str, Any]]:
        character_name = str(character_name or "").strip()
        scene = str(scene or "chat").strip().lower() or "chat"
        limit = max(1, min(12, int(limit)))
        args: List[Any] = [scene, scene, limit]
        sql = (
            "SELECT * FROM expression_patterns "
            "WHERE enabled=1 "
            "AND (scene=? OR scene='' OR scene='any' OR scene='*') "
        )
        if character_name:
            sql += "AND (character_name=? OR character_name='') "
            args = [scene, scene, character_name, limit]
            order_sql = (
                "ORDER BY "
                "CASE WHEN character_name=? THEN 0 WHEN character_name='' THEN 1 ELSE 2 END, "
                "CASE WHEN scene=? THEN 0 WHEN scene IN ('', 'any', '*') THEN 1 ELSE 2 END, "
                "quality_score DESC, use_count DESC, updated_at DESC LIMIT ?"
            )
            args = [scene, character_name, character_name, scene, limit]
            sql = (
                "SELECT * FROM expression_patterns "
                "WHERE enabled=1 "
                "AND (scene=? OR scene='' OR scene='any' OR scene='*') "
                "AND (character_name=? OR character_name='') "
                + order_sql
            )
        else:
            sql += (
                "ORDER BY CASE WHEN character_name='' THEN 0 ELSE 1 END, "
                "CASE WHEN scene=? THEN 0 WHEN scene IN ('', 'any', '*') THEN 1 ELSE 2 END, "
                "quality_score DESC, use_count DESC, updated_at DESC LIMIT ?"
            )
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            meta = _pj(row["meta_json"], {})
            content_list = self._deserialize_expression_content_list(row, meta)
            out.append(
                {
                    "id": row["id"],
                    "character_name": row["character_name"] or "",
                    "scene": row["scene"] or "chat",
                    "situation": row["situation"] or "",
                    "style": row["style"] or "",
                    "example": content_list[0] if content_list else (row["example"] or ""),
                    "content_list": content_list,
                    "source": row["source"] or "manual",
                    "quality_score": float(row["quality_score"] or 0),
                    "use_count": int(row["use_count"] or 0),
                    "enabled": bool(row["enabled"]),
                    "meta": meta,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def bump_expression_pattern_use(self, pattern_ids: List[str]) -> None:
        ids = [str(item).strip() for item in (pattern_ids or []) if str(item).strip()]
        if not ids:
            return
        now = _now_iso()
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    "UPDATE expression_patterns SET use_count=use_count+1, updated_at=? WHERE id=?",
                    [(now, item_id) for item_id in ids],
                )
                conn.commit()

    # ---------- episodes ----------
    def upsert_episode(self, ep: Dict[str, Any]) -> str:
        _id = str(ep.get("id") or "").strip() or f"e_{uuid.uuid4().hex[:10]}"
        st = str(ep.get("status") or "active").strip().lower()
        title = (ep.get("title") or "").strip()
        summary = (ep.get("summary") or "").strip()
        tags = ep.get("tags") if isinstance(ep.get("tags"), list) else []
        tags = [str(t).strip() for t in tags if str(t).strip()]
        said = (
            ep.get("assistant_said")
            if isinstance(ep.get("assistant_said"), list)
            else []
        )
        # normalize assistant_said items to dicts
        norm_said = []
        for s in said:
            if isinstance(s, dict):
                t = (s.get("text") or "").strip()
                if t:
                    norm_said.append({"type": s.get("type") or "commitment", "text": t})
            elif isinstance(s, str) and s.strip():
                norm_said.append({"type": "commitment", "text": s.strip()})
        now = _now_iso()
        before = self.get_episode(_id)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO episodes(id,status,title,summary,tags_json,assistant_said_json,range_start,range_end,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET status=excluded.status,title=excluded.title,summary=excluded.summary,"
                    "tags_json=excluded.tags_json,assistant_said_json=excluded.assistant_said_json,range_start=excluded.range_start,range_end=excluded.range_end,updated_at=excluded.updated_at",
                    (
                        _id,
                        st,
                        title,
                        summary,
                        _j(tags),
                        _j(norm_said),
                        ep.get("range_start"),
                        ep.get("range_end"),
                        now if before is None else (before.get("created_at") or now),
                        now,
                    ),
                )
                conn.commit()
        self._audit(
            "upsert",
            "episodes",
            _id,
            before,
            {
                "id": _id,
                "status": st,
                "title": title,
                "summary": summary,
                "tags": tags,
                "assistant_said": norm_said,
            },
            note="upsert episode",
        )
        return _id

    def get_episode(self, ep_id: str) -> Optional[Dict[str, Any]]:
        ep_id = (ep_id or "").strip()
        if not ep_id:
            return None
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM episodes WHERE id=?", (ep_id,)).fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "status": r["status"],
            "title": r["title"] or "",
            "summary": r["summary"] or "",
            "tags": _pj(r["tags_json"], []),
            "assistant_said": _pj(r["assistant_said_json"], []),
            "range_start": r["range_start"],
            "range_end": r["range_end"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }

    def delete_episode(self, ep_id: str) -> bool:
        ep_id = (ep_id or "").strip()
        if not ep_id:
            return False
        before = self.get_episode(ep_id)
        if before is None:
            return False
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM episodes WHERE id=?", (ep_id,))
                    conn.commit()
            except Exception as e:
                print(f"[MemorySQLite] 删除 episodes 失败: {e}")
                return False
        self._audit(
            "delete",
            "episodes",
            ep_id,
            before,
            None,
            note="delete episode",
        )
        return True

    def list_episodes(
        self,
        *,
        status: str = "active",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        status = (status or "").strip().lower()
        query = (query or "").strip()
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        with self._connect() as conn:
            if query:
                try:
                    sql = "SELECT e.* FROM episodes_fts f JOIN episodes e ON e.rowid=f.rowid WHERE episodes_fts MATCH ?"
                    args = [query]
                    if status:
                        sql += " AND e.status=?"
                        args.append(status)
                    sql += " ORDER BY e.updated_at DESC LIMIT ? OFFSET ?"
                    args += [limit, offset]
                    rows = conn.execute(sql, args).fetchall()
                except Exception:
                    like = f"%{query}%"
                    sql = (
                        "SELECT * FROM episodes WHERE (title LIKE ? OR summary LIKE ?)"
                    )
                    args = [like, like]
                    if status:
                        sql += " AND status=?"
                        args.append(status)
                    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
                    args += [limit, offset]
                    rows = conn.execute(sql, args).fetchall()
            else:
                sql = "SELECT * FROM episodes"
                args = []
                if status:
                    sql += " WHERE status=?"
                    args.append(status)
                sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
                args += [limit, offset]
                rows = conn.execute(sql, args).fetchall()

        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "status": r["status"],
                    "title": r["title"] or "",
                    "summary": r["summary"] or "",
                    "tags": _pj(r["tags_json"], []),
                    "assistant_said": _pj(r["assistant_said_json"], []),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
        return out

    # ============================================================
    # 🟢 [新增] 屏幕统计数据持久化接口
    # ============================================================

    def save_daily_screen_stats(self, date_str: str, stats_data: dict):
        """
        保存或更新某天的屏幕统计数据。
        :param date_str: 日期字符串 "YYYY-MM-DD"
        :param stats_data: 包含 summary_text, details, cache 等数据的字典
        """
        import json
        from datetime import datetime

        # 尝试提取总时长 (如果 sensor 传了的话)
        total_hours = stats_data.get("total_hours", 0.0)

        # 将字典转为 JSON 字符串存储
        json_str = json.dumps(stats_data, ensure_ascii=False)
        updated_at = datetime.now().isoformat()

        with self._connect() as conn:
            # 使用 UPSERT 语法：如果日期已存在则更新，不存在则插入
            conn.execute(
                """
                INSERT INTO daily_screen_stats (date, summary_json, total_hours, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    total_hours = excluded.total_hours,
                    updated_at = excluded.updated_at
            """,
                (date_str, json_str, total_hours, updated_at),
            )
            conn.commit()

    def get_daily_screen_stats(self, date_str: str) -> dict:
        """
        获取某天的屏幕统计数据 (返回字典)
        """
        import json

        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary_json FROM daily_screen_stats WHERE date = ?",
                (date_str,),
            ).fetchone()

            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception as e:
                    print(f"❌ 解析屏幕统计数据失败: {e}")
                    return {}
        return {}

    def format_screen_stats_for_prompt(self, date_str: str) -> str:
        """
        [Helper] 直接返回适合放入 Prompt 的文本报告
        供 chat_service.py 补写日记时调用
        """
        stats = self.get_daily_screen_stats(date_str)
        if not stats:
            return ""

        # 优先返回预格式化好的文本报告
        if "summary_text" in stats and stats["summary_text"]:
            return stats["summary_text"]

        # 如果没有文本报告，尝试把原始数据转成字符串
        import json

        return json.dumps(stats, ensure_ascii=False, indent=2)


# -------- module-level singleton --------
_STORE: Optional[MemorySQLite] = None
_STORE_LOCK = threading.Lock()


def get_memory_store() -> MemorySQLite:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemorySQLite()
        return _STORE


# 方案 A：作为 module-level helper (推荐，因为上面代码里已经有 store 参数传入了)
def format_profile_for_prompt(store: MemorySQLite) -> str:
    p = store.get_profile()
    name = (p.get("name") or "").strip() if isinstance(p.get("name"), str) else ""
    likes = p.get("likes") if isinstance(p.get("likes"), list) else []
    dislikes = p.get("dislikes") if isinstance(p.get("dislikes"), list) else []
    notes = p.get("notes") if isinstance(p.get("notes"), list) else []

    lines = []
    if name:
        lines.append(f"· 称呼/名字：{name}")
    if likes:
        lines.append("· 喜欢：" + "；".join([str(x) for x in likes[:10]]))
    if dislikes:
        lines.append("· 不喜欢/禁忌：" + "；".join([str(x) for x in dislikes[:10]]))
    if notes:
        for n in notes[:12]:
            n = str(n).strip()
            if n:
                lines.append("· " + n)
    return "\n".join(lines).strip()


def format_notes_for_prompt(store: MemorySQLite, max_items: int = 24) -> str:
    # 这里调用的是 store.list_items，不需要 list_only_notes
    items = store.list_items(status="active", limit=max(1, int(max_items)), offset=0)
    lines = []
    for it in items:
        tp = it.get("type", "other")
        text = (it.get("text") or "").strip()
        # 过滤掉档案类型的 item，避免重复
        if tp in ["agent_profile", "user_profile"]:
            continue
        if text:
            lines.append(f"- [{tp}] {text}")
    return "\n".join(lines).strip()


def format_active_tasks_for_prompt(store: MemorySQLite, limit: int = 6) -> str:
    items = store.list_items(
        status="active", type_="todo", limit=max(1, int(limit)), offset=0
    )
    lines = []
    cutoff = datetime.now() - timedelta(days=3)
    for it in items:
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        updated_at = str(it.get("updated_at") or "").strip()
        try:
            if updated_at:
                dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                if dt < cutoff:
                    continue
        except Exception:
            pass
        short_time = updated_at[5:16].replace("T", " ") if len(updated_at) >= 16 else ""
        if short_time:
            lines.append(f"- {text}（更新:{short_time}）")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines).strip()


def format_recent_episodes_for_prompt(store: MemorySQLite, limit: int = 3) -> str:
    eps = store.list_episodes(status="active", limit=max(1, int(limit)), offset=0)
    lines = []
    cutoff = datetime.now() - timedelta(days=2)
    for ep in eps:
        title = (ep.get("title") or "").strip() or "对话总结"
        summ = (ep.get("summary") or "").strip()
        updated_at = str(ep.get("updated_at") or ep.get("created_at") or "").strip()
        try:
            if updated_at:
                dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone().replace(tzinfo=None)
                if dt < cutoff:
                    continue
        except Exception:
            pass
        if summ:
            lines.append(f"- {title}: {summ}")
    return "\n".join(lines).strip()


from datetime import datetime, timedelta
