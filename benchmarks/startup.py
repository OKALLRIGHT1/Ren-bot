"""Fresh-process Qt startup, plain reply and first successful Chroma recall."""
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
MODEL = "startup-fixture"
REPLY = "今天过得很好。"
MEMORY = "书架上的蓝色笔记本"
MARKER = "STARTUP_SAMPLE "


def embedding_service(enabled):
    from modules.embeddings.service import EmbeddingService

    class FixtureEmbedding(EmbeddingService):
        def embed(self, texts):
            return [[1., 0., 0.] for _ in texts]

    return FixtureEmbedding(enabled=enabled, api_url="fixture", model=MODEL, expected_dimension=3)


def seed():
    import chromadb
    from modules.advanced_memory import AdvancedMemorySystem
    from modules.memory_core import MemoryCoreService, MemoryVectorIndex
    from modules.memory_sqlite import get_memory_store

    core = MemoryCoreService(get_memory_store())
    core.initialize()
    core.upsert_memory_record(kind="fact", key="notebook", content=MEMORY, subject_id="owner")
    client = chromadb.PersistentClient(path=str(Path.cwd() / "memory_db"))
    collection = client.get_or_create_collection(
        AdvancedMemorySystem._memory_vector_collection_name(MODEL, 3),
        metadata={"embedding_model": MODEL, "embedding_dimension": 3, "index_version": 1},
    )
    index = MemoryVectorIndex(repository=core.repository, collection=collection,
                              embedding_service=embedding_service(True))
    assert index.process_pending()["indexed"] == 1


def measure(mode, start, delay, idle_only):
    import psutil

    def offline(event, args):
        # asyncio uses a loopback socket pair to wake the Windows event loop.
        if event == "socket.connect" and isinstance(args[1], tuple) and args[1][0] in {"127.0.0.1", "::1"}:
            return
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise OSError("offline startup benchmark")

    sys.addaudithook(offline)
    import boot
    from boot import Live2DApplication, config
    import modules.advanced_memory as memory
    from modules.qt_gui import QtChatTrayApp
    from modules.live2d_transport import LegacyLocalWebSocketTransport
    import services.chat_service as chat
    from modules.llm_types import ChatCompletionResult

    results = {}
    process = psutil.Process()
    warmup_submitted = False

    def mark(name, since):
        now = time.perf_counter()
        results[name] = dict(seconds=now - start, action_seconds=now - since,
                             rss_mib=process.memory_info().rss / 2**20,
                             chroma="chromadb" in sys.modules,
                             networkx="networkx" in sys.modules,
                             sherpa="sherpa_onnx" in sys.modules,
                             sounddevice="sounddevice" in sys.modules)

    embedding = embedding_service(mode == "vector")
    memory.build_configured_embedding_service = lambda **kw: embedding
    original_query = memory.AdvancedMemorySystem._query_memory_vector

    def observed_query(self, text, **scope):
        begin = time.perf_counter()
        rows = original_query(self, text, **scope)
        if "vector" not in results and any(MEMORY in row["document"] for row in rows):
            mark("vector", begin)
        return rows

    memory.AdvancedMemorySystem._query_memory_vector = observed_query

    async def plain_reply(*args, **kwargs):
        return REPLY

    async def completion(*args, **kwargs):
        return ChatCompletionResult(text=REPLY)

    chat.chat_with_ai = plain_reply
    chat.chat_complete = completion
    chat.chat_with_ai_stream = None

    async def deliver(self, delivery):
        pass

    LegacyLocalWebSocketTransport.deliver = deliver
    config.START_MINIMIZED_TO_TRAY = False
    config.VOICE_SENSOR_ENABLED = False
    config.AUTO_DIARY_ENABLED = False
    config.GUI_BACKEND = "qt"

    async def no_listener(*args):
        pass

    class BenchmarkApplication(Live2DApplication):
        def initialize(self):
            self._maybe_autostart_external_services = lambda: None
            self._init_display_mqtt = lambda: None
            self._skip_external_stop_on_cleanup = True
            super().initialize()
            schedule_warmup = getattr(self.brain, "prewarm_memory_vector_index", None)
            if schedule_warmup is not None:
                def observe_warmup():
                    nonlocal warmup_submitted
                    warmup_submitted = schedule_warmup() is not None

                self.brain.prewarm_memory_vector_index = observe_warmup
            self.gui_ws_server.start = no_listener
            self.gui_http_server.start = lambda: None
            self._publish_gui_activity_endpoint = lambda: None
            self._start_live2d_renderer_when_ready = lambda: None
            for sensor in (self.screen_sensor, self.music_sensor):
                if sensor:
                    sensor.start = lambda loop: None

        def _run_tk_gui(self):
            raise RuntimeError("Qt benchmark failed")  # propagate the Qt error

    QtChatTrayApp._start_global_listeners = lambda self: None

    def exercise(ui):
        ui._app.processEvents()
        mark("before_panel", start)
        results["before_panel"]["warmup_submitted"] = warmup_submitted
        ui._switch_to_panel()
        ui._app.processEvents()
        assert ui._win.isVisible() and ui._input.isEnabled() and ui._input.isVisible()
        mark("window", start)
        results["window"]["warmup_submitted"] = warmup_submitted
        if delay:
            ready = time.perf_counter()
            while time.perf_counter() - ready < delay:
                ui._app.processEvents()
                time.sleep(.001)
            mark("idle", ready)
            results["idle"]["index_open"] = "memory_vector_index" in vars(ui.brain)
        if idle_only:
            return
        append = ui._append_history_message
        begin = time.perf_counter()

        def observe(role, text):
            append(role, text)
            if role == "assistant" and REPLY in text and "reply" not in results:
                mark("reply", begin)

        ui._append_history_message = observe
        ui._input.setPlainText("你好呀")
        ui._input.sendRequested.emit()
        while "reply" not in results:
            ui._app.processEvents()
            if time.perf_counter() - begin > 25:
                raise RuntimeError("Fixture reply did not reach Qt chat history")
            time.sleep(.001)
        if mode == "vector":
            assert "vector" in results, "Ordinary reply did not retrieve the seeded record"
        else:
            assert "vector" not in results

    QtChatTrayApp.run = exercise
    boot.ROOT_DIR = str(Path.cwd())  # keep the instance lock and migration in the fixture
    boot.Live2DApplication = BenchmarkApplication
    boot.main()
    print(MARKER + json.dumps(results), flush=True)


