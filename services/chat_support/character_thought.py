"""Character Thought: light pre-reply stance / emotion for natural chat."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence

EMOTION_LEVELS = ("light", "medium", "heavy")
WANT_VALUES = (
    "light_ack",
    "soft_care",
    "light_question",
    "playful",
    "direct_answer",
    "hold",
)
ANGLE_VALUES = (
    "daily_ack",
    "tease",
    "closeness",
    "care",
    "direct",
    "light_explain",
)
AVOID_CLOSED = frozenset(
    {
        "abstract_label",
        "paraphrase_summary",
        "advice_dump",
        "over_emote",
        "assistant_tone",
    }
)

DISTRESS_MARKERS = (
    "累坏",
    "好累",
    "难受",
    "痛苦",
    "崩溃",
    "崩了",
    "快崩",
    "撑不住",
    "想哭",
    "哭了",
    "绝望",
    "心力交瘁",
    "焦虑",
    "恐慌",
    "害怕",
    "无助",
    "受不了",
    "熬不下去",
    "心里堵",
    "抑郁",
    "心累",
    "烦死",
    "崩溃了",
)

SCHEDULE_CONTRAST_MARKERS = (
    "昨天",
    "今天",
    "前天",
    "明天",
    "上周",
    "这周",
    "居家",
    "开会",
    "上班",
    "下班",
    "加班",
    "台风",
    "对比",
)


@dataclass
class CharacterThought:
    situation: str = ""
    stance: str = ""
    emotion_level: str = "light"
    want: str = "light_ack"
    avoid: List[str] = field(default_factory=list)
    angle: str = "daily_ack"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clip(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def has_distress_markers(user_text: str) -> bool:
    raw = str(user_text or "")
    return any(marker in raw for marker in DISTRESS_MARKERS)


def looks_like_schedule_contrast(user_text: str) -> bool:
    raw = str(user_text or "")
    if not raw:
        return False
    hits = sum(1 for marker in SCHEDULE_CONTRAST_MARKERS if marker in raw)
    # 日常对比：至少两个时间/日程线索，或「昨天…今天…」成对出现
    if "昨天" in raw and "今天" in raw:
        return True
    return hits >= 2


def clamp_emotion_level(user_text: str, thought: CharacterThought) -> CharacterThought:
    """Code-side emotion clamp: distress beats schedule light-cap."""
    level = str(thought.emotion_level or "light").strip().lower()
    if level not in EMOTION_LEVELS:
        level = "light"

    if has_distress_markers(user_text):
        # 明确难受：禁止压成 light
        if level == "light":
            level = "medium"
        thought.emotion_level = level
        if thought.want == "light_ack":
            thought.want = "soft_care"
        if thought.angle == "daily_ack":
            thought.angle = "care"
        return thought

    if looks_like_schedule_contrast(user_text):
        # 无痛苦词的日程对比：不超过 light
        thought.emotion_level = "light"
        if thought.want in {"soft_care", "hold"}:
            thought.want = "light_ack"
        if thought.angle == "care":
            thought.angle = "daily_ack"
        return thought

    thought.emotion_level = level
    return thought


def parse_thought_payload(raw: Any) -> CharacterThought:
    data: Dict[str, Any]
    if isinstance(raw, CharacterThought):
        data = raw.to_dict()
    elif isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            data = {}
        else:
            # strip markdown fences / leading junk
            fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
            if fence:
                text = fence.group(1).strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
            try:
                parsed = json.loads(text)
                data = parsed if isinstance(parsed, dict) else {}
            except Exception:
                data = {}

    emotion = str(data.get("emotion_level") or "light").strip().lower()
    if emotion not in EMOTION_LEVELS:
        emotion = "light"
    want = str(data.get("want") or "light_ack").strip().lower()
    if want not in WANT_VALUES:
        want = "light_ack"
    angle = str(data.get("angle") or "daily_ack").strip().lower()
    if angle not in ANGLE_VALUES:
        angle = "daily_ack"

    avoid_raw = data.get("avoid") or []
    avoid: List[str] = []
    if isinstance(avoid_raw, str):
        avoid_raw = [part.strip() for part in avoid_raw.split(",") if part.strip()]
    if isinstance(avoid_raw, (list, tuple)):
        for item in avoid_raw:
            key = str(item or "").strip().lower()
            if key in AVOID_CLOSED and key not in avoid:
                avoid.append(key)
    if "abstract_label" not in avoid:
        avoid.append("abstract_label")
    if "paraphrase_summary" not in avoid:
        avoid.append("paraphrase_summary")

    return CharacterThought(
        situation=_clip(data.get("situation") or "", 40),
        stance=_clip(data.get("stance") or "", 40),
        emotion_level=emotion,
        want=want,
        avoid=avoid,
        angle=angle,
    )


def build_thought_messages(
    *,
    character_name: str,
    character_prompt_excerpt: str,
    recent: Sequence[Any],
    user_text: str,
    just_switched_character: bool = False,
) -> List[Dict[str, str]]:
    name = str(character_name or "当前角色").strip() or "当前角色"
    excerpt = _clip(character_prompt_excerpt or "", 500)
    recent_lines: List[str] = []
    for item in list(recent or [])[-6:]:
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip() or "user"
            content = _clip(item.get("content") or "", 80)
            if content:
                recent_lines.append(f"{role}: {content}")
        else:
            content = _clip(item, 80)
            if content:
                recent_lines.append(content)
    recent_block = "\n".join(recent_lines) if recent_lines else "（无）"

    switch_note = ""
    if just_switched_character:
        switch_note = (
            "\n- 你刚切换为当前角色，不要沿用上一角色的口癖与自称习惯。"
        )

    system = (
        f"你不是在对用户说话。你是角色「{name}」的内心草稿器，只输出一个 JSON 对象。\n"
        "字段：situation, stance, emotion_level, want, avoid, angle。\n"
        f"emotion_level 只能是 {list(EMOTION_LEVELS)}；"
        f"want 只能是 {list(WANT_VALUES)}；"
        f"angle 只能是 {list(ANGLE_VALUES)}；"
        f"avoid 只能从 {sorted(AVOID_CLOSED)} 中选子集。\n"
        "规则：situation/stance 各不超过 40 字，不编造事实；"
        "日常对比无痛苦词→light，明确难受→允许 medium/heavy；"
        "只输出 JSON。"
        f"{switch_note}"
    )
    if excerpt:
        system += f"\n\n【角色气质摘录】\n{excerpt}"

    user = (
        f"【最近几句】\n{recent_block}\n\n"
        f"【用户本轮】\n{str(user_text or '').strip()}\n\n"
        "请输出本轮内心 JSON。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def format_thought_for_prompt(
    thought: CharacterThought,
    *,
    short_shell: bool = True,
    just_switched_character: bool = False,
) -> str:
    """压缩注入：只给立场/档位/意图，不重复系统里的「怎么写」。"""
    avoid_bits = [
        a
        for a in (thought.avoid or [])
        if a in {"abstract_label", "paraphrase_summary", "advice_dump"}
    ]
    avoid = ",".join(avoid_bits) if avoid_bits else "abstract_label"
    # 一行式，减少「参考说明书」感
    stance = thought.stance or "先接住"
    situation = thought.situation or ""
    head = f"{situation}｜{stance}" if situation else stance
    lines = [
        "【本轮内心·勿照念】",
        f"{head}；档={thought.emotion_level}；意图={thought.want}；避={avoid}",
    ]
    if just_switched_character:
        lines.append("刚换角，勿沿用上一角色口癖。")
    return "\n".join(lines)


def _is_thought_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return "timeout" in text or "timed out" in text


def _looks_like_llm_failure(raw: Any) -> bool:
    text = str(raw or "").strip()
    if not text:
        return True
    if text.startswith("❌"):
        return True
    if text.startswith("（") and ("失败" in text or "错误" in text):
        return True
    return False


def generate_character_thought(
    *,
    chat_fn: Callable[..., str],
    character_name: str,
    character_prompt_excerpt: str,
    recent: Sequence[Any],
    user_text: str,
    just_switched_character: bool = False,
    timeout_ms: int = 2500,
    max_tokens: int = 220,
    on_error: str = "skip_thought",
) -> tuple[Optional[CharacterThought], str, float]:
    """Returns (thought_or_None, skip_reason, latency_ms)."""
    started = time.perf_counter()
    messages = build_thought_messages(
        character_name=character_name,
        character_prompt_excerpt=character_prompt_excerpt,
        recent=recent,
        user_text=user_text,
        just_switched_character=just_switched_character,
    )
    timeout_s = max(0.2, float(timeout_ms or 2500) / 1000.0)
    token_budget = max(1, int(max_tokens or 220))

    try:
        raw = chat_fn(
            messages,
            task_type="gatekeeper",
            caller="character_thought",
            timeout_sec=timeout_s,
            max_tokens=token_budget,
        )
    except Exception as exc:
        latency = (time.perf_counter() - started) * 1000.0
        reason = "timeout" if _is_thought_timeout(exc) else "error"
        if on_error == "block_reply":
            if reason == "timeout":
                raise TimeoutError("character_thought_timeout") from exc
            raise
        return None, reason, latency

    if _looks_like_llm_failure(raw):
        latency = (time.perf_counter() - started) * 1000.0
        if on_error == "block_reply":
            raise TimeoutError("character_thought_timeout")
        return None, "timeout", latency

    thought = parse_thought_payload(raw)
    thought = clamp_emotion_level(user_text, thought)
    latency = (time.perf_counter() - started) * 1000.0
    return thought, "", latency
