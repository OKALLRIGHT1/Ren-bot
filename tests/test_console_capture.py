import io
import sys

import core.console_capture as capture


def _reset_capture(monkeypatch):
    monkeypatch.setattr(capture, "_installed_path", None)
    monkeypatch.setattr(capture, "_installed_writer", None)


def test_stdout_and_stderr_share_rotating_writer(tmp_path, monkeypatch):
    _reset_capture(monkeypatch)
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    path = tmp_path / "console.log"

    capture.install_console_capture(
        str(path),
        max_bytes=32,
        backup_count=2,
    )
    installed_stdout = sys.stdout
    installed_stderr = sys.stderr
    installed_stdout.write("a" * 24)
    installed_stderr.write("b" * 24)
    installed_stdout.flush()

    assert installed_stdout._writer is installed_stderr._writer
    assert path.exists()
    assert (tmp_path / "console.log.1").exists()
    assert stdout.getvalue() == "a" * 24
    assert stderr.getvalue() == "b" * 24


def test_install_is_idempotent_for_same_path(tmp_path, monkeypatch):
    _reset_capture(monkeypatch)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    path = tmp_path / "console.log"

    capture.install_console_capture(str(path))
    first_stdout = sys.stdout
    first_writer = sys.stdout._writer
    capture.install_console_capture(str(path))

    assert sys.stdout is first_stdout
    assert sys.stdout._writer is first_writer


def test_log_open_failure_preserves_original_streams(tmp_path, monkeypatch):
    _reset_capture(monkeypatch)
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    class BrokenWriter:
        def __init__(self, *args, **kwargs):
            raise OSError("disk unavailable")

    monkeypatch.setattr(capture, "RotatingTextWriter", BrokenWriter)
    result = capture.install_console_capture(str(tmp_path / "console.log"))

    assert result == (tmp_path / "console.log").resolve()
    assert sys.stdout is stdout
    assert sys.stderr is stderr
