from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.gui_api.memory_service import MemoryGuiService


class FakeRepo:
    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {
            "r1": {
                "id": "r1",
                "kind": "preference",
                "key": "likes.music",
                "subject_id": "owner",
                "session_id": "",
                "content": "喜欢爵士",
                "confidence": 0.9,
                "importance": 0.8,
                "status": "active",
                "manual_lock": False,
                "source_type": "manual",
                "source_id": "1",
                "metadata": {},
            }
        }
        self.next_id = 2

    def list_records(self, **kwargs: Any) -> List[Dict[str, Any]]:
        status = str(kwargs.get("status") or "active")
        rows = list(self.records.values())
        if status:
            rows = [row for row in rows if str(row.get("status") or "") == status]
        return rows

    def get_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self.records.get(record_id)

    def upsert_record(self, **kwargs: Any):
        record_id = str(kwargs.get("id") or f"r{self.next_id}")
        self.next_id += 1
        row = {
            "id": record_id,
            "kind": kwargs.get("kind") or "other",
            "key": kwargs.get("key") or "",
            "subject_id": kwargs.get("subject_id") or "owner",
            "session_id": kwargs.get("session_id") or "",
            "content": kwargs.get("content") or "",
            "confidence": float(kwargs.get("confidence") or 1.0),
            "importance": float(kwargs.get("importance") or 0.7),
            "status": kwargs.get("status") or "active",
            "manual_lock": bool(kwargs.get("manual_lock")),
            "source_type": kwargs.get("source_type") or "manual_gui",
            "source_id": kwargs.get("source_id") or record_id,
            "metadata": dict(kwargs.get("metadata") or {}),
        }
        self.records[record_id] = row
        return record_id, True

    def update_record(self, record_id: str, changes: Dict[str, Any]) -> bool:
        row = self.records.get(record_id)
        if not row:
            return False
        row.update(changes)
        return True

    def update_record_metadata(
        self,
        record_id: str,
        metadata: Dict[str, Any],
        remove_keys: tuple = (),
    ) -> bool:
        row = self.records.get(record_id)
        if not row:
            return False
        current = dict(row.get("metadata") or {})
        current.update(metadata or {})
        for key in remove_keys:
            current.pop(key, None)
        row["metadata"] = current
        return True

    def delete_record(self, record_id: str) -> bool:
        return self.records.pop(record_id, None) is not None

    def list_persons(self) -> List[Dict[str, Any]]:
        # Repository shape uses person_id/display_name; service must normalize.
        return [
            {"person_id": "owner", "display_name": "我", "relationship": "owner"},
            {
                "person_id": "qq:10001",
                "display_name": "群友甲",
                "relationship": "group_member",
            },
        ]


