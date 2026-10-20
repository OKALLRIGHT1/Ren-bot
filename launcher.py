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
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _prune_launcher_log()
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        debug_log = Path(os.environ.get("TEMP", ".")) / "suzu_launcher_debug.log"
        with debug_log.open("a", encoding="utf-8") as f:
            f.write(line)
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

    # 优先检测自带便携运行时 (runtime / python_env / python)
    for sub in ("runtime", "python_env", "python"):
        p = ROOT / sub
        for cand in (
            p / "pythonw.exe",
            p / "python.exe",
            p / "Scripts" / "pythonw.exe",
            p / "Scripts" / "python.exe",
        ):
            if cand.is_file():
                add_python(str(cand))

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

            ctypes.windll.user32.MessageBoxW(None, message, "Ren-bot", 0x10)
            return
        except Exception:
            pass


def _start_process() -> tuple[subprocess.Popen | None, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["GUI_BACKEND"] = "qt"
    env["SUZU_AUTOSTART_LIVE2D"] = "1"

    for sub in ("runtime", "python_env", "python"):
        p = ROOT / sub
        if p.is_dir():
            lib_bin = p / "Library" / "bin"
            scripts = p / "Scripts"
            extra = [str(p)]
            if lib_bin.is_dir():
                extra.append(str(lib_bin))
            if scripts.is_dir():
                extra.append(str(scripts))
            env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
            pyside_plugins = p / "Lib" / "site-packages" / "PySide6" / "plugins"
            if pyside_plugins.is_dir():
                env.setdefault("QT_PLUGIN_PATH", str(pyside_plugins))
            break

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


def _monitor_startup(proc: subprocess.Popen) -> None:
    started_at = time.time()
    while time.time() - started_at < 3.0:
        code = proc.poll()
        if code is not None and code != 0:
            _show_error(
                f"启动失败，退出码：{code}\n\n"
                f"可双击「安装环境.bat」修复依赖。\n"
                f"启动日志：{LOG_PATH}\n"
                f"安装日志：{setup_env.log_path(ROOT)}"
            )
            return
        time.sleep(0.2)


def _run_splash(proc: subprocess.Popen) -> None:
    _log("entering _run_splash")
    try:
        import tkinter as tk
        from tkinter import ttk
        _log("tkinter and ttk imported successfully")
    except Exception as exc:
        _log(f"tkinter import failed: {exc}")
        _monitor_startup(proc)
        return

    try:
        root = tk.Tk()
        _log("tk.Tk() instance created successfully")
    except Exception as exc:
        _log(f"tk.Tk() init failed: {exc}")
        _monitor_startup(proc)
        return

    app_title = "Live2D-Suzu"
    root.title(app_title)
    root.resizable(False, False)
    width, height = 380, 130
    try:
        ws = root.winfo_screenwidth()
        hs = root.winfo_screenheight()
        x = max(0, (ws - width) // 2)
        y = max(0, (hs - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        root.geometry(f"{width}x{height}")

    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.lift()
        root.focus_force()
    except Exception:
        pass

    icon = ROOT / "assets" / "icon.ico"
    if icon.is_file():
        try:
            root.iconbitmap(str(icon))
        except Exception:
            pass

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, text=f"正在启动 {app_title}…")
    label.pack(anchor="w")
    hint = ttk.Label(frame, text="首次启动或环境加载较慢时，请稍候。")
    hint.pack(anchor="w", pady=(6, 8))
    bar = ttk.Progressbar(frame, mode="indeterminate", length=340)
    bar.pack(fill="x")
    bar.start(12)

    started_at = time.time()

    def _is_backend_ready() -> bool:
        try:
            import urllib.request

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:8097/gui/health", timeout=0.3) as resp:
                if 200 <= int(resp.status) < 300:
                    return True
        except Exception:
            pass
        return False

    def tick():
        code = proc.poll()
        elapsed = time.time() - started_at
        if code is not None and code != 0:
            _log(f"child process exited with code {code}")
            root.destroy()
            _show_error(
                f"{app_title} 启动失败，退出码：{code}\n\n"
                f"可双击「安装环境.bat」修复依赖。\n"
                f"启动日志：{LOG_PATH}\n"
                f"安装日志：{setup_env.log_path(ROOT)}"
            )
            return
        # 后端健康检查就绪且展示满至少 2.0 秒，让用户能看清进度过渡
        if elapsed >= 2.0 and _is_backend_ready():
            _log(f"backend ready after {elapsed:.2f}s, closing splash")
            root.destroy()
            return
        if elapsed >= 8.0:
            label.configure(text="启动指令已发出，主窗口仍在加载中。")
            hint.configure(text="如果没有出现窗口，请查看 logs\\launcher.log。")
        if elapsed >= 15.0:
            _log(f"splash timeout after {elapsed:.2f}s, closing splash")
            root.destroy()
            return
        root.after(200, tick)

    root.after(200, tick)
    root.mainloop()


def _unpack_bundled_runtime() -> bool:
    zip_path = ROOT / "runtime.zip"
    if not zip_path.is_file():
        return False
    _log("found runtime.zip, unpacking...")
    try:
        code = subprocess.call(
            ["tar", "-xf", str(zip_path), "-C", str(ROOT)],
            cwd=str(ROOT),
            startupinfo=_startupinfo(),
            creationflags=_creationflags(),
        )
        if code == 0:
            try:
                zip_path.unlink()
            except Exception:
                pass
            _log("unpacked runtime.zip via tar successfully")
            return True
    except Exception as exc:
        _log(f"tar unpack failed: {exc}, fallback to zipfile")
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(ROOT)
        try:
            zip_path.unlink()
        except Exception:
            pass
        _log("unpacked runtime.zip via zipfile successfully")
        return True
    except Exception as exc:
        _log(f"zipfile unpack failed: {exc}")
        return False


def _ensure_runtime() -> bool:
    _unpack_bundled_runtime()
    for sub in ("runtime", "python_env", "python"):
        p = ROOT / sub
        for cand in (
            p / "pythonw.exe",
            p / "python.exe",
            p / "Scripts" / "pythonw.exe",
            p / "Scripts" / "python.exe",
        ):
            if cand.is_file():
                setup_env.maybe_copy_env_file(ROOT)
                return True

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
    _log(f"launcher main() started. frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
    if not APP_ENTRY.exists():
        msg = f"找不到 boot.py。\n\n当前识别的项目目录：{ROOT}"
        _log(msg)
        _show_error(msg)
        return 1

    def _is_core_already_running() -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes
            import hashlib
            normalized_root = str(ROOT.resolve()).lower()
            digest = hashlib.sha1(normalized_root.encode("utf-8")).hexdigest()
            lock_name = f"Local\\Live2D_Suzu_core_{digest}"
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle.restype = ctypes.c_bool
            kernel32.GetLastError.argtypes = ()
            kernel32.GetLastError.restype = ctypes.c_ulong

            handle = kernel32.CreateMutexW(None, False, lock_name)
            if not handle:
                return False
            last_err = kernel32.GetLastError()
            kernel32.CloseHandle(handle)
            return last_err == 183
        except Exception as exc:
            _log(f"mutex check error: {exc}")
            return False

    if _is_core_already_running():
        _log("an instance of Live2D-Suzu is already running, exiting duplicate launcher")
        _show_error("Live2D-Suzu 已经在运行中。\n\n如需重新启动，请先在任务栏右下角托盘区或任务管理器中关闭正在运行的程序。")
        return 0

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
            f"无法启动 Ren-bot。\n\n{error}\n\n"
            f"可双击「安装环境.bat」修复依赖。\n"
            f"安装日志：{setup_env.log_path(ROOT)}"
        )
        return 1

    _run_splash(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
