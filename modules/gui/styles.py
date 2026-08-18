import json
from pathlib import Path

# 定义内置的主题字典
THEMES = {
    # macOS 风格：雾白 / 磨砂玻璃（默认推荐）
    "Frost (雾白玻璃)": {
        "accent": "#0A84FF",
        "accent_hover": "#0071E3",
        "accent_soft": "#E8F1FF",
        # 设置页：浅灰底 + 纯白卡片分层；主面板透明度单独控
        "bg_app": "#EBECEF",
        "bg_card": "#FFFFFF",
        "bg_soft": "#F4F5F7",
        "border": "#D8DBE0",
        "border_strong": "#C5C9D0",
        "text_primary": "#1D1D1F",
        "text_secondary": "#6E6E73",
        "text_muted": "#8E8E93",
        "success": "#30D158",
        "success_soft": "#D8F8E1",
        "warning": "#FF9F0A",
        "danger": "#FF453A",
        "console_bg": "#1C1C1E",
        "console_fg": "#F5F5F7",
        "console_border": "#2C2C2E",
        "console_selection_bg": "#0A84FF",
        "console_selection_fg": "#FFFFFF",
        "glass": True,
        "radius_card": 20,
        "radius_control": 12,
        # 主悬浮面板：略透即可，别透到看不清
        "panel_card_alpha": 0.96,
        "panel_soft_alpha": 0.92,
        "panel_border_alpha": 0.70,
        # 设置中心：默认实心（灰底白卡才看得出分层）
        "settings_card_alpha": 1.0,
        "settings_soft_alpha": 1.0,
        "settings_border_alpha": 0.85,
        "font_family": "'Segoe UI Variable', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei UI', 'Microsoft YaHei'",
    },
    "Indigo (靛蓝)": {
        "accent": "#6366F1",
        "accent_hover": "#4F46E5",
        "accent_soft": "#EEF2FF",
        "bg_app": "#F5F7FB",
        "bg_card": "#FFFFFF",
        "bg_soft": "#F3F4F6",
        "border": "#E5E7EB",
        "border_strong": "#D1D5DB",
        "text_primary": "#111827",
        "text_secondary": "#6B7280",
        "text_muted": "#9CA3AF",
        "success": "#10B981",
        "success_soft": "#D1FAE5",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "console_bg": "#0B1220",
        "console_fg": "#E5E7EB",
        "console_border": "#1F2937",
        "console_selection_bg": "#1E293B",
        "console_selection_fg": "#E5E7EB",
    },
    "Sakura (樱花)": {
        "accent": "#F472B6",
        "accent_hover": "#EC4899",
        "accent_soft": "#FFF1F2",
        "bg_app": "#FDF2F8",
        "bg_card": "#FFFFFF",
        "bg_soft": "#FCE7F3",
        "border": "#FBCFE8",
        "border_strong": "#F9A8D4",
        "text_primary": "#4C1D95",
        "text_secondary": "#831843",
        "text_muted": "#9D174D",
        "success": "#10B981",
        "success_soft": "#D1FAE5",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "console_bg": "#1C1917",
        "console_fg": "#F9A8D4",
        "console_border": "#3F3F46",
        "console_selection_bg": "#831843",
        "console_selection_fg": "#FBCFE8",
    },
    "Emerald (极光)": {
        "accent": "#10B981",
        "accent_hover": "#059669",
        "accent_soft": "#ECFDF5",
        "bg_app": "#F0FDF4",
        "bg_card": "#FFFFFF",
        "bg_soft": "#D1FAE5",
        "border": "#A7F3D0",
        "border_strong": "#6EE7B7",
        "text_primary": "#064E3B",
        "text_secondary": "#065F46",
        "text_muted": "#047857",
        "success": "#10B981",
        "success_soft": "#D1FAE5",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "console_bg": "#022C22",
        "console_fg": "#A7F3D0",
        "console_border": "#064E3B",
        "console_selection_bg": "#065F46",
        "console_selection_fg": "#ECFDF5",
    },
    "Cyberpunk (赛博)": {
        "accent": "#06B6D4",
        "accent_hover": "#0891B2",
        "accent_soft": "#164E63",
        "bg_app": "#09090B",
        "bg_card": "#18181B",
        "bg_soft": "#27272A",
        "border": "#3F3F46",
        "border_strong": "#52525B",
        "text_primary": "#F4F4F5",
        "text_secondary": "#A1A1AA",
        "text_muted": "#71717A",
        "success": "#10B981",
        "success_soft": "#064E3B",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "console_bg": "#000000",
        "console_fg": "#22D3EE",
        "console_border": "#27272A",
        "console_selection_bg": "#164E63",
        "console_selection_fg": "#CFFAFE",
    },
    # 深色玻璃：夜间/Live2D 同屏时更不抢戏
    "Noir Glass (暗夜玻璃)": {
        "accent": "#64D2FF",
        "accent_hover": "#5AC8F5",
        "accent_soft": "#1C2A33",
        "bg_app": "#0F1115",
        "bg_card": "#1C1C1E",
        "bg_soft": "#2C2C2E",
        "border": "#3A3A3C",
        "border_strong": "#48484A",
        "text_primary": "#F5F5F7",
        "text_secondary": "#A1A1A6",
        "text_muted": "#8E8E93",
        "success": "#30D158",
        "success_soft": "#16351F",
        "warning": "#FFD60A",
        "danger": "#FF453A",
        "console_bg": "#000000",
        "console_fg": "#D1D1D6",
        "console_border": "#2C2C2E",
        "console_selection_bg": "#0A84FF",
        "console_selection_fg": "#FFFFFF",
        "glass": True,
        "radius_card": 20,
        "radius_control": 12,
        "panel_card_alpha": 0.94,
        "panel_soft_alpha": 0.90,
        "panel_border_alpha": 0.72,
        "settings_card_alpha": 1.0,
        "settings_soft_alpha": 1.0,
        "settings_border_alpha": 0.80,
        "font_family": "'Segoe UI Variable', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei UI', 'Microsoft YaHei'",
    },
}

