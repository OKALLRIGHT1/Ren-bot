"""Pure text helpers used by ChatService.

Keep this module free of ChatService state so helpers can be moved safely.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional

_INTERNAL_PROMPT_INSTRUCTION_LINE_RE = re.compile(
    r"^\s*(?:"
    r"【动作/微表情】.*"
    r"|正文不要引用标签和动作.*"
    r"|回复只要[「\"]?正文[」\"]?本身.*"
    r"|不含任何额外说明.*"
    r"|优先使用 function calling.*"
    r"|不要在正文(?:里写|夹带) \[CMD:.*"
    r"|只有当系统消息里出现【本轮可用工具】.*"
    r"|只有系统提供了 tools.*"
    r"|当前不直接暴露完整工具参数.*"
    r"|可发现能力摘要.*"
    r"|这些工具需要通过副脑委托.*"
    r"|你不是在对用户说话.*"
    r"|只输出(?:一个 JSON|修正后的回复正文).*"
    r"|每次回复必须在正文最开头加一个情绪标签.*"
    r"|严禁提及自己是 AI.*"
    r")\s*$",
    re.IGNORECASE,
)

_LEAKED_TOOL_PROMPT_SECTION_RE = re.compile(
    r"(?:"
    r"【(?:"
    r"工具调用规则|调用方式|调用格式|可延迟发现的工具|本轮可用工具|可用工具能力|工具|"
    r"可委托任务|委托方式|委托格式|远程MCP工具|工具状态|工具使用记录|"
    r"输出格式规范|输出格式(?:（非常重要）)?|对话边界|回复风格|性格设定|"
    r"代码助手模式|系统反馈|副脑模式|本轮内心·勿照念|表达习惯参考|"
    r"重要：工具调用规则|Live2D自我识别"
    r")】"
    r"|\[Tool Observation\])"
    r"[\s\S]*"
)

_EMO_TAG_ANY_RE = re.compile(
    r"<\s*/?\s*emo(?:\s*=\s*[a-zA-Z_]+)?\s*>", re.IGNORECASE
)


def clean_text_for_tts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\*#]+", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def strip_wrapping_quotes(text: str) -> str:
    cleaned = str(text or "").strip()
    quote_pairs = {
        '"': '"',
        "'": "'",
        "“": "”",
        "‘": "’",
        "「": "」",
        "『": "』",
        "《": "》",
    }
    changed = True
    while changed and len(cleaned) >= 2:
        changed = False
        first = cleaned[0]
        last = cleaned[-1]
        if quote_pairs.get(first) == last:
            cleaned = cleaned[1:-1].strip()
            changed = True
    return cleaned


def strip_search_followup_fillers(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    fillers = [
        "你",
        "她",
        "请",
        "可以",
        "能不能",
        "帮我",
        "帮忙",
        "麻烦",
        "先",
        "再",
        "一下",
        "一查",
        "查一下",
        "查一查",
        "查查",
        "搜一下",
        "搜一搜",
        "搜搜",
        "搜索一下",
        "查",
        "搜",
        "搜索",
        "联网",
        "上网",
        "看看",
        "给我",
        "回答我",
        "回复我",
        "告诉我",
        "再回答",
        "再回复",
        "再说",
        "然后回答",
        "然后告诉我",
        "之后回答",
        "后再回答",
        "好吗",
        "行吗",
        "吗",
        "吧",
    ]
    cleaned = raw
    for token in sorted(fillers, key=len, reverse=True):
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"[\s，。,！!？?：:;；、】【\"'“”‘’()（）\-]+", "", cleaned)
    return cleaned.strip()


def is_generic_search_followup_request(text: str) -> bool:
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    wants_search = any(
        token in raw
        for token in ("查", "搜", "搜索", "联网", "上网", "百度", "google", "bing")
    )
    if not wants_search:
        return False
    if any(token in raw for token in ("http://", "https://", "萌百", "萌娘百科")):
        return False
    if any(token in raw for token in ("怎么写", "代码", "文件", ".py", ".md", "\\", "/")):
        return False
    has_context_ref = any(
        token in raw
        for token in ("上面", "上文", "刚才", "刚刚", "前面", "那个", "这个", "之前", "同上")
    )
    has_reply_followup = any(
        token in raw
        for token in ("再回答", "再回复", "回答我", "回复我", "告诉我", "然后回答", "然后告诉我")
    )
    if is_search_retry_correction_request(raw):
        return True
    residual = strip_search_followup_fillers(raw)
    if not has_context_ref and not has_reply_followup:
        return not residual
    if len(residual) >= 8:
        return False
    return True


def is_search_retry_correction_request(text: str) -> bool:
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    has_search_retry = any(
        token in raw for token in ("重新查", "重查", "再查", "重新搜", "再搜")
    )
    if not has_search_retry:
        return False
    return any(
        token in raw
        for token in ("过时", "旧闻", "不对", "不太对", "不是", "错了", "今年", "2026")
    )


def is_search_topic_candidate(
    text: str,
    *,
    looks_structured_reply: Optional[Callable[[str], bool]] = None,
) -> bool:
    raw = str(text or "").strip()
    if not raw or len(raw) < 4:
        return False
    lower = raw.lower()
    if is_generic_search_followup_request(raw):
        return False
    if raw.startswith(("/", "#", "!", "！")):
        return False
    if any(token in lower for token in ("[cmd:", "workspace_ops", "apply_change")):
        return False
    if looks_structured_reply is not None and looks_structured_reply(raw):
        return False
    return True


_LOCAL_OR_CODE_HINTS = (
    "http://",
    "https://",
    ".py",
    ".md",
    "workspace",
    "代码",
    "文件",
)

_WEATHER_PLUGIN_HINTS = (
    "天气",
    "气温",
    "温度",
    "几度",
    "多少度",
    "体感",
    "下雨",
    "降雨",
    "带伞",
    "雨伞",
    "天气预报",
    "高温",
    "低温",
)
_TYPHOON_HINTS = ("台风", "热带风暴", "热带气旋")
_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|thought|redacted_thinking)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_TAG_RE = re.compile(
    r"</?(think|thinking|thought|redacted_thinking)\b[^>]*>",
    re.IGNORECASE,
)

_SEARCHWORTHY_HINTS = (
    "最新",
    "今天",
    "实时",
    "新闻",
    "价格",
    "行情",
    "股价",
    "汇率",
    "日期",
    "时间",
    "是什么",
    "是谁",
    "叫什么",
    "知不知道",
    "听说过",
    "了解",
    "为什么",
    "怎么",
    "如何",
    "多少",
    "有没有",
    "哪部",
    "哪个",
    "什么梗",
)

_FACT_QUESTION_MARKERS = re.compile(
    r"(吗|么|呢|？|\?|什么|哪个|哪部|谁|几|多少|有没有|会不会|是不是|知不知道|听说过)"
)
_FACT_TIME_FRAME = re.compile(
    r"(最近|近期|目前|现在|刚刚|刚才|今天|今日|昨天|昨日|明天|明日|"
    r"这周|本周|上周|下周|这个月|本月|上个月|今年|去年|实时|最新)"
)
_FACT_PLACE = re.compile(
    r"(这边|那里|国内|国外|当地|中国|[一-龥]{2,8}(?:省|市|县|区|镇|村|州))"
)
_FACT_ASK_FRAME = re.compile(
    r"(会有|有没有|会不会|是不是|叫什么|是什么|是谁|多少|怎么样|如何|什么时候|何时)"
)
_PERSONAL_STATE_QUESTION = re.compile(
    r"(你|你们|您).{0,10}(怎么样|怎样|还好|还在|想|累|烦|忙|饿|困|开心|难过)"
)
_PERSONAL_OR_PLAN_QUESTION = re.compile(
    r"(晚饭|午饭|早饭|早餐|午餐|晚餐|宵夜).{0,6}吃|"
    r"吃什么|玩什么|看什么|干什么|去做什么|"
    r"好不好玩|好不好看|好不好吃|要不要|好不好"
)
_PREFERENCE_OBJECT = re.compile(r"什么(?:好玩|好看|好吃|有趣)的")


def is_weather_plugin_question(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if any(token in raw for token in _TYPHOON_HINTS):
        return False
    return any(token in raw for token in _WEATHER_PLUGIN_HINTS)


def strip_think_tags(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    raw = _THINK_BLOCK_RE.sub("", raw)
    raw = _THINK_TAG_RE.sub("", raw)
    return raw.strip()


def stamp_search_query_with_current_year(
    query: str, *, now: Optional[datetime] = None
) -> str:
    raw = str(query or "").strip()
    if not raw:
        return ""
    current = now or datetime.now()
    year = str(current.year)
    if re.search(r"20\d{2}", raw):
        return raw
    if any(token in raw for token in ("今天", "今日", "现在", "目前", "实时", "今年")):
        return f"{year}年 {raw}"
    return raw


def _looks_like_local_or_code_request(text: str) -> bool:
    lower = str(text or "").strip().lower()
    return any(token in lower for token in _LOCAL_OR_CODE_HINTS)


def _looks_like_question(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if raw.endswith(("?", "？", "吗", "么", "呢")):
        return True
    return bool(_FACT_QUESTION_MARKERS.search(raw))


def _looks_like_personal_or_casual_question(text: str) -> bool:
    raw = str(text or "").strip()
    if _PERSONAL_STATE_QUESTION.search(raw):
        return True
    if _PERSONAL_OR_PLAN_QUESTION.search(raw):
        return True
    if _PREFERENCE_OBJECT.search(raw):
        return True
    if re.fullmatch(r"(最近|今天|现在)怎么样[吗么呢？?]*", raw):
        return True
    # "你最近会不会来" is about the other person; "你知道上周台风叫什么" is not.
    if re.match(r"^(那)?(你|你们|您)", raw) and not re.search(
        r"(知道|听说过)", raw
    ):
        return True
    return False


def is_searchworthy_question(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or len(raw) < 4:
        return False
    if _looks_like_local_or_code_request(raw):
        return False
    if is_weather_plugin_question(raw):
        return False
    if any(token in raw for token in _SEARCHWORTHY_HINTS):
        return True
    if raw.endswith(("?", "？", "吗", "么", "呢")):
        return True
    return False


def is_direct_fact_search_question(text: str) -> bool:
    """Ask about an external, checkable fact — not a topic-word list."""
    raw = str(text or "").strip()
    if not raw or len(raw) < 4:
        return False
    if _looks_like_local_or_code_request(raw):
        return False
    if not _looks_like_question(raw):
        return False
    if _looks_like_personal_or_casual_question(raw):
        return False
    if is_weather_plugin_question(raw):
        return False

    has_time = bool(_FACT_TIME_FRAME.search(raw))
    has_place = bool(_FACT_PLACE.search(raw))
    has_ask = bool(_FACT_ASK_FRAME.search(raw))
    # Place + time, or a public ask framed by time/place, needs a source.
    if has_ask and (has_time or has_place):
        return True
    if has_time and has_place:
        return True
    if re.search(r"(叫什么|是谁|是什么|什么梗|多少钱|现价|多少)", raw):
        return True
    return False


def looks_like_uncertain_answer(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    hints = (
        "我不太确定",
        "我不确定",
        "我不知道",
        "不太清楚",
        "不清楚",
        "记不清",
        "没法确定",
        "无法确认",
        "我不太懂",
        "可能是",
        "大概是",
        "好像是",
        "印象里",
        "我查不到",
    )
    return any(hint in raw for hint in hints)


def is_link_request(text: str) -> bool:
    raw = str(text or "")
    lower = raw.lower()
    if "链接" in raw or "网址" in raw:
        return True
    return ("link" in lower) or ("url" in lower)


def extract_first_url(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"https?://[^\s)）]+", str(text))
    if not match:
        return ""
    return match.group(0).rstrip(".,;，。)")


def strip_urls(text: str) -> str:
    return re.sub(r"https?://[^\s)）]+", "", str(text or "")).strip()


def extract_url_from_tool_results(ctx: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ctx, dict):
        return ""
    results = ctx.get("_tool_results") or []
    if not isinstance(results, list):
        results = [results]
    for item in results:
        url = extract_first_url(str(item or ""))
        if url:
            return url
    return ""


def build_share_title(text: str, url: str) -> str:
    cleaned = re.sub(r"\s+", " ", strip_urls(text)).strip()
    if cleaned:
        return cleaned[:48]
    return url


def build_share_content(text: str, title: str) -> str:
    cleaned = re.sub(r"\s+", " ", strip_urls(text)).strip()
    if not cleaned:
        return ""
    if cleaned.startswith(title):
        cleaned = cleaned[len(title) :].strip()
    return cleaned[:80]


def strip_emo_tags_anywhere(text: str, emo_tag_re: re.Pattern[str]) -> str:
    cleaned = emo_tag_re.sub("", text or "")
    return _EMO_TAG_ANY_RE.sub("", cleaned)


def strip_cmd_anywhere(text: str, cmd_re: re.Pattern[str]) -> str:
    return cmd_re.sub("", text or "")


_LEAKED_TOOL_CALL_RE = re.compile(
    r"^[A-Za-z][\w\-]*(?:\s*\(.*\)|\s*\".*\")\s*$"
)


def looks_like_leaked_tool_call(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if raw.startswith("[CMD:"):
        return True
    if re.fullmatch(r"\[CMD:.*\]", raw, flags=re.DOTALL | re.IGNORECASE):
        return True
    return bool(_LEAKED_TOOL_CALL_RE.fullmatch(raw))


def strip_internal_tags(text: str) -> str:
    raw = strip_think_tags(str(text or ""))
    raw = re.sub(r"\[tool_use\]\s*\[[^\]]*\]\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[tool_use\]\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[search_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[web_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[moegirl_meta\][^\n]*\n?", "", raw, flags=re.IGNORECASE)
    raw = "\n".join(
        line for line in raw.splitlines() if not _INTERNAL_PROMPT_INSTRUCTION_LINE_RE.match(line)
    )
    raw = _LEAKED_TOOL_PROMPT_SECTION_RE.sub("", raw)
    return raw.strip()


def compress_sensor_text(text: str, max_len: int = 800) -> str:
    compressed = str(text or "").replace("\r\n", "\n").strip()
    if not compressed:
        return ""

    compressed = re.sub(r"\n{3,}", "\n\n", compressed)
    lines = [line.strip() for line in compressed.split("\n") if line.strip()]
    if len(lines) > 8:
        compressed = "\n".join(lines[:8])
    else:
        compressed = "\n".join(lines)

    if len(compressed) > max_len:
        compressed = compressed[: max_len - 3].rstrip() + "..."

    return compressed
