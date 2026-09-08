from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from .watch import fold_zh

ACTIONS = ("eq_list", "typhoon", "weather_alert", "status")
QUERY_CUES = (
    "有没有",
    "有木有",
    "查一下",
    "查下",
    "查查",
    "查询",
    "列表",
    "最近",
    "现在",
    "最新",
    "看看",
    "吗",
    "呢",
)
DISASTER_CUES = (
    "地震预警",
    "暴雨预警",
    "气象预警",
    "预警信号",
    "强对流",
    "台风路径",
    "余震",
    "地震",
    "台风",
)
PLACE_STOP = (
    "有没有",
    "有木有",
    "查一下",
    "查下",
    "查查",
    "查询",
    "列表",
    "最近",
    "现在",
    "最新",
    "看看",
    "地震预警",
    "余震",
    "地震",
    "台风路径",
    "台风",
    "暴雨预警",
    "气象预警",
    "预警信号",
    "强对流",
    "新闻",
    "报道",
    "消息",
    "附近",
    "一带",
    "发生",
    "记录",
    "情况",
    "信息",
)
PLACE_TRAIL = re.compile(r"[有没吗呢啊嘛呀的了吧么在到吗？?！!，。、\s]+")
KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")


def looks_like_disaster_query(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if (
        "天气" in raw
        and "预警" not in raw
        and "地震" not in raw
        and "台风" not in raw
    ):
        return False
    has_disaster = any(cue in raw for cue in DISASTER_CUES)
    has_query = any(cue in raw for cue in QUERY_CUES) or ("查" in raw)
    return has_disaster and has_query


def infer_action(text: str) -> str:
    raw = str(text or "")
    if "台风" in raw and "地震" not in raw:
        return "typhoon"
    if any(
        word in raw
        for word in ("气象预警", "天气预警", "暴雨预警", "强对流", "预警信号")
    ) and "地震" not in raw:
        return "weather_alert"
    if "状态" in raw and "地震" not in raw and "台风" not in raw:
        return "status"
    return "eq_list"


def extract_place(text: str, regions: Optional[Sequence[Any]] = None) -> str:
    raw = fold_zh(str(text or "").strip())
    if not raw:
        return ""
    candidates = []
    for region in regions or []:
        label = str(getattr(region, "label", "") or "").strip()
        if label:
            candidates.append(label)
        for keyword in getattr(region, "keywords", None) or []:
            key = str(keyword or "").strip()
            if key:
                candidates.append(key)
    candidates.sort(key=len, reverse=True)
    for item in candidates:
        folded = fold_zh(item)
        if folded and folded in raw:
            return item
    leftover = raw
    for cue in sorted(PLACE_STOP, key=len, reverse=True):
        leftover = leftover.replace(cue, " ")
    leftover = PLACE_TRAIL.sub("", leftover)
    leftover = leftover.strip()
    if leftover in {"感", "感觉", "这部剧", "剧"}:
        return ""
    return leftover[:20]


def parse_kv(text: str) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for key, value in KV_RE.findall(str(text or "")):
        params[key] = value.strip().strip("'\"")
    return params


def parse_invocation(text: str) -> Tuple[str, str]:
    raw = str(text or "").strip()
    params = parse_kv(raw)
    action = str(params.get("action") or "").strip()
    place = str(params.get("place") or "").strip()
    leftover = KV_RE.sub(" ", raw).strip()
    tokens = leftover.split()
    if not action and tokens and tokens[0] in ACTIONS:
        action = tokens[0]
        leftover = leftover[len(action) :].strip()
    if not place:
        place = leftover
    if action in ACTIONS:
        if action == "eq_list" and not place:
            place = ""
        return action, place
    if looks_like_disaster_query(raw):
        return infer_action(raw), extract_place(raw)
    if leftover in {"最近地震", "地震列表"}:
        return "eq_list", ""
    if leftover in {"台风", "最近台风", "台风列表"}:
        return "typhoon", ""
    if leftover in {"气象预警", "预警", "暴雨预警"}:
        return "weather_alert", ""
    return "", place