class FakeCore:
    def __init__(self) -> None:
        self.repository = FakeRepo()
        self._initialized = True

    def initialize(self) -> None:
        self._initialized = True

    def list_memory_records(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.repository.list_records(**kwargs)

    def list_persons(self) -> List[Dict[str, Any]]:
        return self.repository.list_persons()

    def get_memory_record(self, record_id: str):
        return self.repository.get_record(record_id)

    def upsert_memory_record(self, **kwargs: Any) -> str:
        record_id, _ = self.repository.upsert_record(**kwargs)
        return record_id

    def update_memory_record(self, record_id: str, **changes: Any) -> bool:
        return self.repository.update_record(record_id, changes)

    def set_memory_category_override(self, record_id: str, category_id: str) -> bool:
        if category_id:
            return self.repository.update_record_metadata(
                record_id, {"category_override": category_id}
            )
        return self.repository.update_record_metadata(
            record_id, {}, remove_keys=("category_override",)
        )

    def delete_memory_record(self, record_id: str) -> bool:
        return self.repository.delete_record(record_id)


class FakeBrain:
    def get_memory_vector_status(self) -> Dict[str, Any]:
        return {
            "rebuild_required": False,
            "indexed_count": 3,
            "pending_count": 1,
            "model": "bge",
        }

    def rebuild_memory_vector_index(self) -> Dict[str, Any]:
        return {"queued": 3, "collection": "memory_core_v1_bge"}

    def test_embedding_connection(self) -> Dict[str, Any]:
        return {"state": "ready", "model": "bge", "dimension": 1024}


def test_list_core_records_with_category_tree(monkeypatch):
    service = MemoryGuiService(memory_core=FakeCore(), brain=FakeBrain())

    class _FakeCharacterManager:
        @staticmethod
        def get_all_characters():
            return {
                "char_6aab46": {"name": "高松灯"},
                "suzu": {"name": "五十铃怜"},
            }

    import sys
    import types

    fake_mod = types.ModuleType("modules.character_manager")
    fake_mod.character_manager = _FakeCharacterManager()
    monkeypatch.setitem(sys.modules, "modules.character_manager", fake_mod)

    data = service.list_core_records(status="active")
    assert data["ok"] is True
    assert len(data["data"]["records"]) == 1
    assert data["data"]["records"][0]["category"] == "likes.music"
    assert any(item["id"] == "likes" for item in data["data"]["categories"])
    person_ids = [item["id"] for item in data["data"]["persons"]]
    assert person_ids[0] == "owner"
    assert "character:char_6aab46" in person_ids
    assert "character:suzu" in person_ids
    assert "qq:10001" in person_ids
    labels = {item["id"]: item["label"] for item in data["data"]["persons"]}
    assert labels["character:char_6aab46"] == "高松灯"
    assert labels["qq:10001"] == "群友甲"
    assert data["data"]["category_tree"]
    likes = next(item for item in data["data"]["category_tree"] if item["id"] == "likes")
    assert any(child["id"] == "likes.music" for child in likes.get("children") or [])


def test_profile_overview_groups_by_category(monkeypatch):
    service = MemoryGuiService(memory_core=FakeCore(), brain=FakeBrain())

    class _FakeCharacterManager:
        @staticmethod
        def get_all_characters():
            return {"char_6aab46": {"name": "高松灯"}}

    import sys
    import types

    fake_mod = types.ModuleType("modules.character_manager")
    fake_mod.character_manager = _FakeCharacterManager()
    monkeypatch.setitem(sys.modules, "modules.character_manager", fake_mod)

    overview = service.get_profile_overview(person_id="owner")
    assert overview["ok"] is True
    assert overview["data"]["person_id"] == "owner"
    groups = {item["id"]: item for item in overview["data"]["groups"]}
    assert "likes" in groups
    music = next(
        (child for child in groups["likes"]["children"] if child["id"] == "likes.music"),
        None,
    )
    assert music is not None
    assert any("爵士" in str(row.get("content") or "") for row in music["records"])


def test_upsert_and_category_override():
    service = MemoryGuiService(memory_core=FakeCore(), brain=FakeBrain())
    created = service.upsert_core_record(
        {
            "kind": "fact",
            "key": "identity.name",
            "content": "叫小铃",
            "subject_id": "owner",
            "category_override": "identity",
        }
    )
    assert created["ok"] is True
    record_id = created["data"]["id"]
    detail = service.get_core_record(record_id)
    assert detail["ok"] is True
    assert detail["data"]["category"] == "identity"
    cleared = service.set_category_override(record_id, "")
    assert cleared["ok"] is True


def test_delete_core_record():
    service = MemoryGuiService(memory_core=FakeCore(), brain=FakeBrain())
    deleted = service.delete_core_record("r1")
    assert deleted["ok"] is True
    missing = service.get_core_record("r1")
    assert missing["ok"] is False


def test_vector_status_and_rebuild():
    service = MemoryGuiService(memory_core=FakeCore(), brain=FakeBrain())
    status = service.vector_status()
    assert status["ok"] is True
    assert status["data"]["indexed_count"] == 3
    rebuilt = service.rebuild_vector_index()
    assert rebuilt["ok"] is True
    assert rebuilt["data"]["queued"] == 3
    embedding = service.test_embedding()
    assert embedding["ok"] is True
    assert embedding["data"]["state"] == "ready"


def test_embedding_selection_roundtrip(tmp_path, monkeypatch):
    from modules import runtime_settings
    from services.gui_api.memory_service import MemoryGuiService

    monkeypatch.setattr(
        runtime_settings,
        "RUNTIME_SETTINGS_PATH",
        tmp_path / "runtime.json",
    )
    import config

    monkeypatch.setattr(
        config,
        "MODELS",
        {
            "local-bge": {
                "model": "bge-m3",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "ollama",
                "purposes": ["embedding"],
                "embedding_endpoint_path": "/embeddings",
                "embedding_dimension": 1024,
                "embedding_provider": "ollama",
            },
            "remote-bge": {
                "model": "BAAI/bge-m3",
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "sk-test",
                "purposes": ["embedding"],
                "embedding_endpoint_path": "/embeddings",
                "embedding_dimension": 1024,
                "embedding_provider": "openai_compatible",
            },
        },
    )
    service = MemoryGuiService(memory_core=FakeCore(), brain=FakeBrain())
    saved = service.save_embedding_selection(
        {"model_ids": ["local-bge", "remote-bge"]}
    )
    assert saved["ok"] is True
    assert saved["data"]["model_ids"] == ["local-bge", "remote-bge"]
    loaded = service.get_embedding_selection()
    assert loaded["data"]["model_id"] == "local-bge"
    assert any(item["id"] == "remote-bge" for item in loaded["data"]["candidates"])
