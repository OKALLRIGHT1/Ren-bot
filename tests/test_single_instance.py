from pathlib import Path

from main import (
    CORE_ALREADY_RUNNING_EXIT_CODE,
    RESTART_EXIT_CODE,
    classify_worker_exit,
)
from core.single_instance import FileSingleInstanceLock, make_lock_name


def test_lock_name_is_stable_for_same_workspace(tmp_path: Path):
    first = make_lock_name(tmp_path, "core")
    second = make_lock_name(tmp_path, "core")

    assert first == second
    assert "Live2D_Suzu_core_" in first


def test_file_single_instance_lock_rejects_second_holder(tmp_path: Path):
    first = FileSingleInstanceLock(tmp_path, "core")
    second = FileSingleInstanceLock(tmp_path, "core")

    assert first.acquire()
    assert not second.acquire()

    first.release()
    assert second.acquire()
    second.release()


def test_watchdog_waits_when_core_is_already_running():
    assert classify_worker_exit(0) == "stop"
    assert classify_worker_exit(RESTART_EXIT_CODE) == "restart"
    assert classify_worker_exit(CORE_ALREADY_RUNNING_EXIT_CODE) == "wait_for_core"
    assert classify_worker_exit(1) == "recover"
