from __future__ import annotations

import atexit
import ctypes
import hashlib
import os
from pathlib import Path
from typing import Optional


def make_lock_name(root: str | os.PathLike[str], role: str) -> str:
    normalized_root = str(Path(root).resolve()).lower()
    normalized_role = "".join(
        ch if ch.isalnum() else "_" for ch in str(role or "main")
    ).strip("_")
    digest = hashlib.sha1(normalized_root.encode("utf-8")).hexdigest()
    return f"Local\\Live2D_Suzu_{normalized_role}_{digest}"


class SingleInstanceLock:
    def __init__(self, root: str | os.PathLike[str], role: str):
        self.root = Path(root).resolve()
        self.role = str(role or "main")
        self._handle = None
        self._fallback: Optional[FileSingleInstanceLock] = None

    def acquire(self) -> bool:
        if os.name != "nt":
            self._fallback = FileSingleInstanceLock(self.root, self.role)
            return self._fallback.acquire()

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        )
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.GetLastError.argtypes = ()
        kernel32.GetLastError.restype = ctypes.c_ulong

        handle = kernel32.CreateMutexW(None, True, make_lock_name(self.root, self.role))
        last_error = kernel32.GetLastError()
        if not handle:
            return False
        if last_error == 183:
            kernel32.CloseHandle(handle)
            return False
        self._handle = (kernel32, handle)
        atexit.register(self.release)
        return True

    def release(self) -> None:
        if self._fallback is not None:
            self._fallback.release()
            self._fallback = None
            return
        if self._handle is None:
            return
        kernel32, handle = self._handle
        self._handle = None
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass


class FileSingleInstanceLock:
    def __init__(self, root: str | os.PathLike[str], role: str):
        self.root = Path(root).resolve()
        self.role = str(role or "main")
        self.path = self.root / "data" / f"live2d_suzu_{self.role}.lock"
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            return False

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        self._file = lock_file
        atexit.register(self.release)
        return True

    def release(self) -> None:
        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass
