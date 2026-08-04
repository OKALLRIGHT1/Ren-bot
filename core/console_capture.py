from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional, TextIO


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


class RotatingTextWriter:
    """Thread-safe size-based text writer shared by stdout and stderr."""

    def __init__(self, path: Path, *, max_bytes: int, backup_count: int):
        self.path = path
        self.max_bytes = max(1, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.RLock()
        self._file = path.open("a", encoding="utf-8", buffering=1)

    def _rotate(self) -> None:
        self._file.close()
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                os.replace(source, target)
        if self.path.exists():
            os.replace(
                self.path,
                self.path.with_name(f"{self.path.name}.1"),
            )
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def write(self, text: str) -> int:
        payload = str(text)
        with self._lock:
            self._file.seek(0, os.SEEK_END)
            current_size = self._file.tell()
            incoming_size = len(payload.encode("utf-8", errors="replace"))
            if current_size > 0 and current_size + incoming_size > self.max_bytes:
                self._rotate()
            self._file.write(payload)
        return len(payload)

    def flush(self) -> None:
        with self._lock:
            self._file.flush()


class TeeStream:
    """Mirror stdout/stderr to a shared writer and preserve the original stream."""

    def __init__(self, original: Optional[TextIO], writer: RotatingTextWriter):
        self._original = original
        self._writer = writer
        self.encoding = getattr(original, "encoding", "utf-8") or "utf-8"
        self.errors = getattr(original, "errors", "replace") or "replace"

    def write(self, text):
        payload = "" if text is None else str(text)
        if self._original is not None:
            try:
                self._original.write(payload)
            except Exception:
                pass
        try:
            self._writer.write(payload)
        except Exception:
            pass
        return len(payload)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass
        try:
            self._writer.flush()
        except Exception:
            pass

    def isatty(self):
        if self._original is None:
            return False
        try:
            return bool(self._original.isatty())
        except Exception:
            return False

    def fileno(self):
        if self._original is None:
            raise OSError("stdout/stderr stream has no file descriptor")
        return self._original.fileno()

    def close(self):
        try:
            self._writer.flush()
        except Exception:
            pass

    @property
    def closed(self):
        return False

    def __getattr__(self, name):
        if self._original is None:
            raise AttributeError(name)
        return getattr(self._original, name)


_installed_path: Optional[Path] = None
_installed_writer: Optional[RotatingTextWriter] = None


def install_console_capture(
    log_path: str = "./logs/console.log",
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    """Capture later console output without allowing log failures to block startup."""

    global _installed_path, _installed_writer
    path = Path(log_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    if _installed_path == path and _installed_writer is not None:
        return path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = RotatingTextWriter(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
    except Exception:
        return path

    sys.stdout = TeeStream(sys.stdout, writer)
    sys.stderr = TeeStream(sys.stderr, writer)
    _installed_writer = writer
    _installed_path = path
    return path
