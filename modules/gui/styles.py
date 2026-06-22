import json
from pathlib import Path

# 定义内置的主题字典
THEMES = {
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
    }
}

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
        return runtime.get("theme_name", "Indigo (靛蓝)")
    except Exception:
        return "Indigo (靛蓝)"

def get_ui_palette() -> dict:
    theme_name = get_current_theme_name()
    if theme_name not in THEMES:
        theme_name = "Indigo (靛蓝)"
    
    base = dict(THEMES[theme_name])
    
    p = dict(base)
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
    except Exception:
        pass
    return p

# 各种基础控件风格（滚动条、提示框、输入框焦点等）
def get_common_qss(p: dict) -> str:
    return f"""
        /* 针对所有的 QScrollBar 进行深度美化 */
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 0px 0px 0px 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {p["border_strong"]};
            min-height: 20px;
            border-radius: 4px;
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
            height: 8px;
            margin: 0px 0px 0px 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: {p["border_strong"]};
            min-width: 20px;
            border-radius: 4px;
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
            background-color: {p["bg_card"]};
            color: {p["text_primary"]};
            border: 1px solid {p["border_strong"]};
            border-radius: 6px;
            padding: 4px 8px;
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}

        /* 输入框的 focus 状态环 */
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
            border: 2px solid {p["accent"]};
            outline: none;
        }}

        /* 隐藏掉系统难看的 Focus 虚线框 */
        * {{
            outline: none;
        }}
        
        /* 美化 QComboBox 下拉栏 */
        QComboBox {{
            padding: 4px 10px;
            border-radius: 8px;
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
            border-radius: 8px;
            selection-background-color: {p["accent_soft"]};
            selection-color: {p["accent_hover"]};
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 8px;
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
    common = get_common_qss(p)
    bg_qss, bg_active = get_background_image_qss(for_settings=False)
    
    if bg_active:
        bg_card_style = hex_to_rgba(p["bg_card"], 0.75)
        bg_soft_style = hex_to_rgba(p["bg_soft"], 0.75)
        border_style = hex_to_rgba(p["border"], 0.5)
        console_bg_style = hex_to_rgba(p["console_main"]["bg"], 0.75)
    else:
        bg_card_style = p["bg_card"]
        bg_soft_style = p["bg_soft"]
        border_style = p["border"]
        console_bg_style = p["console_main"]["bg"]
        
    return common + f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei';
            color: {p["text_primary"]};
        }}
        QFrame#container {{
            background-color: {bg_card_style};
            {bg_qss}
            border-radius: 20px;
            border: 1px solid {border_style};
        }}
        QLabel#statusLabel {{
            color: {p["text_muted"]};
            font-size: 11px;
            font-weight: 600;
            margin-left: 4px;
        }}
        QLabel#characterLabel {{
            color: {p["text_secondary"]};
            font-size: 11px;
            margin-left: 8px;
        }}
        QLabel#workSessionLabel {{
            color: {p["text_secondary"]};
            background: {p["bg_soft"]};
            border: 1px solid {p["border"]};
            border-radius: 8px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 8px;
        }}
        QPushButton#windowCtl {{
            background: transparent;
            color: {p["border_strong"]};
            border: none;
            font-weight: bold;
            font-size: 15px;
            width: 22px;
            height: 22px;
        }}
        QPushButton#windowCtl:hover {{
            color: {p["danger"]};
        }}
        QFrame#heroCard {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {p["accent_soft"]}, stop:1 {p["bg_app"]});
            border: 1px solid {border_style};
            border-radius: 16px;
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
            background: {p["bg_card"]};
            color: {p["text_secondary"]};
            border: 1px solid {p["border"]};
            border-radius: 11px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
        }}
        QTextEdit#historyView {{
            background-color: {console_bg_style};
            border: 1px solid {p["console_main"]["border"]};
            border-radius: 12px;
            color: {p["console_main"]["fg"]};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', 'Segoe UI', 'Microsoft YaHei', monospace;
            font-size: 12px;
            line-height: 1.6;
            padding: 10px;
            selection-background-color: {p["console_main"]["selection_bg"]};
            selection-color: {p["console_main"]["selection_fg"]};
        }}
        QFrame#inputShell {{
            background-color: {bg_soft_style};
            border-radius: 15px;
            border: 1px solid transparent;
            min-height: 36px;
            max-height: 36px;
        }}
        QFrame#inputShell:hover {{
            background-color: {p["bg_card"]};
            border-color: {p["accent"]};
        }}
        QLineEdit#chatInput {{
            background: transparent;
            border: none;
            color: {p["text_primary"]};
            font-size: 14px;
            padding-left: 6px;
        }}
        QLineEdit#chatInput:focus {{
            border: none;
        }}
        QPushButton#sendButton {{
            background-color: {p["accent"]};
            color: white;
            border-radius: 15px;
            font-weight: bold;
            font-size: 14px;
            width: 30px;
            height: 30px;
            border: none;
        }}
        QPushButton#sendButton:hover {{
            background-color: {p["accent_hover"]};
        }}
        QPushButton#toolbarBtn {{
            background: transparent;
            color: {p["text_secondary"]};
            border: none;
            font-size: 15px;
            width: 27px;
            height: 27px;
            border-radius: 9px;
        }}
        QPushButton#toolbarBtn:hover {{
            background: {p["bg_soft"]};
            color: {p["text_primary"]};
        }}
        QMenu {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 12px;
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
    common = get_common_qss(p)
    bg_qss, bg_active = get_background_image_qss(for_settings=True)
    
    if bg_active:
        bg_card_style = hex_to_rgba(p["bg_card"], 0.75)
        bg_soft_style = hex_to_rgba(p["bg_soft"], 0.75)
        border_style = hex_to_rgba(p["border"], 0.5)
    else:
        bg_card_style = p["bg_card"]
        bg_soft_style = p["bg_soft"]
        border_style = p["border"]
        
    return common + f"""
        QDialog {{
            background-color: {p["bg_app"]};
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QDialog#SettingsDialog {{
            {bg_qss}
        }}
        QFrame#settingsNavCard, QFrame#settingsContentCard {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: 18px;
        }}
        QFrame#settingsHeaderCard, QFrame#launchCard {{
            background: {bg_card_style};
            border: 1px solid {border_style};
            border-radius: 14px;
        }}
        QFrame#settingsActionBar {{
            background: {bg_soft_style};
            border: 1px solid {border_style};
            border-radius: 16px;
        }}
        QLabel#settingsNavTitle {{
            font-size: 16px;
            font-weight: 700;
            color: {p["text_primary"]};
        }}
        QLabel#settingsNavHint, QLabel#settingsPageDesc, QLabel#launchDesc {{
            color: {p["text_secondary"]};
            font-size: 12px;
        }}
        QLabel#settingsPageTitle, QLabel.header, QLabel#launchTitle {{
            font-size: 20px;
            font-weight: 700;
            color: {p["text_primary"]};
        }}
        QListWidget#settingsNav {{
            background: transparent;
            border: none;
            outline: none;
            padding: 4px;
        }}
        QListWidget#settingsNav::item {{
            padding: 10px 12px;
            margin: 2px 0;
            border-radius: 10px;
            color: {p["text_secondary"]};
        }}
        QListWidget#settingsNav::item:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QTableWidget {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 12px;
            gridline-color: {p["border"]};
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
            background: {p["bg_soft"]};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p["border"]};
        }}
        QPushButton {{
            background: {p["bg_card"]};
            color: {p["text_primary"]};
            border: 1px solid {p["border_strong"]};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p["accent"]};
            color: {p["accent_hover"]};
        }}
        QPushButton#primaryAction {{
            background: {p["accent"]};
            color: white;
            border: none;
        }}
        QPushButton#primaryAction:hover {{
            background: {p["accent_hover"]};
            color: white;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QListWidget, QTabWidget::pane {{
            border-radius: 10px;
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            color: {p["text_primary"]};
        }}
        QPushButton#tableActionBtn {{
            min-width: 64px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 600;
        }}
        QTextBrowser#consoleView, QPlainTextEdit#consoleView {{
            background-color: {p["console_codex"]["bg"]};
            border: 1px solid {p["console_codex"]["border"]};
            border-radius: 12px;
            color: {p["console_codex"]["fg"]};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace;
            font-size: 12px;
            padding: 10px;
            selection-background-color: {p["console_codex"]["selection_bg"]};
            selection-color: {p["console_codex"]["selection_fg"]};
        }}
        QPushButton#tableDangerBtn {{
            min-width: 64px;
            padding: 6px 12px;
            font-size: 13px;
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
    common = get_common_qss(p)
    return common + f"""
        QDialog {{
            background-color: {p["bg_app"]};
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QFrame#dialogShell {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 18px;
        }}
        QFrame#dialogHeader, QFrame#dialogSection {{
            background: {p["bg_soft"]};
            border: 1px solid {p["border"]};
            border-radius: 14px;
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
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 12px;
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
            background-color: {p["console_codex"]["bg"]};
            border: 1px solid {p["console_codex"]["border"]};
            border-radius: 12px;
            color: {p["console_codex"]["fg"]};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace;
            font-size: 12px;
            padding: 10px;
            selection-background-color: {p["console_codex"]["selection_bg"]};
            selection-color: {p["console_codex"]["selection_fg"]};
        }}
        QHeaderView::section {{
            background: {p["bg_soft"]};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p["border"]};
        }}
        QPushButton {{
            background: {p["bg_card"]};
            color: {p["text_primary"]};
            border: 1px solid {p["border_strong"]};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p["accent"]};
            color: {p["accent_hover"]};
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
            background: {p["bg_card"]};
            color: {p["text_primary"]};
        }}
        QCheckBox {{
            color: {p["text_primary"]};
            spacing: 6px;
        }}
    """

