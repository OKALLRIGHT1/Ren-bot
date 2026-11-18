"""Plugin market cards with modern theme-aware styling, group pills, and status filters."""

from __future__ import annotations

import functools
import os

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.styles import get_ui_palette, hex_to_rgba
from modules.gui.user_ia import (
    CAPABILITY_GROUP_LABELS,
    capability_group,
    capability_group_label,
)
from modules.plugin_market import (
    PluginMarketError,
    default_catalog_cache_path,
    default_installed_root,
    fetch_catalog,
    http_get,
    install_from_github,
    load_cached_catalog,
    market_status,
    save_cached_catalog,
    uninstall_plugin,
)


def _is_dark_palette(palette: dict) -> bool:
    bg = str(palette.get("bg_card") or "#FFFFFF").strip().lstrip("#")
    if len(bg) == 3:
        bg = "".join(ch * 2 for ch in bg)
    if len(bg) == 6:
        try:
            r = int(bg[0:2], 16)
            g = int(bg[2:4], 16)
            b = int(bg[4:6], 16)
            return (0.299 * r + 0.587 * g + 0.114 * b) < 128
        except ValueError:
            return False
    return False


def _badge_style(group: str, palette: dict | None = None) -> str:
    p = palette or get_ui_palette()
    dark = _is_dark_palette(p)
    if dark:
        table = {
            "companion": ("rgba(99, 102, 241, 0.20)", "#A5B4FC", "rgba(129, 140, 248, 0.36)"),
            "desktop": ("rgba(16, 185, 129, 0.18)", "#6EE7B7", "rgba(52, 211, 153, 0.34)"),
            "qq": ("rgba(59, 130, 246, 0.20)", "#93C5FD", "rgba(96, 165, 250, 0.36)"),
            "developer": ("rgba(245, 158, 11, 0.20)", "#FCD34D", "rgba(251, 191, 36, 0.36)"),
        }
        default_tuple = (
            hex_to_rgba(p.get("bg_soft", "#27272A"), 0.9),
            p.get("text_secondary", "#A1A1AA"),
            hex_to_rgba(p.get("border", "#3F3F46"), 0.8),
        )
    else:
        table = {
            "companion": ("#EEF2FF", "#4338CA", "#C7D2FE"),
            "desktop": ("#ECFDF5", "#047857", "#A7F3D0"),
            "qq": ("#EFF6FF", "#1D4ED8", "#BFDBFE"),
            "developer": ("#FEF3C7", "#B45309", "#FDE68A"),
        }
        default_tuple = ("#F3F4F6", "#4B5563", "#E5E7EB")

    background, foreground, border = table.get(group, default_tuple)
    return (
        f"background-color: {background}; color: {foreground}; border: 1px solid {border};"
        "padding: 1px 7px; border-radius: 7px; font-size: 10px; font-weight: 600;"
    )


def _group_icon_spec(group: str, palette: dict | None = None) -> tuple[str, str, str]:
    p = palette or get_ui_palette()
    dark = _is_dark_palette(p)
    icons = {
        "companion": "✦",
        "desktop": "⌘",
        "qq": "◈",
        "developer": "⚙",
    }
    glyph = icons.get(group, "✦")
    if dark:
        colors = {
            "companion": ("rgba(99, 102, 241, 0.22)", "#A5B4FC"),
            "desktop": ("rgba(16, 185, 129, 0.20)", "#6EE7B7"),
            "qq": ("rgba(59, 130, 246, 0.22)", "#93C5FD"),
            "developer": ("rgba(245, 158, 11, 0.22)", "#FCD34D"),
        }
    else:
        colors = {
            "companion": ("#EEF2FF", "#4F46E5"),
            "desktop": ("#ECFDF5", "#059669"),
            "qq": ("#EFF6FF", "#2563EB"),
            "developer": ("#FEF3C7", "#D97706"),
        }
    bg, fg = colors.get(group, ("#F3F4F6", "#4B5563"))
    return glyph, bg, fg


