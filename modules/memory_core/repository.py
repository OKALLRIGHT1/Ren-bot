from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


CURRENT_SCHEMA_VERSION = 1
CHARACTER_SUBJECT_PREFIX = "character:"
CHARACTER_PROFILE_REPAIR_KEY = "character_profile_repair_v1"
PERSONA_CURRENT_REPAIR_KEY = "persona_current_repair_v1"
USER_TASK_QUESTION_ARCHIVE_KEY = "user_task_question_archive_v1"
PERSONA_KINDS = frozenset({"preference", "fact", "rule", "profile", "relation"})
PERSONA_SUPERSEDE_KINDS = ("preference", "fact", "rule", "profile", "relation")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_memory_time(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def normalize_memory_time(value: Any) -> Optional[str]:
    timestamp = parse_memory_time(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def is_current(record: Optional[dict[str, Any]], now: Any = None) -> bool:
    if not record:
        return False
    if str(record.get("status") or "") != "active":
        return False
    if isinstance(now, (int, float)):
        now_ts = float(now)
    else:
        now_ts = parse_memory_time(now)
    if now_ts is None:
        now_ts = time.time()
    valid_from = parse_memory_time(record.get("valid_from"))
    valid_until = parse_memory_time(record.get("valid_until"))
    if valid_from is not None and valid_from > now_ts:
        return False
    if valid_until is not None and valid_until <= now_ts:
        return False
    return True


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_json(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        parsed = json.loads(value)
    except Exception:
        return default
    return parsed


class MemoryCoreRepository:
    def __init__(self, store: Any) -> None:
        self.store = store

    def _connect(self) -> sqlite3.Connection:
        return self.store._connect()

    def initialize(
        self,
        *,
        character_catalog: Optional[dict[str, dict[str, Any]]] = None,
    ) -> dict[str, int]:
        self._backup_before_first_migration()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_core_meta ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS persons (
                    person_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    relationship TEXT NOT NULL DEFAULT '',
                    memory_scope TEXT NOT NULL DEFAULT 'private',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_person_platform_user "
                "ON persons(platform, user_id) WHERE user_id<>''"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL DEFAULT '',
                    subject_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    normalized_key TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    importance REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    valid_from TEXT,
                    valid_until TEXT,
                    manual_lock INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_confirmed_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_records_scope "
                "ON memory_records(status, subject_id, session_id, kind, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_records_key "
                "ON memory_records(status, subject_id, kind, key)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_vector_jobs (
                    record_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL DEFAULT 'upsert',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '',
                    dimension INTEGER,
                    content_hash TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_vector_jobs_status "
                "ON memory_vector_jobs(status, updated_at)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_records_source "
                "ON memory_records(source_type, source_id) "
                "WHERE source_type<>'' AND source_id<>''"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL DEFAULT 'transcript',
                    evidence_id TEXT NOT NULL DEFAULT '',
                    quote TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    observed_at TEXT,
                    UNIQUE(memory_id, evidence_type, evidence_id),
                    FOREIGN KEY(memory_id) REFERENCES memory_records(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_evidence_observed_at(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS person_profile_snapshots (
                    person_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    records_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 1,
                    manual_override INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reply_feedback (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    person_id TEXT NOT NULL DEFAULT '',
                    character_name TEXT NOT NULL DEFAULT '',
                    reply_text TEXT NOT NULL DEFAULT '',
                    followup_text TEXT NOT NULL DEFAULT '',
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    score INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reply_feedback_session "
                "ON reply_feedback(session_id, created_at DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_query_log (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    person_id TEXT NOT NULL DEFAULT '',
                    query TEXT NOT NULL,
                    impression TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL DEFAULT 'none',
                    candidate_ids_json TEXT NOT NULL DEFAULT '[]',
                    selected_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts "
                    "USING fts5(content, kind, key, content='memory_records', content_rowid='rowid')"
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_records_ai AFTER INSERT ON memory_records BEGIN
                      INSERT INTO memory_records_fts(rowid, content, kind, key)
                      VALUES (new.rowid, new.content, new.kind, new.key);
                    END
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_records_ad AFTER DELETE ON memory_records BEGIN
                      INSERT INTO memory_records_fts(memory_records_fts, rowid, content, kind, key)
                      VALUES('delete', old.rowid, old.content, old.kind, old.key);
                    END
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_records_au AFTER UPDATE ON memory_records BEGIN
                      INSERT INTO memory_records_fts(memory_records_fts, rowid, content, kind, key)
                      VALUES('delete', old.rowid, old.content, old.kind, old.key);
                      INSERT INTO memory_records_fts(rowid, content, kind, key)
                      VALUES (new.rowid, new.content, new.kind, new.key);
                    END
                    """
                )
            except sqlite3.OperationalError:
                pass
            self._ensure_expression_columns(conn)
            conn.execute(
                "INSERT INTO memory_core_meta(key,value,updated_at) VALUES('schema_version',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(CURRENT_SCHEMA_VERSION), _now_iso()),
            )
            conn.commit()

        migrated = self._migrate_legacy_rows() + self._migrate_legacy_files()
        repaired = self._repair_legacy_character_profiles(character_catalog or {})
        current_repaired = self._repair_persona_current_records()
        archived_tasks = self._archive_question_user_tasks()
        self._ensure_persona_unique_indexes()
        self._archive_empty_records()
        self.enqueue_missing_vector_jobs()
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "migrated": migrated,
            "repaired": repaired + current_repaired + archived_tasks,
        }

    def _archive_empty_records(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_records SET status='archived', updated_at=? "
                "WHERE status='active' AND lower(trim(content)) IN ('', '[]', '{}', 'null', 'none')",
                (_now_iso(),),
            )
            conn.commit()

    def _backup_before_first_migration(self) -> None:
        db_path = Path(str(getattr(self.store, "db_path", "") or ""))
        if not db_path.exists():
            return
        conn = self._connect()
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_core_meta'"
        ).fetchone()
        if has_meta:
            return
        backup_path = db_path.with_name(f"{db_path.stem}.pre-memory-core-v1.bak{db_path.suffix}")
        if backup_path.exists():
            return
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(backup_path)) as destination:
            conn.backup(destination)

    @staticmethod
    def _ensure_expression_columns(conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(expression_patterns)").fetchall()
        if not rows:
            return
        columns = {str(row[1]) for row in rows}
        additions = {
            "character_id": "character_id TEXT NOT NULL DEFAULT ''",
            "session_id": "session_id TEXT NOT NULL DEFAULT ''",
            "person_id": "person_id TEXT NOT NULL DEFAULT ''",
            "status": "status TEXT NOT NULL DEFAULT 'active'",
            "confidence": "confidence REAL NOT NULL DEFAULT 0.5",
            "positive_count": "positive_count INTEGER NOT NULL DEFAULT 0",
            "negative_count": "negative_count INTEGER NOT NULL DEFAULT 0",
            "last_selected_at": "last_selected_at TEXT",
            "evidence_json": "evidence_json TEXT NOT NULL DEFAULT '[]'",
        }
        for column, ddl in additions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE expression_patterns ADD COLUMN {ddl}")

    @staticmethod
    def _ensure_evidence_observed_at(conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(memory_evidence)").fetchall()
        columns = {str(row[1]) for row in rows}
        if "observed_at" not in columns:
            conn.execute("ALTER TABLE memory_evidence ADD COLUMN observed_at TEXT")

    def _meta_value(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute(
            "SELECT value FROM memory_core_meta WHERE key=?",
            (key,),
        ).fetchone()
        return str(row["value"] if row else "")

    def _set_meta_value(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO memory_core_meta(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, _now_iso()),
        )

    def _repair_persona_current_records(self) -> int:
        conn = self._connect()
        if self._meta_value(conn, PERSONA_CURRENT_REPAIR_KEY) == "done":
            return 0
        now = _now_iso()
        now_ts = time.time()
        rows = conn.execute(
            "SELECT * FROM memory_records WHERE status='active' AND key<>'' "
            "AND kind IN (" + ",".join("?" for _ in PERSONA_KINDS) + ")",
            tuple(PERSONA_KINDS),
        ).fetchall()
        groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        for row in rows:
            identity = (
                str(row["subject_id"] or ""),
                str(row["kind"] or ""),
                str(row["key"] or ""),
            )
            groups.setdefault(identity, []).append(row)
        locked_conflicts: list[str] = []
        for members in groups.values():
            locked = [row for row in members if bool(row["manual_lock"])]
            if len(locked) > 1:
                locked_conflicts.extend(str(row["id"]) for row in locked)
        if locked_conflicts:
            raise RuntimeError(
                "persona current repair stopped: multiple manual_lock actives "
                + ",".join(locked_conflicts)
            )
        repaired = 0
        for members in groups.values():
            keep = max(
                members,
                key=lambda row: (
                    1 if bool(row["manual_lock"]) else 0,
                    str(row["last_confirmed_at"] or ""),
                    str(row["updated_at"] or ""),
                ),
            )
            keep_id = str(keep["id"])
            if str(keep["session_id"] or ""):
                conn.execute(
                    "UPDATE memory_records SET session_id='', updated_at=? WHERE id=?",
                    (now, keep_id),
                )
                repaired += 1
            for row in members:
                if str(row["id"]) == keep_id:
                    continue
                conn.execute(
                    "UPDATE memory_records SET status='superseded', valid_until=?, updated_at=? WHERE id=?",
                    (now, now, str(row["id"])),
                )
                self._enqueue_vector_job_conn(conn, str(row["id"]), "delete", now)
                repaired += 1
            valid_until = parse_memory_time(keep["valid_until"])
            if valid_until is not None and valid_until <= now_ts:
                conn.execute(
                    "UPDATE memory_records SET status='superseded', updated_at=? WHERE id=?",
                    (now, keep_id),
                )
                self._enqueue_vector_job_conn(conn, keep_id, "delete", now)
                repaired += 1
        self._set_meta_value(conn, PERSONA_CURRENT_REPAIR_KEY, "done")
        conn.commit()
        return repaired

    @staticmethod
    def _looks_like_question_user_task(content: str) -> bool:
        raw = str(content or "").strip()
        if not raw:
            return False
        if "?" in raw or "？" in raw:
            return True
        if raw.rstrip().endswith(("吗", "么", "呢", "嘛")):
            return True
        return any(
            cue in raw
            for cue in ("还记得", "记得吗", "记得不", "你记得", "记得我", "记得上次", "记得之前")
        )

    def _archive_question_user_tasks(self) -> int:
        conn = self._connect()
        now = _now_iso()
        rows = conn.execute(
            "SELECT id, content FROM memory_records "
            "WHERE status='active' AND kind='other' AND key='user_task'"
        ).fetchall()
        archived = 0
        for row in rows:
            if not self._looks_like_question_user_task(str(row["content"] or "")):
                continue
            conn.execute(
                "UPDATE memory_records SET status='archived', updated_at=? WHERE id=?",
                (now, str(row["id"])),
            )
            self._enqueue_vector_job_conn(conn, str(row["id"]), "delete", now)
            archived += 1
        self._set_meta_value(conn, USER_TASK_QUESTION_ARCHIVE_KEY, "done")
        conn.commit()
        return archived

    def _persona_unique_kind_clause(self) -> str:
        kinds = ",".join(f"'{item}'" for item in sorted(PERSONA_KINDS))
        return f"kind IN ({kinds})"

    def _ensure_persona_unique_indexes(self) -> None:
        kind_clause = self._persona_unique_kind_clause()
        with self._connect() as conn:
            existing = {
                str(row[0]): str(row[1] or "")
                for row in conn.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='index' AND name IN ("
                    "'idx_memory_records_persona_active',"
                    "'idx_memory_records_session_active')"
                )
            }
            persona_sql = (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_records_persona_active "
                "ON memory_records(subject_id, kind, key) "
                f"WHERE status='active' AND key<>'' AND session_id='' AND {kind_clause}"
            )
            session_sql = (
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_records_session_active "
                "ON memory_records(subject_id, session_id, kind, key) "
                f"WHERE status='active' AND key<>'' AND session_id<>'' AND {kind_clause}"
            )
            if existing.get("idx_memory_records_persona_active") and kind_clause not in existing[
                "idx_memory_records_persona_active"
            ]:
                conn.execute("DROP INDEX IF EXISTS idx_memory_records_persona_active")
            if existing.get("idx_memory_records_session_active") and kind_clause not in existing[
                "idx_memory_records_session_active"
            ]:
                conn.execute("DROP INDEX IF EXISTS idx_memory_records_session_active")
            conn.execute(persona_sql)
            conn.execute(session_sql)
            conn.commit()

    def _migrate_legacy_rows(self) -> int:
        migrated = 0
        conn = self._connect()
        table_names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "memory_items" in table_names:
            rows = conn.execute("SELECT * FROM memory_items").fetchall()
            for row in rows:
                tags = _parse_json(row["tags_json"], [])
                kind = str(row["type"] or "other")
                character_id = self._legacy_character_id(tags)
                if character_id:
                    subject_id = self.character_subject_id(character_id)
                    key = self._legacy_character_key(
                        tags,
                        source_id=str(row["id"] or ""),
                        content=str(row["text"] or ""),
                    )
                else:
                    subject_id = (
                        "owner"
                        if kind == "user_profile" or "role:user" in tags
                        else ""
                    )
                    key = self._legacy_key(tags)
                migrated += int(
                    self.upsert_record(
                        kind=self._map_legacy_kind(kind),
                        key=key,
                        subject_id=subject_id,
                        content=str(row["text"] or "").strip(),
                        source_type="legacy_memory_item",
                        source_id=str(row["id"] or ""),
                        confidence=float(row["confidence"] or 0.5),
                        importance=1.0 if int(row["pin"] or 0) else 0.6,
                        status=str(row["status"] or "active"),
                        manual_lock=str(row["source"] or "") == "manual" and bool(row["pin"]),
                        metadata={"legacy_tags": tags, "legacy_source": row["source"]},
                    )[1]
                )
        if "episodes" in table_names:
            rows = conn.execute("SELECT * FROM episodes").fetchall()
            for row in rows:
                summary = str(row["summary"] or "").strip()
                if not summary:
                    continue
                migrated += int(
                    self.upsert_record(
                        kind="episode",
                        key=str(row["title"] or "").strip(),
                        content=summary,
                        source_type="legacy_episode",
                        source_id=str(row["id"] or ""),
                        confidence=0.8,
                        importance=0.6,
                        status=str(row["status"] or "active"),
                        metadata={
                            "title": row["title"],
                            "range_start": row["range_start"],
                            "range_end": row["range_end"],
                        },
                    )[1]
                )
        if "profile" in table_names:
            rows = conn.execute("SELECT * FROM profile").fetchall()
            for row in rows:
                value = _parse_json(row["value_json"], row["value_json"])
                if value in (None, "", [], {}):
                    continue
                content = value if isinstance(value, str) else _json(value)
                migrated += int(
                    self.upsert_record(
                        kind="profile",
                        key=str(row["key"] or ""),
                        subject_id="owner",
                        content=str(content).strip(),
                        source_type="legacy_profile",
                        source_id=str(row["key"] or ""),
                        confidence=0.9,
                        importance=0.8,
                    )[1]
                )
        if "qq_user_profiles" in table_names:
            rows = conn.execute("SELECT * FROM qq_user_profiles").fetchall()
            for row in rows:
                person_id = "owner" if bool(row["is_owner"]) else f"qq:{row['user_id']}"
                self.upsert_person(
                    person_id=person_id,
                    platform="qq",
                    user_id=str(row["user_id"] or ""),
                    display_name=str(row["remark_name"] or row["nickname"] or ""),
                    relationship=str(row["relationship_to_owner"] or ""),
                    memory_scope=str(row["memory_scope"] or "private"),
                )
                fields = {
                    "identity_summary": row["identity_summary"],
                    "reply_style": row["reply_style"],
                    "notes": row["notes"],
                }
                for key, value in fields.items():
                    text = str(value or "").strip()
                    if not text:
                        continue
                    migrated += int(
                        self.upsert_record(
                            kind="profile" if key != "reply_style" else "preference",
                            key=key,
                            subject_id=person_id,
                            content=text,
                            source_type="legacy_qq_profile",
                            source_id=f"{row['user_id']}:{key}",
                            confidence=0.85,
                            importance=0.7,
                        )[1]
                    )
        return migrated

    def _migrate_legacy_files(self) -> int:
        db_path = Path(str(getattr(self.store, "db_path", "") or "")).resolve()
        memory_dir = db_path.parent
        project_root = memory_dir.parent
        migrated = 0
        profile_paths = [
            project_root / "profile.json",
            project_root / "memory_db" / "profile.json",
            memory_dir / "profile.json",
        ]
        seen_paths: set[Path] = set()
        for path in profile_paths:
            if path in seen_paths or not path.exists():
                continue
            seen_paths.add(path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            user = payload.get("user") if isinstance(payload, dict) else None
            if not isinstance(user, dict):
                user = payload if isinstance(payload, dict) else {}
            fields = {
                "name": user.get("name"),
                "dislikes": user.get("dislikes"),
                "status": user.get("status"),
                "notes": user.get("notes"),
            }
            likes = user.get("likes")
            if isinstance(likes, dict):
                for category, values in likes.items():
                    fields[f"likes.{category}"] = values
            elif isinstance(likes, list):
                fields["likes.general"] = likes
            for key, value in fields.items():
                values = value if isinstance(value, list) else [value]
                for index, item in enumerate(values):
                    text = str(item or "").strip()
                    if not text:
                        continue
                    _record_id, created = self.upsert_record(
                        kind="preference" if key.startswith(("likes", "dislikes")) else "profile",
                        key=key if len(values) == 1 else f"{key}.{index}",
                        subject_id="owner",
                        content=text,
                        source_type="legacy_profile_json",
                        source_id=f"{path.name}:{key}:{index}",
                        confidence=0.85,
                        importance=0.7,
                    )
                    migrated += int(created)

        learning_paths = [memory_dir / "learning.db", project_root / "memory_db" / "learning.db"]
        learning_path = next((path for path in learning_paths if path.exists()), None)
        if learning_path is not None:
            try:
                legacy = sqlite3.connect(str(learning_path))
                legacy.row_factory = sqlite3.Row
                rows = legacy.execute("SELECT preferences FROM user_preferences LIMIT 1").fetchall()
                for row in rows:
                    preferences = _parse_json(row["preferences"], {})
                    if not isinstance(preferences, dict):
                        continue
                    for key, value in preferences.items():
                        if value in (None, "", [], {}):
                            continue
                        content = value if isinstance(value, str) else _json(value)
                        _record_id, created = self.upsert_record(
                            kind="preference",
                            key=f"reply.{key}",
                            subject_id="owner",
                            content=str(content),
                            source_type="legacy_learning_preferences",
                            source_id=str(key),
                            confidence=0.65,
                            importance=0.55,
                        )
                        migrated += int(created)
                feedback_rows = legacy.execute(
                    "SELECT id,timestamp,user_input,response,reaction,feedback_type FROM interaction_feedback"
                ).fetchall()
                for row in feedback_rows:
                    reaction = str(row["reaction"] or "neutral").strip().lower()
                    score = 1 if reaction == "positive" else (-1 if reaction == "negative" else 0)
                    labels = [reaction] if reaction != "neutral" else [str(row["feedback_type"] or "neutral")]
                    self.record_feedback(
                        {
                            "id": f"legacy_learning_{row['id']}",
                            "session_id": "legacy:global",
                            "person_id": "owner",
                            "reply_text": row["response"],
                            "followup_text": row["user_input"],
                            "labels": labels,
                            "score": score,
                            "source": "legacy_learning_db",
                            "created_at": row["timestamp"],
                        }
                    )
                legacy.close()
            except Exception:
                pass

        feedback_path = project_root / "data" / "reply_effect" / "reply_effects.jsonl"
        if feedback_path.exists():
            try:
                lines = feedback_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                lines = []
            for index, line in enumerate(lines):
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                reply = item.get("reply") if isinstance(item.get("reply"), dict) else {}
                self.record_feedback(
                    {
                        "id": f"legacy_reply_effect_{index}",
                        "session_id": str(item.get("session_id") or "legacy:global"),
                        "person_id": "owner",
                        "reply_text": reply.get("text"),
                        "followup_text": item.get("followup_text"),
                        "labels": item.get("labels") or [],
                        "score": item.get("score") or 0,
                        "source": item.get("source") or "legacy_reply_effect",
                        "created_at": datetime.fromtimestamp(
                            float(item.get("ts") or time.time()), timezone.utc
                        ).isoformat(),
                    }
                )
        return migrated

    @staticmethod
    def _map_legacy_kind(kind: str) -> str:
        mapping = {
            "user_profile": "profile",
            "agent_profile": "profile",
            "assistant_said": "episode",
            "fact": "fact",
            "preference": "preference",
            "rule": "rule",
        }
        return mapping.get(kind, "other")

    @staticmethod
    def _legacy_key(tags: Iterable[Any]) -> str:
        ignored = {"role:user", "role:agent", "likes", "user_profile", "agent_profile"}
        return next(
            (
                str(tag)
                for tag in tags
                if str(tag) not in ignored and not str(tag).startswith("role:")
            ),
            "",
        )

    @staticmethod
    def character_subject_id(character_id: str) -> str:
        character_id = str(character_id or "").strip()
        return f"{CHARACTER_SUBJECT_PREFIX}{character_id}" if character_id else ""

    @staticmethod
    def character_id_from_subject(subject_id: str) -> str:
        subject_id = str(subject_id or "").strip()
        if not subject_id.startswith(CHARACTER_SUBJECT_PREFIX):
            return ""
        return subject_id[len(CHARACTER_SUBJECT_PREFIX) :].strip()

    @staticmethod
    def _legacy_character_id(tags: Iterable[Any]) -> str:
        for tag in tags:
            value = str(tag or "").strip()
            if value.startswith("role:") and value not in {"role:user", "role:agent"}:
                return value.split(":", 1)[1].strip()
        return ""

    @classmethod
    def _legacy_character_key(
        cls,
        tags: Iterable[Any],
        *,
        source_id: str,
        content: str,
    ) -> str:
        values = [str(tag or "").strip().lower() for tag in tags]
        semantic = [value for value in values if value and not value.startswith("role:")]
        if "name" in semantic:
            return "name"
        if "traits" in semantic:
            prefix = "identity.traits"
        elif "dislikes" in semantic:
            prefix = "dislikes.general"
        elif "likes" in semantic:
            category = next(
                (
                    value
                    for value in semantic
                    if value not in {"likes", "agent_profile", "user_profile"}
                ),
                "general",
            )
            category = re.sub(r"[^a-z0-9_-]+", "", category) or "general"
            prefix = f"likes.{category}"
        else:
            prefix = "identity.profile"
        digest_source = str(source_id or "").strip() or str(content or "").strip()
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:10]
        return f"{prefix}.{digest}"

    def _repair_legacy_character_profiles(
        self,
        character_catalog: dict[str, dict[str, Any]],
    ) -> int:
        conn = self._connect()
        marker = conn.execute(
            "SELECT value FROM memory_core_meta WHERE key=?",
            (CHARACTER_PROFILE_REPAIR_KEY,),
        ).fetchone()
        if marker:
            return 0
        rows = conn.execute(
            "SELECT * FROM memory_records WHERE source_type='legacy_memory_item'"
        ).fetchall()
        role_rows: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            metadata = _parse_json(row["metadata_json"], {})
            tags = metadata.get("legacy_tags") if isinstance(metadata, dict) else []
            tags = tags if isinstance(tags, list) else []
            role_id = self._legacy_character_id(tags)
            if not role_id:
                continue
            item = {"row": row, "metadata": metadata, "tags": tags, "role_id": role_id}
            role_rows.append(item)
            grouped.setdefault(role_id, []).append(item)
        if role_rows:
            self._backup_before_character_profile_repair()

        canonical_ids: dict[str, str] = {}
        normalized_catalog = {
            str(character_id): dict(payload or {})
            for character_id, payload in character_catalog.items()
            if str(character_id).strip()
        }
        for role_id, items in grouped.items():
            if role_id in normalized_catalog:
                canonical_ids[role_id] = role_id
                continue
            names = {
                str(item["row"]["content"] or "").strip()
                for item in items
                if "name" in {str(tag).strip().lower() for tag in item["tags"]}
            }
            matches = []
            for character_id, payload in normalized_catalog.items():
                labels = {str(payload.get("name") or "").strip()}
                aliases = payload.get("aliases") if isinstance(payload.get("aliases"), list) else []
                labels.update(str(alias or "").strip() for alias in aliases)
                if names & {label for label in labels if label}:
                    matches.append(character_id)
            canonical_ids[role_id] = matches[0] if len(matches) == 1 else role_id

        prepared: list[dict[str, Any]] = []
        for item in role_rows:
            row = item["row"]
            canonical_id = canonical_ids[item["role_id"]]
            key = self._legacy_character_key(
                item["tags"],
                source_id=str(row["source_id"] or ""),
                content=str(row["content"] or ""),
            )
            semantic_prefix = key.rsplit(".", 1)[0] if key != "name" else "name"
            normalized_content = re.sub(r"\s+", "", str(row["content"] or "")).lower()
            prepared.append(
                {
                    **item,
                    "canonical_id": canonical_id,
                    "subject_id": self.character_subject_id(canonical_id),
                    "key": key,
                    "dedupe_key": (canonical_id, semantic_prefix, normalized_content),
                }
            )

        duplicate_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for item in prepared:
            duplicate_groups.setdefault(item["dedupe_key"], []).append(item)

        repaired = 0
        now = _now_iso()
        with self._connect() as update_conn:
            for items in duplicate_groups.values():
                items.sort(
                    key=lambda item: (
                        item["role_id"] == item["canonical_id"],
                        bool(item["row"]["manual_lock"]),
                        float(item["row"]["confidence"] or 0),
                        str(item["row"]["updated_at"] or ""),
                    ),
                    reverse=True,
                )
                winner = items[0]
                winner_id = str(winner["row"]["id"])
                for index, item in enumerate(items):
                    row = item["row"]
                    metadata = dict(item["metadata"] or {})
                    metadata.update(
                        {
                            "character_id": item["canonical_id"],
                            "legacy_role_id": item["role_id"],
                            "character_profile_repaired": True,
                        }
                    )
                    source_id = str(row["source_id"] or "")
                    content = str(row["content"] or "").strip()
                    if content == "温柔 / 冷静 (初始性格)":
                        status = "archived"
                        metadata["archive_reason"] = "synthetic_character_placeholder"
                    elif index > 0:
                        status = "superseded"
                        metadata["deduplicated_into"] = winner_id
                    else:
                        status = str(row["status"] or "active")
                    changed = (
                        str(row["subject_id"] or "") != item["subject_id"]
                        or str(row["key"] or "") != item["key"]
                        or str(row["status"] or "") != status
                        or _json(metadata) != str(row["metadata_json"] or "{}")
                    )
                    if not changed:
                        continue
                    update_conn.execute(
                        "UPDATE memory_records SET subject_id=?, key=?, status=?, metadata_json=?, updated_at=? WHERE id=?",
                        (
                            item["subject_id"],
                            item["key"],
                            status,
                            _json(metadata),
                            now,
                            row["id"],
                        ),
                    )
                    repaired += 1
            update_conn.execute(
                "INSERT INTO memory_core_meta(key,value,updated_at) VALUES(?,?,?)",
                (CHARACTER_PROFILE_REPAIR_KEY, "done", now),
            )
            update_conn.commit()
        return repaired

    def _backup_before_character_profile_repair(self) -> None:
        db_path = Path(str(getattr(self.store, "db_path", "") or ""))
        if not db_path.exists():
            return
        backup_path = db_path.with_name(
            f"{db_path.stem}.pre-character-profile-repair-v1.bak{db_path.suffix}"
        )
        if backup_path.exists():
            return
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(backup_path)) as destination:
            self._connect().backup(destination)

    def upsert_person(
        self,
        *,
        person_id: str,
        platform: str = "",
        user_id: str = "",
        display_name: str = "",
        aliases: Optional[list[str]] = None,
        relationship: str = "",
        memory_scope: str = "private",
    ) -> None:
        person_id = str(person_id or "").strip()
        if not person_id:
            return
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO persons(person_id,platform,user_id,display_name,aliases_json,relationship,memory_scope,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(person_id) DO UPDATE SET
                  platform=CASE WHEN excluded.platform<>'' THEN excluded.platform ELSE persons.platform END,
                  user_id=CASE WHEN excluded.user_id<>'' THEN excluded.user_id ELSE persons.user_id END,
                  display_name=CASE WHEN excluded.display_name<>'' THEN excluded.display_name ELSE persons.display_name END,
                  aliases_json=CASE WHEN excluded.aliases_json<>'[]' THEN excluded.aliases_json ELSE persons.aliases_json END,
                  relationship=CASE WHEN excluded.relationship<>'' THEN excluded.relationship ELSE persons.relationship END,
                  memory_scope=CASE WHEN excluded.memory_scope<>'' THEN excluded.memory_scope ELSE persons.memory_scope END,
                  updated_at=excluded.updated_at
                """,
                (
                    person_id,
                    str(platform or ""),
                    str(user_id or ""),
                    str(display_name or ""),
                    _json(aliases or []),
                    str(relationship or ""),
                    str(memory_scope or "private"),
                    now,
                    now,
                ),
            )
            conn.commit()

    def list_persons(self) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM persons ORDER BY CASE WHEN person_id='owner' THEN 0 ELSE 1 END, display_name, person_id"
        ).fetchall()
        return [
            {
                "person_id": row["person_id"],
                "platform": row["platform"],
                "user_id": row["user_id"],
                "display_name": row["display_name"],
                "aliases": _parse_json(row["aliases_json"], []),
                "relationship": row["relationship"],
                "memory_scope": row["memory_scope"],
            }
            for row in rows
        ]

    @staticmethod
    def _normalize_key(kind: str, key: str, content: str) -> str:
        source = f"{kind}|{key}|{content}".strip().lower()
        source = re.sub(r"\s+", "", source)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]

    def _is_persona_kind(self, kind: str) -> bool:
        return str(kind or "").strip().lower() in PERSONA_KINDS

    def _normalize_valid_from(self, value: Any, *, now_ts: float) -> Optional[str]:
        normalized = normalize_memory_time(value)
        if not normalized:
            return None
        parsed = parse_memory_time(normalized)
        if parsed is not None and parsed > now_ts:
            return None
        return normalized

    def upsert_record(
        self,
        *,
        kind: str,
        content: str,
        key: str = "",
        subject_id: str = "",
        session_id: str = "",
        source_type: str = "",
        source_id: str = "",
        confidence: float = 0.5,
        importance: float = 0.5,
        status: str = "active",
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
        manual_lock: bool = False,
        metadata: Optional[dict[str, Any]] = None,
        evidence: Optional[list[dict[str, Any]]] = None,
        supersede_keys: Optional[Iterable[str]] = None,
        observed_at: Optional[str] = None,
    ) -> tuple[str, bool]:
        return self.replace_current_record(
            kind=kind,
            content=content,
            key=key,
            subject_id=subject_id,
            session_id=session_id,
            source_type=source_type,
            source_id=source_id,
            confidence=confidence,
            importance=importance,
            status=status,
            valid_from=valid_from,
            valid_until=valid_until,
            manual_lock=manual_lock,
            metadata=metadata,
            evidence=evidence,
            supersede_keys=supersede_keys,
            observed_at=observed_at,
        )

    def replace_current_record(
        self,
        *,
        kind: str,
        content: str,
        key: str = "",
        subject_id: str = "",
        session_id: str = "",
        source_type: str = "",
        source_id: str = "",
        confidence: float = 0.5,
        importance: float = 0.5,
        status: str = "active",
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
        manual_lock: bool = False,
        metadata: Optional[dict[str, Any]] = None,
        evidence: Optional[list[dict[str, Any]]] = None,
        supersede_keys: Optional[Iterable[str]] = None,
        observed_at: Optional[str] = None,
    ) -> tuple[str, bool]:
        kind = str(kind or "other").strip().lower() or "other"
        content = str(content or "").strip()
        if not content:
            raise ValueError("memory record content is empty")
        key = str(key or "").strip()
        subject_id = str(subject_id or "").strip()
        if self._is_persona_kind(kind):
            session_id = ""
        else:
            session_id = str(session_id or "").strip()
        source_type = str(source_type or "").strip()
        source_id = str(source_id or "").strip()
        confidence = max(0.0, min(1.0, float(confidence)))
        importance = max(0.0, min(1.0, float(importance)))
        status = str(status or "active").strip() or "active"
        now = _now_iso()
        now_ts = time.time()
        raw_valid_from = parse_memory_time(valid_from)
        if raw_valid_from is not None and raw_valid_from > now_ts:
            existing = self._find_active_identity(
                subject_id=subject_id,
                kind=kind,
                key=key,
                session_id=session_id,
            )
            return (str(existing["id"]), False) if existing is not None else ("", False)
        valid_from = self._normalize_valid_from(valid_from, now_ts=now_ts)
        valid_until = normalize_memory_time(valid_until)
        observed_at = normalize_memory_time(observed_at)
        extra_keys = [
            str(item or "").strip()
            for item in (supersede_keys or [])
            if str(item or "").strip() and str(item or "").strip() != key
        ]
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            if source_type and source_id:
                existing_source = conn.execute(
                    "SELECT * FROM memory_records WHERE source_type=? AND source_id=?",
                    (source_type, source_id),
                ).fetchone()
                if existing_source is not None:
                    same_payload = (
                        str(existing_source["kind"] or "") == kind
                        and str(existing_source["content"] or "").strip() == content
                    )
                    if same_payload:
                        conn.commit()
                        return str(existing_source["id"]), False
                    source_id = f"{source_id}:{uuid.uuid4().hex[:8]}"

            if key and subject_id and status == "active":
                locked = self._find_locked_active(
                    conn,
                    subject_id=subject_id,
                    kind=kind,
                    key=key,
                    session_id=session_id,
                )
                if locked is not None and not manual_lock:
                    conn.commit()
                    return str(locked["id"]), False
                kept = self._supersede_identity_rows(
                    conn,
                    subject_id=subject_id,
                    kind=kind,
                    key=key,
                    session_id=session_id,
                    now=now,
                    now_ts=now_ts,
                    keep_content=content,
                    observed_at=observed_at,
                )
                for old_key in extra_keys:
                    self._supersede_identity_rows(
                        conn,
                        subject_id=subject_id,
                        kind=kind,
                        key=old_key,
                        session_id=session_id,
                        now=now,
                        now_ts=now_ts,
                        keep_content=None,
                        observed_at=observed_at,
                        kinds=PERSONA_SUPERSEDE_KINDS if self._is_persona_kind(kind) else (kind,),
                    )
                if kept is not None:
                    self._insert_evidence(conn, str(kept["id"]), evidence or [], now, observed_at)
                    conn.execute(
                        "UPDATE memory_records SET last_confirmed_at=?, updated_at=? WHERE id=?",
                        (now, now, str(kept["id"])),
                    )
                    conn.commit()
                    return str(kept["id"]), False

            record_id = f"mr_{uuid.uuid4().hex[:16]}"
            conn.execute(
                """
                INSERT INTO memory_records(
                  id,kind,key,subject_id,session_id,content,normalized_key,source_type,source_id,
                  confidence,importance,status,valid_from,valid_until,manual_lock,metadata_json,
                  created_at,updated_at,last_confirmed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id,
                    kind,
                    key,
                    subject_id,
                    session_id,
                    content,
                    self._normalize_key(kind, key, content),
                    source_type,
                    source_id,
                    confidence,
                    importance,
                    status,
                    valid_from,
                    valid_until,
                    1 if manual_lock else 0,
                    _json(metadata or {}),
                    now,
                    now,
                    now,
                ),
            )
            self._insert_evidence(conn, record_id, evidence or [], now, observed_at)
            operation = "upsert" if status == "active" else "delete"
            self._enqueue_vector_job_conn(conn, record_id, operation, now)
            conn.commit()
            return record_id, True
        except Exception:
            conn.rollback()
            raise

    def _find_active_identity(
        self,
        *,
        subject_id: str,
        kind: str,
        key: str,
        session_id: str,
    ) -> Optional[sqlite3.Row]:
        if not key or not subject_id:
            return None
        conn = self._connect()
        if self._is_persona_kind(kind):
            return conn.execute(
                "SELECT * FROM memory_records WHERE status='active' AND subject_id=? AND kind=? AND key=? "
                "ORDER BY manual_lock DESC, last_confirmed_at DESC, updated_at DESC LIMIT 1",
                (subject_id, kind, key),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM memory_records WHERE status='active' AND subject_id=? AND kind=? AND key=? "
            "AND session_id=? ORDER BY manual_lock DESC, last_confirmed_at DESC, updated_at DESC LIMIT 1",
            (subject_id, kind, key, session_id),
        ).fetchone()

    def _find_locked_active(
        self,
        conn: sqlite3.Connection,
        *,
        subject_id: str,
        kind: str,
        key: str,
        session_id: str,
    ) -> Optional[sqlite3.Row]:
        if self._is_persona_kind(kind):
            return conn.execute(
                "SELECT * FROM memory_records WHERE status='active' AND subject_id=? AND kind=? AND key=? "
                "AND manual_lock=1 ORDER BY last_confirmed_at DESC, updated_at DESC LIMIT 1",
                (subject_id, kind, key),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM memory_records WHERE status='active' AND subject_id=? AND kind=? AND key=? "
            "AND session_id=? AND manual_lock=1 ORDER BY last_confirmed_at DESC, updated_at DESC LIMIT 1",
            (subject_id, kind, key, session_id),
        ).fetchone()

    def _supersede_identity_rows(
        self,
        conn: sqlite3.Connection,
        *,
        subject_id: str,
        kind: str,
        key: str,
        session_id: str,
        now: str,
        now_ts: float,
        keep_content: Optional[str],
        observed_at: Optional[str],
        kinds: Optional[Iterable[str]] = None,
    ) -> Optional[sqlite3.Row]:
        kind_list = [str(item).strip() for item in (kinds or (kind,)) if str(item).strip()]
        placeholders = ",".join("?" for _ in kind_list)
        args: list[Any] = [subject_id, key, *kind_list]
        sql = (
            f"SELECT * FROM memory_records WHERE status='active' AND subject_id=? AND key<>'' "
            f"AND key=? AND kind IN ({placeholders})"
        )
        if not self._is_persona_kind(kind):
            sql += " AND session_id=?"
            args.append(session_id)
        rows = conn.execute(sql, args).fetchall()
        until = observed_at or now
        kept: Optional[sqlite3.Row] = None
        for row in rows:
            if (
                keep_content is not None
                and kept is None
                and str(row["content"] or "").strip() == keep_content
            ):
                valid_until = parse_memory_time(row["valid_until"])
                if valid_until is None or valid_until > now_ts:
                    if self._is_persona_kind(kind) and str(row["session_id"] or ""):
                        conn.execute(
                            "UPDATE memory_records SET session_id='', updated_at=? WHERE id=?",
                            (now, str(row["id"])),
                        )
                    kept = row
                    continue
            conn.execute(
                "UPDATE memory_records SET status='superseded', valid_until=?, session_id=CASE "
                "WHEN ? THEN '' ELSE session_id END, updated_at=? WHERE id=?",
                (until, 1 if self._is_persona_kind(kind) else 0, now, str(row["id"])),
            )
            self._enqueue_vector_job_conn(conn, str(row["id"]), "delete", now)
        return kept

    def _insert_evidence(
        self,
        conn: sqlite3.Connection,
        record_id: str,
        evidence: list[dict[str, Any]],
        now: str,
        default_observed_at: Optional[str],
    ) -> None:
        for item in evidence:
            observed_at = normalize_memory_time(item.get("observed_at")) or default_observed_at
            conn.execute(
                "INSERT OR IGNORE INTO memory_evidence("
                "memory_id,evidence_type,evidence_id,quote,created_at,observed_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    record_id,
                    str(item.get("type") or "transcript"),
                    str(item.get("id") or ""),
                    str(item.get("quote") or "")[:500],
                    now,
                    observed_at,
                ),
            )

    def list_records(
        self,
        *,
        status: str = "active",
        subject_id: str = "",
        session_id: str = "",
        kinds: Optional[Iterable[str]] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if status:
            where.append("status=?")
            args.append(status)
        if subject_id:
            if subject_id == "owner":
                where.append("subject_id IN (?, '')")
                args.append(subject_id)
            else:
                where.append("subject_id=?")
                args.append(subject_id)
        if session_id:
            where.append("session_id IN (?, '')")
            args.append(session_id)
        kind_list = [str(item).strip() for item in (kinds or []) if str(item).strip()]
        if kind_list:
            where.append("kind IN (" + ",".join("?" for _ in kind_list) + ")")
            args.extend(kind_list)
        sql = "SELECT * FROM memory_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY manual_lock DESC, importance DESC, confidence DESC, updated_at DESC LIMIT ?"
        args.append(max(1, min(2000, int(limit))))
        rows = self._connect().execute(sql, args).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_current_records(
        self,
        *,
        subject_id: str = "",
        session_id: str = "",
        kinds: Optional[Iterable[str]] = None,
        limit: int = 200,
        now: Any = None,
    ) -> list[dict[str, Any]]:
        rows = self.list_records(
            status="active",
            subject_id=subject_id,
            session_id=session_id,
            kinds=kinds,
            limit=max(1, min(2000, int(limit))),
        )
        return [row for row in rows if is_current(row, now)]

    def get_record(self, record_id: str) -> Optional[dict[str, Any]]:
        row = self._connect().execute(
            "SELECT * FROM memory_records WHERE id=?",
            (str(record_id or "").strip(),),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def update_record(self, record_id: str, changes: dict[str, Any]) -> bool:
        record_id = str(record_id or "").strip()
        current = self.get_record(record_id)
        if not record_id or current is None:
            return False
        allowed = {
            "kind",
            "key",
            "subject_id",
            "session_id",
            "content",
            "confidence",
            "importance",
            "status",
            "valid_from",
            "valid_until",
            "manual_lock",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return False
        if "content" in values and not str(values["content"] or "").strip():
            raise ValueError("memory record content is empty")
        if "confidence" in values:
            values["confidence"] = max(0.0, min(1.0, float(values["confidence"])))
        if "importance" in values:
            values["importance"] = max(0.0, min(1.0, float(values["importance"])))
        if "manual_lock" in values:
            values["manual_lock"] = 1 if bool(values["manual_lock"]) else 0
        merged_kind = str(values.get("kind", current["kind"]) or "other")
        merged_key = str(values.get("key", current["key"]) or "")
        merged_content = str(values.get("content", current["content"]) or "")
        values["normalized_key"] = self._normalize_key(merged_kind, merged_key, merged_content)
        values["updated_at"] = _now_iso()
        assignments = ",".join(f"{key}=?" for key in values)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE memory_records SET {assignments} WHERE id=?",
                [*values.values(), record_id],
            )
            merged_status = str(values.get("status", current.get("status")) or "active")
            self._enqueue_vector_job_conn(
                conn,
                record_id,
                "upsert" if merged_status == "active" else "delete",
                str(values["updated_at"]),
            )
            conn.commit()
        return True

    def update_record_metadata(
        self,
        record_id: str,
        updates: dict[str, Any],
        *,
        remove_keys: Iterable[str] = (),
    ) -> bool:
        record_id = str(record_id or "").strip()
        current = self.get_record(record_id)
        if not record_id or current is None:
            return False
        metadata = dict(current.get("metadata") or {})
        metadata.update(dict(updates or {}))
        for key in remove_keys:
            metadata.pop(str(key), None)
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_records SET metadata_json=?, updated_at=? WHERE id=?",
                (_json(metadata), _now_iso(), record_id),
            )
            conn.commit()
        return True

    def delete_record(self, record_id: str) -> bool:
        record_id = str(record_id or "").strip()
        if not record_id:
            return False
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM memory_records WHERE id=?", (record_id,))
            if cursor.rowcount:
                self._enqueue_vector_job_conn(conn, record_id, "delete", _now_iso())
            conn.commit()
        return bool(cursor.rowcount)

    @staticmethod
    def _enqueue_vector_job_conn(
        conn: sqlite3.Connection,
        record_id: str,
        operation: str,
        now: Optional[str] = None,
    ) -> None:
        timestamp = now or _now_iso()
        conn.execute(
            "INSERT INTO memory_vector_jobs(record_id,operation,status,attempts,last_error,created_at,updated_at) "
            "VALUES(?,?,'pending',0,'',?,?) "
            "ON CONFLICT(record_id) DO UPDATE SET operation=excluded.operation, "
            "status='pending', attempts=0, last_error='', updated_at=excluded.updated_at",
            (str(record_id), str(operation), timestamp, timestamp),
        )

    def enqueue_missing_vector_jobs(self) -> int:
        now = _now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO memory_vector_jobs(record_id,operation,status,attempts,last_error,created_at,updated_at) "
                "SELECT id,'upsert','pending',0,'',?,? FROM memory_records "
                "WHERE status='active' AND id NOT IN (SELECT record_id FROM memory_vector_jobs)",
                (now, now),
            )
            conn.commit()
        return int(cursor.rowcount or 0)

    def list_vector_jobs(
        self,
        *,
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memory_vector_jobs"
        args: list[Any] = []
        if status:
            sql += " WHERE status=?"
            args.append(str(status))
        sql += " ORDER BY updated_at, record_id LIMIT ?"
        args.append(max(1, min(2000, int(limit))))
        rows = self._connect().execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def mark_vector_job_indexed(
        self,
        record_id: str,
        *,
        model: str,
        dimension: int,
        content_hash: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE memory_vector_jobs SET status='indexed', model=?, dimension=?, "
                "content_hash=?, last_error='', updated_at=? WHERE record_id=?",
                (
                    str(model or ""),
                    int(dimension),
                    str(content_hash or ""),
                    _now_iso(),
                    str(record_id or ""),
                ),
            )
            conn.commit()
        return bool(cursor.rowcount)

    def mark_vector_job_failed(self, record_id: str, error: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE memory_vector_jobs SET status='failed', attempts=attempts+1, "
                "last_error=?, updated_at=? WHERE record_id=?",
                (str(error or "")[:500], _now_iso(), str(record_id or "")),
            )
            conn.commit()
        return bool(cursor.rowcount)

    def vector_job_stats(self) -> dict[str, int]:
        stats = {"pending": 0, "processing": 0, "indexed": 0, "failed": 0}
        rows = self._connect().execute(
            "SELECT status, COUNT(*) AS count FROM memory_vector_jobs GROUP BY status"
        ).fetchall()
        for row in rows:
            status = str(row["status"] or "")
            if status in stats:
                stats[status] = int(row["count"] or 0)
        return stats

    def vector_index_compatibility(
        self,
        *,
        model: str,
        dimension: int | None,
    ) -> dict[str, int | bool]:
        clean_model = str(model or "").strip()
        try:
            clean_dimension = int(dimension or 0)
        except (TypeError, ValueError):
            clean_dimension = 0
        if not clean_model or clean_dimension <= 0:
            return {
                "rebuild_required": False,
                "incompatible_count": 0,
                "indexed_count": 0,
            }
        row = self._connect().execute(
            "SELECT COUNT(*) AS indexed_count, "
            "SUM(CASE WHEN model<>? OR dimension IS NULL OR dimension<>? "
            "THEN 1 ELSE 0 END) AS incompatible_count "
            "FROM memory_vector_jobs WHERE status='indexed'",
            (clean_model, clean_dimension),
        ).fetchone()
        indexed_count = int(row["indexed_count"] or 0)
        incompatible_count = int(row["incompatible_count"] or 0)
        return {
            "rebuild_required": incompatible_count > 0,
            "incompatible_count": incompatible_count,
            "indexed_count": indexed_count,
        }

    def rebuild_vector_jobs(self) -> int:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_vector_jobs(record_id,operation,status,attempts,last_error,created_at,updated_at) "
                "SELECT id,CASE WHEN status='active' THEN 'upsert' ELSE 'delete' END,"
                "'pending',0,'',?,? FROM memory_records WHERE 1=1 "
                "ON CONFLICT(record_id) DO UPDATE SET operation=excluded.operation, "
                "status='pending', attempts=0, last_error='', updated_at=excluded.updated_at",
                (now, now),
            )
            conn.execute(
                "UPDATE memory_vector_jobs SET operation='delete', status='pending', "
                "attempts=0, last_error='', updated_at=? "
                "WHERE record_id NOT IN (SELECT id FROM memory_records)",
                (now,),
            )
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM memory_vector_jobs WHERE status='pending'"
            ).fetchone()
            conn.commit()
        return int(count["count"] or 0)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "key": row["key"],
            "subject_id": row["subject_id"],
            "session_id": row["session_id"],
            "content": row["content"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "confidence": float(row["confidence"] or 0),
            "importance": float(row["importance"] or 0),
            "status": row["status"],
            "valid_from": row["valid_from"],
            "valid_until": row["valid_until"],
            "manual_lock": bool(row["manual_lock"]),
            "metadata": _parse_json(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_confirmed_at": row["last_confirmed_at"] if "last_confirmed_at" in row.keys() else None,
        }

    def list_transcript_candidates(
        self,
        *,
        session_id: str,
        query_terms: Iterable[str] = (),
        limit: int = 240,
    ) -> list[dict[str, Any]]:
        if not session_id:
            return []
        terms = list(
            dict.fromkeys(
                str(term or "").strip()
                for term in query_terms
                if len(str(term or "").strip()) >= 2
            )
        )[:80]
        sql = (
            "SELECT id,ts_iso,role,content,meta_json FROM transcript "
            "WHERE session_id=? AND role IN ('user','assistant','summary') "
        )
        args: list[Any] = [session_id]
        if terms:
            patterns = [f"%{term}%" for term in terms]
            sql += "AND (" + " OR ".join("content LIKE ?" for _ in terms) + ") "
            args.extend(patterns)
            match_score = "+".join(
                "CASE WHEN content LIKE ? THEN 1 ELSE 0 END" for _ in terms
            )
            sql += (
                f"ORDER BY ({match_score}) DESC, "
                "CASE WHEN role IN ('assistant','summary') THEN 1 ELSE 0 END DESC, "
                "id DESC LIMIT ?"
            )
            args.extend(patterns)
        else:
            sql += "ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(1000, int(limit))))
        conn = self._connect()
        rows = conn.execute(sql, args).fetchall()
        linked_question_ids: dict[int, int] = {}
        questions_with_linked_answers: set[int] = set()
        matched_user_ids = [
            int(row["id"])
            for row in rows
            if row["role"] == "user"
            and (
                not terms
                or sum(term.lower() in str(row["content"] or "").lower() for term in terms)
                >= 2
            )
        ]
        if matched_user_ids:
            placeholders = ",".join("?" for _ in matched_user_ids)
            linked_rows = conn.execute(
                "SELECT answer.id,answer.ts_iso,answer.role,answer.content,answer.meta_json,"
                "question.id AS matched_question_id FROM transcript question "
                "JOIN transcript answer ON answer.id=("
                "SELECT MIN(next.id) FROM transcript next "
                "WHERE next.session_id=question.session_id AND next.id>question.id "
                "AND next.role IN ('user','assistant','summary')) "
                f"WHERE question.id IN ({placeholders}) "
                "AND answer.role IN ('assistant','summary')",
                matched_user_ids,
            ).fetchall()
            existing_ids = {int(row["id"]) for row in rows}
            for row in linked_rows:
                row_id = int(row["id"])
                matched_question_id = int(row["matched_question_id"])
                linked_question_ids[row_id] = matched_question_id
                questions_with_linked_answers.add(matched_question_id)
                if row_id not in existing_ids:
                    rows.append(row)
                    existing_ids.add(row_id)
        result: list[dict[str, Any]] = []
        for row in rows:
            meta = _parse_json(row["meta_json"], {})
            if bool(meta.get("hidden")) or str(meta.get("path") or "").startswith("tool_"):
                continue
            row_id = int(row["id"])
            matched_question_id = linked_question_ids.get(row_id)
            if matched_question_id is not None:
                meta = {
                    **meta,
                    "query_context_match": True,
                    "matched_question_id": matched_question_id,
                }
            if row_id in questions_with_linked_answers:
                meta = {**meta, "has_linked_answer": True}
            result.append(
                {
                    "id": f"tr:{row_id}",
                    "kind": "transcript",
                    "key": "",
                    "subject_id": "",
                    "session_id": session_id,
                    "content": str(row["content"] or "").strip(),
                    "source_type": "transcript",
                    "source_id": str(row_id),
                    "confidence": 0.75 if row["role"] in {"assistant", "summary"} else 0.55,
                    "importance": 0.5,
                    "status": "active",
                    "manual_lock": False,
                    "metadata": {"role": row["role"], "ts": row["ts_iso"], **meta},
                    "created_at": row["ts_iso"],
                    "updated_at": row["ts_iso"],
                }
            )
        return result

    def save_query_log(
        self,
        *,
        session_id: str,
        person_id: str,
        query: str,
        impression: str,
        intent: str,
        candidate_ids: list[str],
        selected_ids: list[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_query_log(id,session_id,person_id,query,impression,intent,candidate_ids_json,selected_ids_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    f"mq_{uuid.uuid4().hex[:16]}",
                    session_id,
                    person_id,
                    query,
                    impression,
                    intent,
                    _json(candidate_ids),
                    _json(selected_ids),
                    _now_iso(),
                ),
            )
            conn.commit()

    def save_profile_snapshot(
        self,
        *,
        person_id: str,
        summary: str,
        record_ids: list[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO person_profile_snapshots(person_id,summary,records_json,version,manual_override,updated_at) "
                "VALUES(?,?,?,?,0,?) ON CONFLICT(person_id) DO UPDATE SET "
                "summary=excluded.summary, records_json=excluded.records_json, "
                "version=person_profile_snapshots.version+1, updated_at=excluded.updated_at "
                "WHERE person_profile_snapshots.manual_override=0 AND "
                "(person_profile_snapshots.summary<>excluded.summary OR "
                "person_profile_snapshots.records_json<>excluded.records_json)",
                (person_id, summary, _json(record_ids), 1, _now_iso()),
            )
            conn.commit()

    def update_expression_learning_fields(
        self,
        pattern_id: str,
        *,
        session_id: str,
        person_id: str,
        confidence: float,
        evidence: list[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE expression_patterns SET session_id=?, person_id=?, confidence=?, evidence_json=? WHERE id=?",
                (
                    session_id,
                    person_id,
                    max(0.0, min(1.0, float(confidence))),
                    _json(evidence),
                    pattern_id,
                ),
            )
            conn.commit()

    def mark_expressions_selected(self, pattern_ids: list[str]) -> None:
        ids = [str(item).strip() for item in pattern_ids if str(item).strip()]
        if not ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE expression_patterns SET last_selected_at=? WHERE id=?",
                [(_now_iso(), pattern_id) for pattern_id in ids],
            )
            conn.commit()

    def apply_expression_feedback(self, pattern_ids: list[str], score: int) -> None:
        ids = [str(item).strip() for item in pattern_ids if str(item).strip()]
        if not ids or score == 0:
            return
        positive_delta = 1 if score > 0 else 0
        negative_delta = 1 if score < 0 else 0
        quality_delta = 0.25 if score > 0 else -0.75
        with self._connect() as conn:
            conn.executemany(
                "UPDATE expression_patterns SET "
                "positive_count=positive_count+?, negative_count=negative_count+?, "
                "quality_score=MAX(0,MIN(10,quality_score+?)), "
                "enabled=CASE WHEN negative_count+?>=3 AND positive_count=0 THEN 0 ELSE enabled END, "
                "updated_at=? WHERE id=?",
                [
                    (
                        positive_delta,
                        negative_delta,
                        quality_delta,
                        negative_delta,
                        _now_iso(),
                        pattern_id,
                    )
                    for pattern_id in ids
                ],
            )
            conn.commit()

    def record_feedback(self, payload: dict[str, Any]) -> str:
        feedback_id = str(payload.get("id") or f"rf_{uuid.uuid4().hex[:16]}")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO reply_feedback(id,session_id,person_id,character_name,reply_text,followup_text,labels_json,score,source,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    feedback_id,
                    str(payload.get("session_id") or ""),
                    str(payload.get("person_id") or ""),
                    str(payload.get("character_name") or ""),
                    str(payload.get("reply_text") or "")[:1200],
                    str(payload.get("followup_text") or "")[:1200],
                    _json(payload.get("labels") or []),
                    int(payload.get("score") or 0),
                    str(payload.get("source") or ""),
                    str(payload.get("created_at") or _now_iso()),
                ),
            )
            conn.commit()
        return feedback_id

    def feedback_stats(self, *, session_id: str, limit: int = 80) -> dict[str, Any]:
        rows = self._connect().execute(
            "SELECT labels_json,score FROM reply_feedback WHERE session_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, max(1, min(500, int(limit)))),
        ).fetchall()
        labels: dict[str, int] = {}
        total = 0
        for row in rows:
            total += int(row["score"] or 0)
            for label in _parse_json(row["labels_json"], []):
                key = str(label or "").strip()
                if key:
                    labels[key] = labels.get(key, 0) + 1
        return {"count": len(rows), "score_sum": total, "labels": labels}

    def list_feedback(self, *, session_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM reply_feedback"
        args: list[Any] = []
        if session_id:
            sql += " WHERE session_id=?"
            args.append(session_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        rows = self._connect().execute(sql, args).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "person_id": row["person_id"],
                "character_name": row["character_name"],
                "reply_text": row["reply_text"],
                "followup_text": row["followup_text"],
                "labels": _parse_json(row["labels_json"], []),
                "score": int(row["score"] or 0),
                "source": row["source"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
