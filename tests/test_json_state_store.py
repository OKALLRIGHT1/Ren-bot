import json

import pytest

from modules import json_state_store as state


@pytest.fixture(params=["list", "tasks"])
def store_case(request, tmp_path):
    path = tmp_path / "nested" / "state.json"
    if request.param == "list":
        return state.JsonListStore(path), [{"text": "旧记录"}], [{"text": "新记录"}]
    return state.JsonTaskStore(path), {"tasks": {"old": {}}}, {"tasks": {"new": {}}}


def test_save_replaces_complete_json(store_case):
    store, old, new = store_case
    assert store.save(old)
    assert store.save(new)
    assert store.load() == new
    assert list(store.path.parent.iterdir()) == [store.path]


def test_serialization_failure_preserves_previous_bytes(store_case, caplog):
    store, old, new = store_case
    assert store.save(old)
    original = store.path.read_bytes()
    if isinstance(new, list):
        new[0]["invalid"] = object()
    else:
        new["tasks"]["new"]["invalid"] = object()
    assert store.save(new) is False
    assert store.path.read_bytes() == original
    assert store.load() == old
    assert "Failed to save JSON state" in caplog.text
    assert list(store.path.parent.iterdir()) == [store.path]


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_io_failure_preserves_previous_file(store_case, monkeypatch, failure_point):
    store, old, new = store_case
    assert store.save(old)
    original = store.path.read_bytes()

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(state.os, failure_point, fail)
    assert store.save(new) is False
    assert store.path.read_bytes() == original
    assert store.load() == old
    assert list(store.path.parent.iterdir()) == [store.path]


def test_replace_observes_complete_file_and_old_destination(store_case, monkeypatch):
    store, old, new = store_case
    assert store.save(old)
    real_replace = state.os.replace

    def inspect_replace(source, destination):
        assert source.parent == destination.parent
        assert json.loads(destination.read_text(encoding="utf-8")) == old
        assert json.loads(source.read_text(encoding="utf-8")) == new
        real_replace(source, destination)

    monkeypatch.setattr(state.os, "replace", inspect_replace)
    assert store.save(new)


def test_retention_and_task_history(tmp_path):
    events = state.JsonListStore(tmp_path / "events.json", max_items=2)
    for i in range(3):
        events.append({"n": i})
    assert events.recent() == [{"n": 1}, {"n": 2}]
    tasks = state.JsonTaskStore(tmp_path / "tasks.json", max_tasks=1, max_history=2)
    data = {"tasks": {"old": {"updated_at": "2020"}, "new": {"updated_at": "2021"}}}
    assert tasks.save(data)
    assert len(data["tasks"]) == 2
    assert set(tasks.load()["tasks"]) == {"new"}
    for status in ("queued", "running", "done"):
        assert tasks.set_task_state("new", status)
    assert [h["state"] for h in tasks.get_task("new")["history"]] == ["running", "done"]
