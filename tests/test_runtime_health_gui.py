import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

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
        "tts",
    ]
    assert rows[1]["state"] == "degraded"
    assert rows[1]["state_label"] == "需注意"
    assert rows[1]["summary"] == "事件已过期"


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
    assert dialog.component_table.rowCount() == 2
    assert dialog.component_table.item(0, 0).text() == "Live2D 连接"
    assert dialog.component_table.item(0, 1).text() == "离线"
    assert dialog.component_table.item(0, 2).text() == "连接已断开"
    assert dialog.refresh_timer.interval() == 10_000

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
    assert gui._btn_runtime_health.width() <= 24
    assert "运行需注意" in gui._btn_runtime_health.toolTip()
    assert "#F59E0B" in gui._btn_runtime_health.styleSheet()
    assert gui._runtime_health_timer.interval() == 10_000

    gui._btn_runtime_health.click()
    app = QtWidgets.QApplication.instance()
    app.processEvents()
    assert gui._runtime_health_dialog is not None
    assert gui._runtime_health_dialog.isVisible()

    gui._runtime_health_dialog.close()
    gui._tray.hide()
    gui._win.close()
    app.processEvents()