DEFAULT_THEME_NAME = "Frost (雾白玻璃)"

def _merge_palette(base: dict, override: dict) -> dict:
    if not isinstance(override, dict):
        return base
    for k, v in override.items():
        if isinstance(v, str):
            base[k] = v
    return base

def get_current_theme_name() -> str:
    try:
        from modules.runtime_settings import load_runtime_settings
        runtime = load_runtime_settings()
        name = runtime.get("theme_name") or DEFAULT_THEME_NAME
        return str(name)
    except Exception:
        return DEFAULT_THEME_NAME

def get_ui_palette() -> dict:
    theme_name = get_current_theme_name()
    if theme_name not in THEMES:
        theme_name = DEFAULT_THEME_NAME if DEFAULT_THEME_NAME in THEMES else "Indigo (靛蓝)"
    
    base = dict(THEMES[theme_name])
    
    p = dict(base)
    p["theme_name"] = theme_name
    p["glass"] = bool(base.get("glass", False))
    p["radius_card"] = int(base.get("radius_card", 16))
    p["radius_control"] = int(base.get("radius_control", 10))
    p["font_family"] = str(
        base.get(
            "font_family",
            "'Segoe UI', 'Microsoft YaHei'",
        )
    )
    p["console_main"] = {
        "bg": base["console_bg"],
        "fg": base["console_fg"],
        "border": base["console_border"],
        "selection_bg": base["console_selection_bg"],
        "selection_fg": base["console_selection_fg"],
    }
    p["console_codex"] = p["console_main"]
    
    # Allow overrides
    try:
        from modules.runtime_settings import load_runtime_settings
        runtime = load_runtime_settings()
        ui = runtime.get("ui_palette") if isinstance(runtime, dict) else None
        if isinstance(ui, dict):
            for key in list(p.keys()):
                if isinstance(ui.get(key), str):
                    p[key] = ui[key]
            if isinstance(ui.get("console_main"), dict):
                _merge_palette(p["console_main"], ui.get("console_main"))
            if isinstance(ui.get("console_codex"), dict):
                _merge_palette(p["console_codex"], ui.get("console_codex"))
            if "glass" in ui:
                p["glass"] = bool(ui.get("glass"))
    except Exception:
        pass
    return p

def _theme_metrics(p: dict, *, surface: str = "default") -> dict:
    """surface: panel | settings | default — 主面板略透、设置页实心分层。"""
    glass = bool(p.get("glass"))
    radius_card = int(p.get("radius_card") or (20 if glass else 16))
    radius_control = int(p.get("radius_control") or (12 if glass else 10))

    if surface == "panel":
        card_alpha = float(p.get("panel_card_alpha") or (0.96 if glass else 1.0))
        soft_alpha = float(p.get("panel_soft_alpha") or (0.92 if glass else 1.0))
        border_alpha = float(p.get("panel_border_alpha") or (0.70 if glass else 1.0))
    elif surface == "settings":
        card_alpha = float(p.get("settings_card_alpha") or (1.0 if glass else 1.0))
        soft_alpha = float(p.get("settings_soft_alpha") or (1.0 if glass else 1.0))
        border_alpha = float(p.get("settings_border_alpha") or (0.85 if glass else 1.0))
    else:
        # 通用对话框：略透但不飘
        card_alpha = float(p.get("card_alpha") or (0.97 if glass else 1.0))
        soft_alpha = float(p.get("soft_alpha") or (0.94 if glass else 1.0))
        border_alpha = float(p.get("border_alpha") or (0.75 if glass else 1.0))

    return {
        "glass": glass,
        "surface": surface,
        "font": p.get("font_family") or "'Segoe UI', 'Microsoft YaHei'",
        "radius_card": radius_card,
        "radius_control": radius_control,
        "card_alpha": card_alpha,
        "soft_alpha": soft_alpha,
        "border_alpha": border_alpha,
    }


