from pathlib import Path


UI_PALETTE = {
    "accent": "#6366F1",
    "accent_hover": "#4F46E5",
    "accent_soft": "#EEF2FF",
    "bg_app": "#F5F7FB",
    "bg_card": "#FFFFFF",
    "bg_soft": "#F3F4F6",
    "bg_console": "#111827",
    "border": "#E5E7EB",
    "border_strong": "#D1D5DB",
    "text_primary": "#111827",
    "text_secondary": "#6B7280",
    "text_muted": "#9CA3AF",
    "success": "#10B981",
    "success_soft": "#D1FAE5",
    "warning": "#F59E0B",
    "danger": "#EF4444",
}

DEFAULT_CONSOLE = {
    "bg": "#0B1220",
    "fg": "#E5E7EB",
    "border": "#1F2937",
    "selection_bg": "#1E293B",
    "selection_fg": "#E5E7EB",
    "muted": "#94A3B8",
    "label": "#CBD5E1",
}

def _merge_palette(base: dict, override: dict) -> dict:
    if not isinstance(override, dict):
        return base
    for k, v in override.items():
        if isinstance(v, str):
            base[k] = v
    return base

def get_ui_palette() -> dict:
    p = dict(UI_PALETTE)
    p["console_main"] = dict(DEFAULT_CONSOLE)
    p["console_codex"] = dict(DEFAULT_CONSOLE)
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


