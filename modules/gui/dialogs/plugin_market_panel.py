"""Plugin market cards. They use the same compact card layout as the capability list."""

from __future__ import annotations

import functools
import os

from PySide6 import QtCore, QtWidgets

from modules.gui.user_ia import capability_group, capability_group_label
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

_TOOL_BUTTON_STYLE = """
QPushButton {
    background: #FFFFFF;
    color: #374151;
    border: 1px solid #D1D5DB;
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    border-color: #93C5FD;
    color: #1D4ED8;
    background: #EFF6FF;
}
"""

_ACTION_STYLE = """
QPushButton {
    background-color: #FFFFFF; color: #2563EB; border: 1px solid #BFDBFE;
    border-radius: 11px; padding: 1px 8px; font-size: 11px; font-weight: 600;
    min-width: 52px;
}
QPushButton:hover { background-color: #EFF6FF; border-color: #2563EB; }
QPushButton:disabled {
    background-color: #F3F4F6; color: #6B7280; border-color: #D1D5DB;
}
"""

def _badge_style(group: str) -> str:
    palette = {
        "companion": ("#EEF2FF", "#4338CA", "#C7D2FE"),
        "desktop": ("#ECFDF5", "#047857", "#A7F3D0"),
        "qq": ("#EFF6FF", "#1D4ED8", "#BFDBFE"),
        "developer": ("#FEF3C7", "#B45309", "#FDE68A"),
    }
    background, foreground, border = palette.get(group, ("#F3F4F6", "#4B5563", "#E5E7EB"))
    return (
        f"background-color: {background}; color: {foreground}; border: 1px solid {border};"
        "padding: 1px 6px; border-radius: 6px; font-size: 10px; font-weight: 600;"
    )


