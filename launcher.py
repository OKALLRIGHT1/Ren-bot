from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import setup_env

CONDA_ENV = setup_env.CONDA_ENV


def _resolve_root() -> Path:
    candidates: list[Path] = []
    env_root = os.environ.get("LIVE2D_SUZU_ROOT")
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
        if (root / "main.py").exists() and (root / "boot.py").exists():
            return root
        parent = root.parent
        if (parent / "main.py").exists() and (parent / "boot.py").exists():
            return parent
    return Path.cwd().resolve()


ROOT = _resolve_root()
APP_ENTRY = ROOT / "boot.py"
LOG_PATH = ROOT / "logs" / "launcher.log"


def _prune_launcher_log() -> None:
    try:
        if not LOG_PATH.exists() or LOG_PATH.stat().st_size <= 2 * 1024 * 1024:
            return
        with LOG_PATH.open("rb") as fh:
            fh.seek(-(1024 * 1024), os.SEEK_END)
            tail = fh.read()
        LOG_PATH.write_bytes(tail)
    except OSError:
        return


def _log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _prune_launcher_log()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _startupinfo():
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NO_WINDOW


def _frozen_bundle_dir() -> str:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return ""
    return str(getattr(sys, "_MEIPASS", "") or "")


def _set_dll_directory(path: str | None) -> None:
    import ctypes

    if not ctypes.windll.kernel32.SetDllDirectoryW(path):
        raise ctypes.WinError()


@contextmanager
def _external_process_dll_search():
    """Prevent external children from loading DLLs out of PyInstaller's _MEI dir."""
    bundle_dir = _frozen_bundle_dir()
    if not bundle_dir:
        yield
        return
    try:
        _set_dll_directory(None)
    except OSError as exc:
        _log(f"could not clear frozen DLL directory: {exc}")
        yield
        return
    try:
        yield
    finally:
        try:
            _set_dll_directory(bundle_dir)
        except OSError as exc:
            _log(f"could not restore frozen DLL directory: {exc}")


def _candidate_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    seen: set[str] = set()

    def add(cmd: list[str]) -> None:
        key = os.path.normcase(" ".join(cmd))
        if key in seen:
            return
        seen.add(key)
        commands.append(cmd)

    def add_python(python_exe: str) -> None:
        exe = Path(python_exe)
        if os.name == "nt":
            pythonw = exe.with_name("pythonw.exe")
            if pythonw.is_file():
                add([str(pythonw), str(APP_ENTRY)])
        add([str(exe), str(APP_ENTRY)])

    ready = setup_env.ready_python(ROOT)
    if ready:
        add_python(ready)

    local_venv = setup_env.venv_python(ROOT)
    if local_venv.exists():
        add_python(str(local_venv))

    conda_python = setup_env.conda_env_python(CONDA_ENV)
    if conda_python:
        add_python(conda_python)
    else:
        conda_exe = shutil.which("conda")
        if conda_exe:
            add([conda_exe, "run", "-n", CONDA_ENV, "python", str(APP_ENTRY)])

    py_exe = shutil.which("pythonw") or shutil.which("python")
    if py_exe:
        add([py_exe, str(APP_ENTRY)])

    return commands


def _show_error(message: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Live2D-Suzu", 0x10)
            return
        except Exception:
            pass


def _start_process() -> tuple[subprocess.Popen | None, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["GUI_BACKEND"] = "qt"
    env["SUZU_AUTOSTART_LIVE2D"] = "1"

    last_error = ""
    for cmd in _candidate_commands():
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            log_fp = LOG_PATH.open("ab")
            _log(f"try start: {cmd!r} cwd={ROOT}")
            with _external_process_dll_search():
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                    creationflags=_creationflags(),
                    close_fds=True,
                )
            _log(f"started pid={proc.pid}")
            return proc, ""
        except Exception as exc:
            last_error = f"{cmd!r}: {exc}"
            _log(f"start failed: {last_error}")
    return None, last_error or "未找到可用 Python/Conda。"


def _run_splash(proc: subprocess.Popen) -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return

    root = tk.Tk()
    root.title("Live2D-Suzu")
    root.resizable(False, False)
    root.geometry("360x120")
    icon = ROOT / "assets" / "icon.ico"
    if icon.is_file():
        try:
            root.iconbitmap(str(icon))
        except Exception:
            pass
    try:
        root.eval("tk::PlaceWindow . center")
    except Exception:
        pass

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, text="正在启动 Live2D-Suzu…")
    label.pack(anchor="w")
    hint = ttk.Label(frame, text="首次启动或 Conda 环境较慢时，请等一会儿。")
    hint.pack(anchor="w", pady=(6, 8))
    bar = ttk.Progressbar(frame, mode="indeterminate", length=320)
    bar.pack(fill="x")
    bar.start(12)

    started_at = time.time()

    def tick():
        code = proc.poll()
        elapsed = time.time() - started_at
        if code is not None and code != 0:
            root.destroy()
            _show_error(
                f"Live2D-Suzu 启动失败，退出码：{code}\n\n"
                f"可双击「安装环境.bat」修复依赖。\n"
                f"启动日志：{LOG_PATH}\n"
                f"安装日志：{setup_env.log_path(ROOT)}"
            )
            return
        if elapsed >= 8.0:
            label.configure(text="启动指令已发出，主窗口仍在加载中。")
            hint.configure(text="如果没有出现窗口，请查看 logs\\launcher.log。")
        if elapsed >= 10.0:
            root.destroy()
            return
        root.after(250, tick)

    root.after(250, tick)
    root.mainloop()


def _ensure_runtime() -> bool:
    if setup_env.is_environment_ready(ROOT):
        setup_env.maybe_copy_env_file(ROOT)
        return True
    _log("runtime not ready, installing environment")
    setup_script = ROOT / "setup_env.py"
    if getattr(sys, "frozen", False) and setup_script.exists():
        host = setup_env.find_host_python()
        if host:
            _log(f"frozen launcher running {setup_script} with {host}")
            flags = 0
            if os.name == "nt":
                flags = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            code = subprocess.call(
                [host, str(setup_script), "--ensure", "--console"],
                cwd=str(ROOT),
                creationflags=flags,
            )
            return code == 0 and setup_env.is_environment_ready(ROOT)
    return setup_env.ensure_environment(ROOT)


def main() -> int:
    os.chdir(ROOT)
    if not APP_ENTRY.exists():
        msg = f"找不到 boot.py。\n\n当前识别的项目目录：{ROOT}"
        _log(msg)
        _show_error(msg)
        return 1

    if not _ensure_runtime():
        msg = (
            "运行环境未就绪，无法启动。\n\n"
            "请双击「安装环境.bat」，或先安装 Python 3.10+ 并勾选 Add python.exe to PATH。\n\n"
            f"安装日志：{setup_env.log_path(ROOT)}"
        )
        _log(msg)
        _show_error(msg)
        return 1

    proc, error = _start_process()
    if proc is None:
        _show_error(
            f"无法启动 Live2D-Suzu。\n\n{error}\n\n"
            f"可双击「安装环境.bat」修复依赖。\n"
            f"安装日志：{setup_env.log_path(ROOT)}"
        )
        return 1

    _run_splash(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