def get_panel_styles() -> str:
    p = get_ui_palette()
    return f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei';
            color: {p['text_primary']};
        }}
        QFrame#container {{
            background-color: {p['bg_card']};
            border-radius: 20px;
            border: 1px solid {p['border']};
        }}
        QLabel#statusLabel {{
            color: {p['text_muted']};
            font-size: 11px;
            font-weight: 600;
            margin-left: 4px;
        }}
        QLabel#characterLabel {{
            color: {p['text_secondary']};
            font-size: 11px;
            margin-left: 8px;
        }}
        QPushButton#windowCtl {{
            background: transparent;
            color: {p['border_strong']};
            border: none;
            font-weight: bold;
            font-size: 15px;
            width: 22px;
            height: 22px;
        }}
        QPushButton#windowCtl:hover {{
            color: {p['danger']};
        }}
        QFrame#heroCard {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {p['accent_soft']}, stop:1 #F8FAFC);
            border: 1px solid #DDE6FF;
            border-radius: 16px;
        }}
        QLabel#heroTitle {{
            color: {p['text_primary']};
            font-size: 16px;
            font-weight: 700;
        }}
        QLabel#heroHint {{
            color: {p['text_secondary']};
            font-size: 12px;
            line-height: 1.4;
        }}
        QLabel#pillLabel {{
            background: {p['bg_card']};
            color: {p['text_secondary']};
            border: 1px solid #DCE4F7;
            border-radius: 11px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
        }}
                QTextEdit#historyView {{
            background-color: {p['console_main']['bg']};
            border: 1px solid {p['console_main']['border']};
            border-radius: 12px;
            color: {p['console_main']['fg']};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', 'Segoe UI', 'Microsoft YaHei', monospace;
            font-size: 12px;
            line-height: 1.6;
            padding: 10px;
            selection-background-color: {p['console_main']['selection_bg']};
            selection-color: {p['console_main']['selection_fg']};
        }}
        QFrame#inputShell {{
            background-color: {p['bg_soft']};
            border-radius: 15px;
            border: 1px solid transparent;
            min-height: 36px;
            max-height: 36px;
        }}
        QFrame#inputShell:hover {{
            background-color: {p['bg_card']};
            border-color: {p['border']};
        }}
        QLineEdit#chatInput {{
            background: transparent;
            border: none;
            color: {p['text_primary']};
            font-size: 14px;
            padding-left: 6px;
        }}
        QPushButton#sendButton {{
            background-color: {p['accent']};
            color: white;
            border-radius: 15px;
            font-weight: bold;
            font-size: 14px;
            width: 30px;
            height: 30px;
            border: none;
        }}
        QPushButton#sendButton:hover {{
            background-color: {p['accent_hover']};
        }}
        QPushButton#toolbarBtn {{
            background: transparent;
            color: {p['text_secondary']};
            border: none;
            font-size: 15px;
            width: 27px;
            height: 27px;
            border-radius: 9px;
        }}
        QPushButton#toolbarBtn:hover {{
            background: {p['bg_soft']};
            color: {p['text_primary']};
        }}
        QMenu {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 12px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 14px;
            border-radius: 8px;
        }}
        QMenu::item:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
        }}
    """


def get_settings_styles() -> str:
    p = get_ui_palette()
    return f"""
        QDialog {{
            background-color: {p['bg_app']};
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QFrame#settingsNavCard, QFrame#settingsContentCard {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 18px;
        }}
        QFrame#settingsHeaderCard, QFrame#launchCard {{
            background: {p['bg_soft']};
            border: 1px solid {p['border']};
            border-radius: 14px;
        }}
        QLabel#settingsNavTitle {{
            font-size: 16px;
            font-weight: 700;
            color: {p['text_primary']};
        }}
        QLabel#settingsNavHint, QLabel#settingsPageDesc, QLabel#launchDesc {{
            color: {p['text_secondary']};
            font-size: 12px;
        }}
        QLabel#settingsPageTitle, QLabel.header, QLabel#launchTitle {{
            font-size: 20px;
            font-weight: 700;
            color: {p['text_primary']};
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
            color: {p['text_secondary']};
        }}
        QListWidget#settingsNav::item:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
            font-weight: 700;
        }}
        QTableWidget {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 12px;
            gridline-color: {p['border']};
        }}
        QHeaderView::section {{
            background: {p['bg_card']};
            color: {p['text_secondary']};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p['border']};
        }}
        QPushButton {{
            background: {p['bg_card']};
            color: {p['text_primary']};
            border: 1px solid {p['border_strong']};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p['accent']};
            color: {p['accent_hover']};
        }}
        QPushButton#primaryAction {{
            background: {p['accent']};
            color: white;
            border: none;
        }}
        QPushButton#primaryAction:hover {{
            background: {p['accent_hover']};
            color: white;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QListWidget, QTabWidget::pane {{
            border-radius: 10px;
        }}
        QPushButton#tableActionBtn {{
            min-width: 64px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 600;
        }}
                QTextBrowser#consoleView {{
            background-color: {p['console_codex']['bg']};
            border: 1px solid {p['console_codex']['border']};
            border-radius: 12px;
            color: {p['console_codex']['fg']};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace;
            font-size: 12px;
            selection-background-color: {p['console_codex']['selection_bg']};
            selection-color: {p['console_codex']['selection_fg']};
        }}
        QPushButton#tableDangerBtn {{
            min-width: 64px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 700;
            color: #DC2626;
            background: #FEF2F2;
            border: 1px solid #FECACA;
        }}
        QPushButton#tableDangerBtn:hover {{
            color: #B91C1C;
            background: #FEE2E2;
            border-color: #FCA5A5;
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
    return f"""
        QDialog {{
            background-color: {p['bg_app']};
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QFrame#dialogShell {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 18px;
        }}
        QFrame#dialogHeader, QFrame#dialogSection {{
            background: {p['bg_soft']};
            border: 1px solid {p['border']};
            border-radius: 14px;
        }}
        QLabel#dialogTitle {{
            color: {p['text_primary']};
            font-size: 20px;
            font-weight: 700;
        }}
        QLabel#dialogDesc, QLabel#dialogHint {{
            color: {p['text_secondary']};
            font-size: 12px;
        }}
        QTableWidget, QTextEdit, QPlainTextEdit, QListWidget, QTabWidget::pane, QLineEdit, QComboBox {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 12px;
        }}
        QTextBrowser#consoleView {{
            background-color: {p['console_codex']['bg']};
            border: 1px solid {p['console_codex']['border']};
            border-radius: 12px;
            color: {p['console_codex']['fg']};
            font-family: 'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace;
            font-size: 12px;
            selection-background-color: {p['console_codex']['selection_bg']};
            selection-color: {p['console_codex']['selection_fg']};
        }}
        QHeaderView::section {{
            background: {p['bg_card']};
            color: {p['text_secondary']};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p['border']};
        }}
        QPushButton {{
            background: {p['bg_card']};
            color: {p['text_primary']};
            border: 1px solid {p['border_strong']};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p['accent']};
            color: {p['accent_hover']};
        }}
        QPushButton#primaryAction, QPushButton#primary_btn {{
            background: {p['accent']};
            color: white;
            border: none;
        }}
        QPushButton#primaryAction:hover, QPushButton#primary_btn:hover {{
            background: {p['accent_hover']};
            color: white;
        }}
        QPushButton#main_btn {{
            background: {p['bg_card']};
            color: {p['text_primary']};
        }}
        QCheckBox {{
            color: {p['text_primary']};
            spacing: 6px;
        }}
    """


