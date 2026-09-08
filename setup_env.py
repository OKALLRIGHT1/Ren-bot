from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable


MIN_PYTHON = (3, 10)
PREFERRED_VERSIONS = ((3, 12), (3, 11), (3, 10), (3, 13), (3, 14))
CONDA_ENV = os.environ.get("LIVE2D_CONDA_ENV", "live2d-llm")
WINGET_PYTHON_ID = "Python.Python.3.12"
PYTHON_DOWNLOAD_URL = "https://www.python.org/downloads/"
MARKER_NAME = ".suzu-ready"
PIP_INDEX_FALLBACKS = (
    "",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
)
PROBE_MODULES = (
    "PySide6",
    "dotenv",
    "chromadb",
    "jieba",
    "sounddevice",
    "sherpa_onnx",
    "numpy",
    "PIL",
    "aiohttp",
    "requests",
    "openai",
    "cv2",
    "pypinyin",
    "psutil",
    "websockets",
)
LogFn = Callable[[str], None]


def _resolve_root() -> Path:
    env_root = os.environ.get("LIVE2D_SUZU_ROOT")
    candidates = []
    if env_root:
        candidates.append(Path(env_root))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parent)
    for candidate in candidates:
        try:
            root = candidate.resolve()
        except Exception:
            root = candidate
        if (root / "main.py").exists() and (root / "requirements.txt").exists():
            return root
        parent = root.parent
        if (parent / "main.py").exists() and (parent / "requirements.txt").exists():
            return parent
    return Path.cwd().resolve()


ROOT = _resolve_root()
LOG_PATH = ROOT / "logs" / "setup_env.log"


def marker_path(root: Path | None = None) -> Path:
    return (root or ROOT) / MARKER_NAME


def requirements_file(root: Path | None = None) -> Path:
    return (root or ROOT) / "requirements.txt"


def requirements_digest(root: Path | None = None) -> str:
    path = requirements_file(root)
    data = path.read_bytes() if path.exists() else b""
    return hashlib.sha256(data).hexdigest()


def venv_python(root: Path | None = None) -> Path:
    root = root or ROOT
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def venv_pythonw(root: Path | None = None) -> Path:
    root = root or ROOT
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "pythonw.exe"
    return venv_python(root)


def log_path(root: Path | None = None) -> Path:
    return (root or ROOT) / "logs" / "setup_env.log"


def _stamp(message: str) -> str:
    return f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message.rstrip()}"


def make_logger(root: Path | None = None, sink: LogFn | None = None) -> LogFn:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _log(message: str) -> None:
        line = message.rstrip()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(_stamp(line) + "\n")
        except Exception:
            pass
        if sink is not None:
            try:
                sink(line)
                return
            except Exception:
                pass
        try:
            print(line, flush=True)
        except Exception:
            pass

    return _log


def _is_windows_store_stub(path: str) -> bool:
    normalized = path.replace("/", "\\").lower()
    return "\\windowsapps\\" in normalized


def _looks_like_python(path: str) -> bool:
    name = Path(path).name.lower()
    return name in {"python.exe", "python", "python3", "python3.exe"}


def parse_version_tuple(text: str) -> tuple[int, int] | None:
    parts = text.strip().replace(",", " ").split()
    if len(parts) < 2:
        bits = text.strip().split(".")
        if len(bits) >= 2 and bits[0].isdigit() and bits[1].isdigit():
            return int(bits[0]), int(bits[1])
        return None
    if parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]), int(parts[1])
    return None