# 各种基础控件风格（滚动条、提示框、输入框焦点等）
def get_common_qss(p: dict) -> str:
    m = _theme_metrics(p)
    radius = m["radius_control"]
    return f"""
        /* 针对所有的 QScrollBar 进行深度美化 */
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px 1px 2px 1px;
        }}
        QScrollBar::handle:vertical {{
            background: {hex_to_rgba(p["border_strong"], 0.75)};
            min-height: 24px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {p["accent"]};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 1px 2px 1px 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {hex_to_rgba(p["border_strong"], 0.75)};
            min-width: 24px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {p["accent"]};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}

        /* QToolTip 风格美化 */
        QToolTip {{
            background-color: {hex_to_rgba(p["bg_card"], 0.96)};
            color: {p["text_primary"]};
            border: 1px solid {hex_to_rgba(p["border"], 0.9)};
            border-radius: {max(8, radius - 2)}px;
            padding: 6px 10px;
            font-family: {m["font"]};
        }}

        /* 输入框的 focus 状态环 */
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
            background: {hex_to_rgba(p["bg_card"], 0.92 if m["glass"] else 1.0)};
            border: 1px solid {p["border"]};
            border-radius: {radius}px;
            padding: 6px 10px;
            selection-background-color: {p["accent_soft"]};
            selection-color: {p["accent_hover"]};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {p["accent"]};
            background: {p["bg_card"]};
            outline: none;
        }}

        /* 隐藏掉系统难看的 Focus 虚线框 */
        * {{
            outline: none;
        }}
        
        /* 美化 QComboBox 下拉栏 */
        QComboBox {{
            padding: 5px 12px;
            border: 1px solid {p["border"]};
            border-radius: {radius}px;
            background: {hex_to_rgba(p["bg_card"], 0.92 if m["glass"] else 1.0)};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox::down-arrow {{
            image: none; /* remove default arrow */
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 5px solid {p["text_secondary"]};
            margin-right: 8px;
            margin-top: 2px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: {radius}px;
            selection-background-color: {p["accent_soft"]};
            selection-color: {p["accent_hover"]};
            outline: none;
            padding: 4px;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 8px 10px;
            border-radius: 8px;
            min-height: 22px;
        }}

        QPushButton {{
            border-radius: {radius}px;
            padding: 7px 14px;
            border: 1px solid {p["border"]};
            background: {hex_to_rgba(p["bg_soft"], 0.9 if m["glass"] else 1.0)};
            color: {p["text_primary"]};
        }}
        QPushButton:hover {{
            background: {p["accent_soft"]};
            border-color: {hex_to_rgba(p["accent"], 0.35)};
            color: {p["accent_hover"]};
        }}
        QPushButton:pressed {{
            background: {hex_to_rgba(p["accent"], 0.12)};
        }}
        QPushButton#primaryAction, QPushButton[cssClass="primary"] {{
            background: {p["accent"]};
            color: white;
            border: 1px solid {p["accent_hover"]};
            font-weight: 600;
        }}
        QPushButton#primaryAction:hover, QPushButton[cssClass="primary"]:hover {{
            background: {p["accent_hover"]};
            color: white;
        }}
        QGroupBox {{
            border: 1px solid {p["border"]};
            border-radius: {m["radius_card"] - 2}px;
            margin-top: 12px;
            padding-top: 12px;
            background: {hex_to_rgba(p["bg_card"], 0.55 if m["glass"] else 0.0)};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {p["text_secondary"]};
            font-weight: 600;
        }}
        QCheckBox, QRadioButton {{
            spacing: 8px;
            color: {p["text_primary"]};
        }}
        QTabWidget::pane {{
            border: 1px solid {p["border"]};
            border-radius: {m["radius_card"] - 2}px;
            background: {hex_to_rgba(p["bg_card"], 0.88 if m["glass"] else 1.0)};
            top: -1px;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {p["text_secondary"]};
            padding: 8px 14px;
            margin-right: 4px;
            border-radius: 10px;
        }}
        QTabBar::tab:selected {{
            background: {p["accent_soft"]};
            color: {p["accent_hover"]};
            font-weight: 600;
        }}
    """

def get_main_styles(ball_config: dict) -> str:
    bg_color = ball_config.get("bg_color", "#3B82F6")
    text_color = ball_config.get("text_color", "white")
    font_size = ball_config.get("font_size", 14)
    ball_size = ball_config.get("size", 60)
    radius = ball_size // 2

    return f"""
        QPushButton#ball_btn {{
            background-color: {bg_color};
            color: {text_color};
            border-radius: {radius}px;
            border: 2px solid white;
            font-weight: bold;
            font-size: {font_size}px;
            font-family: 'Segoe UI Black', 'Microsoft YaHei';
        }}
        QPushButton#ball_btn:hover {{
            border: 2px solid #DBEAFE;
            margin-top: -2px;
        }}
        QPushButton#ball_btn:pressed {{
            margin-top: 0px;
            border-color: #93C5FD;
        }}
    """

