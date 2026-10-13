"""First-launch migration tests (docs/部分重构.md §13/§18).

场景矩阵：
- 注入 SUZU_DATA_DIR + 旧 brain/data 有配置 → 白名单文件与 memory_db 迁移；
- 注入 + 用户目录已有配置 → 不迁移、只写 skipped 标记；
- 注入 + 已迁移过（标记存在）→ 不再重复；
- 注入 + 无旧 data（全新安装）→ no-op；
- 未注入（开发模式）→ maybe_migrate_for_boot 完全 no-op；
- 旧数据永不删除/覆盖（失败也保留）；runtime_settings 迁移前有备份。
"""

from __future__ import annotations

import json
from pathlib import Path

from modules.user_data_migration import (
    MIGRATION_MARKER,
    migrate_legacy_data,
    maybe_migrate_for_boot,
)


def _make_legacy(brain: Path, *, with_settings: bool = True):
    data = brain / "data"
    data.mkdir(parents=True, exist_ok=True)
    if with_settings:
        (data / "runtime_settings.json").write_text(
            json.dumps({"gui_access_token": "tok-1", "ollama_autostart_enabled": True}),
            encoding="utf-8",
        )
    (data / "characters.json").write_text(json.dumps({"n": 1}), encoding="utf-8")
    (data / "custom_models.json").write_text(json.dumps({"m": 1}), encoding="utf-8")
    (data / "console.log").write_text("noise", encoding="utf-8")  # 不迁
    (data / "old.bak").write_text("noise", encoding="utf-8")  # 不迁
    (data / "pycdc").mkdir()  # 开发工具源码，不迁
    (data / "pycdc" / "x.cpp").write_text("", encoding="utf-8")
    (data / "assistant_badges").mkdir()
    (data / "assistant_badges" / "b.json").write_text("{}", encoding="utf-8")
    mem = brain / "memory_db"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "chroma.sqlite3").write_text("db", encoding="utf-8")
    store = brain / "memory"
    store.mkdir(parents=True, exist_ok=True)
    (store / "memory.sqlite").write_text("sqlite", encoding="utf-8")
    (store / "memory.sqlite-wal").write_text("wal", encoding="utf-8")
    return data


def test_full_migration_copies_whitelist_and_memory_db(tmp_path: Path):
    brain = tmp_path / "brain"
    user = tmp_path / "AppData" / "Suzu"
    _make_legacy(brain)

    report = migrate_legacy_data(brain, user)

    assert report is not None
    target = user / "data"
    assert (target / "runtime_settings.json").is_file()
    assert json.loads((target / "runtime_settings.json").read_text(encoding="utf-8"))["gui_access_token"] == "tok-1"
    assert (target / "characters.json").is_file()
    assert (target / "custom_models.json").is_file()
    assert (target / "assistant_badges" / "b.json").is_file()
    assert (user / "memory_db" / "chroma.sqlite3").is_file()
    assert (user / "memory" / "memory.sqlite").is_file()
    assert (user / "memory" / "memory.sqlite-wal").is_file()
    # 噪音不迁
    assert not (target / "console.log").exists()
    assert not (target / "old.bak").exists()
    assert not (target / "pycdc").exists()
    # runtime_settings 有备份
    baks = list((brain / "data").glob("runtime_settings.before-migration-*.bak.json"))
    assert len(baks) == 1
    # 迁移标记存在
    assert (user / MIGRATION_MARKER).is_file()
    # 旧数据未删除
    assert (brain / "data" / "runtime_settings.json").is_file()


def test_second_call_is_noop(tmp_path: Path):
    brain = tmp_path / "brain"
    user = tmp_path / "Suzu"
    _make_legacy(brain)
    first = migrate_legacy_data(brain, user)
    assert first is not None
    # 第二次：标记存在 → 不再动
    (user / "data" / "runtime_settings.json").write_text("touched", encoding="utf-8")
    assert migrate_legacy_data(brain, user) is None
    assert (user / "data" / "runtime_settings.json").read_text(encoding="utf-8") == "touched"


def test_user_data_present_skips(tmp_path: Path):
    brain = tmp_path / "brain"
    user = tmp_path / "Suzu"
    _make_legacy(brain)
    (user / "data").mkdir(parents=True)
    (user / "data" / "runtime_settings.json").write_text("{}", encoding="utf-8")
    assert migrate_legacy_data(brain, user) is None
    assert (user / MIGRATION_MARKER).is_file()  # skipped 标记
    assert not (user / "data" / "characters.json").exists()


def test_fresh_install_no_legacy_is_noop(tmp_path: Path):
    brain = tmp_path / "brain"  # 无 data/
    user = tmp_path / "Suzu"
    assert migrate_legacy_data(brain, user) is None
    assert not (user / MIGRATION_MARKER).exists()


def test_boot_gate_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUZU_DATA_DIR", raising=False)
    _make_legacy(tmp_path / "brain")
    assert maybe_migrate_for_boot(tmp_path / "brain") is None
    # 开发模式绝不创建用户目录或标记
    assert not (tmp_path / "Suzu").exists()


def test_boot_gate_with_env_migrates(tmp_path: Path, monkeypatch):
    user = tmp_path / "AppData" / "Suzu"
    monkeypatch.setenv("SUZU_DATA_DIR", str(user))
    brain = tmp_path / "brain"
    _make_legacy(brain)
    report = maybe_migrate_for_boot(brain)
    assert report is not None
    assert (user / "data" / "runtime_settings.json").is_file()


def test_migration_failure_preserves_legacy(tmp_path: Path, monkeypatch):
    brain = tmp_path / "brain"
    user = tmp_path / "Suzu"
    _make_legacy(brain)

    import modules.user_data_migration as mig

    real_copytree = mig.shutil.copytree

    def flaky_copytree(src, dst, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(mig.shutil, "copytree", flaky_copytree)
    report = migrate_legacy_data(brain, user)
    monkeypatch.setattr(mig.shutil, "copytree", real_copytree)

    # 文件类照常迁移（copy2 不受 copytree 失败影响）
    assert report is not None
    assert "characters.json" in report["files"]
    assert "runtime_settings.json" in report["files"]
    # 目录类失败记入 errors，不抛异常、不阻断
    assert any("assistant_badges" in e for e in report["errors"])
    assert any("memory_db" in e for e in report["errors"])
    assert any("memory/" in e or e.startswith("memory:") for e in report["errors"])
    # 旧数据完整保留（失败不删除源）
    assert (brain / "data" / "characters.json").is_file()
    assert (brain / "memory_db" / "chroma.sqlite3").is_file()
    assert (brain / "memory" / "memory.sqlite").is_file()
    # 已成功的文件在新位置
    assert (user / "data" / "characters.json").is_file()
