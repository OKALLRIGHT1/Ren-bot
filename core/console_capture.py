from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional, TextIO


class TeeStream:
    """Mirror stdout/stderr to a file while preserving the original stream."""

    def __init__(self, original: Optional[TextIO], log_path: Path):
        self._original = original
        self._log_path = log_path
        self._lock = threading.RLock()
        self._file = log_path.open("a", encoding="utf-8", buffering=1)
        self.encoding = getattr(original, "encoding", "utf-8") or "utf-8"
        self.errors = getattr(original, "errors", "replace") or "replace"

    def write(self, text):
        if text is None:
            return 0
        if not isinstance(text, str):
            text = str(text)
        with self._lock:
            if self._original is not None:
                try:
                    self._original.write(text)
                except Exception:
                    pass
            try:
                self._file.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self):
        with self._lock:
            if self._original is not None:
                try:
                    self._original.flush()
                except Exception:
                    pass
            try:
                self._file.flush()
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
            self._file.flush()
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


def install_console_capture(log_path: str = "./logs/console.log") -> Path:
    """Capture later print/stdout/stderr output into a tail-able log file."""

    global _installed_path
    path = Path(log_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path = path.resolve()

    if _installed_path == path:
        return path

    if not isinstance(sys.stdout, TeeStream):
        sys.stdout = TeeStream(sys.stdout, path)
    if not isinstance(sys.stderr, TeeStream):
        sys.stderr = TeeStream(sys.stderr, path)

    _installed_path = path
    return path
