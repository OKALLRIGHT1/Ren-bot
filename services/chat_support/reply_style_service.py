from __future__ import annotations

import random
import re
from typing import Any, Callable, Dict, Optional

from services.chat_support import text_utils


class ReplyStyleService:
    def __init__(
        self,
        *,
        emo_set: set[str],
        emo_tag_re: re.Pattern[str],
        cmd_re: re.Pattern[str],
    ) -> None:
        self.emo_set = emo_set
        self.emo_tag_re = emo_tag_re
        self.cmd_re = cmd_re

    def normalize_emo(self, value: Any) -> Optional[str]:
        if not value:
            return None
        text = str(value).strip().lower()
        text = text.strip("<>").strip()
        if text.startswith("emo="):
            text = text.split("=", 1)[1].strip()
        return text if text in self.emo_set else None

    def clean_text_for_tts(self, text: str) -> str:
        return text_utils.clean_text_for_tts(text)

    def strip_wrapping_quotes(self, text: str) -> str:
        return text_utils.strip_wrapping_quotes(text)

    def get_character_catchphrase_config(self) -> Dict[str, Any]:
        try:
            from modules.character_manager import character_manager

            cfg = character_manager.get_catchphrase_config()
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            return {"enabled": False, "text": "", "probability": 0}
        text = str(cfg.get("text", "") or "").strip()
        try:
            probability = int(cfg.get("probability", 0))
        except Exception:
            probability = 0
        probability = max(0, min(100, probability))
        return {
            "enabled": bool(cfg.get("enabled", False)) and bool(text) and probability > 0,
            "text": text,
            "probability": probability,
        }

    def catchphrase_variants(self, cfg: Optional[Dict[str, Any]] = None) -> list[str]:
        phrases = {"……はい。", "……はい"}
        if cfg is None:
            cfg = self.get_character_catchphrase_config()
        if isinstance(cfg, dict):
            text = str(cfg.get("text", "") or "").strip()
            if text:
                phrases.add(text)
                phrases.add(re.sub(r"[。.!！?？]+$", "", text).strip())
        return sorted((phrase for phrase in phrases if phrase), key=len, reverse=True)

    def strip_model_catchphrase(
        self, text: str, cfg: Optional[Dict[str, Any]] = None
    ) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""
        phrases = self.catchphrase_variants(cfg)
        if not phrases:
            return raw

        def same_phrase(value: str) -> bool:
            item = str(value or "").strip()
            item_soft = re.sub(r"[。.!！?？]+$", "", item).strip()
            return any(item == phrase or item_soft == phrase for phrase in phrases)

        lines = [line for line in raw.split("\n") if not same_phrase(line)]
        cleaned = "\n".join(lines).strip()
        if not cleaned:
            return ""

        for phrase in phrases:
            cleaned = re.sub(rf"[ \t]*{re.escape(phrase)}\s*$", "", cleaned).rstrip()
        return cleaned.strip()

    def apply_character_catchphrase(self, text: str) -> str:
        cfg = self.get_character_catchphrase_config()
        clean = self.strip_model_catchphrase(text, cfg)
        if not clean:
            return ""
        if not cfg.get("enabled"):
            return clean
        phrase = str(cfg.get("text") or "").strip()
        if not phrase:
            return clean
        try:
            probability = int(cfg.get("probability", 0))
        except Exception:
            probability = 0
        if probability <= 0 or random.random() * 100 >= probability:
            return clean
        if clean.rstrip().endswith(("?", "？")):
            return clean
        sep = " " if re.match(r"^[A-Za-z0-9]", phrase) else ""
        return clean.rstrip() + sep + phrase

    def strip_emo_tags_anywhere(self, text: str) -> str:
        return text_utils.strip_emo_tags_anywhere(text, self.emo_tag_re)

    def strip_cmd_anywhere(self, text: str) -> str:
        return text_utils.strip_cmd_anywhere(text, self.cmd_re)

    def strip_internal_tags(self, text: str) -> str:
        return text_utils.strip_internal_tags(text)

    def extract_emo_tag(self, text: str) -> tuple[Optional[str], str]:
        raw = text or ""
        match = self.emo_tag_re.search(raw)
        if match:
            emo = self.normalize_emo(match.group(1))
            clean = self.emo_tag_re.sub("", raw, count=1).strip()
            return emo, clean
        return None, raw

    def looks_structured_reply(self, text: str) -> bool:
        raw = self.clean_text_for_tts(str(text or ""))
        if not raw:
            return False
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        if len(lines) >= 3:
            return True
        if re.search(r"(^|\n)\s*(\d+\.\s|[-*]\s)", raw):
            return True
        if raw.count("：") + raw.count(":") >= 3:
            return True
        return False

    def needs_natural_polish(self, text: str, scene: str = "chat") -> bool:
        raw = self.clean_text_for_tts(str(text or ""))
        raw = self.strip_model_catchphrase(raw).strip()
        if not raw:
            return False
        formal_markers = (
            "用户",
            "当前",
            "根据",
            "首先",
            "其次",
            "另外",
            "总之",
            "可以考虑",
            "如果你需要",
            "建议你",
            "建议先",
        )
        if scene == "sensor":
            formal_markers = formal_markers + (
                "屏幕",
                "画面",
                "窗口",
                "正在",
                "我看到",
                "看起来",
            )
            if len(raw) > 18:
                return True
        else:
            if len(raw) > 36:
                return True
        if raw.count("，") >= 2:
            return True
        return any(marker in raw for marker in formal_markers)

    def detect_feedback(
        self,
        user_text: str,
        *,
        negative_keywords: tuple[str, ...],
        positive_keywords: tuple[str, ...],
    ) -> tuple[str, str]:
        text = (user_text or "").strip().lower()
        if not text:
            return "neutral", "neutral"
        if any(keyword in text for keyword in negative_keywords):
            return "explicit_negative", "negative"
        if any(keyword in text for keyword in positive_keywords):
            return "explicit", "positive"
        return "neutral", "neutral"

    def looks_like_plain_reaction_text(
        self,
        text: str,
        *,
        wants_detailed_answer: Callable[[str], bool],
    ) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if len(raw) > 48:
            return False
        lowered = raw.lower()
        slow_complaint = any(hint in raw for hint in ("慢", "卡", "等", "超时"))
        hard_task_hints = (
            "[cmd:",
            "http://",
            "https://",
            "```",
            "/",
            "\\",
            "查",
            "搜",
            "搜索",
            "链接",
            "生成",
            "画图",
            "截图",
            "打开",
            "运行",
            "报错",
            "失败",
            "接口",
            "配置",
            "插件",
            "文件",
            "提交",
            "上传",
            "github",
            "git ",
            "python",
            "rust",
            "cargo",
            "npm",
        )
        if any(hint in lowered for hint in hard_task_hints) and not slow_complaint:
            return False
        question_hints = (
            "?",
            "？",
            "为什么",
            "怎么",
            "如何",
            "多少",
            "哪里",
            "啥情况",
            "什么情况",
            "能不能",
            "可不可以",
            "怎么办",
        )
        if any(hint in raw for hint in question_hints):
            if not any(hint in raw for hint in ("在吗", "还在吗", "醒着吗")):
                return False
        if wants_detailed_answer(raw) and not slow_complaint:
            return False
        return True

    def build_short_reaction(
        self,
        user_text: str,
        *,
        wants_detailed_answer: Callable[[str], bool],
    ) -> tuple[str, str]:
        raw = self.strip_wrapping_quotes(user_text)
        if not self.looks_like_plain_reaction_text(
            raw, wants_detailed_answer=wants_detailed_answer
        ):
            return "", "neutral"
        compact = re.sub(r"\s+", "", raw.lower())
        if not compact:
            return "", "neutral"

        def pick(options: tuple[str, ...]) -> str:
            return random.choice(options)

        if any(keyword in compact for keyword in ("在吗", "还在吗", "醒着吗")):
            return pick(("我在。", "在。", "嗯，我在。")), "neutral"
        if compact in {"嗯", "恩", "哦", "噢", "好", "行", "ok", "okay", "收到"}:
            return pick(("嗯。", "好。", "我知道了。")), "neutral"
        if any(keyword in compact for keyword in ("谢谢", "谢啦", "感谢", "thx", "thanks")):
            return pick(("嗯。", "不用谢。", "没事。")), "happy"
        if any(keyword in compact for keyword in ("过了", "成功了", "跑通了", "好了", "搞定了", "可以了", "ok了")):
            return pick(("嗯，稳了。", "这样就好。", "先别再动它了。")), "happy"
        if any(keyword in compact for keyword in ("好慢", "跑得慢", "跑的慢", "太慢", "卡", "超时", "等好久", "跑不动")):
            return pick(("确实慢，先别急。", "像是卡在重活上了。", "先等它把这轮跑完。")), "think"
        if any(keyword in compact for keyword in ("累", "困", "撑不住", "不想动", "没精神")):
            return pick(("先缓一下。", "别硬撑。", "休息几分钟也行。")), "concern"
        if any(keyword in compact for keyword in ("烦", "崩溃", "麻了", "服了", "无语", "裂开", "难受")):
            return pick(("先别急。", "嗯，这个确实烦。", "先停一下也可以。")), "concern"
        if len(compact) <= 8 and any(keyword in compact for keyword in ("早", "晚安", "睡了", "拜")):
            return pick(("嗯。", "晚安。", "早点休息。")), "neutral"
        return "", "neutral"

    def extract_apply_confirmation(
        self,
        user_text: str,
        *,
        apply_cmd_re: re.Pattern[str],
        id_token_re: re.Pattern[str],
        apply_confirm_keywords: tuple[str, ...],
    ) -> tuple[bool, str, str]:
        text = (user_text or "").strip()
        if not text:
            return False, "", ""
        match = apply_cmd_re.search(text)
        if match:
            return True, match.group(1), match.group(2)
        lowered = text.lower()
        if not any(keyword in lowered for keyword in apply_confirm_keywords):
            return False, "", ""
        token_match = id_token_re.search(text)
        if token_match:
            return True, token_match.group(1), token_match.group(2)
        return False, "", ""
