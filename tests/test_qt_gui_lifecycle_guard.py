from modules.gui.app import QtChatTrayApp


class DeletedSignal:
    def emit(self, *args):
        raise RuntimeError("Internal C++ object (_Bridge) already deleted.")


class DeletedBridge:
    sig_append = DeletedSignal()
    sig_status = DeletedSignal()
    sig_refresh_character = DeletedSignal()
    sig_sync_active_character_visual = DeletedSignal()
    sig_trigger_costume_name = DeletedSignal()
    sig_apply_character_switch = DeletedSignal()
    sig_sedentary_popup = DeletedSignal()


def test_public_bridge_emitters_ignore_deleted_qt_bridge():
    app = QtChatTrayApp.__new__(QtChatTrayApp)
    app._bridge = DeletedBridge()

    app.append("system", "hello")
    app.set_status("Idle")
    app.refresh_character_status()
    app.sync_active_character_visual()
    app.trigger_costume_by_name("default")
    app.apply_character_switch("model.json")
    app.show_sedentary_popup("Code", 60)


def test_toggle_show_hide_ignores_deleted_qt_window():
    app = QtChatTrayApp.__new__(QtChatTrayApp)
    app._win = None

    app.toggle_show_hide()
