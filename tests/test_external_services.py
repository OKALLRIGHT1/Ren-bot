from __future__ import annotations

from modules.external_services import (
    _build_launch_argv,
    _resolve_launch_spec,
    dump_services_settings,
    ensure_service,
    load_services_settings,
    parse_host_port,
    start_service,
    stop_managed_services,
)
from services.gui_api.external_services_service import ExternalServicesGuiService


def test_parse_host_port_from_health_url():
    host, port = parse_host_port(health_url="http://127.0.0.1:9880/health")
    assert host == "127.0.0.1"
    assert port == 9880


def test_load_and_dump_services_settings_roundtrip():
    runtime = {
        "external_services": {
            "gptsovits": {
                "autostart_enabled": True,
                "autostop_enabled": True,
                "command": "D:/tools/gpt-sovits/start.bat",
                "args": "--port 9880",
                "health_url": "http://127.0.0.1:9880",
            }
        }
    }
    loaded = load_services_settings(runtime)
    assert loaded["gptsovits"]["autostart_enabled"] is True
    assert loaded["gptsovits"]["command"].endswith("start.bat")
    dumped = dump_services_settings(loaded)
    assert dumped["gptsovits"]["autostop_enabled"] is True
    assert dumped["ollama"]["autostart_enabled"] is False


def test_ensure_requires_command_for_custom_service(monkeypatch):
    monkeypatch.setattr(
        "modules.external_services.resolve_live_endpoint",
        lambda **kwargs: {
            "running": False,
            "host": "127.0.0.1",
            "port": 6099,
            "health_url": "http://127.0.0.1:6099",
            "matched_primary": False,
            "listening_pids": [],
        },
    )
    result = ensure_service(
        "napcat",
        {
            "external_services": {
                "napcat": {"autostart_enabled": True, "command": ""}
            }
        },
        force=True,
    )
    assert result["ok"] is False
    assert result["error"] == "command_required"


def test_gui_service_save_and_list(tmp_path, monkeypatch):
    store = {
        "external_services": {
            "ollama": {"autostart_enabled": False, "autostop_enabled": False}
        }
    }

    def load():
        return dict(store)

    def update(patch):
        store.update(patch or {})
        return dict(store)

    service = ExternalServicesGuiService(load_runtime=load, update_runtime=update)
    saved = service.save_services(
        {
            "services": {
                "gptsovits": {
                    "autostart_enabled": True,
                    "autostop_enabled": True,
                    "command": "D:/x/api.py",
                    "health_url": "http://127.0.0.1:9880",
                }
            }
        }
    )
    assert saved["ok"] is True
    ids = {item["id"] for item in saved["data"]["services"]}
    assert "gptsovits" in ids
    assert store["external_services"]["gptsovits"]["autostart_enabled"] is True


def test_stop_managed_only_touches_started(monkeypatch):
    # no processes started by us
    result = stop_managed_services(
        {
            "external_services": {
                "ollama": {"autostop_enabled": True},
                "napcat": {"autostop_enabled": True},
            }
        }
    )
    assert result["ok"] is True
    assert all(item.get("stopped") is False for item in result["results"])


def test_windows_bat_launch_uses_hidden_cmd_call(monkeypatch):
    argv, flags, startupinfo = _build_launch_argv(r"D:\tools\napcat\launch.bat", ["--quiet"])
    assert argv[:4] == ["cmd.exe", "/d", "/c", "call"]
    assert argv[4].endswith("launch.bat")
    assert "--quiet" in argv
    import subprocess

    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not flags & subprocess.DETACHED_PROCESS
    assert startupinfo is not None


def test_gptsovits_directory_resolves_pythonw_api_v2(tmp_path):
    root = tmp_path / "GPT-SoVITS"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    pythonw = runtime / "pythonw.exe"
    api = root / "api_v2.py"
    pythonw.write_bytes(b"")
    api.write_text("", encoding="utf-8")

    spec = _resolve_launch_spec("gptsovits", str(root), ["--port", "9880"], "")

    assert spec["ok"] is True
    assert spec["command"] == str(pythonw)
    assert spec["args"] == [str(api), "--port", "9880"]
    assert spec["cwd"] == str(root)


def test_gptsovits_known_bat_resolves_pythonw_api_v2(tmp_path):
    root = tmp_path / "GPT-SoVITS"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    pythonw = runtime / "pythonw.exe"
    api = root / "api_v2.py"
    bat = root / "api_v2.bat"
    pythonw.write_bytes(b"")
    api.write_text("", encoding="utf-8")
    bat.write_text("start runtime\\python.exe api_v2.py\n", encoding="utf-8")

    spec = _resolve_launch_spec("gptsovits", str(bat), [], "")

    assert spec["ok"] is True
    assert spec["command"] == str(pythonw)
    assert spec["args"] == [str(api)]
    assert spec["cwd"] == str(root)