def _python_identity(command: list[str]) -> tuple[str, tuple[int, int]] | None:
    try:
        result = subprocess.run(
            command
            + [
                "-c",
                "import sys; print(sys.executable); print(sys.version_info[0], sys.version_info[1])",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    executable = lines[-2]
    version = parse_version_tuple(lines[-1])
    if not executable or version is None:
        return None
    if _is_windows_store_stub(executable):
        return None
    if version < MIN_PYTHON:
        return None
    return executable, version


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _visible_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def _windows_python_dirs() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    names = [
        "Python312",
        "Python311",
        "Python310",
        "Python313",
        "Python314",
    ]
    dirs: list[Path] = []
    for name in names:
        dirs.append(local / name)
        dirs.append(program_files / name)
        dirs.append(program_files_x86 / name)
    return dirs


def _candidate_host_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    if not getattr(sys, "frozen", False) and _looks_like_python(sys.executable):
        commands.append([sys.executable])
    if os.name == "nt":
        py_launcher = shutil.which("py")
        if py_launcher and not _is_windows_store_stub(py_launcher):
            for major, minor in PREFERRED_VERSIONS:
                commands.append([py_launcher, f"-{major}.{minor}"])
            commands.append([py_launcher, "-3"])
        for folder in _windows_python_dirs():
            exe = folder / "python.exe"
            if exe.exists():
                commands.append([str(exe)])
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and not _is_windows_store_stub(found):
            commands.append([found])
    return commands


def _rank_version(version: tuple[int, int]) -> tuple[int, int, int]:
    try:
        preferred = PREFERRED_VERSIONS.index(version)
    except ValueError:
        preferred = len(PREFERRED_VERSIONS)
    return (preferred, -version[0], -version[1])


def find_host_python() -> str | None:
    found: list[tuple[str, tuple[int, int]]] = []
    seen: set[str] = set()
    for command in _candidate_host_commands():
        identity = _python_identity(command)
        if identity is None:
            continue
        executable, version = identity
        key = os.path.normcase(os.path.abspath(executable))
        if key in seen:
            continue
        seen.add(key)
        found.append((executable, version))
    if not found:
        return None
    found.sort(key=lambda item: _rank_version(item[1]))
    return found[0][0]


def conda_executable() -> str | None:
    found = shutil.which("conda")
    if found and not _is_windows_store_stub(found):
        return found
    return None


def conda_env_python(env_name: str = CONDA_ENV) -> str | None:
    conda = conda_executable()
    if conda is None:
        return None
    identity = _python_identity([conda, "run", "-n", env_name, "python"])
    if identity is None:
        return None
    return identity[0]


def read_marker(root: Path | None = None) -> dict | None:
    path = marker_path(root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_marker(python_exe: str, kind: str, root: Path | None = None) -> None:
    root = root or ROOT
    identity = _python_identity([python_exe])
    version = ".".join(str(part) for part in identity[1]) if identity else ""
    payload = {
        "requirements_sha256": requirements_digest(root),
        "python_executable": os.path.abspath(python_exe),
        "python_version": version,
        "kind": kind,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = marker_path(root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe_command(python_exe: str) -> list[str]:
    module_list = ", ".join(PROBE_MODULES)
    return [
        python_exe,
        "-c",
        "import importlib, sys\n"
        f"mods = {PROBE_MODULES!r}\n"
        "missing = []\n"
        "for name in mods:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception:\n"
        "        missing.append(name)\n"
        "if missing:\n"
        "    sys.stderr.write('missing: ' + ', '.join(missing) + '\\n')\n"
        "    raise SystemExit(1)\n"
        f"print('probe ok: {module_list}')\n",
    ]


def probe_python(python_exe: str) -> tuple[bool, str]:
    if not python_exe or not Path(python_exe).exists():
        return False, "python 不存在"
    try:
        result = subprocess.run(
            probe_command(python_exe),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, (result.stdout or "").strip()
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"exit {result.returncode}"


def is_environment_ready(root: Path | None = None, *, probe: bool = False) -> bool:
    root = root or ROOT
    marker = read_marker(root)
    if not marker:
        return False
    if marker.get("requirements_sha256") != requirements_digest(root):
        return False
    python_exe = str(marker.get("python_executable") or "")
    if not python_exe or not Path(python_exe).exists():
        return False
    if not probe:
        return True
    ok, _detail = probe_python(python_exe)
    return ok


def maybe_copy_env_file(root: Path | None = None) -> bool:
    root = root or ROOT
    example = root / ".env.example"
    target = root / ".env"
    if target.exists() or not example.exists():
        return False
    shutil.copyfile(example, target)
    return True


def _run_logged(
    command: list[str],
    log: LogFn,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    visible: bool = False,
) -> int:
    log("$ " + " ".join(command))
    flags = _visible_creationflags() if visible else _creationflags()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
    except OSError as exc:
        log(f"无法启动进程: {exc}")
        return 1
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if text:
            log(text)
    return int(proc.wait())


def pip_install_commands(python_exe: str, requirements: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    for index in PIP_INDEX_FALLBACKS:
        cmd = [
            python_exe,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ]
        if index:
            cmd.extend(["-i", index, "--trusted-host", index.split("/")[2]])
        commands.append(cmd)
    return commands


def _pip_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    return env


def install_requirements(python_exe: str, root: Path, log: LogFn) -> bool:
    requirements = requirements_file(root)
    if not requirements.exists():
        log(f"找不到 {requirements}")
        return False
    upgrade_pip = [
        python_exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
        "pip",
        "setuptools",
        "wheel",
    ]
    if _run_logged(upgrade_pip, log, cwd=root, env=_pip_env()) != 0:
        log("升级 pip 失败，继续尝试安装依赖。")
    for command in pip_install_commands(python_exe, requirements):
        if _run_logged(command, log, cwd=root, env=_pip_env()) == 0:
            return True
        log("当前软件源安装失败，尝试下一个镜像。")
    return False


def create_venv(host_python: str, root: Path, log: LogFn) -> str | None:
    python_path = venv_python(root)
    venv_dir = root / ".venv"
    if python_path.exists():
        identity = _python_identity([str(python_path)])
        if identity is not None:
            return str(python_path)
        log("本地 .venv 不可用，将重新创建。")
        try:
            shutil.rmtree(venv_dir)
        except OSError as exc:
            log(f"删除旧 .venv 失败: {exc}")
            return None
    elif venv_dir.exists():
        log("本地 .venv 不完整，将重新创建。")
        try:
            shutil.rmtree(venv_dir)
        except OSError as exc:
            log(f"删除旧 .venv 失败: {exc}")
            return None
    log(f"正在创建虚拟环境: {venv_dir}")
    code = _run_logged([host_python, "-m", "venv", str(venv_dir)], log, cwd=root)
    if code != 0 or not python_path.exists():
        log("创建 .venv 失败。")
        return None
    return str(python_path)


def select_install_target(root: Path, log: LogFn) -> tuple[str, str] | None:
    python_path = venv_python(root)
    if python_path.exists() and _python_identity([str(python_path)]) is not None:
        log(f"使用已有虚拟环境: {python_path}")
        return "venv", str(python_path)
    conda_python = conda_env_python()
    if conda_python:
        log(f"使用已有 Conda 环境 {CONDA_ENV}: {conda_python}")
        return "conda", conda_python
    host = find_host_python()
    if host is None:
        log("未找到 Python 3.10 或更高版本。")
        return None
    log(f"使用宿主 Python 创建环境: {host}")
    created = create_venv(host, root, log)
    if created is None:
        return None
    return "venv", created


def winget_install_command() -> list[str]:
    return [
        "winget",
        "install",
        "-e",
        "--id",
        WINGET_PYTHON_ID,
        "--scope",
        "user",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]


def _prepend_windows_python_paths() -> None:
    extra: list[str] = []
    launcher = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Launcher"
    extra.append(str(launcher))
    for folder in _windows_python_dirs():
        extra.append(str(folder))
        extra.append(str(folder / "Scripts"))
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([item for item in extra if item] + [current])


def try_install_python_windows(log: LogFn) -> str | None:
    if os.name != "nt":
        return None
    if shutil.which("winget") is None:
        log("未找到 winget，无法自动安装 Python。")
        return None
    log(f"正在通过 winget 安装 {WINGET_PYTHON_ID} …")
    code = _run_logged(winget_install_command(), log, visible=True)
    _prepend_windows_python_paths()
    host = find_host_python()
    if host:
        log(f"Python 安装完成: {host}")
        return host
    if code != 0:
        log("winget 安装 Python 失败。")
    else:
        log("Python 可能已安装，但当前进程还读不到 PATH。请关闭窗口后重新运行。")
    return None


def _python_missing_message() -> str:
    return (
        "未找到 Python 3.10 或更高版本。\n\n"
        f"请安装 Python 并勾选 Add python.exe to PATH：\n{PYTHON_DOWNLOAD_URL}\n\n"
        "安装完成后重新双击「安装环境.bat」或「启动.bat」。"
    )


def ensure_environment(
    root: Path | None = None,
    *,
    ui: str | None = None,
    force: bool = False,
    log: LogFn | None = None,
) -> bool:
    root = root or ROOT
    ui = ui or default_ui()
    if ui == "gui":
        return _ensure_with_gui(root, force=force)
    if log is None:
        log = make_logger(root, sink=(lambda _msg: None) if ui == "silent" else None)
    return _ensure_body(root, force=force, log=log)


def default_ui() -> str:
    forced = os.environ.get("LIVE2D_SETUP_UI", "").strip().lower()
    if forced in {"gui", "console", "silent"}:
        return forced
    stdout = getattr(sys, "stdout", None)
    try:
        if stdout is not None and stdout.isatty():
            return "console"
    except Exception:
        pass
    return "gui" if os.name == "nt" else "console"


def _ensure_body(root: Path, force: bool, log: LogFn) -> bool:
    log(f"项目目录: {root}")
    if not force and is_environment_ready(root):
        copied = maybe_copy_env_file(root)
        if copied:
            log("已从 .env.example 创建 .env，请填入 API Key。")
        log("运行环境已就绪。")
        return True

    target = select_install_target(root, log)
    if target is None and os.name == "nt":
        if try_install_python_windows(log):
            target = select_install_target(root, log)
    if target is None:
        log(_python_missing_message())
        return False

    kind, python_exe = target
    log(f"开始安装依赖到 {python_exe}")
    if not install_requirements(python_exe, root, log):
        log("依赖安装失败。")
        return False
    ok, detail = probe_python(python_exe)
    if not ok:
        log(f"依赖安装后仍缺模块: {detail}")
        return False
    write_marker(python_exe, kind, root)
    copied = maybe_copy_env_file(root)
    if copied:
        log("已从 .env.example 创建 .env，请填入 API Key。")
    log("运行环境安装完成。")
    return True


def _ensure_with_gui(root: Path, force: bool) -> bool:
    try:
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception:
        return _ensure_body(root, force, make_logger(root))

    result = {"ok": False}
    messages: queue.Queue[str] = queue.Queue()

    def sink(message: str) -> None:
        messages.put(message)

    log = make_logger(root, sink=sink)

    window = tk.Tk()
    window.title("Live2D-Suzu 环境安装")
    window.geometry("560x320")
    window.resizable(False, False)
    try:
        window.eval("tk::PlaceWindow . center")
    except Exception:
        pass

    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    title = ttk.Label(frame, text="正在准备运行环境，首次安装可能需要几分钟。")
    title.pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate")
    bar.pack(fill="x", pady=(8, 8))
    bar.start(12)
    output = ScrolledText(frame, height=12, wrap="word", state="disabled")
    output.pack(fill="both", expand=True)

    def append(text: str) -> None:
        output.configure(state="normal")
        output.insert("end", text + "\n")
        output.see("end")
        output.configure(state="disabled")

    def pump() -> None:
        while True:
            try:
                line = messages.get_nowait()
            except queue.Empty:
                break
            append(line)
        if worker.is_alive():
            window.after(120, pump)
        else:
            bar.stop()
            if result["ok"]:
                title.configure(text="环境已就绪。")
                window.after(600, window.destroy)
            else:
                title.configure(text="环境安装失败，请查看 logs\\setup_env.log")

    def work() -> None:
        result["ok"] = _ensure_body(root, force, log)
        messages.put("完成。" if result["ok"] else "失败。")

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    window.after(120, pump)
    window.protocol("WM_DELETE_WINDOW", window.destroy)
    window.mainloop()
    return bool(result["ok"])


def ready_python(root: Path | None = None) -> str | None:
    marker = read_marker(root)
    if not marker:
        return None
    python_exe = str(marker.get("python_executable") or "")
    if python_exe and Path(python_exe).exists():
        return python_exe
    return None


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the Live2D-Suzu Python environment.")
    parser.add_argument("--ensure", action="store_true", help="Install only if the environment is not ready.")
    parser.add_argument("--check", action="store_true", help="Exit 0 if ready, 1 otherwise.")
    parser.add_argument("--start", action="store_true", help="Ensure the environment, then start the app.")
    parser.add_argument("--force", action="store_true", help="Reinstall dependencies even if ready.")
    parser.add_argument("--gui", action="store_true", help="Show a progress window.")
    parser.add_argument("--console", action="store_true", help="Write progress to the console.")
    parser.add_argument("--silent", action="store_true", help="Write progress only to the log file.")
    return parser.parse_args(list(argv) if argv is not None else None)


def _ui_from_args(args: argparse.Namespace) -> str:
    if args.gui:
        return "gui"
    if args.console:
        return "console"
    if args.silent:
        return "silent"
    return default_ui()


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    root = ROOT
    if args.check:
        return 0 if is_environment_ready(root, probe=True) else 1
    ui = _ui_from_args(args)
    ok = ensure_environment(root, ui=ui, force=args.force)
    if not ok:
        return 1
    if args.start:
        import launcher

        return int(launcher.main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
