"""
Live2D-Suzu 主程序入口
重构后使用Application类管理整个应用
"""
import os
import sys

import config

# 设置Qt插件路径（必须在导入任何Qt模块之前）
pyside6_path = os.path.join(os.path.dirname(__file__), '..', '..', 'envs', 'live2d-llm', 'lib', 'site-packages', 'PySide6')
if not os.path.exists(pyside6_path):
    # 如果相对路径不行，尝试使用sys.prefix
    import site
    for site_dir in site.getsitepackages():
        test_path = os.path.join(site_dir, 'PySide6')
        if os.path.exists(test_path):
            pyside6_path = test_path
            break

if os.path.exists(pyside6_path):
    plugins_path = os.path.join(pyside6_path, 'plugins')
    if os.path.exists(plugins_path):
        os.environ['QT_PLUGIN_PATH'] = plugins_path
        # 添加PySide6到PATH
        os.environ['PATH'] = pyside6_path + os.pathsep + os.environ.get('PATH', '')

from core.application import Live2DApplication


def main():
    """应用主函数"""
    config.load_custom_models()
    app = Live2DApplication()
    app.run()


if __name__ == "__main__":
    main()
