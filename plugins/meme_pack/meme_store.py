from __future__ import annotations

import hashlib
import json
import mimetypes
import random
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
RECALL_ALIAS_GROUPS = {
    "疑问": ("疑问", "疑惑", "困惑", "不解", "问号", "懵", "迷惑", "奇怪", "什么情况", "怎么回事"),
    "调侃": ("调侃", "逗", "狡猾", "坏笑", "偷笑", "嘴硬", "吐槽", "可爱", "笨"),
    "亲近": ("亲近", "喜欢", "贴贴", "抱抱", "陪我", "撒娇", "老婆", "亲昵"),
    "尴尬": ("尴尬", "无语", "沉默", "绷", "草", "冷场", "僵住", "汗"),
    "安慰": ("安慰", "摸摸", "难受", "委屈", "别怕", "没事", "陪你", "抱"),
    "日常": ("日常", "普通", "随手", "轻松", "平静"),
}


def _now() -> float:
    return time.time()


def _split_tags(text: str) -> list[str]:
    parts = re.split(r"[,，、;；\s]+", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _expand_recall_terms(terms: Iterable[str]) -> list[str]:
    out: list[str] = []

    def add(term: str) -> None:
        clean = str(term or "").strip().lower()
        if clean and clean not in out:
            out.append(clean)

    raw_terms = [str(term or "").strip() for term in terms if str(term or "").strip()]
    for term in raw_terms:
        add(term)
    for term in raw_terms:
        lower = term.lower()
        for key, aliases in RECALL_ALIAS_GROUPS.items():
            if key in lower or any(alias.lower() in lower for alias in aliases):
                for alias in aliases:
                    add(alias)
    return out


def _chinese_ngrams(text: str, *, limit: int = 120) -> list[str]:
    compact = re.sub(r"[^\u4e00-\u9fff]+", "", str(text or ""))
    if len(compact) < 2:
        return []
    grams: list[str] = []
    for size in (2, 3):
        for idx in range(0, max(0, len(compact) - size + 1)):
            gram = compact[idx : idx + size]
            if gram not in grams:
                grams.append(gram)
                if len(grams) >= limit:
                    return grams
    return grams


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            return _split_tags(value)
    return []


@dataclass(slots=True)
class MemeAsset:
    id: int
    file_hash: str
    file_path: str
    file_name: str
    mime_type: str
    description: str
    tags: list[str]
    emotion: str
    enabled: bool
    banned: bool
    usage_count: int
    success_count: int
    reject_count: int
    last_used_at: float
    created_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MemeAsset":
        return cls(
            id=int(row["id"]),
            file_hash=str(row["file_hash"] or ""),
            file_path=str(row["file_path"] or ""),
            file_name=str(row["file_name"] or ""),
            mime_type=str(row["mime_type"] or ""),
            description=str(row["description"] or ""),
            tags=_json_list(row["tags_json"]),
            emotion=str(row["emotion"] or ""),
            enabled=bool(row["enabled"]),
            banned=bool(row["banned"]),
            usage_count=int(row["usage_count"] or 0),
            success_count=int(row["success_count"] or 0),
            reject_count=int(row["reject_count"] or 0),
            last_used_at=float(row["last_used_at"] or 0),
            created_at=float(row["created_at"] or 0),
        )


class MemeStore:
    def __init__(self, database_path: str | Path, assets_dir: str | Path):
        self.database_path = Path(database_path)
        self.assets_dir = Path(assets_dir)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path))
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meme_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    tags_json TEXT DEFAULT '[]',
                    emotion TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    banned INTEGER DEFAULT 0,
                    vlm_processed INTEGER DEFAULT 0,
                    usage_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    reject_count INTEGER DEFAULT 0,
                    last_used_at REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meme_enabled ON meme_assets(enabled, banned)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meme_emotion ON meme_assets(emotion)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meme_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meme_id INTEGER,
                    session_id TEXT DEFAULT '',
                    trigger_text TEXT DEFAULT '',
                    reply_text TEXT DEFAULT '',
                    selected_reason TEXT DEFAULT '',
                    event_type TEXT DEFAULT 'sent',
                    created_at REAL NOT NULL
                )
                """
            )

    def _hash_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _guess_tags(self, path: Path, extra_tags: Iterable[str] | None = None) -> list[str]:
        tags: list[str] = []
        tags.extend(_split_tags(path.stem.replace("_", " ").replace("-", " ")))
        tags.extend(str(x).strip() for x in (extra_tags or []) if str(x).strip())
        out: list[str] = []
        for tag in tags:
            if tag not in out:
                out.append(tag)
        return out[:12]

    def import_file(
        self,
        source_path: str | Path,
        *,
        tags: Iterable[str] | None = None,
        emotion: str = "",
        description: str = "",
        source: str = "manual",
    ) -> tuple[bool, str]:
        src = Path(source_path).expanduser().resolve()
        if not src.exists() or not src.is_file():
            return False, f"文件不存在: {src}"
        if src.suffix.lower() not in IMAGE_EXTS:
            return False, f"不支持的图片格式: {src.name}"

        file_hash = self._hash_file(src)
        with self._session() as conn:
            row = conn.execute(
                "SELECT id, file_path FROM meme_assets WHERE file_hash=? LIMIT 1",
                (file_hash,),
            ).fetchone()
            if row:
                return False, f"已存在: {src.name}"

        ext = src.suffix.lower() or ".png"
        dest = self.assets_dir / f"{file_hash}{ext}"
        if not dest.exists():
            shutil.copy2(str(src), str(dest))

        guessed_tags = self._guess_tags(src, tags)
        if emotion and emotion not in guessed_tags:
            guessed_tags.insert(0, emotion)
        mime_type = mimetypes.guess_type(str(dest))[0] or "image/*"
        now = _now()
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO meme_assets(
                    file_hash, file_path, file_name, mime_type, description,
                    tags_json, emotion, source, enabled, banned, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    file_hash,
                    str(dest),
                    src.name,
                    mime_type,
                    description.strip(),
                    json.dumps(guessed_tags, ensure_ascii=False),
                    emotion.strip(),
                    source,
                    now,
                    now,
                ),
            )
        return True, src.name

    def import_directory(self, directory: str | Path, *, tags: Iterable[str] | None = None) -> dict[str, int]:
        root = Path(directory).expanduser().resolve()
        stats = {"imported": 0, "skipped": 0, "failed": 0}
        if not root.exists() or not root.is_dir():
            stats["failed"] += 1
            return stats
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                continue
            ok, _message = self.import_file(path, tags=tags)
            if ok:
                stats["imported"] += 1
            else:
                stats["skipped"] += 1
        return stats

    def list_assets(self, *, enabled_only: bool = True, limit: int = 200) -> list[MemeAsset]:
        sql = "SELECT * FROM meme_assets"
        params: list[Any] = []
        if enabled_only:
            sql += " WHERE enabled=1 AND banned=0"
        sql += " ORDER BY last_used_at ASC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._session() as conn:
            return [MemeAsset.from_row(row) for row in conn.execute(sql, params).fetchall()]

    def search_assets(
        self,
        query: str = "",
        *,
        include_disabled: bool = True,
        limit: int = 500,
    ) -> list[MemeAsset]:
        clean = str(query or "").strip()
        sql = "SELECT * FROM meme_assets"
        clauses: list[str] = []
        params: list[Any] = []
        if not include_disabled:
            clauses.append("enabled=1 AND banned=0")
        if clean:
            like = f"%{clean}%"
            clauses.append(
                "(file_name LIKE ? OR description LIKE ? OR tags_json LIKE ? OR emotion LIKE ?)"
            )
            params.extend([like, like, like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._session() as conn:
            return [MemeAsset.from_row(row) for row in conn.execute(sql, params).fetchall()]

    def get_asset(self, asset_id: int) -> Optional[MemeAsset]:
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM meme_assets WHERE id=? LIMIT 1", (int(asset_id),)
            ).fetchone()
        return MemeAsset.from_row(row) if row else None

    def update_asset(
        self,
        asset_id: int,
        *,
        description: str,
        tags: Iterable[str],
        emotion: str,
        enabled: bool,
        banned: bool,
    ) -> bool:
        now = _now()
        tag_list = [str(tag).strip() for tag in tags if str(tag).strip()]
        with self._session() as conn:
            cur = conn.execute(
                """
                UPDATE meme_assets
                SET description=?, tags_json=?, emotion=?, enabled=?, banned=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(description or "").strip(),
                    json.dumps(list(dict.fromkeys(tag_list))[:24], ensure_ascii=False),
                    str(emotion or "").strip(),
                    1 if enabled else 0,
                    1 if banned else 0,
                    now,
                    int(asset_id),
                ),
            )
        return cur.rowcount > 0

    def set_enabled(self, asset_ids: Iterable[int], enabled: bool) -> int:
        ids = [int(x) for x in asset_ids]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._session() as conn:
            cur = conn.execute(
                f"UPDATE meme_assets SET enabled=?, updated_at=? WHERE id IN ({marks})",
                [1 if enabled else 0, _now(), *ids],
            )
        return int(cur.rowcount or 0)

    def delete_assets(self, asset_ids: Iterable[int], *, delete_files: bool = False) -> int:
        ids = [int(x) for x in asset_ids]
        if not ids:
            return 0
        existing = [self.get_asset(asset_id) for asset_id in ids]
        marks = ",".join("?" for _ in ids)
        with self._session() as conn:
            cur = conn.execute(f"DELETE FROM meme_assets WHERE id IN ({marks})", ids)
            conn.execute(f"DELETE FROM meme_events WHERE meme_id IN ({marks})", ids)
        if delete_files:
            for asset in existing:
                if not asset:
                    continue
                try:
                    path = Path(asset.file_path)
                    if path.exists() and path.is_file():
                        path.unlink()
                except Exception:
                    pass
        return int(cur.rowcount or 0)

    def stats(self) -> dict[str, int]:
        with self._session() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN enabled=1 AND banned=0 THEN 1 ELSE 0 END) AS enabled,
                    SUM(CASE WHEN banned=1 THEN 1 ELSE 0 END) AS banned,
                    SUM(usage_count) AS usage_count
                FROM meme_assets
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "enabled": int(row["enabled"] or 0),
            "banned": int(row["banned"] or 0),
            "usage_count": int(row["usage_count"] or 0),
        }

    def _score_asset(
        self,
        asset: MemeAsset,
        query_terms: list[str],
        emotion: str,
        query_text: str = "",
    ) -> float:
        tags = [t.lower() for t in asset.tags]
        desc = asset.description.lower()
        emo = asset.emotion.lower()
        file_name = asset.file_name.lower()
        metadata = f"{desc} {' '.join(tags)} {emo} {file_name}"
        score = 0.0
        if emotion:
            e = emotion.lower()
            if e == emo:
                score += 4.0
            if e in tags:
                score += 3.0
            if e and e in desc:
                score += 1.5
        for term in query_terms:
            t = term.lower()
            if not t:
                continue
            if t in tags:
                score += 2.5
            if t and t in desc:
                score += 1.2
            if t and t in file_name:
                score += 0.8
        for term in _expand_recall_terms([emotion, *query_terms]):
            if term in metadata:
                score += 0.9
        if query_text:
            meta_grams = set(_chinese_ngrams(metadata, limit=240))
            if meta_grams:
                overlap = sum(1 for gram in _chinese_ngrams(query_text) if gram in meta_grams)
                score += min(2.0, overlap * 0.08)
        score += random.random() * 0.2
        score -= min(asset.usage_count, 20) * 0.03
        if asset.last_used_at:
            age = max(0.0, _now() - asset.last_used_at)
            score += min(age / 86400.0, 2.0) * 0.1
        return score

    def pick(self, query: str = "", *, emotion: str = "", limit: int = 8) -> Optional[MemeAsset]:
        scored = self.rank_assets(query=query, emotion=emotion, limit=max(1, int(limit)))
        top = [asset for score, asset in scored[: max(1, int(limit))] if score > 0]
        if not top:
            top = [asset for _score, asset in scored[: max(1, int(limit))]]
        return random.choice(top) if top else None

    def rank_assets(
        self, query: str = "", *, emotion: str = "", limit: int = 12
    ) -> list[tuple[float, MemeAsset]]:
        assets = self.list_assets(enabled_only=True, limit=1000)
        if not assets:
            return []
        terms = _split_tags(query)
        scored = [
            (self._score_asset(asset, terms, emotion, query_text=query), asset)
            for asset in assets
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[: max(1, int(limit))]

    def mark_used(
        self,
        asset: MemeAsset,
        *,
        session_id: str = "",
        trigger_text: str = "",
        reply_text: str = "",
        reason: str = "",
        event_type: str = "sent",
    ) -> None:
        now = _now()
        with self._session() as conn:
            conn.execute(
                """
                UPDATE meme_assets
                SET usage_count=usage_count+1, success_count=success_count+1,
                    last_used_at=?, updated_at=?
                WHERE id=?
                """,
                (now, now, asset.id),
            )
            conn.execute(
                """
                INSERT INTO meme_events(
                    meme_id, session_id, trigger_text, reply_text,
                    selected_reason, event_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (asset.id, session_id, trigger_text[:500], reply_text[:500], reason[:240], event_type, now),
            )