class _ElidedLabel(QtWidgets.QLabel):
    """Single-line label that dynamically elides text when resized."""

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.setMinimumWidth(40)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "").replace("\n", " ").strip()
        self._update_elided()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        avail = max(120, self.width() - 2) if self.width() > 24 else 320
        elided = self.fontMetrics().elidedText(
            self._full_text, QtCore.Qt.TextElideMode.ElideRight, avail
        )
        super().setText(elided)


class _MarketJob(QtCore.QThread):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self):
        try:
            result = self._work(self.progress.emit)
        except PluginMarketError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"插件市场操作失败：{exc}")
        else:
            self.succeeded.emit(result)


class PluginMarketPanel(QtWidgets.QWidget):
    plugins_changed = QtCore.Signal()

    def __init__(
        self,
        plugin_manager=None,
        parent=None,
        installed_root=None,
        catalog_cache_path=None,
    ):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.installed_root = installed_root or default_installed_root()
        self.catalog_cache_path = catalog_cache_path or default_catalog_cache_path()
        self._developer_visible = False
        self._listings: list[dict] = []
        self._cards: list[dict] = []
        self._busy = False
        self._job: _MarketJob | None = None
        self._on_success = None
        self._report_error = False
        self._loaded = False
        self._active_group = ""
        self._active_status = ""
        self._group_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._status_buttons: dict[str, QtWidgets.QPushButton] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 顶部控制工作台卡片
        self.toolbar_card = QtWidgets.QFrame(self)
        self.toolbar_card.setObjectName("pluginToolbarCard")
        toolbar_layout = QtWidgets.QVBoxLayout(self.toolbar_card)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(8)

        # 第一行：搜索 + 统计胶囊 + 刷新清单按钮
        search_row = QtWidgets.QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setObjectName("pluginSearchInput")
        self.search_input.setPlaceholderText("搜索市场插件名称、说明或触发词…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(34)
        self.search_input.textChanged.connect(self._apply_filter)

        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setObjectName("marketStatsBadge")
        self.stats_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.refresh_button = QtWidgets.QPushButton("↻ 刷新清单")
        self.refresh_button.setObjectName("marketToolBtn")
        self.refresh_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setFixedHeight(34)
        self.refresh_button.clicked.connect(self.refresh_catalog)

        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.stats_label)
        search_row.addWidget(self.refresh_button)
        toolbar_layout.addLayout(search_row)

        # 第二行：分组胶囊 + 状态筛选胶囊 + 说明文案
        pills_row = QtWidgets.QHBoxLayout()
        pills_row.setContentsMargins(0, 0, 0, 0)
        pills_row.setSpacing(6)

        self.group_pills_host = QtWidgets.QWidget(self.toolbar_card)
        self.group_pills_layout = QtWidgets.QHBoxLayout(self.group_pills_host)
        self.group_pills_layout.setContentsMargins(0, 0, 0, 0)
        self.group_pills_layout.setSpacing(5)
        pills_row.addWidget(self.group_pills_host)

        sep = QtWidgets.QFrame(self.toolbar_card)
        sep.setObjectName("filterPillSep")
        sep.setFixedSize(1, 16)
        pills_row.addWidget(sep)

        for status_key, label in (
            ("", "全部状态"),
            ("absent", "可安装"),
            ("update", "可更新"),
            ("installed", "已安装"),
        ):
            btn = QtWidgets.QPushButton(label, self.toolbar_card)
            btn.setObjectName("filterPillBtn")
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(functools.partial(self._set_status_filter, status_key))
            self._status_buttons[status_key] = btn
            pills_row.addWidget(btn)

        pills_row.addStretch(1)

        self.hint_label = QtWidgets.QLabel("安装后进入「本机能力」，不影响自带插件")
        self.hint_label.setObjectName("marketHintLabel")
        pills_row.addWidget(self.hint_label)

        toolbar_layout.addLayout(pills_row)
        layout.addWidget(self.toolbar_card)

        # 下载/状态进度条（忙碌时展开）
        self.progress_bar = QtWidgets.QProgressBar(self)
        self.progress_bar.setObjectName("marketProgressBar")
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 滚动卡片网格
        self.cards_scroll = QtWidgets.QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.cards_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setAutoFillBackground(False)
        outer = QtWidgets.QVBoxLayout(self.cards_container)
        outer.setContentsMargins(0, 2, 2, 4)
        outer.setSpacing(10)

        self.cards_grid = QtWidgets.QGridLayout()
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(12)
        self.cards_grid.setVerticalSpacing(10)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        outer.addLayout(self.cards_grid)

        self.empty_frame = QtWidgets.QFrame(self.cards_container)
        self.empty_frame.setObjectName("pluginEmptyCard")
        empty_layout = QtWidgets.QVBoxLayout(self.empty_frame)
        empty_layout.setContentsMargins(24, 34, 24, 34)
        empty_layout.setSpacing(8)
        empty_icon = QtWidgets.QLabel("📦", self.empty_frame)
        empty_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_icon.setStyleSheet("font-size: 26px; background: transparent;")
        empty_layout.addWidget(empty_icon)

        self.empty_label = QtWidgets.QLabel("点「刷新清单」读取插件仓库", self.empty_frame)
        self.empty_label.setObjectName("pluginEmptyTitle")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_label)

        self.empty_reset_btn = QtWidgets.QPushButton("清除筛选条件", self.empty_frame)
        self.empty_reset_btn.setObjectName("marketToolBtn")
        self.empty_reset_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.empty_reset_btn.setFixedHeight(30)
        self.empty_reset_btn.setVisible(False)
        self.empty_reset_btn.clicked.connect(self._reset_filters)
        empty_btn_row = QtWidgets.QHBoxLayout()
        empty_btn_row.addStretch(1)
        empty_btn_row.addWidget(self.empty_reset_btn)
        empty_btn_row.addStretch(1)
        empty_layout.addLayout(empty_btn_row)

        outer.addWidget(self.empty_frame)
        outer.addStretch(1)
        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll, 1)

        self._rebuild_group_pills()
        self._sync_pill_checked_states()
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = get_ui_palette()
        dark = _is_dark_palette(p)
        card_bg = p.get("bg_card", "#FFFFFF")
        soft_bg = p.get("bg_soft", "#F4F5F7")
        border = p.get("border", "#E5E7EB")
        accent = p.get("accent", "#2563EB")
        accent_hover = p.get("accent_hover", "#1D4ED8")
        accent_soft = p.get("accent_soft", "#EFF6FF")
        text_primary = p.get("text_primary", "#111827")
        text_secondary = p.get("text_secondary", "#6B7280")
        text_muted = p.get("text_muted", "#9CA3AF")
        danger = p.get("danger", "#DC2626")
        disabled_card_bg = hex_to_rgba(soft_bg, 0.68 if dark else 0.78)
        hover_card_bg = hex_to_rgba(accent_soft, 0.22 if dark else 0.35)

        self.setStyleSheet(
            f"""
            QFrame#pluginToolbarCard {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QLineEdit#pluginSearchInput {{
                background: {soft_bg};
                color: {text_primary};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QLineEdit#pluginSearchInput:focus {{
                background: {card_bg};
                border: 1.5px solid {accent};
            }}
            QLabel#marketStatsBadge {{
                background: {soft_bg};
                color: {text_secondary};
                border: 1px solid {border};
                border-radius: 9px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#marketHintLabel {{
                color: {text_muted};
                font-size: 11px;
                background: transparent;
            }}
            QFrame#filterPillSep {{
                background: {border};
                border: none;
            }}
            QPushButton#filterPillBtn {{
                background: transparent;
                color: {text_secondary};
                border: 1px solid transparent;
                border-radius: 13px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#filterPillBtn:hover {{
                background: {soft_bg};
                color: {text_primary};
            }}
            QPushButton#filterPillBtn:checked {{
                background: {accent_soft};
                color: {accent_hover if not dark else accent};
                border: 1px solid {hex_to_rgba(accent, 0.45)};
                font-weight: 700;
            }}
            QPushButton#marketToolBtn {{
                background: {card_bg};
                color: {text_primary};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#marketToolBtn:hover {{
                background: {accent_soft};
                color: {accent_hover if not dark else accent};
                border-color: {hex_to_rgba(accent, 0.5)};
            }}
            QPushButton#marketToolBtn:disabled {{
                color: {text_muted};
                background: {soft_bg};
            }}
            QProgressBar#marketProgressBar {{
                background: {soft_bg};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar#marketProgressBar::chunk {{
                background: {accent};
                border-radius: 3px;
            }}
            QFrame#pluginCard {{
                background: {card_bg};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QFrame#pluginCard:hover {{
                border-color: {hex_to_rgba(accent, 0.55)};
                background: {hover_card_bg};
            }}
            QFrame#pluginCardDisabled {{
                background: {disabled_card_bg};
                border: 1px solid {hex_to_rgba(border, 0.8)};
                border-radius: 14px;
            }}
            QFrame#pluginCardDisabled:hover {{
                border-color: {border};
                background: {card_bg};
            }}
            QPushButton#marketInstallBtn {{
                background: {accent};
                color: #FFFFFF;
                border: 1px solid {accent_hover};
                border-radius: 9px;
                padding: 3px 12px;
                font-size: 11px;
                font-weight: 700;
                min-width: 56px;
            }}
            QPushButton#marketInstallBtn:hover {{
                background: {accent_hover};
            }}
            QPushButton#marketStateBtn {{
                background: {soft_bg};
                color: {text_muted};
                border: 1px solid {border};
                border-radius: 9px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
                min-width: 56px;
            }}
            QPushButton#marketRemoveBtn {{
                background: transparent;
                color: {text_secondary};
                border: 1px solid {border};
                border-radius: 9px;
                padding: 3px 9px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#marketRemoveBtn:hover {{
                background: {hex_to_rgba(danger, 0.12)};
                color: {danger};
                border-color: {hex_to_rgba(danger, 0.45)};
            }}
            QFrame#pluginEmptyCard {{
                background: {hex_to_rgba(soft_bg, 0.55)};
                border: 1px dashed {border};
                border-radius: 14px;
            }}
            QLabel#pluginEmptyTitle {{
                color: {text_secondary};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }}
            """
        )
        if self._listings:
            self._rebuild_cards()

    def _rebuild_group_pills(self) -> None:
        while self.group_pills_layout.count():
            item = self.group_pills_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self._group_buttons.clear()

        specs = [("", "全部")]
        for key, label in CAPABILITY_GROUP_LABELS.items():
            if key == "developer" and not self._developer_visible:
                continue
            specs.append((key, label))

        if self._active_group == "developer" and not self._developer_visible:
            self._active_group = ""

        for group_key, label in specs:
            btn = QtWidgets.QPushButton(label, self.group_pills_host)
            btn.setObjectName("filterPillBtn")
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(functools.partial(self._set_group_filter, group_key))
            self._group_buttons[group_key] = btn
            self.group_pills_layout.addWidget(btn)

        self._sync_pill_checked_states()

    def _sync_pill_checked_states(self) -> None:
        for key, btn in self._group_buttons.items():
            btn.setChecked(key == self._active_group)
        for key, btn in self._status_buttons.items():
            btn.setChecked(key == self._active_status)

    def _set_group_filter(self, group_key: str) -> None:
        self._active_group = str(group_key or "")
        self._sync_pill_checked_states()
        self._apply_filter()

    def _set_status_filter(self, status_key: str) -> None:
        self._active_status = str(status_key or "")
        self._sync_pill_checked_states()
        self._apply_filter()

    def _reset_filters(self) -> None:
        self._active_group = ""
        self._active_status = ""
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self._sync_pill_checked_states()
        self._apply_filter()

    def set_developer_visible(self, visible: bool):
        self._developer_visible = bool(visible)
        self._rebuild_group_pills()
        if self._listings:
            self.show_listings(self._listings)

    def open_market(self):
        cached = load_cached_catalog(self.catalog_cache_path)
        if cached and not self._listings:
            self.show_listings(cached)
        if not self._loaded:
            self.refresh_catalog()

    def refresh_catalog(self):
        if self._busy:
            return

        def work(_progress):
            payload, listings = fetch_catalog(http_get)
            return {"payload": payload, "listings": listings}

        self._start_job(
            work,
            self._catalog_ready,
            busy_text="正在读取插件清单…",
            report_error=False,
        )

    def show_listings(self, listings: list[dict]):
        self._listings = list(listings or [])
        self._rebuild_cards()

    def _catalog_ready(self, result):
        payload = result.get("payload") if isinstance(result, dict) else None
        listings = result.get("listings") if isinstance(result, dict) else None
        if isinstance(payload, dict):
            try:
                save_cached_catalog(self.catalog_cache_path, payload)
            except OSError:
                pass
        self._loaded = True
        self.show_listings(listings or [])
        self.stats_label.setText(self.stats_label.text() or "清单已更新")

    def _bundled_sets(self) -> tuple[set[str], set[str]]:
        manager = self.plugin_manager
        if manager is None:
            return set(), set()
        root = os.path.abspath(getattr(manager, "plugin_dir", "") or "")
        identifiers = set()
        triggers = set()
        if root and os.path.isdir(root):
            for name in os.listdir(root):
                if name.startswith("_") or name.startswith("."):
                    continue
                if os.path.isdir(os.path.join(root, name)):
                    identifiers.add(name)
        for trigger, path in (getattr(manager, "plugin_roots", None) or {}).items():
            try:
                folder = os.path.abspath(path)
                if os.path.commonpath([folder, root]) == root:
                    triggers.add(str(trigger))
                    identifiers.add(os.path.basename(folder))
            except ValueError:
                continue
        return identifiers, triggers

    def _rebuild_cards(self):
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)
        for entry in self._cards:
            card = entry.get("card")
            if card is not None:
                card.hide()
                card.deleteLater()
        self._cards.clear()

        palette = get_ui_palette()
        bundled_ids, bundled_triggers = self._bundled_sets()
        installed_root = self.installed_root
        group_counts: dict[str, int] = {"": 0}
        status_counts: dict[str, int] = {"": 0, "absent": 0, "update": 0, "installed": 0}

        for listing in self._listings:
            trigger = str(listing.get("trigger") or "")
            group = capability_group(trigger)
            if group == "developer" and not self._developer_visible:
                continue
            status = market_status(
                listing,
                bundled_ids=bundled_ids,
                bundled_triggers=bundled_triggers,
                installed_root=installed_root,
            )
            group_counts[""] = group_counts.get("", 0) + 1
            group_counts[group] = group_counts.get(group, 0) + 1
            status_counts[""] = status_counts.get("", 0) + 1
            if status in status_counts:
                status_counts[status] += 1
            elif status == "builtin":
                status_counts["installed"] += 1

            card = self._create_card(listing, group, status, palette)
            blob = " ".join(
                [
                    str(listing.get("name") or ""),
                    trigger,
                    str(listing.get("summary") or ""),
                    capability_group_label(group),
                    str(listing.get("id") or ""),
                ]
            ).lower()
            self._cards.append(
                {
                    "card": card,
                    "group": group,
                    "status": status,
                    "search_blob": blob,
                }
            )

        for key, btn in self._group_buttons.items():
            base = "全部" if key == "" else capability_group_label(key)
            cnt = group_counts.get(key, 0)
            btn.setText(f"{base} {cnt}" if self._listings else base)

        status_titles = {
            "": "全部状态",
            "absent": "可安装",
            "update": "可更新",
            "installed": "已安装",
        }
        for key, btn in self._status_buttons.items():
            base = status_titles.get(key, key)
            cnt = status_counts.get(key, 0)
            if key == "" or not self._listings:
                btn.setText(base)
            else:
                btn.setText(f"{base} {cnt}")

        self._apply_filter()

    def _create_card(
        self, listing: dict, group: str, status: str, palette: dict
    ) -> QtWidgets.QFrame:
        name = str(listing.get("name") or listing.get("id") or "")
        trigger = str(listing.get("trigger") or "")
        version = str(listing.get("version") or "0.0.0")
        summary = str(listing.get("summary") or "").strip() or f"触发词：{trigger}"
        active_card = status in {"installed", "update", "builtin"}

        text_primary = palette.get("text_primary", "#111827")
        text_secondary = palette.get("text_secondary", "#4B5563")
        text_muted = palette.get("text_muted", "#9CA3AF")

        card = QtWidgets.QFrame(self.cards_container)
        card.setObjectName("pluginCard" if active_card else "pluginCardDisabled")
        card.setMinimumHeight(92)
        card.setMaximumHeight(104)
        card.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        outer_row = QtWidgets.QHBoxLayout(card)
        outer_row.setContentsMargins(13, 11, 13, 11)
        outer_row.setSpacing(11)

        glyph, icon_bg, icon_fg = _group_icon_spec(group, palette)
        icon_badge = QtWidgets.QLabel(glyph, card)
        icon_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon_badge.setFixedSize(36, 36)
        icon_badge.setStyleSheet(
            f"background-color: {icon_bg}; color: {icon_fg};"
            "border-radius: 10px; font-size: 15px; font-weight: 700;"
        )
        outer_row.addWidget(icon_badge, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        body_col = QtWidgets.QVBoxLayout()
        body_col.setContentsMargins(0, 0, 0, 0)
        body_col.setSpacing(4)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        name_label = QtWidgets.QLabel(name, card)
        name_label.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {text_primary}; background: transparent;"
        )
        top_row.addWidget(name_label)

        group_label = QtWidgets.QLabel(capability_group_label(group), card)
        group_label.setStyleSheet(_badge_style(group, palette))
        group_label.setFixedHeight(19)
        top_row.addWidget(group_label)

        version_label = QtWidgets.QLabel(f"v{version.lstrip('v')}", card)
        version_label.setStyleSheet(_badge_style("", palette))
        version_label.setFixedHeight(19)
        top_row.addWidget(version_label)
        top_row.addStretch(1)

        action = QtWidgets.QPushButton(self._action_text(status), card)
        action.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        action.setFixedHeight(26)
        can_act = status in {"absent", "update"}
        action.setObjectName("marketInstallBtn" if can_act else "marketStateBtn")
        action.setEnabled(can_act)
        if can_act:
            action.clicked.connect(functools.partial(self._install, dict(listing), status))
        top_row.addWidget(action)

        if status in {"installed", "update"}:
            remove_button = QtWidgets.QPushButton("移除", card)
            remove_button.setObjectName("marketRemoveBtn")
            remove_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            remove_button.setFixedHeight(26)
            remove_button.clicked.connect(functools.partial(self._remove, dict(listing)))
            top_row.addWidget(remove_button)

        body_col.addLayout(top_row)

        description = _ElidedLabel(summary, card)
        description.setStyleSheet(
            f"font-size: 11.5px; color: {text_secondary}; background: transparent;"
        )
        body_col.addWidget(description)

        meta = _ElidedLabel(
            f"⚡ {trigger}   ·   {self._status_text(status)}", card
        )
        meta.setStyleSheet(
            f"font-size: 10.5px; color: {text_muted}; background: transparent;"
        )
        body_col.addWidget(meta)

        outer_row.addLayout(body_col, 1)

        tooltip = "\n".join(
            [
                f"名称：{name}",
                f"版本：{version}",
                f"触发词：{trigger}",
                f"说明：{summary}",
            ]
        )
        card.setToolTip(tooltip)
        description.setToolTip(tooltip)
        meta.setToolTip(tooltip)
        return card

    @staticmethod
    def _action_text(status: str) -> str:
        return {
            "absent": "安装",
            "update": "更新",
            "installed": "已安装",
            "builtin": "已内置",
        }.get(status, "安装")

    @staticmethod
    def _status_text(status: str) -> str:
        return {
            "absent": "未安装",
            "update": "有新版本可更新",
            "installed": "已安装到本机",
            "builtin": "主程序自带",
        }.get(status, "未安装")

    def _apply_filter(self, _text=None):
        text = (
            self.search_input.text().lower().strip()
            if hasattr(self, "search_input")
            else ""
        )
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)
        visible = []
        for entry in self._cards:
            card = entry["card"]
            if self._active_group and entry["group"] != self._active_group:
                card.setVisible(False)
                continue
            if self._active_status:
                if self._active_status == "installed":
                    if entry["status"] not in {"installed", "builtin"}:
                        card.setVisible(False)
                        continue
                elif entry["status"] != self._active_status:
                    card.setVisible(False)
                    continue
            if text and text not in entry["search_blob"]:
                card.setVisible(False)
                continue
            visible.append(entry)

        for index, entry in enumerate(visible):
            card = entry["card"]
            self.cards_grid.addWidget(card, index // 2, index % 2)
            card.setVisible(True)

        installable = sum(1 for entry in visible if entry["status"] == "absent")
        if not self._busy:
            self.stats_label.setText(f"显示 {len(visible)} 项 · 可安装 {installable}")

        has_any_filter = bool(text or self._active_group or self._active_status)
        self.empty_frame.setVisible(not visible)
        self.empty_reset_btn.setVisible(not visible and has_any_filter and bool(self._listings))
        if not self._listings:
            self.empty_label.setText("清单是空的，点右上角「刷新清单」重试")
        elif not visible:
            self.empty_label.setText("没有找到匹配的插件")

    def _install(self, listing: dict, status: str):
        if self._busy:
            return
        name = str(listing.get("name") or listing.get("id") or "插件")
        if status == "update":
            question = f"用仓库里的版本更新「{name}」？你改过的配置会保留。"
        else:
            question = f"要从插件仓库安装「{name}」吗？它会作为本机插件运行。"
        answer = QtWidgets.QMessageBox.question(self, "插件市场", question)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        bundled_ids, bundled_triggers = self._bundled_sets()
        installed_root = self.installed_root

        def work(progress):
            return install_from_github(
                listing,
                installed_root=installed_root,
                bundled_ids=bundled_ids,
                bundled_triggers=bundled_triggers,
                progress=progress,
            )

        self._start_job(
            work,
            lambda _result: self._finish_change(f"✓ 「{name}」已安装"),
            busy_text=f"正在安装「{name}」…",
        )

    def _remove(self, listing: dict):
        if self._busy:
            return
        name = str(listing.get("name") or listing.get("id") or "插件")
        answer = QtWidgets.QMessageBox.question(
            self,
            "插件市场",
            f"从本机移除「{name}」？主程序自带的插件不会受影响。",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        plugin_id = str(listing.get("id") or "")
        installed_root = self.installed_root

        def work(_progress):
            uninstall_plugin(plugin_id, installed_root)
            return plugin_id

        self._start_job(
            work,
            lambda _result: self._finish_change(f"已移除「{name}」"),
            busy_text=f"正在移除「{name}」…",
        )

    def _finish_change(self, message: str):
        manager = self.plugin_manager
        if manager is not None and hasattr(manager, "load_plugins"):
            manager.load_plugins()
        self.plugins_changed.emit()
        self._rebuild_cards()
        self.stats_label.setText(message)

    def _start_job(
        self, work, on_success, busy_text: str, *, report_error: bool = True
    ):
        self._busy = True
        self._report_error = bool(report_error)
        self._on_success = on_success
        self.refresh_button.setEnabled(False)
        self.stats_label.setText(busy_text)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        job = _MarketJob(work, self)
        self._job = job
        # Bound methods stay on the interface thread. A lambda would run inside
        # the worker and pop an empty native window instead of drawing cards.
        job.progress.connect(self._on_progress)
        job.succeeded.connect(self._on_job_succeeded)
        job.failed.connect(self._on_job_failed)
        job.start()

    def _on_progress(self, done: int, total: int):
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            self.stats_label.setText(f"正在下载 {done}/{total}")

    def _release_job(self):
        self._busy = False
        self.refresh_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._on_success = None
        job = self._job
        self._job = None
        if job is not None and job.isFinished():
            job.deleteLater()

    def _on_job_succeeded(self, result):
        callback = self._on_success
        self._release_job()
        if callback is not None:
            callback(result)

    def _on_job_failed(self, message: str):
        report = self._report_error
        self._release_job()
        self.stats_label.setText(message)
        if not self._cards:
            self.empty_label.setText(message)
            self.empty_frame.setVisible(True)
        if report:
            QtWidgets.QMessageBox.warning(self.window(), "插件市场", message)
