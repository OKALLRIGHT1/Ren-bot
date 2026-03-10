# modules/event_logger.py
# Lightweight SQLite event logger for debugging:
# - chat messages (user/assistant/system) with meta JSON
# - events (tool_router / llm / tool_exec / memory) with structured payload
#
# Safe for multi-thread usage via an internal lock. Uses WAL for better concurrency.

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, Optional


def _utc_ts() -> str:
    # ISO8601-ish without timezone (local tools can interpret)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class EventLogger:
    def __init__(self, db_path: str = "./data/events.sqlite", session_id: Optional[str] = None):
        self.db_path = db_path
        self.session_id = session_id or str(uuid.uuid4())
        self._lock = threading.Lock()

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ts_iso TEXT,
                        session_id TEXT,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        meta TEXT,
                        meta_json TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ts_iso TEXT,
                        session_id TEXT,
                        type TEXT,
                        name TEXT,
                        payload TEXT,
                        payload_json TEXT
                    );
                    """
                )
                self._ensure_column(conn, "chat_messages", "ts_iso", "TEXT")
                self._ensure_column(conn, "chat_messages", "session_id", "TEXT")
                self._ensure_column(conn, "chat_messages", "meta", "TEXT")
                self._ensure_column(conn, "chat_messages", "meta_json", "TEXT")

                self._ensure_column(conn, "events", "ts_iso", "TEXT")
                self._ensure_column(conn, "events", "session_id", "TEXT")
                self._ensure_column(conn, "events", "type", "TEXT")
                self._ensure_column(conn, "events", "name", "TEXT")
                self._ensure_column(conn, "events", "payload", "TEXT")
                self._ensure_column(conn, "events", "payload_json", "TEXT")
                conn.commit()
            finally:
                conn.close()

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {r[1] for r in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def log_chat(self, role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> None:
        role = (role or "system").strip()
        content = (content or "").strip()
        if not content:
            return
        payload = json.dumps(meta or {}, ensure_ascii=False)
        ts = int(time.time())
        ts_iso = _utc_ts()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO chat_messages(ts, ts_iso, session_id, role, content, meta, meta_json) VALUES(?,?,?,?,?,?,?)",
                    (ts, ts_iso, self.session_id, role, content, payload, payload),
                )
                conn.commit()
            finally:
                conn.close()

    def add_message(self, role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.log_chat(role, content, meta)

    def log_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        event_type = (event_type or "event").strip()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        ts = int(time.time())
        ts_iso = _utc_ts()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO events(ts, ts_iso, session_id, type, name, payload, payload_json) VALUES(?,?,?,?,?,?,?)",
                    (ts, ts_iso, self.session_id, event_type, event_type, payload_json, payload_json),
                )
                conn.commit()
            finally:
                conn.close()
