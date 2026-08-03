from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MemoryCategory:
    id: str
    label: str
    parent_id: str = ""


CATEGORIES = (
    MemoryCategory("all", "全部记忆"),
    MemoryCategory("identity", "称呼与身份"),
    MemoryCategory("likes", "喜欢"),
    MemoryCategory("likes.music", "音乐", "likes"),
    MemoryCategory("likes.anime", "动漫与角色", "likes"),
    MemoryCategory("likes.games", "游戏", "likes"),
    MemoryCategory("likes.food", "食物", "likes"),
    MemoryCategory("likes.art", "绘画与审美", "likes"),
    MemoryCategory("likes.other", "其他", "likes"),
    MemoryCategory("dislikes", "不喜欢"),
    MemoryCategory("habits", "习惯"),
    MemoryCategory("status", "近期状态"),
    MemoryCategory("interaction", "互动与回复规则"),
    MemoryCategory("events", "经历与事件"),
    MemoryCategory("tasks", "待办与承诺"),
    MemoryCategory("uncategorized", "未分类"),
)

CATEGORY_BY_ID = {item.id: item for item in CATEGORIES}
OVERRIDABLE_CATEGORY_IDS = frozenset(CATEGORY_BY_ID) - {"all", "likes"}

_ART_TERMS = (
    "绘画",
    "插画",
    "构图",
    "五官",
    "镜头",
    "画面",
    "高细节",
    "雨后街道",
    "雨中漫步",
    "fan art",
)
_ANIME_TERMS = (
    "动漫",
    "动画",
    "声优",
    "丰川祥子",
    "高松灯",
    "mygo",
    "ave mujica",
)
_GAME_TERMS = ("游戏", "gaming", "宝可梦", "原神", "骰子", "roco kingdom")
_HABIT_TERMS = ("习惯", "经常", "总是", "深夜", "作息", "每天", "长期")


def category_options(*, include_parent: bool = False) -> tuple[MemoryCategory, ...]:
    blocked = {"all"}
    if not include_parent:
        blocked.add("likes")
    return tuple(item for item in CATEGORIES if item.id not in blocked)


def category_matches(selected_id: str, actual_id: str) -> bool:
    selected = str(selected_id or "all").strip() or "all"
    actual = str(actual_id or "uncategorized").strip() or "uncategorized"
    if selected == "all":
        return True
    return actual == selected or actual.startswith(selected + ".")


def classify_memory_record(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    override = str(metadata.get("category_override") or "").strip()
    if override in OVERRIDABLE_CATEGORY_IDS:
        return override

    key = str(record.get("key") or "").strip().lower()
    kind = str(record.get("kind") or "").strip().lower()
    content = str(record.get("content") or "").strip().lower()
    source_type = str(record.get("source_type") or "").strip().lower()

    if _is_diary_record(key, content, metadata):
        return "uncategorized"
    if key in {"preferred_address", "name", "identity_summary"}:
        return "identity"
    if key.startswith(("identity.", "identity_", "role:")):
        return "identity"
    if key.startswith(("reply.", "interaction.", "interaction_")) or kind == "rule":
        return "interaction"
    if key.startswith("status.") or key == "status":
        return "status"
    if key.startswith(("habit.", "habits.")):
        return "habits"
    if key.startswith("notes.") or key == "notes":
        return "habits" if _contains_any(content, _HABIT_TERMS) else "identity"
    if key.startswith("dislikes"):
        return "dislikes"
    if key.startswith("likes.music"):
        return "likes.music"
    if key.startswith(("likes.games", "likes.game")):
        return "likes.games"
    if key.startswith("likes.food"):
        return "likes.food"
    if key.startswith(("likes.anime", "likes.character")):
        return "likes.anime"
    if key.startswith(("likes.art", "likes.drawing")):
        return "likes.art"
    if key.startswith("likes.general"):
        if _contains_any(content, _ART_TERMS):
            return "likes.art"
        if _contains_any(content, _ANIME_TERMS):
            return "likes.anime"
        if _contains_any(content, _GAME_TERMS):
            return "likes.games"
        return "likes.other"
    if key.startswith("likes"):
        return "likes.other"
    if key == "user_task" or "task" in source_type or "commitment" in source_type:
        return "tasks"
    if kind == "episode":
        return "events"
    return "uncategorized"


def category_counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {item.id: 0 for item in CATEGORIES}
    for record in records:
        actual = classify_memory_record(record)
        counts["all"] += 1
        counts[actual] = counts.get(actual, 0) + 1
        parent_id = CATEGORY_BY_ID.get(actual, MemoryCategory(actual, actual)).parent_id
        if parent_id:
            counts[parent_id] = counts.get(parent_id, 0) + 1
    return counts


def _is_diary_record(key: str, content: str, metadata: dict[str, Any]) -> bool:
    title = str(metadata.get("title") or "").strip().lower()
    legacy_tags = metadata.get("legacy_tags") if isinstance(metadata.get("legacy_tags"), list) else []
    tags = {str(item).strip().lower() for item in legacy_tags}
    return "daily_log" in tags or "日记" in key or "日记" in title or content.startswith("【日记 ")


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)
