from __future__ import annotations

import launcher
import setup_env


def test_external_process_restores_frozen_dll_search_path(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "_frozen_bundle_dir", lambda: r"C:\Temp\_MEI123")
    monkeypatch.setattr(launcher, "_set_dll_directory", calls.append)

    with launcher._external_process_dll_search():
        assert calls == [None]

    assert calls == [None, r"C:\Temp\_MEI123"]


def test_launcher_skips_start_when_setup_fails(monkeypatch):
    started = []
    monkeypatch.setattr(launcher, "_is_core_already_running", lambda: False)
    monkeypatch.setattr(launcher, "_ensure_runtime", lambda: False)
    monkeypatch.setattr(launcher, "_show_error", lambda msg: None)
    monkeypatch.setattr(launcher, "_start_process", lambda: started.append("start") or (None, "x"))
    assert launcher.main() == 1
    assert started == []


def test_ensure_runtime_skips_install_when_ready(monkeypatch):
    monkeypatch.setattr(setup_env, "is_environment_ready", lambda root=None, probe=False: True)
    copied = []
    monkeypatch.setattr(setup_env, "maybe_copy_env_file", lambda root=None: copied.append(True) or False)

    def boom(**kwargs):
        raise AssertionError("should not install")

    monkeypatch.setattr(setup_env, "ensure_environment", boom)
    assert launcher._ensure_runtime() is True
    assert copied == [True]


def test_ensure_runtime_installs_when_missing(monkeypatch):
    monkeypatch.setattr(setup_env, "is_environment_ready", lambda root=None, probe=False: False)
    monkeypatch.setattr(setup_env, "ensure_environment", lambda root=None, **kwargs: True)
    assert launcher._ensure_runtime() is True


def test_candidate_commands_prefer_ready_python(monkeypatch, tmp_path):
    ready = tmp_path / "ready-python.exe"
    ready.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_env, "ready_python", lambda root=None: str(ready))
    monkeypatch.setattr(setup_env, "venv_python", lambda root=None: tmp_path / "missing.exe")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    commands = launcher._candidate_commands()
    assert commands[0][0] == str(ready)
    assert commands[0][1] == str(launcher.APP_ENTRY)

def test_is_backend_ready_uses_runtime_settings(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings_file = data_dir / "runtime_settings.json"
    settings_file.write_text('{"gui_http_port": 12345, "gui_http_prefix": "/custom"}', encoding="utf-8")

    polled_urls = []

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class FakeOpener:
        def open(self, url, timeout=0.3):
            polled_urls.append(url)
            if "12345" in url:
                return FakeResp()
            raise Exception("not ready")

    import urllib.request

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: FakeOpener())
    assert launcher._is_backend_ready(tmp_path) is True
    assert "http://127.0.0.1:12345/custom/health" in polled_urls

