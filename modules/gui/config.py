from dataclasses import dataclass
from typing import Optional

# 尝试从根目录的 config.py 导入图标路径
try:
    from config import QT_ICON_PATH
except Exception:
    # 默认值，如果根目录 config 没定义
    QT_ICON_PATH = "assets/icon.ico"


@dataclass
class QtGuiConfig:
    title: str = "Live2D Agent"
    start_minimized_to_tray: bool = False
    compact_size: tuple = (600, 190)
    full_size: tuple = (760, 560)
    single_click_toggle: bool = True
    double_click_full: bool = True

    # 🟢 关键：默认值使用导入的 QT_ICON_PATH
    icon_path: Optional[str] = QT_ICON_PATH


# 定义默认悬浮球配置
DEFAULT_BALL_CONFIG = {
    "text": "L2D",
    "image_path": None,
    "enable_image": False,
    "size": 60,
    "font_size": 14,
    "bg_color": "#3B82F6",
    "text_color": "white"
}

OFFSET_X = 200
