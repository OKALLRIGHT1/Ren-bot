"""本进程的可写路径。

data/、memory/、memory_db/、logs/ 都从这里解析。没有另一台机器上的大脑目录。

解析顺序：
1. ``SUZU_DATA_DIR`` / ``SUZU_USER_DIR``（安装版指向用户数据目录，例如 ``%APPDATA%\\Suzu``）；
2. 没注入时用相对路径，启动器负责让 cwd 指向本仓库。


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
