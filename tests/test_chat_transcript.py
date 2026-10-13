import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets, QtGui

from modules.gui.widgets.chat_transcript import ChatTranscript
from modules.gui.widgets.chat_input import ChatInput
from modules.memory.short_term import ShortTermMemoryManager


def _app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_transcript_keeps_consecutive_assistant_as_separate_bubbles():
    _app()
    view = ChatTranscript()
    view.append_message("assistant", "……身体会碎的。")
    view.append_message("assistant", "我不要……你变成冰冷的石头。")
    bubbles = [b.text() for b in view.findChildren(QtWidgets.QLabel, "chatBubble_assistant")]
    assert bubbles == ["……身体会碎的。", "我不要……你变成冰冷的石头。"]
    view.close()


def test_transcript_keeps_user_on_right_role():
    _app()
    view = ChatTranscript()
    view.append_message("user", "在吗")
    assert view.last_user_text() == "在吗"
    view.close()


def test_chat_input_remembers_history():
    _app()
    box = ChatInput()
    box.remember("第一句")
    box.remember("第二句")
    box._history_step(-1)
    assert box.toPlainText() == "第二句"
    box._history_step(-1)
    assert box.toPlainText() == "第一句"
    box.close()


def test_short_term_clear_drops_ram_only():
    mgr = ShortTermMemoryManager(sqlite_store=None, max_short_term=8)
    mgr.append("user", "hi")
    mgr.append("assistant", "hello")
    assert mgr.get_context()
    mgr.clear()
    assert mgr.get_context() == []


def test_transcript_qq_style_bilateral_layout():
    _app()
    view = ChatTranscript()
    view.set_assistant_identity("凉宫春日", "")
    view.set_user_identity("阿虚", "")

    view.append_message("user", "今天天气真好！")
    view.append_message("assistant", "是啊，一起去探险吧！")

    user_names = [lbl.text() for lbl in view.findChildren(QtWidgets.QLabel, "chatUserName")]
    assistant_names = [lbl.text() for lbl in view.findChildren(QtWidgets.QLabel, "chatAssistantName")]

    assert "阿虚" in user_names
    assert "凉宫春日" in assistant_names

    user_bubbles = [b.text() for b in view.findChildren(QtWidgets.QLabel, "chatBubble_user")]
    assistant_bubbles = [b.text() for b in view.findChildren(QtWidgets.QLabel, "chatBubble_assistant")]

    assert "今天天气真好！" in user_bubbles
    assert "是啊，一起去探险吧！" in assistant_bubbles

    time_labels = view.findChildren(QtWidgets.QLabel, "chatTimeLabel")
    assert len(time_labels) >= 1

    view.resize(500, 600)
    view.close()


def test_transcript_assistant_badge_avatar(tmp_path):
    _app()
    # Create a dummy image
    img_file = tmp_path / "test_badge.png"
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtGui.QColor("#FF69B4"))
    pm.save(str(img_file))

    from modules.gui.widgets.chat_transcript import load_avatar_pixmap, AVATAR_SIZE

    badge_dict = {
        "path": str(img_file),
        "scale": 1.5,
        "offset_x": 0.2,
        "offset_y": -0.1,
    }
    rendered = load_avatar_pixmap(badge_dict, letter="灯", size=AVATAR_SIZE)
    assert not rendered.isNull()
    assert rendered.width() == AVATAR_SIZE
    assert rendered.height() == AVATAR_SIZE

    view = ChatTranscript()
    view.set_assistant_identity("高松灯", badge_dict)
    view.append_message("assistant", "迷子でもいい、前へ進め。")
    assert view._last_assistant_bubble is not None
    assert view._last_assistant_bubble.text() == "迷子でもいい、前へ進め。"
    view.close()


def test_transcript_bubble_expands_like_qq():
    _app()
    view = ChatTranscript()
    view.resize(600, 400)
    view.show()
    QtWidgets.QApplication.processEvents()

    # Medium text that should fit on a single line instead of wrapping at 108px
    text = "……开会啊。Master酱，注意休息……别太累了。"
    view.append_message("assistant", text)
    QtWidgets.QApplication.processEvents()

    from modules.gui.widgets.chat_transcript import _Bubble
    bubbles = view.findChildren(_Bubble)
    assert len(bubbles) == 1
    bubble = bubbles[0]

    # Verify bubble width expands properly beyond the old 108px bug
    assert bubble.width() >= 250
    # And bubble maximum width supports up to ~88% of viewport
    assert bubble.maximumWidth() >= int(600 * 0.85)

    view.close()