def test_napcat_directory_prefers_account_shell_entry(tmp_path):
    root = tmp_path / "napcat"
    account = root / "NapCat.12345.Shell"
    bootmain = root / "bootmain"
    account.mkdir(parents=True)
    bootmain.mkdir()
    account_exe = account / "NapCatWinBootMain.exe"
    account_exe.write_bytes(b"")
    (bootmain / "NapCatWinBootMain.exe").write_bytes(b"")

    spec = _resolve_launch_spec("napcat", str(root), [], "")

    assert spec["ok"] is True
    assert spec["command"] == str(account_exe)
    assert spec["args"] == []
    assert spec["cwd"] == str(account)


def test_napcat_quick_bat_resolves_exe_and_account_id(tmp_path):
    root = tmp_path / "NapCat.12345.Shell"
    root.mkdir()
    executable = root / "NapCatWinBootMain.exe"
    quick_bat = root / "napcat.quick.bat"
    executable.write_bytes(b"")
    quick_bat.write_text(
        "@echo off\n.\\NapCatWinBootMain.exe 2594777156\npause\n",
        encoding="utf-8",
    )

    spec = _resolve_launch_spec("napcat", str(quick_bat), [], "")

    assert spec["ok"] is True
    assert spec["command"] == str(executable)
    assert spec["args"] == ["2594777156"]
    assert spec["cwd"] == str(root)


def test_napcat_directory_rejects_multiple_account_shell_entries(tmp_path):
    root = tmp_path / "napcat"
    for account_id in ("111", "222"):
        account = root / f"NapCat.{account_id}.Shell"
        account.mkdir(parents=True)
        (account / "NapCatWinBootMain.exe").write_bytes(b"")

    spec = _resolve_launch_spec("napcat", str(root), [], "")

    assert spec["ok"] is False
    assert spec["error"] == "ambiguous_command"
    assert len(spec["candidates"]) == 2


def test_ollama_directory_resolves_executable(tmp_path):
    root = tmp_path / "Ollama"
    root.mkdir()
    executable = root / "ollama.exe"
    executable.write_bytes(b"")

    spec = _resolve_launch_spec("ollama", str(root), ["serve"], "")

    assert spec["ok"] is True
    assert spec["command"] == str(executable)
    assert spec["args"] == ["serve"]
    assert spec["cwd"] == str(root)


def test_find_listening_pids_decodes_gbk_netstat(monkeypatch):
    from modules.external_services import find_listening_pids

    sample = (
        "  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       4321\r\n"
        "  TCP    127.0.0.1:6099         0.0.0.0:0              侦听            7788\r\n"
    ).encode("gbk")

    class FakeCompleted:
        stdout = sample
        stderr = b""
        returncode = 0

    monkeypatch.setattr(
        "modules.external_services.subprocess.run",
        lambda *a, **k: FakeCompleted(),
    )
    assert find_listening_pids(3000) == [4321]
    assert find_listening_pids(6099) == [7788]


def test_napcat_status_accepts_alternate_live_port(monkeypatch):
    from modules.external_services import service_status

    def fake_probe(**kwargs):
        assert kwargs.get("service_id") == "napcat"
        return {
            "running": True,
            "tcp_ok": True,
            "http_ok": True,
            "http_status": 200,
            "health_url": "http://127.0.0.1:6099",
            "live_host": "127.0.0.1",
            "live_port": 6099,
            "matched_primary": False,
            "listening_pids": [21904],
            "detail": "在备用端口 127.0.0.1:6099 在线（配置端口为 3000） · HTTP 200 · pid=21904",
        }

    monkeypatch.setattr("modules.external_services.probe_service_health", fake_probe)
    status = service_status(
        "napcat",
        {
            "external_services": {
                "napcat": {
                    "host": "127.0.0.1",
                    "port": 3000,
                    "health_url": "http://127.0.0.1:3000",
                }
            }
        },
    )
    assert status["running"] is True
    assert status["live_port"] == 6099
    assert status["pid"] == 21904
    assert "6099" in status["message"]


