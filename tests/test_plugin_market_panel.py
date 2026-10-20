import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from modules.gui.dialogs.plugin_manager import PluginManagerDialog
from modules.gui.dialogs.plugin_market_panel import PluginMarketPanel
from modules.plugin_manager import PluginManager


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_capability_page_has_market_toggle(tmp_path: Path):
    _app()
    manager = PluginManager(plugin_dir=str(tmp_path / "plugins"), extra_plugin_dirs=[])
    dialog = PluginManagerDialog(plugin_manager=manager, embedded=True)

    assert dialog.market_view_button.text() == "插件市场"
    assert dialog.local_view_button.isChecked()
    assert not dialog.market_panel.isVisible()

    dialog.market_panel._loaded = True
    dialog.show()
    dialog._set_market_mode(True)
    QtWidgets.QApplication.processEvents()

    assert dialog.market_panel.isVisible()
    assert not dialog.cards_scroll.isVisible()
    dialog.close()


def test_market_cards_follow_install_state(tmp_path: Path):
    _app()
    bundled = tmp_path / "plugins"
    (bundled / "app_control").mkdir(parents=True)
    manager = PluginManager(plugin_dir=str(bundled), extra_plugin_dirs=[])
    panel = PluginMarketPanel(
        manager,
        installed_root=str(tmp_path / "installed"),
        catalog_cache_path=str(tmp_path / "catalog.json"),
    )
    panel.set_developer_visible(False)
    panel.resize(700, 500)
    panel.show()
    panel.show_listings(
        [
            {
                "id": "app_control",
                "name": "主程序控制",
                "trigger": "app_control",
                "version": "1.0.0",
                "summary": "重启",
                "path": "app_control",
            },
            {
                "id": "dice_plugin",
                "name": "骰子",
                "trigger": "dice_roll",
                "version": "1.0.0",
                "summary": "掷骰",
                "path": "dice_plugin",
            },
            {
                "id": "agent_runtime",
                "name": "Agent 运行时",
                "trigger": "agent_runtime",
                "version": "0.0.0",
                "summary": "开发工具",
                "path": "agent_runtime",
            },
        ]
    )
    QtWidgets.QApplication.processEvents()

    buttons = [button.text() for button in panel.findChildren(QtWidgets.QPushButton)]
    assert "已内置" in buttons
    assert "安装" in buttons
    assert "开发工具" not in panel.search_input.text()
    assert len(panel._cards) == 2

    panel.search_input.setText("骰子")
    QtWidgets.QApplication.processEvents()
    visible = [entry for entry in panel._cards if not entry["card"].isHidden()]
    assert len(visible) == 1
    panel.close()


def test_catalog_job_draws_cards_on_the_interface_thread(tmp_path: Path):
    app = _app()
    panel = PluginMarketPanel(
        installed_root=str(tmp_path / "installed"),
        catalog_cache_path=str(tmp_path / "catalog.json"),
    )
    panel.resize(700, 500)
    panel.show()
    listing = {
        "id": "dice_plugin",
        "name": "骰子",
        "trigger": "dice_roll",
        "version": "1.0.0",
        "summary": "掷骰",
        "path": "dice_plugin",
    }

    def work(_progress):
        assert QtCore.QThread.currentThread() is not app.thread()
        return {"payload": {"schema": 1, "plugins": [listing]}, "listings": [listing]}

    panel._start_job(work, panel._catalog_ready, busy_text="正在读取插件清单…", report_error=False)
    deadline = time.time() + 3
    while time.time() < deadline and not panel._listings:
        app.processEvents()
        time.sleep(0.02)

    assert [item["id"] for item in panel._listings] == ["dice_plugin"]
    assert panel._cards
    assert panel._cards[0]["card"].thread() is app.thread()
    assert not panel._cards[0]["card"].isHidden()
    panel.close()