def get_memory_dialog_styles() -> str:
    p = get_ui_palette()
    return f"""
        QDialog {{
            background-color: {p['bg_app']};
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QTabWidget::pane {{
            border: 1px solid {p['border']};
            border-radius: 12px;
            background: {p['bg_card']};
        }}
        QTabBar::tab {{
            background: {p['bg_soft']};
            border: 1px solid {p['border']};
            color: {p['text_secondary']};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
            font-weight: 700;
        }}
        QTableWidget, QTextEdit, QPlainTextEdit, QListWidget, QLineEdit, QComboBox {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 10px;
        }}
        QPushButton {{
            border-radius: 10px;
        }}
        QLabel {{
            color: {p['text_primary']};
        }}
    """



def get_character_editor_styles() -> str:
    p = get_ui_palette()
    return f"""
        QWidget {{
            font-family: 'Segoe UI', 'Microsoft YaHei';
        }}
        QFrame#charLeftCard, QFrame#charRightCard, QGroupBox {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 14px;
        }}
        QLabel#charSectionTitle {{
            color: {p['text_primary']};
            font-size: 15px;
            font-weight: 700;
        }}
        QLabel#charHint {{
            color: {p['text_secondary']};
            font-size: 12px;
        }}
        QListWidget, QLineEdit, QTextEdit, QComboBox, QTableWidget {{
            background: {p['bg_card']};
            border: 1px solid {p['border']};
            border-radius: 10px;
        }}
        QListWidget::item {{
            padding: 8px 10px;
            border-radius: 8px;
            margin: 2px 0;
        }}
        QListWidget::item:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
            font-weight: 700;
        }}
        QTabWidget::pane {{
            border: none;
            background: transparent;
        }}
        QTabBar::tab {{
            background: {p['bg_soft']};
            color: {p['text_secondary']};
            border: 1px solid {p['border']};
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {p['accent_soft']};
            color: {p['accent_hover']};
            font-weight: 700;
        }}
        QPushButton {{
            background: {p['bg_card']};
            color: {p['text_primary']};
            border: 1px solid {p['border_strong']};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {p['accent']};
            color: {p['accent_hover']};
        }}
        QPushButton#charPrimary {{
            background: {p['accent']};
            color: white;
            border: none;
        }}
        QPushButton#charPrimary:hover {{
            background: {p['accent_hover']};
            color: white;
        }}
        QPushButton#charDanger {{
            background: #FEF2F2;
            color: #DC2626;
            border: 1px solid #FECACA;
        }}
        QPushButton#charDanger:hover {{
            background: #FEE2E2;
            color: #B91C1C;
            border-color: #FCA5A5;
        }}
            background: {p['bg_card']};
            color: {p['text_secondary']};
            font-weight: 600;
            padding: 8px;
            border: none;
            border-bottom: 1px solid {p['border']};
        }}
        QGroupBox {{
            margin-top: 12px;
            padding-top: 12px;
            font-weight: 700;
            color: {p['text_primary']};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
        }}
    """








