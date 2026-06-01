from __future__ import annotations

import random
import re
from typing import Any, Callable, Iterable

from services.chat_support import text_utils


def format_sensor_seconds(seconds: float | int | None) -> str:
    try:
        sec = max(0.0, float(seconds or 0))
    except Exception:
        sec = 0.0
    if sec < 60:
        return f"{int(sec)}秒"
    minutes = sec / 60.0
    if minutes < 60:
        return f"{minutes:.0f}分钟"
    hours = minutes / 60.0
    return f"{hours:.1f}小时"


def build_sensor_usage_context(
    *,
    app_name: str,
    category: str,
    count: int,
    reason: str,
    app_duration_sec: float | int | None = None,
    current_stay_sec: float | int | None = None,
) -> str:
    app = str(app_name or "").strip() or "当前应用"
    reason_label = "长时间停留" if str(reason or "").strip() == "duration" else "切换窗口"
    return "\n".join(
        [
            "【本次屏幕使用上下文】",
            f"- 应用/窗口：{app}",
            f"- 分类：{str(category or 'other').strip()}",
            f"- 触发原因：{reason_label}",
            f"- 今天打开/切到它：第 {int(count or 1)} 次",
            f"- 今天累计使用这个应用约：{format_sensor_seconds(app_duration_sec)}",
            f"- 本次已停留约：{format_sensor_seconds(current_stay_sec)}",
            "- 回应时可以参考这些数据判断是轻轻提醒、关心、陪伴、疑问，还是一点点吐槽；不要机械复述数字。",
        ]
    )


def build_sensor_spontaneous_style_block(
    *, title: str, category: str, count: int, is_vision: bool
) -> str:
    shapes = (
        "像小声嘟囔半句，句子可以不那么完整。",
        "用一个轻轻的反问收住，不要解释。",
        "只挑一个细节戳一下，别评价整件事。",
        "像刚瞥到时顺口接话，少用完整书面句。",
        "把重点放在 Master 这个人身上，不要讲页面好不好。",
    )
    endings = (
        "可以没句号，像聊天气口。",
        "可以用一点口语词，但不要把少数几个词当固定开头。",
        "别用固定开头，尤其别连续用“嗯……”“这个”“看起来”。",
        "如果没什么好说，就轻轻带过；可以关心、提醒、疑问，不要硬吐槽。",
    )
    category_hint = ""
    cat = str(category or "").strip().lower()
    if cat in {"coding", "work"}:
        category_hint = "窗口像工作/代码场景时，可以关心他是不是卡住、提醒休息，或轻轻戳他又在较劲；别像进度汇报。"
    elif cat in {"browser", "reading"}:
        category_hint = "浏览/阅读场景里，别夸内容实用，改说他看这个东西时显得怎样。"
    elif cat == "gaming":
        category_hint = "游戏场景可以像旁边陪看，也可以轻轻吐槽，但不要突然很激动。"
    elif cat == "video":
        category_hint = "视频场景可以像随口陪看或提醒别看太久，不要复述标题。"

    title_hint = str(title or "").strip()
    if len(title_hint) > 60:
        title_hint = title_hint[:57].rstrip() + "..."
    detail = f"这次线索是“{title_hint}”，今天第 {count} 次。" if title_hint else ""
    mode = "视觉截图" if is_vision else "窗口标题"
    parts = [
        "【临场说话方式】",
        f"- 你只拿{mode}当线索，不要报告观察结果。",
        f"- {random.choice(shapes)}",
        f"- {random.choice(endings)}",
    ]
    if category_hint:
        parts.append(f"- {category_hint}")
    if detail:
        parts.append(f"- {detail}")
    return "\n".join(parts)


def format_sensor_observations(entries: Iterable[Any], max_items: int = 3) -> str:
    if not entries:
        return ""
    lines: list[str] = []
    tail = list(entries)[-max_items:]
    for item in tail:
        if not isinstance(item, dict):
            continue
        time_text = str(item.get("time") or "").strip()
        app = str(item.get("app") or item.get("window_title") or "").strip()
        content = str(item.get("content") or "").strip()
        if content:
            content = text_utils.compress_sensor_text(content, max_len=160)
        prefix = f"{time_text} " if time_text else ""
        if app and content:
            line = f"- {prefix}{app}: {content}"
        elif app:
            line = f"- {prefix}{app}"
        elif content:
            line = f"- {prefix}{content}"
        else:
            continue
        lines.append(line)
    return "\n".join(lines)


def sensor_emotion_intensity(emotion: str) -> float:
    emo = str(emotion or "neutral").strip().lower()
    if emo == "neutral":
        return 0.16
    if emo == "think":
        return 0.22
    if emo in {"happy", "flustered"}:
        return 0.26
    if emo in {"sad", "angry", "confused"}:
        return 0.30
    return 0.24


def looks_like_sensor_template_reply(
    text: str, *, clean_text_fn: Callable[[str], str] = text_utils.clean_text_for_tts
) -> bool:
    clean = clean_text_fn(str(text or "")).strip()
    if not clean:
        return True
    bad_phrases = (
        "用户正在",
        "当前窗口",
        "屏幕上",
        "画面中",
        "我看到",
        "看起来",
        "根据",
        "当前情况",
        "总结",
        "挺实用",
        "步骤详尽",
        "请仔细阅读",
        "需要协助",
        "收获颇丰",
        "至关重要",
        "注意基础",
        "主要内容",
        "值得关注",
        "这也要看",
        "也要看",
        "也要盯",
        "盯着",
        "你又",
        "还真",
    )
    if any(item in clean for item in bad_phrases):
        return True
    if re.search(r"^(这个|这份|这页|这个页面|这个窗口)[，,、]?(挺|很|比较)", clean):
        return True
    if clean.count("，") >= 2 and len(clean) > 24:
        return True
    return False
