import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone

from .retrieval import KnowledgeHit, retrieve_knowledge_chunks

DEFAULT_KNOWLEDGE_MANIFEST_PATH = os.path.join("data", "knowledge_import_manifest.json")
KNOWLEDGE_CHUNKER_VERSION = "plain-v2"
_MANIFEST_LOCK = threading.Lock()
_SOURCE_LOCK_GUARD = threading.Lock()
_SOURCE_LOCKS: dict[str, threading.Lock] = {}

_PLAIN_MIN_CHARS = 400
_PLAIN_MAX_CHARS = 1200
_PLAIN_OVERLAP_CHARS = 100
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_SETEXT_UNDERLINE_RE = re.compile(r"^(?:=+|-+)\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+\S")
_TABLE_RE = re.compile(r"^\s*\|")
_TERM_DEF_RE = re.compile(r"^\s*[^:\n]{1,40}[：:]\s+\S")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")


def normalize_knowledge_source_path(file_path: str) -> str:
    raw = os.path.expanduser(str(file_path or "").strip())
    if not raw:
        return ""
    abs_path = os.path.abspath(raw)
    posix = os.path.normpath(abs_path).replace("\\", "/")
    if os.name == "nt":
        return posix.casefold()
    return posix


def _base_meta(file_path: str) -> dict:
    source_path = normalize_knowledge_source_path(file_path)
    source_dir = source_path.rsplit("/", 1)[0] if "/" in source_path else source_path
    return {
        "source": os.path.basename(str(file_path or "").replace("\\", "/")),
        "source_path": source_path,
        "source_dir": source_dir,
    }


