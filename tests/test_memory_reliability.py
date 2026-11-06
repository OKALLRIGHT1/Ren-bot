"""Regression coverage for scope, vector generations and retry recovery."""
from concurrent.futures import Future
import threading

import pytest

from modules.embeddings import EmbeddingUnavailableError
from modules.memory_core import repository as repository_module
from modules.memory_core.vector_index import MemoryVectorIndex
from tests.test_memory_vector_index import _core, _Collection, _Embedding


def add_record(core, *, session="private_A", subject="owner", **extra):
    return core.upsert_memory_record(
        kind="episode", key="project", content="星河项目预算八万元",
        subject_id=subject, session_id=session, source_type="test",
        importance=0.8, **extra,
    )


@pytest.mark.parametrize("session,subject,extra,visible", [
    ("private_A", "owner", {}, False),
    ("group_B", "owner", {}, True),
    ("", "owner", {}, True),
    ("group_B", "other", {}, False),
    ("group_B", "owner", {"valid_until": "2000-01-01T00:00:00+00:00"}, False),
])
def test_entity_recall_obeys_scope_and_validity(tmp_path, session, subject, extra, visible):
    core = _core(tmp_path)
    rid = add_record(core, session=session, subject=subject, **extra)
    core.repository.link_record_entities(rid, [core.repository.upsert_entity("星河项目")])
    context = core.build_reply_context(
        "星河项目预算", person_id="owner", session_id="group_B",
        include_profile=False, use_llm=False,
    )
    assert (rid in context.selected_ids) is visible
    assert ("八万元" in context.memory_text) is visible


@pytest.mark.parametrize("change", ["update", "delete", "failure", "rebuild"])
def test_new_generation_survives_inflight_embedding(tmp_path, change):
    core = _core(tmp_path)
    rid = add_record(core)
    repo = core.repository
    collection = _Collection()
    embedding = _Embedding()
    index = MemoryVectorIndex(repository=repo, collection=collection, embedding_service=embedding)
    old_generation = repo.list_vector_jobs()[0]["generation"]

    def concurrent_write(documents):
        if change == "delete":
            core.delete_memory_record(rid)
        else:
            core.update_memory_record(rid, content="星河项目预算十万元")
        if change == "rebuild":
            repo.rebuild_vector_jobs()
        if change == "failure":
            raise EmbeddingUnavailableError("old request failed")
        return _Embedding().embed(documents)

    embedding.embed = concurrent_write
    index.process_pending()
    job = repo.list_vector_jobs()[0]
    assert job["generation"] > old_generation
    assert job["status"] == "pending"
    assert job["attempts"] == 0
    embedding.embed = _Embedding().embed
    index.process_pending()
    assert repo.list_vector_jobs()[0]["status"] == "indexed"
    if change == "delete":
        assert rid not in collection.rows
    else:
        assert "十万元" in collection.rows[rid]["document"]


def test_reactivation_during_index_delete_remains_pending(tmp_path):
    core = _core(tmp_path)
    rid = add_record(core)
    repo = core.repository
    collection = _Collection()
    index = MemoryVectorIndex(repository=repo, collection=collection, embedding_service=_Embedding())
    index.process_pending()
    core.update_memory_record(rid, status="archived")
    original_delete = collection.delete

    def concurrent_reactivate(**kwargs):
        original_delete(**kwargs)
        core.update_memory_record(rid, status="active", content="星河项目预算十万元")

    collection.delete = concurrent_reactivate
    index.process_pending()
    assert repo.list_vector_jobs()[0]["status"] == "pending"
    index.process_pending()
    assert "十万元" in collection.rows[rid]["document"]


