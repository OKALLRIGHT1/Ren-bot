from dataclasses import dataclass
from typing import Optional

# 客户端自带资源（与大脑解耦）：assets/avatar.png 是悬浮球默认头像（"灵魂宝石"），
# assets/icon.ico 是窗口图标。这两个文件随客户端一起打包，即使尚未选择大脑目录
# 首次启动也能显示正确的悬浮球头像而非回退文字。
try:
    from config import QT_ICON_PATH
except Exception:
    QT_ICON_PATH = "assets/icon.ico"


@dataclass
class QtGuiConfig:
    title: str = "Live2D-Suzu"
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
    "image_path": "assets/avatar.png",
    "enable_image": True,
    "size": 55,
    "font_size": 14,
    "bg_color": "transparent",
    "text_color": "white",
}