def _is_pokemon_json(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    has_name = any(payload.get(key) for key in ("name_zh", "name_ja", "name_en"))
    has_struct = any(
        key in payload
        for key in (
            "pokemon_list",
            "learn_by_level_up",
            "learn_by_tm",
            "forms",
            "machine_moves",
            "type_effectiveness",
        )
    )
    return bool(has_name and has_struct)


def _is_openie_json(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    docs = payload.get("docs")
    if not isinstance(docs, list) or not docs:
        return False
    for item in docs[:5]:
        if not isinstance(item, dict):
            return False
        if any(key in item for key in ("passage", "extracted_triples", "extracted_entities")):
            return True
    return False


def _textify(value):
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if item.get("name"):
                    parts.append(str(item.get("name")))
                elif item.get("type"):
                    parts.append(str(item.get("type")))
            elif item is not None:
                parts.append(str(item))
        return "、".join(part for part in parts if part)
    if isinstance(value, dict):
        if value.get("name"):
            return str(value.get("name"))
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _build_pokemon_chunks(payload: dict, file_path: str) -> list:
    meta = _base_meta(file_path)
    name_zh = str(payload.get("name_zh") or "").strip()
    name_ja = str(payload.get("name_ja") or "").strip()
    name_en = str(payload.get("name_en") or "").strip()
    entity_type = "招式" if payload.get("power") or payload.get("category") else "特性"
    if payload.get("pokedex_id") or payload.get("forms") or payload.get("profile"):
        entity_type = "宝可梦"

    intro_fields = []
    for key in (
        "introduction",
        "description",
        "intro",
        "effect",
        "additional_effect",
        "profile",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            intro_fields.append(value)
    intro_text = "\n".join(intro_fields[:4]).strip()

    aliases = [item for item in [name_zh, name_ja, name_en] if item]
    summary_lines = [
        f"这是一个{entity_type}词条。",
        f"中文名：{name_zh}" if name_zh else "",
        f"日文名：{name_ja}" if name_ja else "",
        f"英文名：{name_en}" if name_en else "",
    ]
    if payload.get("type"):
        summary_lines.append(f"属性：{_textify(payload.get('type'))}")
    if payload.get("category"):
        summary_lines.append(f"分类：{_textify(payload.get('category'))}")
    if payload.get("basic_info"):
        summary_lines.append(f"基础信息：{_textify(payload.get('basic_info'))}")
    if intro_text:
        summary_lines.append(f"简介：{intro_text}")

    chunks = [
        (
            "\n".join(line for line in summary_lines if line).strip(),
            {
                **meta,
                "entity_type": entity_type,
                "entity_name": name_zh or name_en or name_ja,
                "aliases": "|".join(aliases),
                "chunk_type": "summary",
            },
        )
    ]

    relation_specs = [
        ("pokemon_list", "拥有该特性的宝可梦"),
        ("learn_by_level_up", "可通过升级学会该招式的宝可梦"),
        ("learn_by_tm", "可通过招式学习器学会该招式的宝可梦"),
        ("learn_by_tutor", "可通过教学学会该招式的宝可梦"),
        ("forms", "形态与能力信息"),
        ("abilities", "特性信息"),
    ]
    for key, label in relation_specs:
        value = payload.get(key)
        if not value:
            continue
        line = f"{name_zh or name_en or name_ja} 的{label}：{_textify(value)[:1500]}"
        chunks.append(
            (
                line,
                {
                    **meta,
                    "entity_type": entity_type,
                    "entity_name": name_zh or name_en or name_ja,
                    "aliases": "|".join(aliases),
                    "chunk_type": key,
                },
            )
        )
    return chunks


def _build_openie_chunks(payload: dict, file_path: str) -> list:
    meta = _base_meta(file_path)
    docs = payload.get("docs") if isinstance(payload.get("docs"), list) else []
    chunks = []
    seen = set()

    def add_chunk(text: str, extra_meta: dict):
        text = str(text or "").strip()
        if len(text) < 5 or text in seen:
            return
        seen.add(text)
        chunks.append((text, {**meta, **extra_meta}))

    for idx, doc in enumerate(docs):
        if not isinstance(doc, dict):
            continue
        doc_idx = str(doc.get("idx") or idx)
        passage = str(doc.get("passage") or "").strip()
        entities = [
            str(item).strip()
            for item in (doc.get("extracted_entities") or [])
            if str(item).strip()
        ]
        triples = doc.get("extracted_triples") if isinstance(doc.get("extracted_triples"), list) else []

        if passage:
            add_chunk(
                passage,
                {
                    "chunk_type": "openie_passage",
                    "doc_idx": doc_idx,
                    "aliases": "|".join(entities[:24]),
                },
            )

        if entities:
            add_chunk(
                f"本段涉及的实体：{'、'.join(entities[:30])}",
                {
                    "chunk_type": "openie_entities",
                    "doc_idx": doc_idx,
                    "aliases": "|".join(entities[:24]),
                },
            )

        for triple_idx, triple in enumerate(triples):
            if not isinstance(triple, list) or len(triple) != 3:
                continue
            subject = str(triple[0] or "").strip()
            predicate = str(triple[1] or "").strip()
            obj = str(triple[2] or "").strip()
            if not (subject and predicate and obj):
                continue
            add_chunk(
                f"{subject} {predicate} {obj}",
                {
                    "chunk_type": "openie_triple",
                    "doc_idx": doc_idx,
                    "triple_idx": triple_idx,
                    "entity_name": subject,
                    "aliases": "|".join(
                        item for item in [subject, obj, *entities[:12]] if item
                    ),
                },
            )

    return chunks


def _is_heading_line(line: str) -> bool:
    return bool(_HEADING_RE.match(str(line or "").strip()))


def _is_structural_line(line: str) -> bool:
    text = str(line or "").rstrip()
    if not text.strip():
        return False
    return bool(
        _LIST_RE.match(text) or _TABLE_RE.match(text) or _TERM_DEF_RE.match(text)
    )


def _split_plain_units(content: str) -> list[str]:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    units: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        block = "\n".join(buf).strip()
        buf.clear()
        if block:
            units.append(block)

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            index += 1
            continue
        if _is_heading_line(line):
            flush()
            buf.append(line)
            index += 1
            continue
        if index + 1 < len(lines) and _SETEXT_UNDERLINE_RE.match(
            lines[index + 1].strip()
        ):
            flush()
            buf.append(line)
            buf.append(lines[index + 1])
            flush()
            index += 2
            continue
        if _is_structural_line(line):
            if buf and not all(
                _is_structural_line(item) or not item.strip() for item in buf
            ):
                flush()
            while index < len(lines):
                current = lines[index]
                if not current.strip():
                    if index + 1 < len(lines) and _is_structural_line(lines[index + 1]):
                        buf.append(current)
                        index += 1
                        continue
                    break
                if not _is_structural_line(current):
                    break
                buf.append(current)
                index += 1
            flush()
            continue
        buf.append(line)
        index += 1
    flush()
    return units


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(str(text or ""))
    return [part for part in parts if str(part).strip()]


def _overlap_prefix(text: str, overlap: int = _PLAIN_OVERLAP_CHARS) -> str:
    value = str(text or "")
    if len(value) <= overlap:
        return value
    window = value[-overlap:]
    for offset, char in enumerate(window):
        if char in "。！？!?\n":
            tail = window[offset + 1 :]
            if tail.strip():
                return tail
    return window


def _split_structural_block(text: str) -> list[str]:
    lines = str(text or "").split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > _PLAIN_MAX_CHARS:
            chunks.append("\n".join(current).strip())
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len += extra
    if current:
        block = "\n".join(current).strip()
        if block:
            chunks.append(block)
    return chunks or [str(text or "").strip()]


def _hard_wrap(text: str) -> list[str]:
    value = str(text or "").strip()
    if len(value) <= _PLAIN_MAX_CHARS:
        return [value] if value else []
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + _PLAIN_MAX_CHARS)
        if end < len(value):
            cut = value.rfind("\n", start + 1, end)
            if cut > start:
                end = cut
        piece = value[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(value):
            break
        overlap = _overlap_prefix(value[start:end])
        next_start = end - len(overlap)
        start = end if next_start <= start else next_start
    return chunks


def _split_long_unit(text: str) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    if len(value) <= _PLAIN_MAX_CHARS:
        return [value]
    lines = value.split("\n")
    if all(
        (not line.strip()) or _is_structural_line(line) or _is_heading_line(line)
        for line in lines
    ):
        return _split_structural_block(value)
    sentences = _split_sentences(value)
    if len(sentences) <= 1:
        return _hard_wrap(value)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        piece = sentence.strip()
        if not piece:
            continue
        if not current:
            if len(piece) > _PLAIN_MAX_CHARS:
                chunks.extend(_hard_wrap(piece))
                continue
            current = piece
            continue
        joined = current + piece
        if len(joined) <= _PLAIN_MAX_CHARS:
            current = joined
            continue
        chunks.append(current)
        prefix = _overlap_prefix(current)
        current = (prefix + piece).strip()
        if len(current) > _PLAIN_MAX_CHARS:
            chunks.extend(_hard_wrap(current)[:-1])
            leftover = _hard_wrap(current)
            current = leftover[-1] if leftover else piece
    if current:
        chunks.append(current)
    return chunks


def _merge_plain_units(units: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        piece = str(unit or "").strip()
        if not piece:
            continue
        if len(piece) > _PLAIN_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_unit(piece))
            continue
        if not current:
            current = piece
            continue
        joined = current + "\n\n" + piece
        if len(joined) <= _PLAIN_MAX_CHARS:
            current = joined
            continue
        if len(current) < _PLAIN_MIN_CHARS:
            chunks.extend(_split_long_unit(joined))
            current = ""
            continue
        chunks.append(current)
        current = piece
    if current:
        chunks.append(current)
    return chunks


def _chunk_plain_text(content: str, file_path: str = "") -> list:
    """Paragraph/heading chunker for ordinary .md/.txt knowledge files.

    Old line-split imports are not migrated. Rebuild from source files after upgrade.
    """
    units = _split_plain_units(content)
    texts = _merge_plain_units(units)
    base = _base_meta(file_path) if file_path else {}
    chunks = []
    for index, text in enumerate(texts):
        meta = dict(base)
        meta["chunk_index"] = index
        chunks.append((text, meta))
    return chunks


def _source_lock(source_path: str) -> threading.Lock:
    with _SOURCE_LOCK_GUARD:
        lock = _SOURCE_LOCKS.get(source_path)
        if lock is None:
            lock = threading.Lock()
            _SOURCE_LOCKS[source_path] = lock
        return lock


def _empty_manifest() -> dict:
    return {"version": 1, "files": {}}


def load_knowledge_manifest(manifest_path: str | None = None) -> dict:
    path = manifest_path or DEFAULT_KNOWLEDGE_MANIFEST_PATH
    with _MANIFEST_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return _empty_manifest()
        except Exception:
            return _empty_manifest()
    if not isinstance(payload, dict):
        return _empty_manifest()
    files = payload.get("files")
    if not isinstance(files, dict):
        files = {}
    return {"version": int(payload.get("version") or 1), "files": files}


def _write_knowledge_manifest_unlocked(payload: dict, path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    serialized = json.dumps(payload or _empty_manifest(), ensure_ascii=False, indent=2)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def save_knowledge_manifest(payload: dict, manifest_path: str | None = None) -> None:
    path = manifest_path or DEFAULT_KNOWLEDGE_MANIFEST_PATH
    with _MANIFEST_LOCK:
        _write_knowledge_manifest_unlocked(payload, path)


def upsert_knowledge_manifest_file(
    source_path: str, entry: dict, manifest_path: str | None = None
) -> dict:
    path = manifest_path or DEFAULT_KNOWLEDGE_MANIFEST_PATH
    key = normalize_knowledge_source_path(source_path)
    with _MANIFEST_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            payload = _empty_manifest()
        if not isinstance(payload, dict):
            payload = _empty_manifest()
        files = payload.get("files")
        if not isinstance(files, dict):
            files = {}
        files[key] = dict(entry or {})
        payload["version"] = int(payload.get("version") or 1)
        payload["files"] = files
        _write_knowledge_manifest_unlocked(payload, path)
        return payload


def clear_knowledge_manifest(
    source_path: str | None = None, manifest_path: str | None = None
) -> None:
    path = manifest_path or DEFAULT_KNOWLEDGE_MANIFEST_PATH
    if source_path:
        payload = load_knowledge_manifest(path)
        payload["files"].pop(normalize_knowledge_source_path(source_path), None)
        save_knowledge_manifest(payload, path)
        return
    save_knowledge_manifest(_empty_manifest(), path)


def _file_fingerprint(file_path: str) -> dict:
    stat = os.stat(file_path)
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "sha256": digest.hexdigest(),
        "mtime": int(stat.st_mtime),
        "size": int(stat.st_size),
    }


def _collection_ids(collection, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    try:
        existing = collection.get(ids=ids)
    except Exception:
        return set()
    return {
        str(item).strip()
        for item in (existing.get("ids") or [])
        if str(item).strip()
    }


def _ids_for_source_path(collection, source_path: str) -> list[str]:
    target = normalize_knowledge_source_path(source_path)
    if not target:
        return []
    basename = os.path.basename(str(source_path or "").replace("\\", "/"))
    try:
        rows = collection.get(include=["metadatas"])
    except Exception:
        return []
    found = []
    for item_id, meta in zip(rows.get("ids") or [], rows.get("metadatas") or []):
        meta = meta or {}
        meta_path = normalize_knowledge_source_path(meta.get("source_path") or "")
        if meta_path == target:
            found.append(str(item_id))
            continue
        # Pre-A4 line-split rows often only stored the filename in `source`.
        if not meta_path and basename and str(meta.get("source") or "") == basename:
            found.append(str(item_id))
    return found


def _unchanged_file_can_skip(
    *,
    force: bool,
    previous: dict,
    fingerprint: dict,
    previous_ids: list[str],
    collection,
    source_path: str,
) -> bool:
    if force:
        return False
    if previous.get("sha256") != fingerprint.get("sha256"):
        return False
    if not previous_ids:
        return False
    if str(previous.get("chunker_version") or "") != KNOWLEDGE_CHUNKER_VERSION:
        return False
    present = _collection_ids(collection, previous_ids)
    if present != set(previous_ids):
        return False
    leftovers = [
        item_id
        for item_id in _ids_for_source_path(collection, source_path)
        if item_id not in previous_ids
    ]
    return not leftovers


def _delete_ids(collection, ids: list[str]) -> int:
    targets = [str(item) for item in ids if str(item).strip()]
    if not targets:
        return 0
    collection.delete(ids=targets)
    return len(targets)


def _add_rows(collection, rows: list[dict], *, existing_ids: set[str]) -> tuple[list[str], list[str]]:
    added: list[str] = []
    failed: list[str] = []
    pending = [item for item in rows if item["id"] not in existing_ids]
    if not pending:
        return added, failed
    try:
        collection.add(
            documents=[item["doc"] for item in pending],
            metadatas=[item["meta"] for item in pending],
            ids=[item["id"] for item in pending],
        )
        return [item["id"] for item in pending], failed
    except Exception:
        for item in pending:
            try:
                collection.add(
                    documents=[item["doc"]],
                    metadatas=[item["meta"]],
                    ids=[item["id"]],
                )
                added.append(item["id"])
            except Exception:
                failed.append(item["id"])
    return added, failed


def _progress(callback, payload: dict) -> None:
    if callable(callback):
        callback(payload)


def _prepare_knowledge_rows(file_path: str, stable_hash_fn) -> tuple[list[dict], str]:
    with open(file_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    specialized_chunks = []
    if file_path.lower().endswith(".json"):
        try:
            payload = json.loads(content)
            if _is_pokemon_json(payload):
                specialized_chunks = _build_pokemon_chunks(payload, file_path)
            elif _is_openie_json(payload):
                specialized_chunks = _build_openie_chunks(payload, file_path)
        except Exception:
            specialized_chunks = []
    chunks = specialized_chunks or _chunk_plain_text(content, file_path)
    rows = []
    for chunk, meta in chunks:
        if len(chunk) < 5:
            continue
        rows.append(
            {
                "id": "know_" + stable_hash_fn(chunk),
                "doc": chunk,
                "meta": meta,
            }
        )
    return rows, content


def import_knowledge_from_file(
    collection,
    stable_hash_fn,
    file_path: str,
    progress_callback=None,
    *,
    force: bool = False,
    manifest_path: str | None = None,
):
    if not os.path.exists(file_path):
        return 0
    source_path = normalize_knowledge_source_path(file_path)
    if not source_path:
        return {
            "ok": False,
            "status": "invalid_path",
            "added": 0,
            "skipped": 0,
            "failed": 0,
            "total": 0,
            "source_path": "",
        }
    manifest_file = manifest_path or DEFAULT_KNOWLEDGE_MANIFEST_PATH
    with _source_lock(source_path):
        fingerprint = _file_fingerprint(file_path)
        manifest = load_knowledge_manifest(manifest_file)
        previous = manifest["files"].get(source_path) or {}
        previous_ids = [
            str(item)
            for item in (previous.get("chunk_ids") or [])
            if str(item).strip()
        ]
        if _unchanged_file_can_skip(
            force=force,
            previous=previous,
            fingerprint=fingerprint,
            previous_ids=previous_ids,
            collection=collection,
            source_path=source_path,
        ):
            _progress(
                progress_callback,
                {
                    "stage": "skipped",
                    "file_path": file_path,
                    "source_path": source_path,
                    "total": len(previous_ids),
                    "added": 0,
                    "skipped": len(previous_ids),
                },
            )
            print(f"[Knowledge] 未改动，跳过: {file_path}")
            return {
                "ok": True,
                "status": "skipped",
                "added": 0,
                "skipped": len(previous_ids),
                "failed": 0,
                "total": len(previous_ids),
                "source_path": source_path,
                "chunk_ids": previous_ids,
            }

        print(f"[Knowledge] 正在读取知识文件: {file_path}")
        rows, _content = _prepare_knowledge_rows(file_path, stable_hash_fn)
        desired_ids = [item["id"] for item in rows]
        desired_set = set(desired_ids)
        if len(desired_ids) != len(desired_set):
            return {
                "ok": False,
                "status": "duplicate_chunk_ids",
                "added": 0,
                "skipped": 0,
                "failed": len(desired_ids),
                "total": len(desired_ids),
                "source_path": source_path,
                "failed_ids": desired_ids,
                "repair_needed": True,
            }

        existing_for_file = set(_ids_for_source_path(collection, source_path))
        already_present = _collection_ids(collection, desired_ids)
        reusable = already_present & desired_set
        to_add = [item for item in rows if item["id"] not in reusable]
        added_ids: list[str] = []
        failed_ids: list[str] = []
        batch_size = 64
        total_batches = max(1, (len(to_add) + batch_size - 1) // batch_size)
        _progress(
            progress_callback,
            {
                "stage": "prepared",
                "file_path": file_path,
                "source_path": source_path,
                "total": len(desired_ids),
                "batch": 0,
                "batches": total_batches,
                "added": 0,
                "skipped": len(reusable),
            },
        )
        for start in range(0, len(to_add), batch_size):
            batch = to_add[start : start + batch_size]
            batch_index = start // batch_size + 1
            _progress(
                progress_callback,
                {
                    "stage": "embedding",
                    "file_path": file_path,
                    "source_path": source_path,
                    "total": len(desired_ids),
                    "batch": batch_index,
                    "batches": total_batches,
                    "added": len(added_ids),
                    "skipped": len(reusable),
                    "batch_size": len(batch),
                },
            )
            batch_added, batch_failed = _add_rows(collection, batch, existing_ids=set())
            added_ids.extend(batch_added)
            failed_ids.extend(batch_failed)
            _progress(
                progress_callback,
                {
                    "stage": "batch_done",
                    "file_path": file_path,
                    "source_path": source_path,
                    "total": len(desired_ids),
                    "batch": batch_index,
                    "batches": total_batches,
                    "added": len(added_ids),
                    "skipped": len(reusable),
                    "failed": len(failed_ids),
                },
            )

        present_after_add = _collection_ids(collection, desired_ids)
        if failed_ids or present_after_add != desired_set:
            rollback_ids = [item_id for item_id in added_ids if item_id not in reusable]
            rollback_error = ""
            try:
                _delete_ids(collection, rollback_ids)
            except Exception as exc:
                rollback_error = str(exc)
            print(f"[Knowledge] 导入失败，已回退本次新增: {file_path}")
            return {
                "ok": False,
                "status": "add_failed",
                "added": 0,
                "skipped": len(reusable),
                "failed": len(failed_ids) or len(desired_set - present_after_add),
                "total": len(desired_ids),
                "source_path": source_path,
                "failed_ids": failed_ids or sorted(desired_set - present_after_add),
                "repair_needed": bool(rollback_error),
                "error": rollback_error or "partial_add",
            }

        stale_ids = [item_id for item_id in existing_for_file if item_id not in desired_set]
        try:
            removed = _delete_ids(collection, stale_ids)
        except Exception as exc:
            rollback_ids = [item_id for item_id in added_ids if item_id not in reusable]
            rollback_error = ""
            try:
                _delete_ids(collection, rollback_ids)
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
            return {
                "ok": False,
                "status": "replace_failed",
                "added": 0,
                "skipped": len(reusable),
                "failed": len(stale_ids),
                "total": len(desired_ids),
                "source_path": source_path,
                "failed_ids": stale_ids,
                "repair_needed": True,
                "error": rollback_error or str(exc),
            }

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            upsert_knowledge_manifest_file(
                source_path,
                {
                    "sha256": fingerprint["sha256"],
                    "mtime": fingerprint["mtime"],
                    "size": fingerprint["size"],
                    "chunk_ids": desired_ids,
                    "chunk_count": len(desired_ids),
                    "chunker_version": KNOWLEDGE_CHUNKER_VERSION,
                    "imported_at": now,
                },
                manifest_file,
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "manifest_failed",
                "added": len(added_ids),
                "skipped": len(reusable),
                "failed": 0,
                "total": len(desired_ids),
                "source_path": source_path,
                "chunk_ids": desired_ids,
                "removed": removed,
                "repair_needed": True,
                "error": str(exc),
            }
        print(
            f"[Knowledge] 成功导入 {len(added_ids)} 条新知识，复用 {len(reusable)} 条，替换 {removed} 条旧片段。"
        )
        return {
            "ok": True,
            "status": "imported",
            "added": len(added_ids),
            "skipped": len(reusable),
            "failed": 0,
            "total": len(desired_ids),
            "source_path": source_path,
            "chunk_ids": desired_ids,
            "removed": removed,
        }


def search_knowledge(collection, query: str, k: int = 3) -> list[KnowledgeHit]:
    return retrieve_knowledge_chunks(collection, query, k=k)


def delete_knowledge_by_dirs(
    collection, dirs, *, manifest_path: str | None = None
) -> int:
    targets = {
        normalize_knowledge_source_path(item).rstrip("/")
        for item in (dirs or [])
        if str(item or "").strip()
    }
    targets.discard("")
    if not targets:
        return 0
    try:
        rows = collection.get(include=["metadatas"])
    except Exception:
        return 0
    ids = rows.get("ids") or []
    metas = rows.get("metadatas") or []
    delete_ids = []
    deleted_sources = set()
    for item_id, meta in zip(ids, metas):
        meta = meta or {}
        source_dir = normalize_knowledge_source_path(meta.get("source_dir") or "").rstrip("/")
        source_path = normalize_knowledge_source_path(meta.get("source_path") or "").rstrip("/")
        matched = source_dir in targets
        if not matched:
            for target in targets:
                if source_path == target or source_path.startswith(target + "/"):
                    matched = True
                    break
        if not matched:
            continue
        delete_ids.append(item_id)
        if source_path:
            deleted_sources.add(source_path)
    if not delete_ids:
        return 0
    try:
        collection.delete(ids=delete_ids)
    except Exception:
        return 0
    if deleted_sources:
        payload = load_knowledge_manifest(manifest_path)
        changed = False
        for source in deleted_sources:
            if payload["files"].pop(source, None) is not None:
                changed = True
        if changed:
            try:
                save_knowledge_manifest(payload, manifest_path)
            except Exception:
                pass
    return len(delete_ids)
