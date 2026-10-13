import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from modules.gui.runtime_health_view import (
    component_rows,
    overall_presentation,
)


def test_overall_presentation_maps_runtime_states_to_user_facing_status():
    assert overall_presentation({"overall": "healthy"}) == {
        "state": "healthy",
        "label": "运行健康",
        "color": "#22C55E",
    }
    assert overall_presentation({"overall": "degraded"})["label"] == "运行需注意"
    assert overall_presentation({"overall": "offline"})["color"] == "#EF4444"
    assert overall_presentation(None) == {
        "state": "unknown",
        "label": "健康状态未知",
        "color": "#94A3B8",
    }


def test_component_rows_use_effective_state_and_sort_attention_first():
    snapshot = {
        "components": {
            "tts": {
                "state": "disabled",
                "effective_state": "disabled",
                "summary": "语音未启用",
                "updated_at": "2026-08-04T01:00:00+00:00",
            },
            "rust_activity": {
                "state": "healthy",
                "effective_state": "degraded",
                "summary": "事件已过期",
                "updated_at": "2026-08-04T02:00:00+00:00",
            },
            "live2d_ws": {
                "state": "offline",
                "summary": "连接已断开",
                "updated_at": "2026-08-04T03:00:00+00:00",
            },
        }
    }

    rows = component_rows(snapshot)

    assert [row["component"] for row in rows] == [
        "live2d_ws",
        "rust_activity",
        "brain",
        "tts",
    ]
    assert rows[1]["state"] == "degraded"
    assert rows[1]["state_label"] == "需注意"
    assert rows[1]["summary"] == "事件已过期"


def test_component_rows_hides_models_and_surfaces_brain():
    rows = component_rows(
        {
            "overall": "degraded",
            "components": {
                "model:gpt-5.4": {
                    "state": "cooldown",
                    "effective_state": "cooldown",
                    "summary": "冷却中",
                },
                "hud_link": {
                    "state": "offline",
                    "effective_state": "offline",
                    "summary": "健康接口不可用",
                },
                "tts": {
                    "state": "healthy",
                    "effective_state": "healthy",
                    "summary": "ok",
                },
            },
        }
    )
    keys = [row["component"] for row in rows]
    assert "model:gpt-5.4" not in keys
    assert "hud_link" not in keys
    assert "brain" in keys
    assert "tts" in keys
    brain = next(row for row in rows if row["component"] == "brain")
    assert brain["component_label"] == "大脑"
    assert brain["state"] == "offline"


def test_component_rows_tolerate_invalid_snapshot():
    assert component_rows(None) == []
    assert component_rows({"components": []}) == []


def test_runtime_health_dialog_renders_overall_and_component_rows():
    from modules.gui.dialogs.runtime_health import RuntimeHealthDialog

    class HealthCenter:
        def snapshot(self):
            return {
                "overall": "degraded",
                "components": {
                    "live2d_ws": {
                        "effective_state": "offline",
                        "summary": "连接已断开",
                        "updated_at": "2026-08-04T03:00:00+00:00",
                    },
                    "tts": {
                        "effective_state": "healthy",
                        "summary": "语音服务可用",
                        "updated_at": "2026-08-04T03:01:00+00:00",
                    },
                },
            }

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = RuntimeHealthDialog(HealthCenter())
    dialog.refresh_status()

    assert dialog.overall_label.text() == "运行需注意"
    assert not (dialog.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint)
    assert dialog.component_table.rowCount() == 3
    assert dialog.component_table.item(0, 0).text() == "桌面形象"
    assert dialog.component_table.item(0, 1).text() == "离线"
    assert dialog.component_table.item(0, 2).text() == "连接已断开"
    assert dialog.refresh_timer.interval() == 30_000

    dialog.close()
    app.processEvents()


def test_runtime_health_dialog_shows_snapshot_failure_without_raising():
    from modules.gui.dialogs.runtime_health import RuntimeHealthDialog

    class BrokenHealthCenter:
        def snapshot(self):
            raise RuntimeError("registry unavailable")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = RuntimeHealthDialog(BrokenHealthCenter())
    dialog.refresh_status()

    assert dialog.overall_label.text() == "状态读取失败"
    assert "registry unavailable" in dialog.summary_label.text()
    assert dialog.component_table.rowCount() == 0

    dialog.close()
    app.processEvents()


