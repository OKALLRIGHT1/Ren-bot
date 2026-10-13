from modules.asr.settings import (
    ASR_ENGINE_QWEN3,
    ASR_ENGINE_ZIPFORMER,
    collect_character_wake_words,
    default_global_wake_words,
    load_asr_settings,
    resolve_asr_hotwords,
    resolve_wake_words,
    should_accept_voice_utterance,
    text_contains_wake_word,
)


def test_character_wake_words_from_name_and_aliases():
    words = collect_character_wake_words(
        {
            "name": "五十铃怜",
            "aliases": ["小铃", "Suzu"],
        }
    )
    assert "五十铃怜" in words
    assert "五十铃" in words
    assert "怜" in words
    assert "小铃" in words
    assert "Suzu" in words


def test_resolve_wake_words_merges_sources():
    settings = {
        "asr_use_character_wake_words": True,
        "asr_include_global_wake_words": True,
        "asr_extra_wake_words": ["自定义词"],
    }
    words = resolve_wake_words(
        settings=settings,
        character={"name": "环彩羽", "aliases": ["彩羽"]},
        global_keywords=["助手"],
    )
    assert "环彩羽" in words
    assert "彩羽" in words
    assert "助手" in words
    assert "自定义词" in words


def test_resolve_uses_editable_global_list():
    settings = {
        "asr_use_character_wake_words": False,
        "asr_include_global_wake_words": True,
        "asr_global_wake_words": ["五十铃", "助手", "500"],
        "asr_extra_wake_words": [],
    }
    words = resolve_wake_words(settings=settings, character=None)
    assert words == ["五十铃", "助手", "500"]


def test_resolve_skips_global_when_disabled():
    settings = {
        "asr_use_character_wake_words": False,
        "asr_include_global_wake_words": False,
        "asr_global_wake_words": ["五十铃", "助手"],
        "asr_extra_wake_words": ["仅自定义"],
    }
    words = resolve_wake_words(settings=settings, character=None)
    assert words == ["仅自定义"]


def test_free_listen_accepts_without_wake_word():
    decision = should_accept_voice_utterance(
        "今天天气怎么样",
        settings={"asr_require_wake_word": False, "asr_min_chars": 2},
        wake_words=["五十铃"],
        is_woken=False,
        last_active_time=0,
        now=1000.0,
    )
    assert decision["accept"] is True
    assert decision["reason"] == "free_listen"


def test_require_wake_blocks_until_name_called():
    settings = {
        "asr_require_wake_word": True,
        "asr_active_window_sec": 20,
        "asr_min_chars": 2,
    }
    blocked = should_accept_voice_utterance(
        "今天天气怎么样",
        settings=settings,
        wake_words=["五十铃", "怜"],
        is_woken=False,
        last_active_time=0,
        now=1000.0,
    )
    assert blocked["accept"] is False
    assert blocked["reason"] == "not_woken"

    woken = should_accept_voice_utterance(
        "五十铃，今天天气怎么样",
        settings=settings,
        wake_words=["五十铃", "怜"],
        is_woken=False,
        last_active_time=0,
        now=1000.0,
    )
    assert woken["accept"] is True
    assert woken["reason"] == "wake_word"


def test_active_window_allows_followup():
    settings = {
        "asr_require_wake_word": True,
        "asr_active_window_sec": 20,
        "asr_min_chars": 2,
    }
    follow = should_accept_voice_utterance(
        "然后呢",
        settings=settings,
        wake_words=["五十铃"],
        is_woken=True,
        last_active_time=990.0,
        now=1000.0,
    )
    assert follow["accept"] is True
    assert follow["reason"] == "active_window"


def test_wake_match_is_case_insensitive():
    assert text_contains_wake_word("hello suzu there", ["Suzu"]) is True


def test_load_asr_settings_defaults():
    cfg = load_asr_settings({})
    assert cfg["asr_require_wake_word"] is True
    assert cfg["asr_use_character_wake_words"] is True
    assert cfg["asr_include_global_wake_words"] is True
    assert cfg["asr_global_wake_words"] == default_global_wake_words()
    assert cfg["asr_engine"] == ASR_ENGINE_ZIPFORMER
    assert cfg["asr_hotwords_from_wake"] is True


def test_load_asr_settings_engine_choices():
    assert load_asr_settings({"asr_engine": "zipformer"})["asr_engine"] == ASR_ENGINE_ZIPFORMER
    assert load_asr_settings({"asr_engine": "qwen3"})["asr_engine"] == ASR_ENGINE_QWEN3
    assert load_asr_settings({"asr_engine": "nope"})["asr_engine"] == ASR_ENGINE_ZIPFORMER


def test_resolve_asr_hotwords_from_wake_words():
    hot = resolve_asr_hotwords(
        settings={"asr_hotwords_from_wake": True},
        wake_words=["五十铃", "怜", "Suzu", "五十铃"],
    )
    assert hot == "五十铃/怜/Suzu"


def test_resolve_asr_hotwords_can_disable():
    hot = resolve_asr_hotwords(
        settings={"asr_hotwords_from_wake": False},
        wake_words=["五十铃"],
    )
    assert hot == ""


def test_save_asr_settings_persists_before_sensor_init(monkeypatch):
    import modules.asr.settings as asr_settings_mod

    disk_data = {}

    class _FakeProjectStore:
        def load(self):
            return dict(disk_data)

        def save(self, patch):
            disk_data.update(patch or {})
            return dict(disk_data)

    class _FakeCharacterProvider:
        def get_active_character(self):
            return {"name": "新角色", "aliases": ["阿新"]}

    monkeypatch.setattr(asr_settings_mod, "_SETTINGS_STORE", None)
    monkeypatch.setattr(asr_settings_mod, "_CHARACTER_PROVIDER", None)
    monkeypatch.setattr(
        "modules.asr.adapter.project_settings_store",
        lambda: _FakeProjectStore(),
    )
    monkeypatch.setattr(
        "modules.asr.adapter.project_character_provider",
        lambda: _FakeCharacterProvider(),
    )

    saved = asr_settings_mod.save_asr_settings(
        {
            "asr_global_wake_words": "小助手\n测试唤醒",
            "asr_extra_wake_words": "专属词",
            "asr_use_character_wake_words": True,
            "asr_include_global_wake_words": True,
        }
    )
    assert disk_data["asr_global_wake_words"] == ["小助手", "测试唤醒"]
    assert disk_data["asr_extra_wake_words"] == ["专属词"]

    # Simulate VoiceSensor.__init__ later calling configure(settings_store=...)
    asr_settings_mod.configure(settings_store=_FakeProjectStore())
    reloaded = asr_settings_mod.load_asr_settings()
    assert reloaded["asr_global_wake_words"] == ["小助手", "测试唤醒"]
    assert reloaded["asr_extra_wake_words"] == ["专属词"]

    words = asr_settings_mod.resolve_wake_words(settings=saved)
    assert "新角色" in words
    assert "阿新" in words
    assert "小助手" in words
    assert "专属词" in words