def test_retry_backoff_recovers_and_is_bounded(tmp_path, monkeypatch):
    core = _core(tmp_path)
    rid = add_record(core)
    repo = core.repository
    clock = [1000.0]
    monkeypatch.setattr(repository_module.time, "time", lambda: clock[0])
    embedding = _Embedding()
    collection = _Collection()
    index = MemoryVectorIndex(repository=repo, collection=collection, embedding_service=embedding)

    def fail(documents):
        raise EmbeddingUnavailableError("temporary outage")

    embedding.embed = fail
    assert index.process_pending()["failed"] == 1
    assert repo.next_vector_job_delay() == 2
    assert index.process_pending()["failed"] == 0
    clock[0] += 2
    embedding.embed = _Embedding().embed
    assert index.process_pending()["indexed"] == 1
    assert rid in collection.rows
    assert repo.next_vector_job_delay() is None

    core.update_memory_record(rid, content="星河项目预算十万元")
    embedding.embed = fail
    for attempt in range(repository_module.VECTOR_MAX_ATTEMPTS):
        assert index.process_pending()["failed"] == 1
        job = repo.list_vector_jobs()[0]
        assert job["attempts"] == attempt + 1
        clock[0] = job["retry_after"]
    assert repo.next_vector_job_delay() is None
    assert index.process_pending()["failed"] == 0
    assert repo.list_vector_jobs()[0]["last_error"] == "temporary outage"
    repo.rebuild_vector_jobs()
    embedding.embed = _Embedding().embed
    assert index.process_pending()["indexed"] == 1


def test_old_vector_schema_and_failed_jobs_migrate_idempotently(tmp_path):
    core = _core(tmp_path)
    repo = core.repository
    rid = add_record(core)
    repo.mark_vector_job_failed(rid, "old failure")
    conn = repo._connect()
    conn.execute("ALTER TABLE memory_vector_jobs DROP COLUMN generation")
    conn.execute("ALTER TABLE memory_vector_jobs DROP COLUMN retry_after")
    conn.commit()
    repo.initialize()
    repo.initialize()
    job = repo.list_due_vector_jobs()[0]
    assert job["record_id"] == rid
    assert job["generation"] == 1
    assert job["status"] == "failed"
    assert job["last_error"] == "old failure"


def test_background_retry_runs_after_error_without_new_chat(tmp_path, monkeypatch):
    from modules import advanced_memory as module

    core = _core(tmp_path)
    add_record(core)
    embedding = _Embedding()
    index = MemoryVectorIndex(repository=core.repository, collection=_Collection(), embedding_service=embedding)
    brain = module.AdvancedMemorySystem.__new__(module.AdvancedMemorySystem)
    brain.memory_core = core
    brain.embedding_service = embedding
    brain.memory_vector_index = index
    brain._vector_lock = threading.Lock()
    brain._vector_schedule_lock = threading.Lock()
    brain._vector_sync_future = None
    brain._vector_retry_timer = None
    queued = []
    timers = []

    class Executor:
        def submit(self, fn, **kwargs):
            future = Future()
            queued.append((future, fn, kwargs))
            return future

    class Timer:
        def __init__(self, delay, fn):
            self.delay, self.fn = delay, fn
            self.cancelled = False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True

    brain._vector_executor = Executor()
    monkeypatch.setattr(module.threading, "Timer", Timer)
    clock = [1000.0]
    monkeypatch.setattr(repository_module.time, "time", lambda: clock[0])
    original_status = embedding.status
    embedding.status = lambda: {**original_status(), "state": "error"}

    def fail(documents):
        raise EmbeddingUnavailableError("temporary outage")

    embedding.embed = fail
    assert brain._schedule_memory_vector_sync()
    future, fn, kwargs = queued.pop(0)
    future.set_result(fn(**kwargs))
    assert timers[-1].delay == 2
    clock[0] += 2
    embedding.embed = _Embedding().embed
    timers[-1].fn()
    future, fn, kwargs = queued.pop(0)
    future.set_result(fn(**kwargs))
    assert core.vector_job_stats()["indexed"] == 1
    assert len(timers) == 1
