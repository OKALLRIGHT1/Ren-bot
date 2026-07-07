import config
from modules.character_manager import DEFAULT_EMOTION_KEYS, EMOTION_MOTION_PREFERENCES
from services.chat_support.emotion_reply_service import EmotionReplyService


def test_default_emotion_keys_include_shy_and_are_unique():
    assert "shy" in DEFAULT_EMOTION_KEYS
    assert "idle_random" in DEFAULT_EMOTION_KEYS
    assert len(DEFAULT_EMOTION_KEYS) == len(set(DEFAULT_EMOTION_KEYS))


def test_shy_is_available_to_llm_and_live2d_defaults():
    assert "shy" in config.EMO_LABELS
    assert "shy" in config.EMO_TO_LIVE2D
    assert "shy" in EMOTION_MOTION_PREFERENCES


def test_shy_is_available_in_emotion_prompts():
    service = EmotionReplyService(
        app_getter=lambda: None,
        clean_text_for_tts=lambda text: text,
        strip_emo_tags=lambda text: text,
        strip_cmd=lambda text: text,
        normalize_emo=lambda value: str(value or "").strip().lower() or None,
    )
    context_prompt = service.build_current_emotion_context({})
    assert "shy" in context_prompt

    source = open("services/chat_support/emotion_reply_service.py", encoding="utf-8").read()
    assert "happy/sad/angry/shy/flustered/confused/think/neutral" in source


def test_shy_is_available_in_sensor_generation_prompts():
    source = open("services/chat_support/sensor_event_service.py", encoding="utf-8").read()
    assert source.count("<emo=happy|sad|angry|shy|flustered|confused|think|neutral>") >= 3


def test_emotion_context_includes_compact_personality_state():
    service = EmotionReplyService(
        app_getter=lambda: None,
        clean_text_for_tts=lambda text: text,
        strip_emo_tags=lambda text: text,
        strip_cmd=lambda text: text,
        normalize_emo=lambda value: str(value or "").strip().lower() or None,
        personality_state_getter=lambda: {
            "mood": "tired",
            "energy": 42,
            "social_mode": "casual",
            "continuity_emotion": "sad",
        },
    )

    context_prompt = service.build_current_emotion_context({})

    assert "mood=tired" in context_prompt
    assert "energy=42" in context_prompt
    assert "social=casual" in context_prompt
    assert "continuity=sad" in context_prompt
    assert context_prompt.count("\n") <= 7


def test_personality_reply_state_keeps_mood_and_continuity_separate():
    from modules.personality_system import PersonalitySystem

    personality = PersonalitySystem()
    personality.state.current_mood = "normal"
    personality.state.energy_level = 88
    personality.state.social_mode = "casual"

    adjusted_emotion, adjusted_intensity = personality.adjust_emotion("happy", 0.8)
    state = personality.get_reply_state()

    assert adjusted_emotion == "happy"
    assert 0 < adjusted_intensity <= 0.8
    assert state["mood"] == "normal"
    assert state["energy"] == 88
    assert state["social_mode"] == "casual"
    assert state["continuity_emotion"] == "happy"
