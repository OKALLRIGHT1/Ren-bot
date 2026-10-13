"""Path anchoring contract (docs/部分重构.md §13.1 问题 1/2).

Brain 侧（repo live2d-llm）：
- SUZU_DATA_DIR / SUZU_USER_DIR 注入 → data/、memory_db/、logs/ 全部
  落在用户数据目录下（安装版 = %APPDATA%\\Suzu）；
- 未注入 → 与历史行为完全一致的相对路径（开发模式，cwd=brain root）。

这是打包前的阻断项：相对路径会让 Brain 把 SQLite/chromadb 写进
Program Files（无写权限，启动即崩）。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

BRAIN_ROOT = Path(__file__).resolve().parents[2]


def _reload_paths_with(monkeypatch, env: dict):
    """清掉 modules.paths 及其下游缓存后按给定环境重载。"""
    for name in (
        "modules.paths",
        "modules.runtime_settings",
        "modules.memory_sqlite",
    ):
        sys.modules.pop(name, None)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.import_module("modules.paths")


class TestInjectedMode:
    def test_data_dir_follows_user_dir(self, monkeypatch):
        paths = _reload_paths_with(monkeypatch, {"SUZU_DATA_DIR": r"C:\UD\Suzu"})
        assert str(Path(paths.data_dir())) == r"C:\UD\Suzu\data"

    def test_memory_db_and_logs_follow_user_dir(self, monkeypatch):
        paths = _reload_paths_with(monkeypatch, {"SUZU_DATA_DIR": r"C:\UD\Suzu"})
        assert str(Path(paths.memory_dir())) == r"C:\UD\Suzu\memory"
        assert str(Path(paths.memory_db_dir())) == r"C:\UD\Suzu\memory_db"
        assert str(Path(paths.logs_dir())) == r"C:\UD\Suzu\logs"

    def test_data_path_joins(self, monkeypatch):
        paths = _reload_paths_with(monkeypatch, {"SUZU_DATA_DIR": r"C:\UD\Suzu"})
        assert str(Path(paths.data_path("events.sqlite"))) == (
            r"C:\UD\Suzu\data\events.sqlite"
        )

    def test_legacy_env_alias_suzu_user_dir(self, monkeypatch):
        paths = _reload_paths_with(monkeypatch, {"SUZU_USER_DIR": r"C:\UD\Suzu"})
        assert str(Path(paths.data_dir())) == r"C:\UD\Suzu\data"

    def test_runtime_settings_path_follows(self, monkeypatch):
        _reload_paths_with(monkeypatch, {"SUZU_DATA_DIR": r"C:\UD\Suzu"})
        rs = importlib.import_module("modules.runtime_settings")
        assert str(rs.RUNTIME_SETTINGS_PATH) == (
            r"C:\UD\Suzu\data\runtime_settings.json"
        )

    def test_memory_sqlite_paths_follow_user_dir(self, monkeypatch, tmp_path):
        root = tmp_path / "Suzu"
        _reload_paths_with(monkeypatch, {"SUZU_DATA_DIR": str(root)})
        memory_sqlite = importlib.import_module("modules.memory_sqlite")
        assert Path(memory_sqlite.DEFAULT_DB_PATH) == root / "memory" / "memory.sqlite"
        assert Path(memory_sqlite.LEGACY_PROFILE_JSON) == root / "memory_db" / "profile.json"
        assert Path(memory_sqlite.LEGACY_EVENTS_DB) == root / "data" / "events.sqlite"


class TestDevMode:
    def test_relative_paths_unchanged_without_env(self, monkeypatch):
        monkeypatch.delenv("SUZU_DATA_DIR", raising=False)
        monkeypatch.delenv("SUZU_USER_DIR", raising=False)
        paths = _reload_paths_with(monkeypatch, {})
        # 开发模式：与历史字面量完全一致（cwd 相对，行为零回归）。
        assert paths.data_dir() == "data"
        assert paths.data_path("runtime_settings.json").endswith("runtime_settings.json")
        assert paths.data_path("runtime_settings.json").startswith("data")
        assert paths.memory_dir() == "./memory"
        assert paths.memory_db_dir() == "memory_db"
        assert paths.logs_dir() == "logs"

    def test_config_memory_db_unchanged_in_dev_mode(self, monkeypatch):
        monkeypatch.delenv("SUZU_DATA_DIR", raising=False)
        monkeypatch.delenv("SUZU_USER_DIR", raising=False)
        for name in ("modules.paths", "config"):
            sys.modules.pop(name, None)
        config = importlib.import_module("config")
        assert config.MEMORY_DB_PATH == "memory_db"
        assert config.CUSTOM_MODELS_PATH in ("data/custom_models.json", r"data\custom_models.json")


def test_no_new_hardcoded_data_literals():
    """契约回归护栏：可写路径不得再写死 './data' 相对字面量。

    排除 modules/paths.py 本身（注释）、GUI 冻结区（阶段 3 删除对象）与文档。
    """
    import re

    exempt_prefixes = ("modules\\gui", "modules/gui", "tests")
    pattern = re.compile(r'''["'](?:\./)?data/[^\s"']+["']''')
    join_data = re.compile(r'''_join_path\([^)]*["']data["']''')
    memory_literal = re.compile(
        r'''["']\./memory(?:/memory\.sqlite)?["']|["']memory/memory\.sqlite["']'''
    )
    hits = []
    roots = (
        BRAIN_ROOT / "modules",
        BRAIN_ROOT / "core",
        BRAIN_ROOT / "integrations",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            rel = str(py.relative_to(BRAIN_ROOT)).replace("\\", "/")
            if rel.startswith("modules/gui") or rel.startswith("tests") or "__pycache__" in rel:
                continue
            if rel == "modules/paths.py":
                continue
            src = py.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(src.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or "docs/部分重构" in line:
                    continue
                if pattern.search(line) or join_data.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:80]}")
                    continue
                if memory_literal.search(line) and "memory_db" not in line:
                    hits.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert hits == [], f"发现新的硬编码 data/ 或 memory 路径:\n" + "\n".join(hits)