def command(*args):
    return [sys.executable, str(Path(__file__).resolve()), *map(str, args)]


def run(samples, compare_warmup):
    files = subprocess.check_output(["git", "ls-files", "plugins"], cwd=ROOT,
                                    encoding="utf-8").splitlines()
    with tempfile.TemporaryDirectory(prefix="ren-startup-") as temp:
        fixture = Path(temp) / "seed"
        fixture.mkdir()
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", ANONYMIZED_TELEMETRY="False",
                   PYTHONUTF8="1", SUZU_DATA_DIR=str(fixture))
        subprocess.run(command("--seed"), cwd=fixture, env=env, check=True)
        for name in files:
            dest = fixture / name
            dest.parent.mkdir(exist_ok=True, parents=True)
            shutil.copyfile(ROOT / name, dest)
        (fixture / "data").mkdir(exist_ok=True)
        (fixture / "data/runtime_settings.json").write_text('{"tts_enabled":false}', encoding="utf-8")
        if compare_warmup:
            cases = [
                (f"{scenario}-{policy}", "vector", delay, idle_only, policy)
                for scenario, delay, idle_only in (("now", 0, False), ("wait", 2, False), ("idle", 5, True))
                for policy in ("demand", "prewarm")
            ]
        else:
            cases = [(mode, mode, 0, False, None) for mode in ("plain", "vector")]
        runs = {label: [] for label, *_ in cases}
        for i in range(samples):
            # Reverse each round so that neither strategy always goes first.
            for label, mode, delay, idle_only, policy in (cases if i % 2 == 0 else reversed(cases)):
                work = Path(temp) / f"{label}-{i}"
                shutil.copytree(fixture, work)
                env["SUZU_DATA_DIR"] = str(work)
                if policy:
                    env["MEMORY_VECTOR_PREWARM"] = "1" if policy == "prewarm" else "0"
                flags = ["--idle-only"] if idle_only else []
                child = subprocess.run(command("--worker", mode, "--delay", delay, *flags, "--start", time.perf_counter()),
                                       cwd=work, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, encoding="utf-8",
                                       errors="replace", timeout=70)
                if child.returncode:
                    raise RuntimeError(child.stdout[-6500:])
                if os.environ.get("REN_BENCH_VERBOSE") == "1":
                    print(child.stdout, flush=True)
                line = next(s for s in child.stdout.splitlines() if s.startswith(MARKER))
                stages = json.loads(line[len(MARKER):])
                if policy:
                    assert not stages["before_panel"]["warmup_submitted"]
                    assert stages["window"]["warmup_submitted"] == (policy == "prewarm"), label
                runs[label].append(stages)
                print(label, i + 1, line, flush=True)
        for label, rows in runs.items():
            for stage in rows[0]:
                metrics = {k: round(statistics.median(row[stage][k] for row in rows), 3)
                           for k in ("seconds", "action_seconds", "rss_mib")}
                print(label, stage, "median", metrics, flush=True)


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--samples", type=int, default=5)
    args.add_argument("--compare-warmup", action="store_true", help="Compare on-demand and background Chroma opening")
    args.add_argument("--delay", type=float, default=0, help=argparse.SUPPRESS)
    args.add_argument("--idle-only", action="store_true", help=argparse.SUPPRESS)
    args.add_argument("--seed", action="store_true", help=argparse.SUPPRESS)
    args.add_argument("--worker", choices=["plain", "vector"], help=argparse.SUPPRESS)
    args.add_argument("--start", type=float, help=argparse.SUPPRESS)
    options = args.parse_args()
    sys.path.insert(0, str(ROOT))
    if options.seed:
        seed()
    elif options.worker:
        measure(options.worker, options.start, options.delay, options.idle_only)
    else:
        run(options.samples, options.compare_warmup)
