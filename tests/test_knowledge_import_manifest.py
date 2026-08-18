from __future__ import annotations

from pathlib import Path

from modules.memory.knowledge_store import (
    KNOWLEDGE_CHUNKER_VERSION,
    import_knowledge_from_file,
    load_knowledge_manifest,
    normalize_knowledge_source_path,
    upsert_knowledge_manifest_file,
)


class MemoryCollection:
    def __init__(self, fail_ids=None, fail_once=None):
        self.ids: list[str] = []
        self.docs: list[str] = []
        self.metas: list[dict] = []
        self.fail_ids = set(fail_ids or [])
        self.fail_once = set(fail_once or [])
        self.add_calls = 0

    def get(self, ids=None, include=None):
        del include
        if ids is None:
            return {"ids": list(self.ids), "metadatas": list(self.metas)}
        existing = [item for item in ids if item in self.ids]
        return {"ids": existing}

    def add(self, documents, metadatas, ids):
        self.add_calls += 1
        if any(item_id in self.fail_ids for item_id in ids):
            raise RuntimeError("add failed")
        once = [item_id for item_id in ids if item_id in self.fail_once]
        if once:
            self.fail_once.difference_update(once)
            raise RuntimeError("add failed once")
        for doc, meta, item_id in zip(documents, metadatas, ids):
            if item_id in self.ids:
                raise RuntimeError(f"duplicate id {item_id}")
            self.docs.append(doc)
            self.metas.append(meta)
            self.ids.append(item_id)

    def delete(self, ids=None):
        drop = set(ids or [])
        keep = [
            (item_id, doc, meta)
            for item_id, doc, meta in zip(self.ids, self.docs, self.metas)
            if item_id not in drop
        ]
        self.ids = [item[0] for item in keep]
        self.docs = [item[1] for item in keep]
        self.metas = [item[2] for item in keep]


def _import(collection, path: Path, manifest: Path, **kwargs):
    return import_knowledge_from_file(
        collection,
        lambda text: str(abs(hash(text))),
        str(path),
        manifest_path=str(manifest),
        **kwargs,
    )


