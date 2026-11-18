"""Cold startup, concurrent first use and restart recovery of optional memory resources."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from modules import advanced_memory
from modules.embeddings import EmbeddingService
from modules.memory_sqlite import MemorySQLite


@pytest.fixture
def brain_factory(tmp_path, monkeypatch):
    store = MemorySQLite(str(tmp_path / "memory.sqlite"))
    embedding = EmbeddingService(enabled=True, model="fixture", expected_dimension=2, api_url="fixture")
    monkeypatch.setattr(embedding, "embed", lambda texts: [[1., 0.] for _ in texts])
    monkeypatch.setattr(advanced_memory, "get_memory_store", lambda: store)
    monkeypatch.setattr(advanced_memory, "load_runtime_settings_strict", lambda: {})
    monkeypatch.setattr(advanced_memory, "build_configured_embedding_service", lambda **kw: embedding)
    monkeypatch.setattr(advanced_memory, "MEMORY_DB_PATH", str(tmp_path / "chroma"))
    return advanced_memory.AdvancedMemorySystem


def test_cold_start_and_plain_recall_without_optional_imports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
from boot import Live2DApplication
from modules.asr import SAMPLE_RATE
from modules.asr.adapter import project_settings_store
from modules.asr.pcm import PcmRateLimiter
from modules.advanced_memory import AdvancedMemorySystem
from modules.embeddings import EmbeddingService
import modules.advanced_memory as memory
memory.build_configured_embedding_service = lambda **kw: EmbeddingService(
    enabled=False, model='fixture', api_url='', expected_dimension=2)
brain = AdvancedMemorySystem()
brain.memory_core.build_reply_context('你好呀', person_id='owner')
assert 'chromadb' not in sys.modules
assert 'networkx' not in sys.modules
assert SAMPLE_RATE == 16000 and PcmRateLimiter().allow(1)
assert 'modules.asr.engine' not in sys.modules
assert 'modules.asr.sensor' not in sys.modules
graph = brain.graph
assert graph is brain.graph and 'networkx' in sys.modules
assert 'chromadb' not in sys.modules
from modules.asr import VoiceSensor, create_asr_engine
from modules.asr.sensor import VoiceSensor as Sensor
from modules.asr.engine import create_asr_engine as factory
assert VoiceSensor is Sensor and create_asr_engine is factory
"""
    env = dict(os.environ, SUZU_DATA_DIR=str(tmp_path), PYTHONPATH=str(root), PYTHONUTF8="1")
    subprocess.run([sys.executable, "-c", script], env=env, cwd=tmp_path, check=True, timeout=40)


@pytest.mark.parametrize("failure", ["client", "collection"])
def test_concurrent_first_use_retries_and_only_opens_requested_collection(brain_factory, monkeypatch, failure):
    brain = brain_factory()
    calls = []
    opened = []
    barrier = threading.Barrier(6)

    def collection(**kwargs):
        opened.append(kwargs["name"])
        if failure == "collection" and len(opened) == 1:
            raise RuntimeError("collection busy")
        time.sleep(.02)  # Opening a real collection releases the GIL.
        return SimpleNamespace(metadata=kwargs["metadata"])

    def connect(path):
        calls.append(path)
        if failure == "client" and len(calls) == 1:
            raise OSError("client busy")
        time.sleep(.02)
        return SimpleNamespace(get_or_create_collection=collection)

    monkeypatch.setattr("chromadb.PersistentClient", connect)
    assert brain._vector_sync_future is None
    error = OSError if failure == "client" else RuntimeError
    with pytest.raises(error, match=f"{failure} busy"):
        _ = brain.memory_vector_index

    def access():
        barrier.wait(timeout=5)
        return brain.memory_vector_index

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(access) for _ in range(6)]
        indexes = [job.result(timeout=10) for job in jobs]
    assert all(index is indexes[0] for index in indexes)
    assert len(calls) == (2 if failure == "client" else 1)
    assert opened == [brain.memory_vector_collection_name] * (2 if failure == "collection" else 1)
    assert not {"knowledge_collection", "memory_collection", "graph"}.intersection(vars(brain))


def test_restart_with_pending_job_indexes_without_a_chat(brain_factory):
    first = brain_factory()
    record_id, _ = first.memory_core.repository.upsert_record(
        kind="fact", content="蓝色笔记本", subject_id="owner",
    )
    restarted = brain_factory()
    assert restarted._vector_sync_future.result(timeout=20)["indexed"] == 1
    rows = restarted.query_memory_vector("笔记本")
    assert [row["id"] for row in rows] == [record_id]
    assert not {"knowledge_collection", "memory_collection"}.intersection(vars(restarted))
    restarted._vector_executor.shutdown(wait=True)


def test_first_use_can_be_rebuild_of_absent_collections(brain_factory, monkeypatch):
    brain = brain_factory()
    monkeypatch.setattr(advanced_memory, "clear_knowledge_manifest", lambda: None)
    assert brain.rebuild_memory_vector_index()["queued"] == 0
    assert brain.get_memory_vector_status()["collection_count"] == 0
    assert "knowledge_collection" not in vars(brain)
    assert brain.rebuild_knowledge_collection()
    assert not brain.get_knowledge_stats()["rebuild_required"]
    assert "memory_collection" not in vars(brain)


