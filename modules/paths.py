"""Centralized writable-path resolution for the brain.

打包（docs/部分重构.md §13.1 问题 1）前的路径锚定改造：
所有运行时可写数据（data/、memory/、memory_db/、logs/）统一从这里解析。

解析顺序：
1. ``SUZU_DATA_DIR`` / ``SUZU_USER_DIR`` 环境变量（安装版客户端注入
   ``%APPDATA%\Suzu``，开发版客户端注入外部 Brain 根目录）；
2. Brain 独立运行且未注入时，返回与历史行为完全相同的相对路径，
   此时启动器负责让 cwd 指向 Brain 根目录。


注意：plugins/ 特殊——代码插件属于程序文件，跟安装目录走，不进用户数据
目录；只有用户自装插件（未来功能）才需要进 AppData。所以 plugins 保持
相对路径（./plugins），由打包布局保证其位置。

迁移期间禁止在新代码里写死 ``./data`` 相对路径：新引用一律 import 本模块。
"""

from __future__ import annotations

import os
from pathlib import Path


def user_dir() -> str:
    """用户数据根目录；空字符串表示「沿用 cwd 相对路径」（开发模式）。"""
    return (
        os.environ.get("SUZU_DATA_DIR", "").strip()
        or os.environ.get("SUZU_USER_DIR", "").strip()
    )


def data_dir() -> str:
    """data/ 目录（runtime_settings、events.sqlite、characters 等）。"""
    root = user_dir()
    if root:
        return str(Path(root) / "data")
    return "data"


def data_path(filename: str) -> str:
    """data/ 下单个文件的完整路径（兼容历史 'data/xxx.json' 字面量）。"""
    return str(Path(data_dir()) / filename)


def memory_dir() -> str:
    """主记忆库目录（memory.sqlite）。开发模式保持历史字面量 ``./memory``。"""
    root = user_dir()
    if root:
        return str(Path(root) / "memory")
    return "./memory"


def memory_db_dir() -> str:
    """memory_db/ 目录（chromadb 持久库）。"""
    root = user_dir()
    if root:
        return str(Path(root) / "memory_db")
    return "memory_db"


def logs_dir() -> str:
    """logs/ 目录（console.log 等）。"""
    root = user_dir()
    if root:
        return str(Path(root) / "logs")
    return "logs"
