from modules.asr_settings import (
    collect_character_wake_words,
    default_global_wake_words,
    load_asr_settings,
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
