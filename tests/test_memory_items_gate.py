import pytest

from modules.memory_sqlite import MemorySQLite


def test_upsert_todo_allowed(tmp_path):
    store = MemorySQLite(str(tmp_path / "m.sqlite"))
    item_id = store.upsert_item({"type": "todo", "text": "买牛奶"})
    row = store.get_item(item_id)
    assert row is not None
    assert row["type"] == "todo"


def test_upsert_preference_blocked(tmp_path):
    store = MemorySQLite(str(tmp_path / "m.sqlite"))
    with pytest.raises(ValueError, match="not allowed"):
        store.upsert_item({"type": "preference", "text": "喜欢猫"})


def test_upsert_legacy_flag(tmp_path):
    store = MemorySQLite(str(tmp_path / "m.sqlite"))
    item_id = store.upsert_item(
        {
            "type": "preference",
            "text": "legacy",
            "allow_legacy_write": True,
        }
    )
    assert store.get_item(item_id)["type"] == "preference"
