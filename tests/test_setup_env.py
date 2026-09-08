from __future__ import annotations

import json
from pathlib import Path

import setup_env


def _write_requirements(root: Path, text: str = "PySide6\n") -> None:
    (root / "requirements.txt").write_text(text, encoding="utf-8")


def test_parse_version_tuple():
    assert setup_env.parse_version_tuple("3 12") == (3, 12)
    assert setup_env.parse_version_tuple("3.10") == (3, 10)
    assert setup_env.parse_version_tuple("nope") is None


def test_windows_store_stub_detection():
    assert setup_env._is_windows_store_stub(
        r"C:\Users\a\AppData\Local\Microsoft\WindowsApps\python.exe"
    )
    assert not setup_env._is_windows_store_stub(
        r"C:\Users\a\AppData\Local\Programs\Python\Python312\python.exe"
    )


def test_requirements_digest_changes_with_file(tmp_path: Path):
    _write_requirements(tmp_path, "aaa\n")
    first = setup_env.requirements_digest(tmp_path)
    _write_requirements(tmp_path, "bbb\n")
    second = setup_env.requirements_digest(tmp_path)
    assert first != second
    assert len(first) == 64


def test_maybe_copy_env_file_copies_once(tmp_path: Path):
    (tmp_path / ".env.example").write_text("KEY=\n", encoding="utf-8")
    assert setup_env.maybe_copy_env_file(tmp_path) is True
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEY=\n"
    (tmp_path / ".env").write_text("KEY=secret\n", encoding="utf-8")
    assert setup_env.maybe_copy_env_file(tmp_path) is False
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEY=secret\n"


def test_environment_ready_requires_matching_marker(tmp_path: Path, monkeypatch):
    _write_requirements(tmp_path, "PySide6\n")
    python_exe = tmp_path / "python.exe"
    python_exe.write_text("", encoding="utf-8")
    assert setup_env.is_environment_ready(tmp_path) is False

    monkeypatch.setattr(setup_env, "_python_identity", lambda cmd: (str(python_exe), (3, 12)))
    setup_env.write_marker(str(python_exe), "venv", tmp_path)
    assert setup_env.is_environment_ready(tmp_path) is True

    _write_requirements(tmp_path, "PySide6\nnumpy\n")
    assert setup_env.is_environment_ready(tmp_path) is False


def test_is_environment_ready_probes_when_requested(tmp_path: Path, monkeypatch):
    _write_requirements(tmp_path)
    python_exe = tmp_path / "python.exe"
    python_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_env, "_python_identity", lambda cmd: (str(python_exe), (3, 12)))
    setup_env.write_marker(str(python_exe), "venv", tmp_path)
    monkeypatch.setattr(setup_env, "probe_python", lambda exe: (False, "missing: PySide6"))
    assert setup_env.is_environment_ready(tmp_path) is True
    assert setup_env.is_environment_ready(tmp_path, probe=True) is False


def test_pip_install_commands_include_mirrors(tmp_path: Path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("PySide6\n", encoding="utf-8")
    commands = setup_env.pip_install_commands("python", requirements)
    assert commands[0][-1] == str(requirements)
    assert "-i" not in commands[0]
    assert any("tuna.tsinghua.edu.cn" in " ".join(cmd) for cmd in commands)
    assert any("aliyun.com" in " ".join(cmd) for cmd in commands)


def test_winget_install_command_uses_user_scope():
    cmd = setup_env.winget_install_command()
    assert cmd[:5] == ["winget", "install", "-e", "--id", setup_env.WINGET_PYTHON_ID]
    assert "--scope" in cmd and "user" in cmd
    assert "--accept-package-agreements" in cmd


def test_ensure_environment_short_circuits_when_ready(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(setup_env, "is_environment_ready", lambda root=None, probe=False: True)
    copied = []
    monkeypatch.setattr(setup_env, "maybe_copy_env_file", lambda root=None: copied.append(root) or False)
    monkeypatch.setattr(
        setup_env,
        "select_install_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not install")),
    )
    assert setup_env.ensure_environment(tmp_path, ui="silent") is True
    assert copied == [tmp_path]


def test_ensure_environment_creates_venv_and_marker(tmp_path: Path, monkeypatch):
    _write_requirements(tmp_path)
    (tmp_path / ".env.example").write_text("KEY=\n", encoding="utf-8")
    host = tmp_path / "host" / "python.exe"
    host.parent.mkdir()
    host.write_text("", encoding="utf-8")
    venv_py = setup_env.venv_python(tmp_path)

    monkeypatch.setattr(setup_env, "find_host_python", lambda: str(host))
    monkeypatch.setattr(setup_env, "conda_env_python", lambda env_name=setup_env.CONDA_ENV: None)
    monkeypatch.setattr(setup_env, "_python_identity", lambda cmd: (cmd[0], (3, 12)))

    def fake_run(command, log, **kwargs):
        if "-m" in command and "venv" in command:
            venv_py.parent.mkdir(parents=True, exist_ok=True)
            venv_py.write_text("", encoding="utf-8")
        return 0

    monkeypatch.setattr(setup_env, "_run_logged", fake_run)
    monkeypatch.setattr(setup_env, "probe_python", lambda exe: (True, "probe ok"))

    logs: list[str] = []
    assert setup_env.ensure_environment(tmp_path, ui="silent", log=logs.append) is True
    marker = json.loads((tmp_path / ".suzu-ready").read_text(encoding="utf-8"))
    assert marker["kind"] == "venv"
    assert Path(marker["python_executable"]) == venv_py
    assert (tmp_path / ".env").exists()
    assert any("venv" in line.lower() or "虚拟环境" in line for line in logs)


def test_ensure_environment_prefers_existing_conda(tmp_path: Path, monkeypatch):
    _write_requirements(tmp_path)
    conda_py = tmp_path / "conda" / "python.exe"
    conda_py.parent.mkdir()
    conda_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_env, "conda_env_python", lambda env_name=setup_env.CONDA_ENV: str(conda_py))
    monkeypatch.setattr(setup_env, "find_host_python", lambda: (_ for _ in ()).throw(AssertionError("host")))
    monkeypatch.setattr(setup_env, "_python_identity", lambda cmd: (cmd[0], (3, 10)))
    monkeypatch.setattr(setup_env, "_run_logged", lambda *args, **kwargs: 0)
    monkeypatch.setattr(setup_env, "probe_python", lambda exe: (True, "ok"))

    assert setup_env.ensure_environment(tmp_path, ui="silent") is True
    marker = json.loads((tmp_path / ".suzu-ready").read_text(encoding="utf-8"))
    assert marker["kind"] == "conda"
    assert Path(marker["python_executable"]) == conda_py


def test_ensure_environment_fails_without_python(tmp_path: Path, monkeypatch):
    _write_requirements(tmp_path)
    monkeypatch.setattr(setup_env, "conda_env_python", lambda env_name=setup_env.CONDA_ENV: None)
    monkeypatch.setattr(setup_env, "find_host_python", lambda: None)
    monkeypatch.setattr(setup_env, "try_install_python_windows", lambda log: None)
    assert setup_env.ensure_environment(tmp_path, ui="silent") is False


def test_check_cli_uses_probe(monkeypatch):
    monkeypatch.setattr(setup_env, "is_environment_ready", lambda root=None, probe=False: probe)
    assert setup_env.main(["--check"]) == 0


def test_windows_helper_scripts_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "setup_env.py").is_file()
    assert (root / "setup_env.cmd").is_file()
    assert (root / "安装环境.bat").is_file()
    assert (root / "启动.bat").is_file()
    assert (root / "启动-无窗口.vbs").is_file()
