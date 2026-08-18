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
    reason = str(reason or "").strip()
    reason_label = "长时间停留" if reason == "duration" else "切换窗口"
    session_n = max(1, int(count or 1))
    # 挂机/久坐时不要把「会话次数」说成「打开了 N 次」——用户体感是一直在同一页
    if reason == "duration":
        session_line = (
            f"- 今天对这个应用的独立使用会话约 {session_n} 段"
            "（短时间失焦再回来不算重新打开；不要说成打开了 N 次）"
        )
    else:
        session_line = (
            f"- 今天回到这个应用的独立会话：第 {session_n} 段"
            "（不是原始切窗次数；勿夸张成反复打开）"
        )
    return "\n".join(
        [
            "【本次屏幕使用上下文】",
            f"- 应用/窗口：{app}",
            f"- 分类：{str(category or 'other').strip()}",
            f"- 触发原因：{reason_label}",
            session_line,
            f"- 今天累计使用这个应用约：{format_sensor_seconds(app_duration_sec)}",
            f"- 本次已停留约：{format_sensor_seconds(current_stay_sec)}",
            "- 优先根据「本次已停留」说话；不要机械复述次数，更不要说「打开了十二次」这类夸张说法。",
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
    detail = (
        f"这次线索是“{title_hint}”（独立会话约第 {max(1, int(count or 1))} 段，勿说成反复打开）。"
        if title_hint
        else ""
    )
    mode = "视觉截图" if is_vision else "窗口标题"
    parts = [
        "【临场】",
        f"- 线索：{mode}。{random.choice(shapes)}{random.choice(endings)}",
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


# 屏幕吐槽专用：禁止把会话次数念成「打开了 N 次」。
# 与 Character Natural 的 forbidden_phrase_guard 分开，勿混进 QQ 闲聊。
_CN_NUM = r"(?:\d+|[一二三四五六七八九十两俩仨几好多]+)"
SENSOR_OPEN_COUNT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        rf"打开了\s*{_CN_NUM}\s*次",
        rf"打开过\s*{_CN_NUM}\s*次",
        rf"打开\s*{_CN_NUM}\s*次",
        rf"切了\s*{_CN_NUM}\s*次",
        rf"切到了?\s*{_CN_NUM}\s*次",
        rf"切回来\s*{_CN_NUM}\s*次",
        rf"又打开了?\s*{_CN_NUM}\s*次",
        rf"反复打开了?\s*{_CN_NUM}\s*次",
        rf"打开了?\s*十\s*几\s*次",
        rf"打开了?\s*好多\s*次",
        rf"打开了?\s*好几\s*次",
    )
)


def find_sensor_open_count_phrases(text: str) -> list[str]:
    """Return matched open-count exaggeration spans (sensor-only)."""
    clean = str(text or "").strip()
    if not clean:
        return []
    hits: list[str] = []
    for pattern in SENSOR_OPEN_COUNT_PATTERNS:
        for match in pattern.finditer(clean):
            span = match.group(0)
            if span and span not in hits:
                hits.append(span)
    return hits


def strip_sensor_open_count_phrases(text: str) -> str:
    """
    Rule-only strip of open-count exaggeration; no polish LLM.
    Keeps the rest of the sentence usable when possible.
    """
    clean = str(text or "")
    if not clean:
        return ""
    for pattern in SENSOR_OPEN_COUNT_PATTERNS:
        clean = pattern.sub("", clean)
    # 去掉因删次数留下的连接词碎片
    clean = re.sub(r"(已经|居然|竟然|又|还|都){1,2}[，,、\s]*$", "", clean)
    clean = re.sub(r"[，,]{2,}", "，", clean)
    clean = re.sub(r"。{2,}", "。", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    clean = re.sub(r"^[，,、。．\s]+", "", clean)
    clean = re.sub(r"[，,、\s]+$", "", clean)
    return clean.strip(" ，,。．\n\t")


def sanitize_sensor_open_count_reply(text: str) -> tuple[str, list[str]]:
    """
    If reply exaggerates open counts, strip those spans.
    Returns (cleaned_text, hits). Empty cleaned_text means caller should drop.
    """
    hits = find_sensor_open_count_phrases(text)
    if not hits:
        return str(text or "").strip(), []
    cleaned = strip_sensor_open_count_phrases(text)
    # 删完后几乎没内容 → 交由上层丢弃，避免只剩「你今天」之类残句
    if len(re.sub(r"\s+", "", cleaned)) < 2:
        return "", hits
    return cleaned, hits


def looks_like_sensor_template_reply(
    text: str, *, clean_text_fn: Callable[[str], str] = text_utils.clean_text_for_tts
) -> bool:
    clean = clean_text_fn(str(text or "")).strip()
    if not clean:
        return True
    # 只拦观察报告/客服腔。口语里的「你又 / 还真 / 看起来 / 盯着」不要当模板。
    bad_phrases = (
        "用户正在",
        "当前窗口",
        "屏幕上",
        "画面中",
        "我看到",
        "根据屏幕",
        "根据窗口",
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
    )
    if any(item in clean for item in bad_phrases):
        return True
    if re.search(r"^(这个|这份|这页|这个页面|这个窗口)[，,、]?(挺|很|比较)", clean):
        return True
    return False
