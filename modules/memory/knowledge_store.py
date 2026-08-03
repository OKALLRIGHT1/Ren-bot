import json
import os

from .retrieval import retrieve_knowledge_chunks


def _base_meta(file_path: str) -> dict:
    return {
        "source": os.path.basename(file_path),
        "source_path": os.path.abspath(file_path),
        "source_dir": os.path.abspath(os.path.dirname(file_path)),
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


def import_knowledge_from_file(collection, stable_hash_fn, file_path: str, progress_callback=None) -> int:
    if not os.path.exists(file_path):
        return 0
    print(f"[Knowledge] 正在读取知识文件: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

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

    if specialized_chunks:
        chunks = specialized_chunks
    else:
        chunks = [
            (c.strip(), _base_meta(file_path)) for c in content.split("\n") if c.strip()
        ]
    valid_rows = []
    for chunk, meta in chunks:
        if len(chunk) < 5:
            continue
        valid_rows.append(
            {
                "id": "know_" + stable_hash_fn(chunk),
                "doc": chunk,
                "meta": meta,
            }
        )

    count = 0
    skipped = 0
    batch_size = 64
    total_batches = max(1, (len(valid_rows) + batch_size - 1) // batch_size)
    if callable(progress_callback):
        progress_callback(
            {
                "stage": "prepared",
                "file_path": file_path,
                "total": len(valid_rows),
                "batch": 0,
                "batches": total_batches,
                "added": count,
                "skipped": skipped,
            }
        )
    for start in range(0, len(valid_rows), batch_size):
        batch_index = start // batch_size + 1
        batch = valid_rows[start : start + batch_size]
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "embedding",
                    "file_path": file_path,
                    "total": len(valid_rows),
                    "batch": batch_index,
                    "batches": total_batches,
                    "added": count,
                    "skipped": skipped,
                    "batch_size": len(batch),
                }
            )
        batch_ids = [item["id"] for item in batch]
        try:
            existing = collection.get(ids=batch_ids)
            existing_ids = {
                str(item).strip()
                for item in (existing.get("ids") or [])
                if str(item).strip()
            }
        except Exception:
            existing_ids = set()

        new_docs = []
        new_metas = []
        new_ids = []
        for item in batch:
            if item["id"] in existing_ids:
                skipped += 1
                continue
            new_docs.append(item["doc"])
            new_metas.append(item["meta"])
            new_ids.append(item["id"])

        if not new_ids:
            continue
        try:
            collection.add(
                documents=new_docs,
                metadatas=new_metas,
                ids=new_ids,
            )
            count += len(new_ids)
        except Exception:
            for item in batch:
                if item["id"] in existing_ids:
                    continue
                try:
                    collection.add(
                        documents=[item["doc"]],
                        metadatas=[item["meta"]],
                        ids=[item["id"]],
                    )
                    count += 1
                except Exception:
                    pass
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": "batch_done",
                    "file_path": file_path,
                    "total": len(valid_rows),
                    "batch": batch_index,
                    "batches": total_batches,
                    "added": count,
                    "skipped": skipped,
                }
            )
    print(f"[Knowledge] 成功导入 {count} 条新知识！")
    return {"added": count, "skipped": skipped, "total": count + skipped}


def search_knowledge(collection, query: str, k: int = 3):
    return retrieve_knowledge_chunks(collection, query, k=k)


def delete_knowledge_by_dirs(collection, dirs) -> int:
    targets = {
        os.path.abspath(str(item or "")).rstrip("\\/")
        for item in (dirs or [])
        if str(item or "").strip()
    }
    if not targets:
        return 0
    try:
        rows = collection.get(include=["metadatas"])
    except Exception:
        return 0
    ids = rows.get("ids") or []
    metas = rows.get("metadatas") or []
    delete_ids = []
    for item_id, meta in zip(ids, metas):
        meta = meta or {}
        source_dir = os.path.abspath(str(meta.get("source_dir") or "")).rstrip("\\/")
        source_path = os.path.abspath(str(meta.get("source_path") or "")).rstrip("\\/")
        if source_dir in targets:
            delete_ids.append(item_id)
            continue
        for target in targets:
            if source_path.startswith(target + os.sep):
                delete_ids.append(item_id)
                break
    if not delete_ids:
        return 0
    try:
        collection.delete(ids=delete_ids)
        return len(delete_ids)
    except Exception:
        return 0