def hex_to_rgba(hex_str: str, alpha: float) -> str:
    hex_str = hex_str.strip().lstrip('#')
    if len(hex_str) == 3:
        hex_str = ''.join([c*2 for c in hex_str])
    if len(hex_str) == 6:
        r = int(hex_str[0:2], 16)
        g = int(hex_str[2:4], 16)
        b = int(hex_str[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"
    return hex_str

def get_background_image_qss(for_settings: bool) -> tuple[str, bool]:
    try:
        from modules.runtime_settings import load_runtime_settings
        runtime = load_runtime_settings()
        bg_path = runtime.get("bg_image_path", "").strip()
        
        enabled = False
        if for_settings:
            enabled = bool(runtime.get("bg_image_settings_enabled", False))
        else:
            enabled = bool(runtime.get("bg_image_main_enabled", False))
            
        if enabled and bg_path:
            p = Path(bg_path)
            if p.exists():
                normalized_path = str(p.absolute()).replace("\\", "/")
                return f'border-image: url("{normalized_path}") 0 0 0 0 stretch stretch;', True
    except Exception:
        pass
    return "", False

def get_panel_styles() -> str:
    p = get_ui_palette()
    m = _theme_metrics(p, surface="panel")
    common = get_common_qss(p)
    bg_qss, bg_active = get_background_image_qss(for_settings=False)
    # 主悬浮窗：默认接近实心；只有开了背景图才明显透
    if bg_active:
        card_alpha = min(m["card_alpha"], 0.88)
        soft_alpha = min(m["soft_alpha"], 0.82)
        border_alpha = min(m["border_alpha"], 0.60)
        use_alpha = True
    elif m["glass"]:
        card_alpha = m["card_alpha"]
        soft_alpha = m["soft_alpha"]
        border_alpha = m["border_alpha"]
        use_alpha = True
    else:
        use_alpha = False
    radius_card = m["radius_card"]
    radius_ctl = m["radius_control"]

    if use_alpha:
        bg_card_style = hex_to_rgba(p["bg_card"], card_alpha)
        bg_soft_style = hex_to_rgba(p["bg_soft"], soft_alpha)
        border_style = hex_to_rgba(p["border"], border_alpha)
        console_bg_style = hex_to_rgba(p["console_main"]["bg"], 0.94 if m["glass"] else 0.86)
    else:
        bg_card_style = p["bg_card"]
        bg_soft_style = p["bg_soft"]
        border_style = p["border"]
        console_bg_style = p["console_main"]["bg"]
        
    return common + f"""
        QWidget {{
            font-family: {m["font"]};
            color: {p["text_primary"]};
        }}
        QFrame#container {{
            background-color: {bg_card_style};
            {bg_qss}
            border-radius: {radius_card + 2}px;
            border: 1px solid {border_style};
        }}
        QFrame#titleBar {{
            background: transparent;
            border: none;
            border-radius: 0px;
        }}
        QLabel#statusLabel {{
            color: {p["text_muted"]};
            font-size: 11px;
            font-weight: 600;
            margin-left: 2px;
        }}
        QLabel#characterLabel {{
            color: {p["text_secondary"]};
            font-size: 11px;
            margin-left: 4px;
        }}
        QLabel#workSessionLabel {{
            color: {p["text_secondary"]};
            background: {hex_to_rgba(p["bg_soft"], 0.55 if m["glass"] else 0.9)};
            border: 1px solid {hex_to_rgba(p["border"], 0.35 if m["glass"] else 0.7)};
            border-radius: {max(9, radius_ctl - 1)}px;
            padding: 3px 10px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 4px;
        }}
        QFrame#windowCtlGroup {{
            background: {hex_to_rgba(p["bg_soft"], 0.45 if m["glass"] else 0.85)};
            border: 1px solid {hex_to_rgba(p["border"], 0.30 if m["glass"] else 0.65)};
            border-radius: 10px;
        }}
        QPushButton#windowCtl, QPushButton#windowCtlClose {{
            background: transparent;
            color: {p["text_secondary"]};
            border: none;
            border-radius: 7px;
            font-weight: 700;
            font-size: 14px;
            padding: 0px;
            min-width: 24px;
            max-width: 24px;
            min-height: 22px;
            max-height: 22px;
        }}
        QPushButton#windowCtl:hover {{
            background: {hex_to_rgba(p["accent_soft"], 0.95)};
            color: {p["accent_hover"]};
        }}
        QPushButton#windowCtlClose:hover {{
            background: {hex_to_rgba(p["danger"], 0.14)};
            color: {p["danger"]};
        }}
        QPushButton#windowCtl:pressed, QPushButton#windowCtlClose:pressed {{
            background: {hex_to_rgba(p["border"], 0.55)};
        }}
        QFrame#heroCard {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {hex_to_rgba(p["accent_soft"], 0.92 if m["glass"] else 1.0)}, stop:1 {hex_to_rgba(p["bg_app"], 0.86 if m["glass"] else 1.0)});
            border: 1px solid {border_style};
            border-radius: {radius_card}px;
        }}
        QLabel#heroTitle {{
            color: {p["text_primary"]};
            font-size: 16px;
            font-weight: 700;
        }}
        QLabel#heroHint {{
            color: {p["text_secondary"]};
            font-size: 12px;
            line-height: 1.4;
        }}
        QLabel#pillLabel {{
            background: {bg_card_style};
            color: {p["text_secondary"]};
            border: 1px solid {border_style};
            border-radius: 11px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
        }}
        QTextEdit#historyView {{
            background-color: {console_bg_style};
            border: 1px solid {hex_to_rgba(p["console_main"]["border"], 0.75 if m["glass"] else 1.0)};
            border-radius: {radius_ctl + 2}px;
            color: {p["console_main"]["fg"]};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', 'Segoe UI', 'Microsoft YaHei', monospace;
            font-size: 12px;
            line-height: 1.6;
            padding: 10px;
            selection-background-color: {p["console_main"]["selection_bg"]};
            selection-color: {p["console_main"]["selection_fg"]};
        }}
        QFrame#inputShell {{
            background-color: {hex_to_rgba(p["bg_soft"], 0.42 if m["glass"] else 0.85)};
            border-radius: {radius_ctl + 6}px;
            border: 1px solid {hex_to_rgba(p["border"], 0.28 if m["glass"] else 0.65)};
            min-height: 38px;
            max-height: 38px;
        }}
        QFrame#inputShell:hover {{
            background-color: {hex_to_rgba(p["bg_soft"], 0.58 if m["glass"] else 0.95)};
            border-color: {hex_to_rgba(p["accent"], 0.35)};
        }}
        QLineEdit#chatInput {{
            background: transparent;
            border: none;
            color: {p["text_primary"]};
            font-size: 13px;
            padding-left: 2px;
        }}
        QLineEdit#chatInput:focus {{
            border: none;
        }}
        QPushButton#sendButton {{
            background-color: {p["accent"]};
            color: white;
            border-radius: 15px;
            font-weight: bold;
            font-size: 13px;
            width: 30px;
            height: 30px;
            border: none;
        }}
        QPushButton#sendButton:hover {{
            background-color: {p["accent_hover"]};
        }}
        QFrame#toolsBar {{
            background: transparent;
            border: none;
            border-radius: 0px;
            min-height: 30px;
            max-height: 32px;
        }}
        QFrame#toolsSep {{
            background: {hex_to_rgba(p["border_strong"], 0.35)};
            border: none;
            margin: 0 3px;
        }}
        QPushButton#toolbarBtn {{
            background: transparent;
            color: {p["text_secondary"]};
            border: none;
            font-size: 13px;
            min-width: 28px;
            max-width: 28px;
            min-height: 26px;
            max-height: 26px;
            border-radius: 8px;
            padding: 0px;
        }}
        QPushButton#toolbarBtn:hover {{
            background: {hex_to_rgba(p["bg_soft"], 0.65 if m["glass"] else 0.95)};
            color: {p["text_primary"]};
        }}
        QPushButton#toolbarBtn:pressed {{
            background: {p["accent_soft"]};
            color: {p["accent_hover"]};
        }}
        QMenu {{
            background: {hex_to_rgba(p["bg_card"], 0.98 if m["glass"] else 1.0)};
            border: 1px solid {border_style};
            border-radius: {radius_ctl + 2}px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 14px;
            border-radius: 8px;
            color: {p["text_primary"]};
        }}
        QMenu::item:selected {{
            background: {p["accent"]};
            color: white;
        }}
    """

def get_settings_styles() -> str:
    p = get_ui_palette()
    m = _theme_metrics(p, surface="settings")
    common = get_common_qss(p)
    bg_qss, bg_active = get_background_image_qss(for_settings=True)
    radius_card = m["radius_card"]
    radius_ctl = m["radius_control"]

    # 设置页默认「灰底 + 实心白卡」分层；只有启用背景图时才半透明
    if bg_active:
        bg_card_style = hex_to_rgba(p["bg_card"], min(m["card_alpha"], 0.88))
        bg_soft_style = hex_to_rgba(p["bg_soft"], min(m["soft_alpha"], 0.82))
        border_style = hex_to_rgba(p["border"], min(m["border_alpha"], 0.60))
        dialog_bg = hex_to_rgba(p["bg_app"], 0.92)
    else:
        bg_card_style = p["bg_card"]
        bg_soft_style = p["bg_soft"]
        border_style = p["border"]
        dialog_bg = p["bg_app"]

    # Frost 风格导航：浅蓝选中，更像 macOS 侧栏
    nav_selected_bg = p["accent_soft"] if m["glass"] else p["accent"]
    nav_selected_fg = p["accent_hover"] if m["glass"] else "white"
    group_bg = hex_to_rgba(p["bg_soft"], 0.55) if m["glass"] else "transparent"
        
    return common + f"""
        QDialog {{
            background-color: {dialog_bg};
            font-family: {m["font"]};
        }}
        QDialog#SettingsDialog {{
            {bg_qss}
            background-color: {dialog_bg};
        }}
        QFrame#settingsNavCard, QFrame#settingsContentCard {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {radius_card}px;
        }}
        QFrame#settingsHeaderCard, QFrame#launchCard {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {max(14, radius_card - 4)}px;
        }}
        QFrame#settingsActionBar {{
            background: {bg_soft_style};
            border: 1px solid {border_style};
            border-radius: {max(14, radius_card - 2)}px;
        }}
        QGroupBox {{
            background: {group_bg};
            border: 1px solid {border_style};
            border-radius: {radius_ctl + 2}px;
            margin-top: 14px;
            padding-top: 14px;
            font-weight: 600;
            color: {p["text_primary"]};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            color: {p["text_secondary"]};
        }}
        QLabel#settingsNavTitle {{
            font-size: 17px;
            font-weight: 700;
            color: {p["text_primary"]};
            letter-spacing: 0.2px;
        }}
        QLabel#settingsNavHint, QLabel#settingsPageDesc, QLabel#launchDesc {{
            color: {p["text_secondary"]};
            font-size: 12px;
        }}
        QLabel#settingsPageTitle, QLabel.header, QLabel#launchTitle {{
            font-size: 22px;
            font-weight: 700;
            color: {p["text_primary"]};
            letter-spacing: -0.2px;
        }}
        QListWidget#settingsNav {{
            background: transparent;
            border: none;
            outline: none;
            padding: 6px;
        }}
        QListWidget#settingsNav::item {{
            padding: 11px 14px;
            margin: 3px 2px;
            border-radius: 12px;
            color: {p["text_secondary"]};
            font-size: 13px;
        }}
        QListWidget#settingsNav::item:selected {{
            background: {nav_selected_bg};
            color: {nav_selected_fg};
            font-weight: 700;
        }}
        QListWidget#settingsNav::item:hover {{
            background: {hex_to_rgba(p["accent_soft"], 0.65)};
            color: {p["text_primary"]};
        }}
        QTableWidget {{
            background: {bg_soft_style};
            border: 1px solid {border_style};
            border-radius: {radius_ctl + 2}px;
            gridline-color: {hex_to_rgba(p["border"], 0.55)};
            color: {p["text_primary"]};
        }}
        QTableWidget::item {{
            color: {p["text_primary"]};
            background-color: transparent;
            padding: 4px;
        }}
        QTableWidget::item:selected {{
            background-color: {p["accent_soft"]};
            color: {p["accent_hover"]};
        }}
        QHeaderView::section {{
            background: {bg_soft_style};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 9px;
            border: none;
            border-bottom: 1px solid {border_style};
        }}
        QPushButton {{
            background: {bg_soft_style};
            color: {p["text_primary"]};
            border: 1px solid {border_style};
            border-radius: {radius_ctl}px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {hex_to_rgba(p["accent"], 0.45)};
            color: {p["accent_hover"]};
            background: {p["accent_soft"]};
        }}
        QPushButton#primaryAction {{
            background: {p["accent"]};
            color: white;
            border: 1px solid {p["accent_hover"]};
            font-weight: 700;
        }}
        QPushButton#primaryAction:hover {{
            background: {p["accent_hover"]};
            color: white;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QListWidget, QTabWidget::pane {{
            border-radius: {radius_ctl}px;
            background: {bg_card_style};
            border: 1px solid {border_style};
            color: {p["text_primary"]};
            padding: 6px 10px;
            selection-background-color: {p["accent_soft"]};
            selection-color: {p["accent_hover"]};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {p["accent"]};
            background: {p["bg_card"]};
        }}
        QPushButton#tableActionBtn {{
            min-width: 52px;
            max-width: 72px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
        }}
        QTextBrowser#consoleView, QPlainTextEdit#consoleView {{
            background-color: {p["console_codex"]["bg"]};
            border: 1px solid {p["console_codex"]["border"]};
            border-radius: {radius_ctl + 2}px;
            color: {p["console_codex"]["fg"]};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace;
            font-size: 12px;
            padding: 10px;
            selection-background-color: {p["console_codex"]["selection_bg"]};
            selection-color: {p["console_codex"]["selection_fg"]};
        }}
        QPushButton#tableDangerBtn {{
            min-width: 52px;
            max-width: 72px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 700;
            color: {p["danger"]};
            background: transparent;
            border: 1px solid {p["danger"]};
        }}
        QPushButton#tableDangerBtn:hover {{
            background: {p["danger"]};
            color: white;
        }}
        QPushButton#routerConfigBtn {{
            min-width: 58px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 700;
        }}
    """

def get_tool_dialog_styles() -> str:
    p = get_ui_palette()
    m = _theme_metrics(p)
    common = get_common_qss(p)
    use_glass = bool(m["glass"])
    bg_card_style = hex_to_rgba(p["bg_card"], m["card_alpha"]) if use_glass else p["bg_card"]
    bg_soft_style = hex_to_rgba(p["bg_soft"], m["soft_alpha"]) if use_glass else p["bg_soft"]
    border_style = hex_to_rgba(p["border"], m["border_alpha"]) if use_glass else p["border"]
    radius_card = m["radius_card"]
    radius_ctl = m["radius_control"]
    return common + f"""
        QDialog {{
            background-color: {p["bg_app"]};
            font-family: {m["font"]};
        }}
        QFrame#dialogShell {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {radius_card}px;
        }}
        QFrame#dialogHeader, QFrame#dialogSection {{
            background: {bg_soft_style};
            border: 1px solid {border_style};
            border-radius: {max(12, radius_card - 4)}px;
        }}
        QLabel#dialogTitle {{
            color: {p["text_primary"]};
            font-size: 20px;
            font-weight: 700;
        }}
        QLabel#dialogDesc, QLabel#dialogHint {{
            color: {p["text_secondary"]};
            font-size: 12px;
        }}
        QTableWidget, QTextEdit, QPlainTextEdit, QListWidget, QTabWidget::pane, QLineEdit, QComboBox {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {radius_ctl + 2}px;
            color: {p["text_primary"]};
        }}
        QTableWidget::item {{
            color: {p["text_primary"]};
            background-color: transparent;
            padding: 4px;
        }}
        QTableWidget::item:selected {{
            background-color: {p["accent_soft"]};
            color: {p["accent_hover"]};
        }}
        QTextBrowser#consoleView, QPlainTextEdit#consoleView {{
            background-color: {hex_to_rgba(p["console_codex"]["bg"], 0.9 if use_glass else 1.0)};
            border: 1px solid {hex_to_rgba(p["console_codex"]["border"], 0.8)};
            border-radius: {radius_ctl + 2}px;
            color: {p["console_codex"]["fg"]};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace;
            font-size: 12px;
            padding: 10px;
            selection-background-color: {p["console_codex"]["selection_bg"]};
            selection-color: {p["console_codex"]["selection_fg"]};
        }}
        QHeaderView::section {{
            background: {bg_soft_style};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {border_style};
        }}
        QPushButton {{
            background: {bg_card_style};
            color: {p["text_primary"]};
            border: 1px solid {hex_to_rgba(p["border_strong"], 0.75 if use_glass else 1.0)};
            border-radius: {radius_ctl}px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p["accent"]};
            color: {p["accent_hover"]};
            background: {p["accent_soft"]};
        }}
        QPushButton#primaryAction, QPushButton#primary_btn {{
            background: {p["accent"]};
            color: white;
            border: none;
        }}
        QPushButton#primaryAction:hover, QPushButton#primary_btn:hover {{
            background: {p["accent_hover"]};
            color: white;
        }}
        QPushButton#main_btn {{
            background: {bg_card_style};
            color: {p["text_primary"]};
        }}
        QCheckBox {{
            color: {p["text_primary"]};
            spacing: 6px;
        }}
    """

def get_memory_dialog_styles() -> str:
    p = get_ui_palette()
    m = _theme_metrics(p)
    common = get_common_qss(p)
    use_glass = bool(m["glass"])
    bg_card_style = hex_to_rgba(p["bg_card"], m["card_alpha"]) if use_glass else p["bg_card"]
    bg_soft_style = hex_to_rgba(p["bg_soft"], m["soft_alpha"]) if use_glass else p["bg_soft"]
    border_style = hex_to_rgba(p["border"], m["border_alpha"]) if use_glass else p["border"]
    radius_ctl = m["radius_control"]
    return common + f"""
        QDialog {{
            background-color: {p["bg_app"]};
            font-family: {m["font"]};
        }}
        QTabWidget::pane {{
            border: 1px solid {border_style};
            border-radius: {radius_ctl}px;
            background: {bg_card_style};
        }}
        QTabBar::tab {{
            background: {bg_soft_style};
            border: 1px solid {border_style};
            color: {p["text_secondary"]};
            border-top-left-radius: {radius_ctl}px;
            border-top-right-radius: {radius_ctl}px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QTableWidget, QTextEdit, QPlainTextEdit, QListWidget, QLineEdit, QComboBox,
        QTreeWidget#memoryCategoryTree, QTreeWidget#memoryProfileOverview {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {radius_ctl}px;
            color: {p["text_primary"]};
        }}
        QTreeWidget#memoryCategoryTree {{
            padding: 6px;
            outline: none;
        }}
        QTreeWidget#memoryCategoryTree::item {{
            min-height: 28px;
            padding: 3px 6px;
            border-radius: 6px;
            color: {p["text_secondary"]};
        }}
        QTreeWidget#memoryCategoryTree::item:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QTreeWidget#memoryProfileOverview {{
            padding: 8px;
            outline: none;
        }}
        QTreeWidget#memoryProfileOverview::item {{
            min-height: 27px;
            padding: 3px 7px;
            color: {p["text_secondary"]};
        }}
        QTreeWidget#memoryProfileOverview::item:selected {{
            background: {p["accent_soft"]};
            color: {p["text_primary"]};
        }}
        QWidget#memoryEditorPanel {{
            background: {bg_soft_style};
            border: 1px solid {border_style};
            border-radius: {radius_ctl}px;
        }}
        QHeaderView::section {{
            background: {bg_soft_style};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 7px;
            border: none;
            border-bottom: 1px solid {border_style};
        }}
        QGroupBox {{
            color: {p["text_primary"]};
            border: 1px solid {border_style};
            border-radius: {radius_ctl}px;
            margin-top: 8px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }}
        QPushButton {{
            border-radius: {radius_ctl}px;
        }}
        QPushButton#memoryPrimaryAction {{
            background: {p["accent"]};
            color: white;
            border: 1px solid {p["accent"]};
            font-weight: 700;
        }}
        QPushButton#memoryPrimaryAction:hover {{
            background: {p["accent_hover"]};
            border-color: {p["accent_hover"]};
        }}
        QPushButton#memoryDangerAction {{
            background: transparent;
            color: {p["danger"]};
            border: 1px solid {p["danger"]};
        }}
        QPushButton#memoryDangerAction:hover {{
            background: {p["danger"]};
            color: white;
        }}
        QLabel {{
            color: {p["text_primary"]};
        }}
    """

def get_character_editor_styles() -> str:
    p = get_ui_palette()
    m = _theme_metrics(p)
    common = get_common_qss(p)
    use_glass = bool(m["glass"])
    bg_card_style = hex_to_rgba(p["bg_card"], m["card_alpha"]) if use_glass else p["bg_card"]
    bg_soft_style = hex_to_rgba(p["bg_soft"], m["soft_alpha"]) if use_glass else p["bg_soft"]
    border_style = hex_to_rgba(p["border"], m["border_alpha"]) if use_glass else p["border"]
    radius_card = m["radius_card"]
    radius_ctl = m["radius_control"]
    return common + f"""
        QWidget {{
            font-family: {m["font"]};
        }}
        QFrame#charLeftCard, QFrame#charRightCard, QGroupBox {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {max(12, radius_card - 4)}px;
        }}
        QLabel#charSectionTitle {{
            color: {p["text_primary"]};
            font-size: 15px;
            font-weight: 700;
        }}
        QLabel#charHint {{
            color: {p["text_secondary"]};
            font-size: 12px;
        }}
        QListWidget, QLineEdit, QTextEdit, QComboBox, QTableWidget {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: {radius_ctl}px;
            color: {p["text_primary"]};
        }}
        QListWidget::item {{
            padding: 8px 10px;
            border-radius: 8px;
            margin: 2px 0;
            color: {p["text_secondary"]};
        }}
        QListWidget::item:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QTabWidget::pane {{
            border: none;
            background: transparent;
        }}
        QTabBar::tab {{
            background: {bg_soft_style};
            color: {p["text_secondary"]};
            border: 1px solid {border_style};
            border-top-left-radius: {radius_ctl}px;
            border-top-right-radius: {radius_ctl}px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QPushButton {{
            background: {bg_card_style};
            color: {p["text_primary"]};
            border: 1px solid {hex_to_rgba(p["border_strong"], 0.75 if use_glass else 1.0)};
            border-radius: {radius_ctl}px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p["accent"]};
            color: {p["accent_hover"]};
            background: {p["accent_soft"]};
        }}
        QPushButton#charPrimary {{
            background: {p["accent"]};
            color: white;
            border: none;
        }}
        QPushButton#charPrimary:hover {{
            background: {p["accent_hover"]};
            color: white;
        }}
        QPushButton#charDanger {{
            background: transparent;
            color: {p["danger"]};
            border: 1px solid {p["danger"]};
        }}
        QPushButton#charDanger:hover {{
            background: {p["danger"]};
            color: white;
        }}
        QHeaderView::section {{
            background: {bg_soft_style};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {border_style};
        }}
        QGroupBox {{
            margin-top: 12px;
            padding-top: 12px;
            font-weight: 700;
            color: {p["text_primary"]};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
        }}
    """