def test_qt_window_and_reply_only_load_required_backends(tmp_path):
    script = Path(__file__).resolve().parents[1] / "benchmarks" / "startup.py"
    seed = tmp_path / "seed"
    seed.mkdir()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", MEMORY_VECTOR_PREWARM="1",
               SUZU_DATA_DIR=str(seed), ANONYMIZED_TELEMETRY="False")
    subprocess.run([sys.executable, str(script), "--seed"], cwd=seed, env=env,
                   check=True, timeout=40)
    for mode in ("plain", "vector"):
        work = tmp_path / mode
        shutil.copytree(seed, work)
        (work / "data").mkdir(exist_ok=True)
        (work / "data/runtime_settings.json").write_text('{"tts_enabled":false}', encoding="utf-8")
        env["SUZU_DATA_DIR"] = str(work)
        child = subprocess.run(
            [sys.executable, str(script), "--worker", mode, "--start", str(time.perf_counter())],
            cwd=work, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        assert child.returncode == 0, child.stdout + child.stderr
        line = next(line for line in child.stdout.splitlines() if line.startswith("STARTUP_SAMPLE "))
        stages = json.loads(line.removeprefix("STARTUP_SAMPLE "))
        assert not stages["before_panel"]["warmup_submitted"]
        assert stages["window"]["warmup_submitted"] == (mode == "vector")
        assert not stages["before_panel"]["chroma"]
        if mode == "plain":
            assert not stages["window"]["chroma"]
        assert stages["reply"]["chroma"] == (mode == "vector")
        assert ("vector" in stages) == (mode == "vector")
        for stage in stages.values():
            assert not any(stage[module] for module in ("networkx", "sherpa", "sounddevice"))


def test_panel_requests_warmup_only_on_first_visible_input():
    from modules.gui.app import QtChatTrayApp
    from modules.gui.config import QtGuiConfig

    calls = []
    gui = QtChatTrayApp(
        lambda *_: None,
        cfg=QtGuiConfig(start_minimized_to_tray=True),
        on_chat_ready_callback=lambda: calls.append("ready"),
    )
    try:
        gui._app.processEvents()
        gui._toggle_ball_from_tray()
        gui._app.processEvents()
        assert gui._win.isVisible() and not gui._input.isVisible()
        assert calls == []
        for _ in range(2):
            gui.toggle_hotkey()  # open panel
            gui._app.processEvents()
            assert gui._input.isVisible()
            gui.toggle_hotkey()  # collapse to ball
            gui._app.processEvents()
        assert calls == ["ready"]
    finally:
        gui._tray.hide()
        gui._win.close()
        gui._app.processEvents()


@pytest.mark.parametrize("skip", ["opt_out", "core_off", "disabled", "unconfigured", "error", "open"])
def test_prewarm_skips_unneeded_work(brain_factory, monkeypatch, skip):
    brain = brain_factory()
    monkeypatch.setitem(advanced_memory.MEMORY_SETTINGS, "memory_vector_prewarm", skip != "opt_out")
    brain.memory_core.enabled = skip != "core_off"
    if skip in {"disabled", "unconfigured", "error"}:
        monkeypatch.setattr(brain.embedding_service, "status", lambda: {"state": skip})
    if skip == "open":
        brain.memory_vector_index = object()
    monkeypatch.setattr(brain._vector_executor, "submit", lambda *_: pytest.fail("unexpected warmup"))
    brain.prewarm_memory_vector_index()


def test_prewarm_shares_inflight_open_with_first_query(brain_factory, monkeypatch):
    brain = brain_factory()
    monkeypatch.setitem(advanced_memory.MEMORY_SETTINGS, "memory_vector_prewarm", True)
    opening, release, query_started = threading.Event(), threading.Event(), threading.Event()
    opens, embeddings = [], []
    main_thread = threading.get_ident()

    def open_collection(name, metadata):
        opens.append((name, threading.get_ident()))
        opening.set()
        assert release.wait(5)
        return SimpleNamespace(metadata=metadata, query=lambda **kw: {"ids": [["fixture"]]})

    def embed(texts):
        embeddings.extend(texts)
        return [[1., 0.] for _ in texts]

    monkeypatch.setattr(brain.embedding_service, "embed", embed)
    monkeypatch.setattr(brain, "_schedule_memory_vector_sync", lambda **kw: query_started.set())
    brain.chroma_client = SimpleNamespace(get_or_create_collection=open_collection)
    warmup = brain.prewarm_memory_vector_index()
    assert opening.wait(5)
    assert [name for name, _ in opens] == [brain.memory_vector_collection_name]
    assert opens[0][1] != main_thread
    assert not warmup.done() and not embeddings
    try:
        with ThreadPoolExecutor(max_workers=1) as chat:
            query = chat.submit(brain.query_memory_vector, "笔记本")
            try:
                assert query_started.wait(5)
            finally:
                release.set()
            assert [row["id"] for row in query.result(timeout=5)] == ["fixture"]
        assert warmup.result(timeout=5) is brain.memory_vector_index
        assert len(opens) == 1 and embeddings == ["笔记本"]
        assert not {"knowledge_collection", "memory_collection", "graph"}.intersection(vars(brain))
    finally:
        release.set()
        brain._vector_executor.shutdown(wait=True)


def test_failed_prewarm_leaves_first_query_free_to_retry(brain_factory, monkeypatch):
    import chromadb

    brain = brain_factory()
    monkeypatch.setitem(advanced_memory.MEMORY_SETTINGS, "memory_vector_prewarm", True)
    real_client = chromadb.PersistentClient
    attempts = []

    def open_client(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise OSError("temporarily unavailable")
        return real_client(path=path)

    monkeypatch.setattr("chromadb.PersistentClient", open_client)
    try:
        assert brain.prewarm_memory_vector_index().result(timeout=5) is None
        assert not {"chroma_client", "current_memory_collection", "memory_vector_index"}.intersection(vars(brain))
        assert brain.query_memory_vector("笔记本") == []
        assert len(attempts) == 2
    finally:
        brain._vector_executor.shutdown(wait=True)