_QUIET_BUTTON_STYLE = """
QPushButton {
    background-color: #FFFFFF; color: #4B5563; border: 1px solid #E5E7EB;
    border-radius: 6px; padding: 1px 8px; font-size: 11px; font-weight: 600;
    min-width: 38px;
}
QPushButton:hover { background-color: #FEF2F2; color: #B91C1C; border-color: #FECACA; }
"""


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

    def __init__(self, plugin_manager=None, parent=None, installed_root=None, catalog_cache_path=None):
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

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        hint = QtWidgets.QLabel("来自插件仓库。安装后出现在「本机能力」里，不会改动主程序自带的插件。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(hint)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("搜索插件名称、说明或触发词…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filter)
        self.search_input.setFixedHeight(30)
        self.search_input.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setStyleSheet("color: #6B7280; font-size: 11px; padding: 0 4px;")
        self.refresh_button = QtWidgets.QPushButton("刷新清单")
        self.refresh_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setFixedHeight(30)
        self.refresh_button.setStyleSheet(_TOOL_BUTTON_STYLE)
        self.refresh_button.clicked.connect(self.refresh_catalog)
        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.stats_label)
        filter_row.addWidget(self.refresh_button)
        layout.addLayout(filter_row)

        self.cards_scroll = QtWidgets.QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setAutoFillBackground(False)
        outer = QtWidgets.QVBoxLayout(self.cards_container)
        outer.setContentsMargins(0, 2, 2, 2)
        outer.setSpacing(8)
        self.cards_grid = QtWidgets.QGridLayout()
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(10)
        self.cards_grid.setVerticalSpacing(8)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        outer.addLayout(self.cards_grid)
        self.empty_label = QtWidgets.QLabel("点「刷新清单」读取插件仓库")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #9CA3AF; font-size: 12px; padding: 28px 0;")
        outer.addWidget(self.empty_label)
        outer.addStretch(1)
        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll, 1)

    def set_developer_visible(self, visible: bool):
        self._developer_visible = bool(visible)
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

        self._start_job(work, self._catalog_ready, busy_text="正在读取插件清单…", report_error=False)

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

        bundled_ids, bundled_triggers = self._bundled_sets()
        installed_root = self.installed_root
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
            card = self._create_card(listing, group, status)
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
        self._apply_filter()

    def _create_card(self, listing: dict, group: str, status: str) -> QtWidgets.QFrame:
        name = str(listing.get("name") or listing.get("id") or "")
        trigger = str(listing.get("trigger") or "")
        version = str(listing.get("version") or "0.0.0")
        summary = str(listing.get("summary") or "").strip() or f"触发词：{trigger}"
        enabled = status in {"installed", "update", "builtin"}

        card = QtWidgets.QFrame(self.cards_container)
        card.setObjectName("pluginCard" if enabled else "pluginCardDisabled")
        card.setFixedHeight(78)
        card.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(3)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        name_label = QtWidgets.QLabel(name)
        name_label.setStyleSheet(
            "font-weight: 700; font-size: 13px; color: #111827; background: transparent;"
            if enabled
            else "font-weight: 700; font-size: 13px; color: #6B7280; background: transparent;"
        )
        top_row.addWidget(name_label)

        group_label = QtWidgets.QLabel(capability_group_label(group))
        group_label.setStyleSheet(_badge_style(group))
        group_label.setFixedHeight(18)
        top_row.addWidget(group_label)

        version_label = QtWidgets.QLabel(version)
        version_label.setStyleSheet(_badge_style(""))
        version_label.setFixedHeight(18)
        top_row.addWidget(version_label)
        top_row.addStretch(1)

        action = QtWidgets.QPushButton(self._action_text(status))
        action.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        action.setFixedHeight(22)
        action.setStyleSheet(_ACTION_STYLE)
        action.setEnabled(status in {"absent", "update"})
        if status in {"absent", "update"}:
            action.clicked.connect(functools.partial(self._install, dict(listing), status))
        top_row.addWidget(action)

        if status in {"installed", "update"}:
            remove_button = QtWidgets.QPushButton("移除")
            remove_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            remove_button.setFixedHeight(22)
            remove_button.setStyleSheet(_QUIET_BUTTON_STYLE)
            remove_button.clicked.connect(functools.partial(self._remove, dict(listing)))
            top_row.addWidget(remove_button)
        card_layout.addLayout(top_row)

        description = QtWidgets.QLabel()
        description.setStyleSheet("font-size: 11px; color: #4B5563; background: transparent;")
        description.setWordWrap(False)
        description.setText(
            description.fontMetrics().elidedText(
                summary.replace("\n", " "),
                QtCore.Qt.TextElideMode.ElideRight,
                280,
            )
        )
        card_layout.addWidget(description)

        meta = QtWidgets.QLabel()
        meta.setStyleSheet("font-size: 10px; color: #9CA3AF; background: transparent;")
        meta.setWordWrap(False)
        meta.setText(
            meta.fontMetrics().elidedText(
                f"触发词: {trigger}  ·  {self._status_text(status)}",
                QtCore.Qt.TextElideMode.ElideRight,
                290,
            )
        )
        card_layout.addWidget(meta)
        tooltip = "\n".join(
            [
                f"名称：{name}",
                f"版本：{version}",
                f"触发词：{trigger}",
                f"说明：{summary}",
            ]
        )
        card.setToolTip(tooltip)
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
            "update": "可更新",
            "installed": "已安装",
            "builtin": "主程序自带",
        }.get(status, "未安装")

    def _apply_filter(self, _text=None):
        text = self.search_input.text().lower().strip() if hasattr(self, "search_input") else ""
        while self.cards_grid.count():
            self.cards_grid.takeAt(0)
        visible = []
        for entry in self._cards:
            card = entry["card"]
            if text and text not in entry["search_blob"]:
                card.setVisible(False)
                continue
            visible.append(entry)
        for index, entry in enumerate(visible):
            card = entry["card"]
            self.cards_grid.addWidget(card, index // 2, index % 2)
            card.setVisible(True)
        installable = sum(1 for entry in visible if entry["status"] == "absent")
        self.stats_label.setText(f"显示 {len(visible)} 项 · 可安装 {installable}")
        self.empty_label.setVisible(not visible)
        if not self._listings:
            self.empty_label.setText("清单是空的")
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
            lambda _result: self._finish_change(f"「{name}」已安装"),
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
            lambda _result: self._finish_change(f"「{name}」已移除"),
            busy_text=f"正在移除「{name}」…",
        )

    def _finish_change(self, message: str):
        manager = self.plugin_manager
        if manager is not None and hasattr(manager, "load_plugins"):
            manager.load_plugins()
        self.plugins_changed.emit()
        self._rebuild_cards()
        self.stats_label.setText(message)

    def _start_job(self, work, on_success, busy_text: str, *, report_error: bool = True):
        self._busy = True
        self._report_error = bool(report_error)
        self._on_success = on_success
        self.refresh_button.setEnabled(False)
        self.stats_label.setText(busy_text)
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
            self.stats_label.setText(f"正在下载 {done}/{total}")

    def _release_job(self):
        self._busy = False
        self.refresh_button.setEnabled(True)
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
            self.empty_label.setVisible(True)
        if report:
            QtWidgets.QMessageBox.warning(self.window(), "插件市场", message)
