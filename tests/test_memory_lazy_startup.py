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
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1",
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
        assert not stages["window"]["chroma"]
        assert stages["reply"]["chroma"] == (mode == "vector")
        assert ("vector" in stages) == (mode == "vector")
        for stage in stages.values():
            assert not any(stage[module] for module in ("networkx", "sherpa", "sounddevice"))
