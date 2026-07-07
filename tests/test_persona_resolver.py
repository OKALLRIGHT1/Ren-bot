from modules.persona_resolver import PersonaResolver


def sample_characters():
    return {
        "sakiko": {
            "name": "丰川祥子",
            "aliases": ["祥子", "小祥"],
            "prompt": "你是丰川祥子。",
            "qq_profile": {"nickname": "祥子Bot"},
        },
        "sakura": {
            "name": "万年樱",
            "aliases": ["樱子"],
            "prompt": "你是万年樱。",
            "qq_profile": {"nickname": "万年樱"},
        },
    }


def test_resolves_exact_character_name():
    resolver = PersonaResolver(lambda: sample_characters())

    match = resolver.resolve("丰川祥子")

    assert match is not None
    assert match.character_id == "sakiko"
    assert match.name == "丰川祥子"
    assert match.prompt == "你是丰川祥子。"
    assert match.matched_by == "name"


def test_resolves_character_alias():
    resolver = PersonaResolver(lambda: sample_characters())

    match = resolver.resolve("小祥")

    assert match is not None
    assert match.character_id == "sakiko"
    assert match.matched_text == "小祥"
    assert match.matched_by == "alias"


def test_resolves_qq_nickname():
    resolver = PersonaResolver(lambda: sample_characters())

    match = resolver.resolve("祥子Bot")

    assert match is not None
    assert match.character_id == "sakiko"
    assert match.matched_by == "qq_nickname"


def test_returns_none_for_unknown_persona():
    resolver = PersonaResolver(lambda: sample_characters())

    assert resolver.resolve("不存在的角色") is None


def test_ambiguous_alias_is_not_silently_selected():
    def characters():
        data = sample_characters()
        data["other"] = {
            "name": "另一个祥子",
            "aliases": ["小祥"],
            "prompt": "另一个 prompt",
        }
        return data

    resolver = PersonaResolver(characters)

    match = resolver.resolve("小祥")

    assert match is not None
    assert match.ambiguous is True
    assert match.character_id == ""
    assert [item["name"] for item in match.candidates] == ["丰川祥子", "另一个祥子"]


def test_extracts_leading_actor_command():
    resolver = PersonaResolver(lambda: sample_characters())

    actor, remaining = resolver.extract_leading_actor("让小祥回复邮件 msg_123")

    assert actor is not None
    assert actor.character_id == "sakiko"
    assert remaining == "回复邮件 msg_123"


def test_does_not_extract_actor_without_leading_let():
    resolver = PersonaResolver(lambda: sample_characters())

    actor, remaining = resolver.extract_leading_actor("小祥回复邮件 msg_123")

    assert actor is None
    assert remaining == "小祥回复邮件 msg_123"
