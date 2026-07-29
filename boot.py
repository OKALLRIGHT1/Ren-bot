"""
Live2D-Suzu 主程序入口
重构后使用Application类管理整个应用
"""

import os
import sys

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


def main():
    """应用主函数"""
    lock = SingleInstanceLock(ROOT_DIR, "core")
    if not lock.acquire():
        print("⚠️ [核心程序] 已有 Live2D-Suzu 核心实例在运行，本次启动已退出。")
        sys.exit(CORE_ALREADY_RUNNING_EXIT_CODE)
    config.load_custom_models()
    app = Live2DApplication()
    import __main__

    __main__.app_instance = app
    exit_code = app.run()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