def test_start_skips_when_alternate_port_already_open(monkeypatch):
    monkeypatch.setattr(
        "modules.external_services.resolve_live_endpoint",
        lambda **kwargs: {
            "running": True,
            "host": "127.0.0.1",
            "port": 6099,
            "health_url": "http://127.0.0.1:6099",
            "matched_primary": False,
            "listening_pids": [21904],
        },
    )
    monkeypatch.setattr(
        "modules.external_services.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("online service must not be launched again")
        ),
    )
    result = start_service(
        "napcat",
        {
            "external_services": {
                "napcat": {
                    "autostart_enabled": True,
                    "command": "D:/fake/napcat.bat",
                    "host": "127.0.0.1",
                    "port": 3000,
                    "health_url": "http://127.0.0.1:3000",
                }
            }
        },
        force=True,
    )
    assert result["ok"] is True
    assert result["started"] is False
    assert result["running"] is True
    assert result["port"] == 6099
    assert "跳过重复拉起" in result["message"]


def test_start_service_waits_for_port_even_if_bat_exits(monkeypatch, tmp_path):
    bat = tmp_path / "start.bat"
    bat.write_text("@echo off\n", encoding="utf-8")

    class FakeProc:
        def __init__(self):
            self.pid = 4242
            self._polls = 0

        def poll(self):
            self._polls += 1
            # Simulate .bat launcher exiting quickly after spawn.
            return 0 if self._polls >= 2 else None

    calls = {"open": 0}

    def fake_port_open(host, port, timeout=0.6):
        del host, timeout
        calls["open"] += 1
        return calls["open"] >= 3 and int(port) == 3000

    monkeypatch.setattr(
        "modules.external_services.subprocess.Popen",
        lambda *a, **k: FakeProc(),
    )
    monkeypatch.setattr("modules.external_services.is_port_open", fake_port_open)
    monkeypatch.setattr(
        "modules.external_services.find_listening_pids",
        lambda port: [5555] if int(port) == 3000 and calls["open"] >= 3 else [],
    )
    monkeypatch.setattr("modules.external_services.time.sleep", lambda *_: None)

    result = start_service(
        "napcat",
        {
            "external_services": {
                "napcat": {
                    "autostart_enabled": True,
                    "command": str(bat),
                    "health_url": "http://127.0.0.1:3000",
                    "host": "127.0.0.1",
                    "port": 3000,
                    "wait_seconds": 3,
                }
            }
        },
        force=True,
        wait_seconds=3,
    )
    assert result["ok"] is True
    assert result["running"] is True
    assert result["pid"] == 5555


def test_start_service_accepts_alternate_port_during_warmup(monkeypatch, tmp_path):
    executable = tmp_path / "NapCatWinBootMain.exe"
    executable.write_bytes(b"")

    class FakeProc:
        pid = 4242

        @staticmethod
        def poll():
            return None

    probes = {"count": 0}

    def fake_resolve(**kwargs):
        probes["count"] += 1
        running = probes["count"] >= 2
        return {
            "running": running,
            "host": "127.0.0.1",
            "port": 6099 if running else 3000,
            "health_url": "http://127.0.0.1:6099" if running else "http://127.0.0.1:3000",
            "matched_primary": not running,
            "listening_pids": [5555] if running else [],
        }

    monkeypatch.setattr("modules.external_services.resolve_live_endpoint", fake_resolve)
    monkeypatch.setattr("modules.external_services.subprocess.Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr("modules.external_services.is_port_open", lambda *a, **k: False)
    monkeypatch.setattr("modules.external_services.time.sleep", lambda *_: None)

    result = start_service(
        "napcat",
        {
            "external_services": {
                "napcat": {
                    "autostart_enabled": True,
                    "command": str(executable),
                    "host": "127.0.0.1",
                    "port": 3000,
                    "health_url": "http://127.0.0.1:3000",
                }
            }
        },
        force=True,
    )

    assert result["ok"] is True
    assert result["running"] is True
    assert result["port"] == 6099
    assert result["pid"] == 5555


def test_restart_cleanup_keeps_external_services():
    from core.application import Live2DApplication

    app = Live2DApplication.__new__(Live2DApplication)
    app.logger = None
    app.voice_sensor = None
    app.plugin_manager = None
    app.loop = None
    app.gui_ws_server = None
    app.gui_http_server = None
    app.chat_gateway_server = None
    app.last_summary_date = "2099-01-01"
    app.screen_sensor = None
    app._load_runtime_settings = lambda: {}
    calls = []

    def fake_stop(_runtime=None):
        calls.append("stop")
        return {"ok": True, "results": []}

    import modules.external_services as external_services

    original = external_services.stop_managed_services
    external_services.stop_managed_services = fake_stop
    try:
        app.cleanup(stop_external_services=False)
        assert calls == []
        app.cleanup(stop_external_services=True)
        assert calls == ["stop"]
    finally:
        external_services.stop_managed_services = original