def test_unchanged_file_is_skipped(tmp_path: Path):
    source = tmp_path / "note.md"
    source.write_text("这是一段不会改动的设定说明。里面有两句。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()

    first = _import(collection, source, manifest)
    second = _import(collection, source, manifest)

    assert first["status"] == "imported"
    assert first["added"] == 1
    assert second["status"] == "skipped"
    assert second["added"] == 0
    assert collection.add_calls == 1
    payload = load_knowledge_manifest(str(manifest))
    key = normalize_knowledge_source_path(str(source))
    assert payload["files"][key]["chunk_count"] == 1
    assert payload["files"][key]["chunker_version"] == KNOWLEDGE_CHUNKER_VERSION


def test_changed_file_replaces_old_chunks(tmp_path: Path):
    source = tmp_path / "lore.md"
    source.write_text("第一版设定。只讲房间布置。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()

    first = _import(collection, source, manifest)
    source.write_text("第二版设定。房间改成靠窗，并且补充了灯光。", encoding="utf-8")
    second = _import(collection, source, manifest)

    assert first["chunk_ids"] != second["chunk_ids"]
    assert collection.ids == second["chunk_ids"]
    assert "第一版设定" not in "\n".join(collection.docs)
    assert "第二版设定" in "\n".join(collection.docs)
    assert second["removed"] == 1


def test_partial_add_does_not_update_manifest(tmp_path: Path):
    source = tmp_path / "fail.md"
    source.write_text("第一版稳定内容。这里有两句。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()
    first = _import(collection, source, manifest)
    old_ids = list(collection.ids)

    source.write_text("第二版会失败的内容。这里也有两句。", encoding="utf-8")
    failing = MemoryCollection(fail_ids=set())
    failing.ids = list(collection.ids)
    failing.docs = list(collection.docs)
    failing.metas = list(collection.metas)

    def fail_all(documents, metadatas, ids):
        raise RuntimeError("embedding down")

    failing.add = fail_all
    result = _import(failing, source, manifest)

    assert result["ok"] is False
    assert result["status"] == "add_failed"
    payload = load_knowledge_manifest(str(manifest))
    key = normalize_knowledge_source_path(str(source))
    assert payload["files"][key]["chunk_ids"] == first["chunk_ids"]
    assert failing.ids == old_ids


def test_retry_after_failure_succeeds(tmp_path: Path):
    source = tmp_path / "retry.md"
    source.write_text("第一版设定。这里有两句。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()
    first = _import(collection, source, manifest)

    source.write_text("第二版设定。这里也有两句。", encoding="utf-8")
    original_add = collection.add
    fail = {"on": True}

    def maybe_fail(documents, metadatas, ids):
        if fail["on"]:
            raise RuntimeError("embedding down")
        return original_add(documents, metadatas, ids)

    collection.add = maybe_fail
    failed = _import(collection, source, manifest)
    fail["on"] = False
    assert failed["ok"] is False
    assert failed["status"] == "add_failed"
    assert collection.ids == first["chunk_ids"]

    retried = _import(collection, source, manifest)
    assert retried["ok"] is True
    assert retried["status"] == "imported"
    assert retried["chunk_ids"] != first["chunk_ids"]
    assert collection.ids == retried["chunk_ids"]


def test_path_aliases_share_one_manifest_key(tmp_path: Path):
    source = tmp_path / "alias.md"
    source.write_text("同一份文件的不同路径写法。这里有两句。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()

    first = _import(collection, source, manifest)
    mixed = str(source).replace("\\", "/") if "\\" in str(source) else str(source)
    if mixed == str(source):
        mixed = str(source.resolve())
    second = _import(collection, Path(mixed), manifest)

    assert first["source_path"] == second["source_path"]
    assert second["status"] == "skipped"
    payload = load_knowledge_manifest(str(manifest))
    assert list(payload["files"]) == [first["source_path"]]


def test_legacy_manifest_without_chunker_version_reimports(tmp_path: Path):
    source = tmp_path / "legacy.md"
    source.write_text("这是一段不会改动的设定说明。里面有两句。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()
    first = _import(collection, source, manifest)
    key = normalize_knowledge_source_path(str(source))
    payload = load_knowledge_manifest(str(manifest))
    payload["files"][key].pop("chunker_version", None)
    upsert_knowledge_manifest_file(str(source), payload["files"][key], str(manifest))

    second = _import(collection, source, manifest)

    assert first["status"] == "imported"
    assert second["status"] == "imported"
    assert collection.add_calls == 1
    refreshed = load_knowledge_manifest(str(manifest))
    assert refreshed["files"][key]["chunker_version"] == KNOWLEDGE_CHUNKER_VERSION


def test_legacy_line_chunks_are_replaced_on_relearn(tmp_path: Path):
    source = tmp_path / "old_lines.md"
    source.write_text("第一句设定说明。第二句补充房间。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()
    first = _import(collection, source, manifest)
    leftover_id = "know_legacy_line"
    collection.ids.append(leftover_id)
    collection.docs.append("第一句设定说明。")
    collection.metas.append({"source": source.name})
    key = normalize_knowledge_source_path(str(source))
    payload = load_knowledge_manifest(str(manifest))
    payload["files"][key].pop("chunker_version", None)
    upsert_knowledge_manifest_file(str(source), payload["files"][key], str(manifest))

    second = _import(collection, source, manifest)

    assert first["status"] == "imported"
    assert second["status"] == "imported"
    assert leftover_id not in collection.ids
    assert collection.ids == second["chunk_ids"]
    assert all("。" in doc for doc in collection.docs)


def test_force_reimports_unchanged_file(tmp_path: Path):
    source = tmp_path / "force.md"
    source.write_text("强制重建也不会改正文。这里有两句。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    collection = MemoryCollection()
    first = _import(collection, source, manifest)
    forced = _import(collection, source, manifest, force=True)
    assert first["status"] == "imported"
    assert forced["status"] == "imported"
    assert forced["added"] == 0
    assert forced["skipped"] == first["total"]