def get_memory_dialog_styles() -> str:
    p = get_ui_palette()
    common = get_common_qss(p)
    return common + f"""
        QDialog {{
            background-color: {p["bg_app"]};
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QTabWidget::pane {{
            border: 1px solid {p["border"]};
            border-radius: 12px;
            background: {p["bg_card"]};
        }}
        QTabBar::tab {{
            background: {p["bg_soft"]};
            border: 1px solid {p["border"]};
            color: {p["text_secondary"]};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QTableWidget, QTextEdit, QPlainTextEdit, QListWidget, QLineEdit, QComboBox {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 10px;
            color: {p["text_primary"]};
        }}
        QPushButton {{
            border-radius: 10px;
        }}
        QLabel {{
            color: {p["text_primary"]};
        }}
    """

def get_character_editor_styles() -> str:
    p = get_ui_palette()
    common = get_common_qss(p)
    return common + f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QFrame#charLeftCard, QFrame#charRightCard, QGroupBox {{
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 14px;
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
            background: {p["bg_card"]};
            border: 1px solid {p["border"]};
            border-radius: 10px;
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
            background: {p["bg_soft"]};
            color: {p["text_secondary"]};
            border: 1px solid {p["border"]};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p["accent"]};
            color: white;
            font-weight: 700;
        }}
        QPushButton {{
            background: {p["bg_card"]};
            color: {p["text_primary"]};
            border: 1px solid {p["border_strong"]};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p["accent"]};
            color: {p["accent_hover"]};
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
            background: {p["bg_soft"]};
            color: {p["text_secondary"]};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p["border"]};
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
