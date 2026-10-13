from modules.plugin_manager import PluginManager


def test_observe_english_keys_need_word_boundary():
    assert PluginManager._observe_key_matches("screen", "look at the screen")
    assert not PluginManager._observe_key_matches("screen", "fullscreen window")
    assert not PluginManager._observe_key_matches("photo", "photography book")
    assert PluginManager._observe_key_matches("screenshot", "please screenshot this")


def test_observe_chinese_keys_still_substring():
    assert PluginManager._observe_key_matches("看屏幕", "帮我看屏幕现在怎样")
    assert not PluginManager._observe_key_matches("看屏幕", "帮我看看表情")
    assert PluginManager._observe_key_matches("截图", "把截图发我")