def test_main_window_health_indicator_refreshes_and_opens_detail_dialog():
    from modules.gui.app import QtChatTrayApp
    from modules.gui.config import QtGuiConfig

    class HealthCenter:
        def snapshot(self):
            return {"overall": "degraded", "components": {}}

    gui = QtChatTrayApp(
        lambda *_args, **_kwargs: None,
        runtime_health=HealthCenter(),
        cfg=QtGuiConfig(start_minimized_to_tray=True),
    )

    assert gui._btn_runtime_health.text() == "●"
    assert gui._btn_runtime_health.width() <= 28
    assert "运行需注意" in gui._btn_runtime_health.toolTip()
    assert "#F59E0B" in gui._btn_runtime_health.styleSheet()
    assert gui._runtime_health_timer.interval() == 30_000

    pos = gui._btn_runtime_health.mapTo(gui._win, gui._btn_runtime_health.rect().center())
    press = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QPointF(pos),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    release = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QPointF(pos),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    gui._win.mousePressEvent(press)
    gui._btn_runtime_health.click()
    gui._win.mouseReleaseEvent(release)
    app = QtWidgets.QApplication.instance()
    app.processEvents()
    assert gui._runtime_health_dialog is not None
    assert gui._runtime_health_dialog.isVisible()

    gui._runtime_health_dialog.hide()
    app.processEvents()
    assert not gui._runtime_health_dialog.isVisible()
    gui._btn_runtime_health.click()
    app.processEvents()
    assert gui._runtime_health_dialog.isVisible()

    gui._runtime_health_dialog.close()
    app.processEvents()
    gui._btn_runtime_health.click()
    app.processEvents()
    assert gui._runtime_health_dialog is not None
    assert gui._runtime_health_dialog.isVisible()

    gui._runtime_health_dialog.close()
    app.processEvents()
    gui._dot.click()
    app.processEvents()
    assert gui._runtime_health_dialog is not None
    assert gui._runtime_health_dialog.isVisible()
    assert not (
        gui._runtime_health_dialog.windowFlags()
        & QtCore.Qt.WindowType.WindowStaysOnTopHint
    )

    gui._runtime_health_dialog.close()
    gui._tray.hide()
    gui._win.close()
    app.processEvents()


def test_runtime_health_accessible_while_settings_open_and_in_settings_nav(monkeypatch):
    from modules.gui.app import QtChatTrayApp
    from modules.gui.config import QtGuiConfig
    from modules.gui.dialogs.settings import SettingsDialog

    class HealthCenter:
        def snapshot(self):
            return {
                "overall": "healthy",
                "components": {
                    "tts": {
                        "effective_state": "healthy",
                        "summary": "语音服务可用",
                        "updated_at": "2026-08-04T03:01:00+00:00",
                    }
                },
            }

    orig_safe_init = SettingsDialog._safe_init_page

    def fast_init(self, fn, label):
        if label == "运行健康":
            orig_safe_init(self, fn, label)
        else:
            self.stack.addWidget(QtWidgets.QWidget())

    monkeypatch.setattr(SettingsDialog, "_safe_init_page", fast_init)

    gui = QtChatTrayApp(
        lambda *_args, **_kwargs: None,
        runtime_health=HealthCenter(),
        cfg=QtGuiConfig(start_minimized_to_tray=True),
    )

    gui._on_settings_clicked()
    app = QtWidgets.QApplication.instance()
    app.processEvents()

    assert gui._settings_dialog is not None
    assert (
        gui._settings_dialog.windowModality()
        == QtCore.Qt.WindowModality.NonModal
    )

    # Clicking runtime health while SettingsDialog is open must still open RuntimeHealthDialog
    gui._btn_runtime_health.click()
    app.processEvents()
    assert gui._runtime_health_dialog is not None
    assert gui._runtime_health_dialog.isVisible()

    # And navigating to runtime_health inside SettingsDialog must render the embedded health page
    gui._settings_dialog.open_page("runtime_health")
    app.processEvents()
    assert gui._settings_dialog._current_page_id == "runtime_health"

    gui._runtime_health_dialog.close()
    gui._settings_dialog.close()
    gui._tray.hide()
    gui._win.close()
    app.processEvents()

