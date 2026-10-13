"""First-launch migration of user data into SUZU_DATA_DIR (install builds).

安装版首启语义（docs/部分重构.md §13/§18）：
- 客户端注入 ``SUZU_DATA_DIR=%APPDATA%\\Suzu`` 后，Brain 的 data/、
  memory/、memory_db/ 都指向用户数据目录；
- 用户数据目录里还没有 runtime_settings.json 时，从旧开发位置
  ``<brain>/data/`` 迁移**配置类**文件（白名单，不是全量拷贝——日志、
  备份、临时文件不迁）；
- 一次性：迁移成功后写迁移标记，不再重复；
- 失败不致命：保留旧数据，Brain 以全新配置启动（token 会重新生成，
  外部服务需要重新配置——但绝不删除或覆盖用户旧数据）。

目录类（memory/、memory_db/、data/ 下的子目录）跟随迁移；绝对路径字段
（外部服务 command 等）按 §13 规则：旧路径存在则保留，失效则由
external_services.py 的自动发现兜底，不在迁移层重写。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

# 配置类文件：用户设置与状态，安装版首启必须带走。
# 命名规则：*.json 状态文件显式列出；排除 *.log / *.bak* / *.tmp。
DATA_FILES: tuple[str, ...] = (
    "runtime_settings.json",
    "custom_models.json",
    "characters.json",
    "display_state_config.json",
    "recent_chat_history.json",
    "sensor_stats.json",
    "app_category_rules.json",
    "model_transport_state.json",
    "codex_session.json",
    "codex_tasks.json",
    "delegate_session.json",
    "delegate_tasks.json",
    "qq_reminders.json",
    "disaster_warning_state.json",
    "episode_state.json",
    "knowledge_import_manifest.json",
    "agently_mail_seen.json",
)

# 数据库类（SQLite 等）与子目录：整库带走。
DATA_DB_FILES: tuple[str, ...] = (
    "events.sqlite",
    "plugin_secrets.db",
)
# data/ 下需要整体迁移的子目录（用户状态数据；开发工具源码如 pycdc、
# python-uncompyle6-* 不迁——那不是用户数据）。
DATA_SUBDIRS: tuple[str, ...] = (
    "assistant_badges",
    "info_sources",
    "magic_girl_daily",
    "qq_file_browser",
    "qq_reminders",
    "reply_effect",
    "outbound",
    "backups",
    "codex_backups",
)

MIGRATION_MARKER = "migration_from_legacy_data.done.json"


def migrate_legacy_data(brain_root: Path, user_root: Path) -> Optional[dict]:
    """把旧 ``<brain_root>/data`` 的配置迁到 ``<user_root>/data``。

    返回迁移报告 dict；不需要迁移（未注入/已迁移/无旧数据）返回 None。
    只在 ``user_root/data/runtime_settings.json`` 缺失时执行——它既是
    「首启」判据，也是外部服务 autostart 配置的关键载体。
    """
    legacy_data = brain_root / "data"
    target_data = user_root / "data"
    marker = user_root / MIGRATION_MARKER
    if marker.is_file():
        return None  # 已迁移过，一次性保证
    if not legacy_data.is_dir():
        return None  # 全新安装（无旧数据），不产生迁移动作
    if (target_data / "runtime_settings.json").is_file():
        # 用户目录已有配置（例如手动复制过）——写标记防重复检查，不动文件。
        _write_marker(marker, {"skipped": "user_data_already_present"})
        return None

    report: dict = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": [], "dirs": [], "errors": []}
    target_data.mkdir(parents=True, exist_ok=True)
    # 迁移前先备份旧 runtime_settings（可回滚依据）。
    legacy_settings = legacy_data / "runtime_settings.json"
    if legacy_settings.is_file():
        backup = legacy_data / f"runtime_settings.before-migration-{time.strftime('%Y%m%d-%H%M%S')}.bak.json"
        try:
            shutil.copy2(legacy_settings, backup)
        except Exception as exc:  # 备份失败不阻断迁移，但要记录
            report["errors"].append(f"backup_failed: {exc}")

    def _copy_file(name: str, bucket: str) -> None:
        src = legacy_data / name
        if not src.is_file():
            return
        dst = target_data / name
        if dst.exists():
            return
        try:
            if src.stat().st_size > 200 * 1024 * 1024:
                report["errors"].append(f"oversize_skipped: {name}")
                return
            shutil.copy2(src, dst)
            report[bucket].append(name)
        except Exception as exc:
            report["errors"].append(f"{name}: {exc}")

    for name in DATA_FILES:
        _copy_file(name, "files")
    for name in DATA_DB_FILES:
        _copy_file(name, "files")
    def _copy_tree(src: Path, dst: Path, label: str) -> None:
        if not src.is_dir() or dst.exists():
            return
        try:
            shutil.copytree(src, dst)
            report["dirs"].append(label)
        except Exception as exc:
            report["errors"].append(f"{label}: {exc}")

    for name in DATA_SUBDIRS:
        _copy_tree(legacy_data / name, target_data / name, name)
    # chromadb 与主 SQLite 记忆库都跟用户走；程序目录不得留下可写状态。
    _copy_tree(brain_root / "memory_db", user_root / "memory_db", "memory_db/")
    _copy_tree(brain_root / "memory", user_root / "memory", "memory/")

    _write_marker(marker, report)
    return report


def _write_marker(marker: Path, payload: dict) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # 标记写失败最多导致重复检查，不致命


def maybe_migrate_for_boot(brain_root: Path) -> Optional[dict]:
    """boot.py 入口调用：仅当注入了用户数据目录时执行。"""
    import os

    user_dir = os.environ.get("SUZU_DATA_DIR", "").strip()
    if not user_dir:
        return None  # 开发模式：什么都不做（行为零变化）
    return migrate_legacy_data(Path(brain_root), Path(user_dir))
