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
