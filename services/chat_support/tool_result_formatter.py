from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Optional


class ToolResultFormatter:
    def __init__(
        self,
        *,
        get_active_character_profile: Callable[[], dict[str, Any]],
        looks_like_upstream_error_reply: Callable[[str], bool],
        strip_emo_tags: Callable[[str], str],
        strip_cmd: Callable[[str], str],
        strip_internal_tags: Callable[[str], str],
        clean_text_for_tts: Callable[[str], str],
        normalize_qq_reply_style: Callable[[str], str],
        wants_detailed_answer: Callable[[str], bool],
        extract_emo_tag: Callable[[str], tuple[str | None, str]],
        qq_remote_sources: set[str],
        logger: Any = None,
    ) -> None:
        self.get_active_character_profile = get_active_character_profile
        self.looks_like_upstream_error_reply = looks_like_upstream_error_reply
        self.strip_emo_tags = strip_emo_tags
        self.strip_cmd = strip_cmd
        self.strip_internal_tags = strip_internal_tags
        self.clean_text_for_tts = clean_text_for_tts
        self.normalize_qq_reply_style = normalize_qq_reply_style
        self.wants_detailed_answer = wants_detailed_answer
        self.extract_emo_tag = extract_emo_tag
        self.qq_remote_sources = set(qq_remote_sources or set())
        self.logger = logger

    def looks_like_hardware_status_query(self, text: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        subject_hints = (
            "硬件",
            "电脑",
            "系统",
            "机器",
            "主机",
            "cpu",
            "gpu",
            "显卡",
            "内存",
            "memory",
            "磁盘",
            "硬盘",
        )
        status_hints = (
            "状态",
            "情况",
            "占用",
            "使用率",
            "负载",
            "监控",
            "温度",
            "temperature",
            "看一下",
            "看看",
            "检查",
            "怎么样",
            "热不热",
            "高不高",
        )
        if not any(hint in raw for hint in subject_hints):
            return False
        return any(hint in raw for hint in status_hints)

    def hardware_monitor_action_from_query(self, text: str) -> str:
        raw = str(text or "").strip().lower()
        if any(hint in raw for hint in ("gpu", "显卡", "显存")):
            return "gpu"
        if any(hint in raw for hint in ("cpu", "处理器")):
            return "cpu"
        if any(hint in raw for hint in ("内存", "memory", "ram")):
            return "memory"
        if any(hint in raw for hint in ("磁盘", "硬盘", "disk", "空间")):
            return "disk"
        if any(hint in raw for hint in ("温度", "temperature", "热不热")):
            return "temperature"
        return "check"

    def is_market_price_query(self, text: str) -> bool:
        raw = str(text or "")
        lower = raw.lower()
        hints = [
            "金价",
            "银价",
            "油价",
            "汇率",
            "指数",
            "现价",
            "实时价",
            "实时价格",
            "价格",
            "行情",
            "price",
            "quote",
            "rate",
            "index",
            "gold",
            "usd",
            "cny",
            "rmb",
        ]
        return any(hint in lower or hint in raw for hint in hints)

    def has_explicit_market_numbers(self, text: str) -> bool:
        raw = str(text or "")
        patterns = [
            r"\d+(?:\.\d+)?\s*(?:美元/盎司|美元/克|元/克|元/盎司)",
            r"\d+(?:\.\d+)?\s*(?:USD|CNY|RMB)\b",
            r"\d+(?:\.\d+)?\s*(?:%|点)\b",
        ]
        return any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in patterns)

    def is_search_delegate(self, delegate_triggers: list[str], raw_text: str) -> bool:
        trigger_set = {
            str(item or "").strip().lower() for item in (delegate_triggers or [])
        }
        if trigger_set & {"search", "search_web"}:
            return True
        text = str(raw_text or "")
        return ("搜索结果" in text) or ("Exa@" in text) or ("DuckDuckGo" in text)

    @staticmethod
    def _parse_meta_line(text: str, marker: str) -> dict[str, str]:
        raw = str(text or "")
        pattern = rf"\[{re.escape(marker)}\]\s*([^\n]+)"
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if not match:
            return {}
        line = str(match.group(1) or "").strip()
        data: dict[str, str] = {}
        for part in line.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = str(key or "").strip().lower()
            value = str(value or "").strip()
            if key:
                data[key] = value
        return data

    def parse_search_meta(self, text: str) -> dict[str, str]:
        return self._parse_meta_line(text, "search_meta")

    def parse_web_meta(self, text: str) -> dict[str, str]:
        return self._parse_meta_line(text, "web_meta")

    def parse_moegirl_meta(self, text: str) -> dict[str, str]:
        return self._parse_meta_line(text, "moegirl_meta")

    def search_result_lacks_explicit_fact(self, text: str) -> bool:
        raw = str(text or "")
        meta = self.parse_search_meta(raw)
        if not raw:
            return True
        if "未在摘要中发现具体数值" in raw:
            return True
        if meta:
            if (
                str(meta.get("need_numeric") or "0") == "1"
                and str(meta.get("has_numbers") or "0") != "1"
            ):
                return True
            if str(meta.get("has_numbers") or "0") == "1":
                return False
            if (
                str(meta.get("has_links") or "0") == "1"
                or str(meta.get("has_published") or "0") == "1"
            ):
                return False
        if self.has_explicit_market_numbers(raw):
            return False
        explicit_markers = [
            "链接：",
            "关键数值：",
            "发布时间",
            "published",
            "source:",
        ]
        if any(marker.lower() in raw.lower() for marker in explicit_markers):
            return False
        return True

    def web_result_lacks_body(self, text: str) -> bool:
        meta = self.parse_web_meta(text)
        if not meta:
            return False
        return str(meta.get("has_body") or "0") != "1"

    def should_fallback_from_moegirl(self, results: list[str]) -> bool:
        if not results:
            return True
        combined = "\n".join(str(item) for item in results if str(item).strip())
        meta = self.parse_moegirl_meta(combined)
        status = str(meta.get("status") or "").strip().lower()
        if status == "not_found":
            return True
        if status == "ambiguous" and str(meta.get("has_page") or "0") != "1":
            return True
        return False

    async def polish_background_delegate_reply(
        self,
        *,
        user_text: str,
        delegate_triggers: list[str],
        delegate_results: list[str],
        delegate_clean: str,
    ) -> tuple[str, str]:
        raw_text = ""
        if delegate_results:
            raw_text = "\n".join(
                str(item) for item in delegate_results if str(item).strip()
            )
        elif delegate_clean:
            raw_text = str(delegate_clean).strip()
        if not raw_text:
            return "后台任务已处理完成。", "neutral"

        prompt = (
            "你现在是在任务完成后回到对话中汇报结果。"
            "保持当前角色的语气，但只做轻度人格化整理。"
            "要求：1) 只基于已给出的任务结果；"
            "2) 不扩展诊断，不脑补未明确提供的信息；"
            "3) 不展示工具调用过程；"
            "4) 最多3句话，尽量简短；"
            "5) 如果结果本身已经很清楚，就直接概述。"
        )
        if not self.wants_detailed_answer(user_text):
            prompt += (
                " 13) 默认像即时聊天，不要写成说明文或总结报告；"
                "14) 优先 1 到 2 句短句。"
            )
        is_market_query = self.is_market_price_query(user_text)
        is_search_delegate = self.is_search_delegate(delegate_triggers, raw_text)
        web_meta = self.parse_web_meta(raw_text)
        if is_market_query and not self.has_explicit_market_numbers(raw_text):
            return (
                "查到了相关新闻和摘要，但当前结果里没有可靠的现价数字，我不想乱报。",
                "neutral",
            )
        if is_market_query:
            prompt += (
                "6) 如果这是价格/行情/汇率/指数类请求，只有在任务结果里明确出现具体数值+单位时才可以引用；"
                "7) 如果任务结果里没有明确数值，明确说未拿到可靠现价，不要自行补任何数字。"
            )
        if is_search_delegate:
            prompt += (
                "8) 如果这是联网搜索结果，只能转述结果里已经明确出现的事实；"
                "9) 不要补充结果中未出现的人名、日期、价格、型号、结论；"
                "10) 若搜索结果只有摘要或标题，明确说是基于摘要的概述。"
            )
        if web_meta:
            prompt += (
                "11) 如果这是网页解析结果，优先依据网页标题和正文摘要来概述；"
                "12) 如果没有提取到可靠正文，就明确说明只拿到了标题或少量页面信息，不要把标题扩写成完整正文。"
            )
        trigger_text = (
            ", ".join(delegate_triggers[:4]) if delegate_triggers else "delegate"
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"原始请求：{user_text}\n"
                    f"任务类型：{trigger_text}\n"
                    f"任务结果：\n{raw_text}\n\n"
                    "请把它整理成一条自然、简短的回灌消息。"
                ),
            },
        ]
        try:
            from modules.llm import chat_with_ai

            reply = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="default",
                caller="chat_delegate_finalize",
            )
            emo, clean = self.extract_emo_tag(reply or "")
            polished = self.clean_text_for_tts(
                self.strip_internal_tags(
                    self.strip_cmd(self.strip_emo_tags(clean or reply or ""))
                )
            ).strip()
            if polished:
                if is_market_query and not self.has_explicit_market_numbers(raw_text):
                    polished = re.sub(
                        r"\d+(?:\.\d+)?\s*(?:美元/盎司|美元/克|元/克|元/盎司|USD|CNY|RMB|%|点)",
                        "",
                        polished,
                        flags=re.IGNORECASE,
                    ).strip(" ，,。；;:：")
                    if not polished or self.has_explicit_market_numbers(polished):
                        polished = "查到了相关新闻和摘要，但当前结果里没有可靠的现价数字，我不想乱报。"
                elif is_search_delegate and self.search_result_lacks_explicit_fact(
                    raw_text
                ):
                    polished = "我查到了相关搜索结果，不过当前拿到的主要是标题和摘要，所以我只能先做保守概述，不想把没写明的细节说死。"
                elif web_meta and self.web_result_lacks_body(raw_text):
                    polished = "我打开了这个链接，不过目前只稳定拿到了标题或少量页面信息，正文没有可靠提取出来，所以我先不把内容说得太满。"
                return polished, (emo or "neutral")
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"Background delegate polish failed: {exc}")
        if is_market_query and not self.has_explicit_market_numbers(raw_text):
            return (
                "查到了相关新闻和摘要，但当前结果里没有可靠的现价数字，我不想乱报。",
                "neutral",
            )
        if is_search_delegate and self.search_result_lacks_explicit_fact(raw_text):
            return (
                "我查到了相关搜索结果，不过当前拿到的主要是标题和摘要，所以我只能先做保守概述，不想把没写明的细节说死。",
                "neutral",
            )
        if web_meta and self.web_result_lacks_body(raw_text):
            return (
                "我打开了这个链接，不过目前只稳定拿到了标题或少量页面信息，正文没有可靠提取出来，所以我先不把内容说得太满。",
                "neutral",
            )
        return raw_text, "neutral"

    async def polish_hardware_status_reply(
        self, *, user_text: str, raw_status: str, ctx: Optional[dict[str, Any]] = None
    ) -> str:
        clean_status = str(raw_status or "").strip()
        if not clean_status:
            return "我没拿到硬件状态，监控插件这边没有返回内容。"
        if self.looks_like_upstream_error_reply(clean_status):
            return clean_status

        profile = self.get_active_character_profile()
        char_name = str(profile.get("name") or "当前角色").strip()
        source = str((ctx or {}).get("source") or "").strip().lower()
        length_rule = (
            "QQ里回复，控制在2到4句，别贴完整表格。"
            if source in self.qq_remote_sources
            else "控制在3到5句，保留关键数字，别贴完整表格。"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"你是角色「{char_name}」。现在只负责把系统硬件监控结果整理成自然回复。"
                    "必须只基于给出的监控结果，不新增诊断，不编造温度或占用。"
                    "优先说CPU、内存、磁盘、GPU里值得注意的项目；如果整体正常，就简短说正常。"
                    f"{length_rule} 不要输出Markdown标题，不要输出工具调用过程。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{str(user_text or '').strip()}\n\n"
                    f"监控原始结果：\n{clean_status[:4000]}\n\n"
                    "请整理成最终回复。"
                ),
            },
        ]
        try:
            from modules.llm import chat_with_ai

            reply = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="default",
                caller="hardware_status_polish",
            )
            polished = self.clean_text_for_tts(
                self.strip_internal_tags(
                    self.strip_cmd(self.strip_emo_tags(reply or ""))
                )
            ).strip()
            if polished and not self.looks_like_upstream_error_reply(polished):
                if source in self.qq_remote_sources:
                    polished = self.normalize_qq_reply_style(polished)
                return polished
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"Hardware status polish failed: {exc}")
        return clean_status
