from modules.memory_core.categories import (
    CATEGORIES,
    category_matches,
    classify_memory_record,
)


def test_category_definitions_have_stable_unique_ids():
    category_ids = [item.id for item in CATEGORIES]

    assert category_ids[0] == "all"
    assert len(category_ids) == len(set(category_ids))
    assert "likes.music" in category_ids
    assert "uncategorized" in category_ids


def test_category_uses_structured_key_prefixes():
    assert (
        classify_memory_record({"key": "likes.music.0", "kind": "preference"})
        == "likes.music"
    )
    assert (
        classify_memory_record({"key": "likes.games.2", "kind": "preference"})
        == "likes.games"
    )
    assert (
        classify_memory_record({"key": "likes.food.1", "kind": "preference"})
        == "likes.food"
    )
    assert (
        classify_memory_record({"key": "dislikes.food.0", "kind": "preference"})
        == "dislikes"
    )
    assert classify_memory_record({"key": "status.1", "kind": "profile"}) == "status"
    assert (
        classify_memory_record({"key": "reply.response_length", "kind": "preference"})
        == "interaction"
    )


def test_category_refines_bounded_general_likes():
    assert (
        classify_memory_record(
            {"key": "likes.general.0", "kind": "preference", "content": "高质量日系动画插画"}
        )
        == "likes.art"
    )
    assert (
        classify_memory_record(
            {"key": "likes.general.1", "kind": "preference", "content": "丰川祥子"}
        )
        == "likes.anime"
    )
    assert (
        classify_memory_record(
            {"key": "likes.general.2", "kind": "preference", "content": "gaming"}
        )
        == "likes.games"
    )
    assert (
        classify_memory_record(
            {"key": "likes.general.3", "kind": "preference", "content": "工作生活平衡"}
        )
        == "likes.other"
    )


def test_category_override_wins_and_unknown_override_is_ignored():
    record = {
        "key": "likes.music.0",
        "kind": "preference",
        "metadata": {"category_override": "habits"},
    }
    assert classify_memory_record(record) == "habits"

    record["metadata"]["category_override"] = "missing"
    assert classify_memory_record(record) == "likes.music"


def test_diary_episode_is_not_an_event():
    row = {
        "kind": "episode",
        "key": "2026-07-09 日记",
        "content": "日记正文",
        "metadata": {"title": "2026-07-09 日记"},
    }

    assert classify_memory_record(row) == "uncategorized"


def test_parent_category_matches_children():
    assert category_matches("likes", "likes.music") is True
    assert category_matches("likes", "likes.food") is True
    assert category_matches("likes.music", "likes.music") is True
    assert category_matches("likes.music", "likes.games") is False
    assert category_matches("all", "uncategorized") is True
