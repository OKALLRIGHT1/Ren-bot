"""
Live2D-Suzu 主程序入口
重构后使用Application类管理整个应用
"""

from __future__ import annotations

import os
import sys
import time

# Force standard streams to use UTF-8 encoding on Windows to prevent UnicodeEncodeError with emojis
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import config
from core.single_instance import SingleInstanceLock


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_ALREADY_RUNNING_EXIT_CODE = 101

# 设置Qt插件路径（必须在导入任何Qt模块之前）
pyside6_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "envs",
    "live2d-llm",
    "lib",
    "site-packages",
    "PySide6",
)
if not os.path.exists(pyside6_path):
    # 如果相对路径不行，尝试使用sys.prefix
    import site

    for site_dir in site.getsitepackages():
        test_path = os.path.join(site_dir, "PySide6")
        if os.path.exists(test_path):
            pyside6_path = test_path
            break

if os.path.exists(pyside6_path):
    plugins_path = os.path.join(pyside6_path, "plugins")
    if os.path.exists(plugins_path):
        os.environ["QT_PLUGIN_PATH"] = plugins_path
        # 添加PySide6到PATH
        os.environ["PATH"] = pyside6_path + os.pathsep + os.environ.get("PATH", "")

from core.application import Live2DApplication


def acquire_core_lock(seconds: float = 25.0) -> SingleInstanceLock | None:
    deadline = time.time() + max(0.0, seconds)
    while True:
        lock = SingleInstanceLock(ROOT_DIR, "core")
        if lock.acquire():
            return lock
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def main():
    """应用主函数"""
    lock = acquire_core_lock()
    if lock is None:
        print("⚠️ [核心程序] 已有 Live2D-Suzu 核心实例在运行，本次启动已退出。")
        sys.exit(CORE_ALREADY_RUNNING_EXIT_CODE)

    # 安装版首启迁移（docs/部分重构.md §13/§18）：注入了 SUZU_DATA_DIR 且
    # 用户数据目录还没有配置时，把旧 data/ 的配置带过去。必须在
    # load_custom_models / runtime_settings 读取之前执行。开发模式
    # （未注入）为 no-op，行为零变化。
    try:
        from modules.user_data_migration import maybe_migrate_for_boot

        report = maybe_migrate_for_boot(ROOT_DIR)
        if isinstance(report, dict):
            migrated = len(report.get("files") or []) + len(report.get("dirs") or [])
            errors = len(report.get("errors") or [])
            print(
                f"📦 [首启迁移] 旧数据已迁入用户数据目录：{migrated} 项"
                f"{'，失败 ' + str(errors) + ' 项' if errors else ''}",
                flush=True,
            )
    except Exception as exc:
        # 迁移失败不阻断启动：Brain 以全新配置起（token 重生成），
        # 旧数据原样保留在 brain/data/。
        print(f"⚠️ [首启迁移] 跳过（{exc}）", flush=True)

    config.load_custom_models()
    app = Live2DApplication()
    import __main__

    __main__.app_instance = app
    exit_code = app.run()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
