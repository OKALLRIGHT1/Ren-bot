import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib import error, request
from urllib.parse import urlparse

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

try:
    from modules.character_manager import character_manager
except Exception:
    character_manager = None

logger = get_logger()

QUESTION_HINTS = (
    "什么",
    "怎么",
    "如何",
    "为什么",
    "为何",
    "是不是",
    "能不能",
    "可以吗",
    "有没有",
    "谁",
    "哪",
    "多少",
    "几个",
    "解释",
    "原理",
    "区别",
    "比较",
)

TIME_SENSITIVE_HINTS = (
    "最新",
    "今天",
    "刚刚",
    "实时",
    "新闻",
    "行情",
    "价格",
    "股价",
    "汇率",
    "天气",
    "时间",
    "日期",
    "热搜",
    "热度",
    "趋势",
    "走势",
    "公告",
    "发生了什么",
    "有什么新",
)

NUMERIC_HINTS = (
    "价格",
    "行情",
    "汇率",
    "指数",
    "点位",
    "价位",
    "现价",
    "报价",
    "金价",
    "银价",
    "油价",
    "股价",
    "利率",
    "收益率",
    "现货",
    "期货",
    "多少",
    "多少钱",
    "几块",
    "几元",
    "几美元",
    "涨跌幅",
    "涨幅",
    "跌幅",
)

NUMERIC_UNITS = (
    "美元/盎司",
    "美元/克",
    "元/克",
    "元/盎司",
    "美元",
    "人民币",
    "CNY",
    "RMB",
    "USD",
    "HKD",
    "EUR",
    "JPY",
    "盎司",
    "克",
    "吨",
    "元",
    "点",
    "点位",
    "%",
    "bps",
)


class Plugin:
    type = "delegate"

    async def gui_check_endpoints(self) -> str:
        settings = getattr(self, "settings", {}) or {}
        provider = str(self._read_setting(settings, "provider", "grok")).strip().lower()
        timeout_sec = self._to_int(
            self._read_setting(settings, "request_timeout_sec", 12), 12, 3, 60
        )

        if provider == "grok":
            base_url = self._resolve_grok_base_url(settings)
            api_key = self._resolve_grok_api_key(settings)
            model = str(
                self._read_setting(settings, "grok_model", "grok-4.20-reasoning")
            ).strip()
            if not api_key:
                return (
                    "⚠️ 当前未配置 Grok/xAI API Key。"
                    "请填写插件设置中的 Grok API Key，或设置环境变量 XAI_API_KEY / GROK_API_KEY。"
                )

            payload = self._build_grok_chat_payload(
                query="请只回复“ok”，不要补充别的内容。",
                model=model,
                settings=settings,
                show_links=False,
            )
            try:
                await self._post_json_async(
                    self._build_url(base_url, "/chat/completions"),
                    payload,
                    self._build_grok_headers(api_key),
                    timeout_sec,
                )
                return f"✅ {base_url} 可用（Grok / {model}）"
            except Exception as exc:
                return f"❌ {base_url} 不可用（Grok / {model}）：{exc}"

        base_url = str(self._read_setting(settings, "base_url", "")).strip()
        remote_base_url = str(
            self._read_setting(settings, "remote_base_url", "")
        ).strip()
        local_base_url = str(self._read_setting(settings, "local_base_url", "")).strip()
        api_key = self._resolve_exa_api_key(settings)

        candidates = self._dedup_non_empty(
            [local_base_url, remote_base_url, base_url]
        )
        if not candidates:
            return "⚠️ 当前没有配置任何 Exa 接口地址。"

        lines: List[str] = []
        headers = self._build_exa_headers(api_key)
        for current_base_url in candidates:
            try:
                await self._post_json_async(
                    self._build_url(current_base_url, "/search"),
                    {"query": "hello", "numResults": 1},
                    headers,
                    min(timeout_sec, 5),
                )
                lines.append(f"✅ {current_base_url} 可用")
            except Exception as exc:
                lines.append(f"❌ {current_base_url} 不可用：{exc}")
        return "\n".join(lines)

    @handle_plugin_errors("联网搜索")
    async def run(self, args, ctx):
        if not bool((ctx or {}).get("delegate_mode", False)):
            return "search_web 现在仅允许通过副脑委托执行。"

        query = self._resolve_search_query(str(args or "").strip(), ctx)
        if not query:
            logger.warning("搜索词为空")
            return "❌ 搜索词不能为空。"

        settings = getattr(self, "settings", {}) or {}
        provider = str(self._read_setting(settings, "provider", "grok")).strip().lower()
        timeout_sec = self._to_int(
            self._read_setting(settings, "request_timeout_sec", 12), 12, 3, 60
        )
        num_results = self._to_int(
            self._read_setting(settings, "num_results", 5), 5, 1, 10
        )
        fallback_ddg = self._to_bool(self._read_setting(settings, "fallback_ddg", True))
        link_request = self._is_link_request(query, ctx)

        logger.info(f"正在搜索: {query}")

        if provider == "grok":
            try:
                return await self._grok_search(
                    query=query,
                    settings=settings,
                    timeout_sec=timeout_sec,
                    show_links=link_request,
                )
            except Exception as exc:
                logger.warning(f"Grok 搜索失败: {exc}")
                if fallback_ddg:
                    return await self._ddg_search(
                        query, num_results, show_links=link_request
                    )
                return f"Grok 搜索失败: {exc}"

        try:
            return await self._exa_search_flow(
                query=query,
                settings=settings,
                num_results=num_results,
                timeout_sec=timeout_sec,
                show_links=link_request,
                fallback_ddg=fallback_ddg,
            )
        except Exception as exc:
            logger.warning(f"Exa 搜索失败: {exc}")
            if fallback_ddg:
                return await self._ddg_search(query, num_results, show_links=link_request)
            return f"搜索失败: {exc}"

    def _resolve_grok_base_url(self, settings: Dict[str, Any]) -> str:
        return (
            str(self._read_setting(settings, "grok_base_url", "")).strip()
            or str(os.getenv("XAI_BASE_URL", "")).strip()
            or "https://api.x.ai/v1"
        )

    def _resolve_grok_api_key(self, settings: Dict[str, Any]) -> str:
        key = str(self._read_setting(settings, "grok_api_key", "")).strip()
        if key:
            return key
        key = str(os.getenv("XAI_API_KEY", "")).strip()
        if key:
            return key
        return str(os.getenv("GROK_API_KEY", "")).strip()

    def _resolve_exa_api_key(self, settings: Dict[str, Any]) -> str:
        key = str(self._read_setting(settings, "api_key", "")).strip()
        if key:
            return key
        return str(os.getenv("EXA_API_KEY", "")).strip()

    def _read_setting(self, settings: Dict[str, Any], key: str, default: Any) -> Any:
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return default if value is None else value

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(value)

    def _to_int(self, value: Any, default: int, min_val: int, max_val: int) -> int:
        try:
            num = int(value)
        except Exception:
            num = default
        return max(min_val, min(max_val, num))

    def _parse_csv_list(self, value: Any) -> List[str]:
        raw = str(value or "").strip()
        if not raw:
            return []
        items = re.split(r"[\s,，;；]+", raw)
        result: List[str] = []
        seen = set()
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _dedup_non_empty(self, values: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _should_use_answer(self, query: str) -> bool:
        text = str(query or "").strip()
        if not text:
            return False
        if self._is_time_sensitive(text):
            return False
        if "?" in text or "？" in text:
            return True
        if text.endswith(("吗", "么", "呢")):
            return True
        return any(k in text for k in QUESTION_HINTS)

    def _is_time_sensitive(self, query: str) -> bool:
        text = str(query or "")
        if any(k in text for k in TIME_SENSITIVE_HINTS):
            return True
        lower = text.lower()
        return bool(
            re.search(
                r"\b(latest|today|news|price|stock|weather|time|date|trend)\b", lower
            )
        )

    def _build_url(self, base_url: str, endpoint: str) -> str:
        base = str(base_url or "").rstrip("/")
        ep = str(endpoint or "").strip()
        if not ep.startswith("/"):
            ep = "/" + ep
        return base + ep

    def _build_exa_headers(self, api_key: str) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Origin": "https://cherry-ai.com",
            "Referer": "https://cherry-ai.com/",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _build_grok_headers(self, api_key: str) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout_sec: int,
    ) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=float(timeout_sec)) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            body = (
                exc.read().decode("utf-8", errors="replace")
                if hasattr(exc, "read")
                else ""
            )
            raise RuntimeError(f"HTTP {exc.code}: {body[:400]}")
        except Exception as exc:
            raise RuntimeError(str(exc))
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return {"raw": raw.decode("utf-8", errors="replace")}

    async def _post_json_async(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout_sec: int,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._post_json, url, payload, headers, timeout_sec
        )

    def _extract_results(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            if isinstance(payload.get("results"), list):
                return payload.get("results") or []
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("results"), list):
                return data.get("results") or []
            if isinstance(data, list):
                return data
        if isinstance(payload, list):
            return payload
        return []

    def _extract_answer(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        answer = payload.get("answer")
        if isinstance(answer, dict):
            for key in ("text", "answer", "content"):
                text = str(answer.get(key) or "").strip()
                if text:
                    return text
        for key in ("answer", "text", "result", "output", "message"):
            text = str(payload.get(key) or "").strip()
            if text:
                return text
        return ""

    def _extract_sources(self, payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        for key in ("sources", "citations", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data.get("results") or []
        return []

    async def _exa_search_flow(
        self,
        *,
        query: str,
        settings: Dict[str, Any],
        num_results: int,
        timeout_sec: int,
        show_links: bool = False,
        fallback_ddg: bool = True,
    ) -> str:
        base_url = str(self._read_setting(settings, "base_url", "")).strip()
        if not base_url:
            base_url = (
                str(os.getenv("EXA_BASE_URL", "")).strip() or "http://localhost:7860"
            )
        remote_base_url = str(
            self._read_setting(settings, "remote_base_url", "")
        ).strip()
        local_base_url = str(self._read_setting(settings, "local_base_url", "")).strip()
        prefer_local_first = self._to_bool(
            self._read_setting(settings, "prefer_local_first", True)
        )
        api_key = self._resolve_exa_api_key(settings)
        use_answer = self._to_bool(self._read_setting(settings, "use_answer", True))
        use_contents = self._to_bool(
            self._read_setting(settings, "use_contents", False)
        )
        contents_max = self._to_int(
            self._read_setting(settings, "contents_max", 3), 3, 1, 10
        )

        candidate_base_urls: List[str] = []
        if local_base_url and prefer_local_first:
            candidate_base_urls.append(local_base_url)
        if remote_base_url:
            candidate_base_urls.append(remote_base_url)
        if base_url:
            candidate_base_urls.append(base_url)
        if local_base_url and not prefer_local_first:
            candidate_base_urls.append(local_base_url)

        candidate_base_urls = self._dedup_non_empty(candidate_base_urls)

        if candidate_base_urls:
            last_exc: Optional[Exception] = None
            for current_base_url in candidate_base_urls:
                try:
                    logger.info(f"尝试 Exa 接口: {current_base_url}")
                    if use_answer and self._should_use_answer(query):
                        answer_text = await self._exa_answer(
                            current_base_url, api_key, query, timeout_sec
                        )
                        if answer_text:
                            return answer_text

                    results = await self._exa_search(
                        current_base_url, api_key, query, num_results, timeout_sec
                    )
                    if results:
                        if use_contents:
                            results = await self._exa_contents_merge(
                                current_base_url,
                                api_key,
                                results,
                                contents_max,
                                timeout_sec,
                            )
                        return self._format_results(
                            query,
                            results,
                            provider=f"Exa@{current_base_url}",
                            show_links=show_links,
                        )
                except Exception as exc:
                    last_exc = exc
                    logger.warning(f"Exa 接口失败 ({current_base_url}): {exc}")
                    continue

            if last_exc:
                raise last_exc

        if fallback_ddg:
            return await self._ddg_search(query, num_results, show_links=show_links)
        return f"未找到关于 '{query}' 的相关结果。"

    async def _exa_search(
        self,
        base_url: str,
        api_key: str,
        query: str,
        num_results: int,
        timeout_sec: int,
    ) -> List[Dict[str, Any]]:
        data = await self._post_json_async(
            self._build_url(base_url, "/search"),
            {"query": query, "numResults": num_results},
            self._build_exa_headers(api_key),
            timeout_sec,
        )
        return self._extract_results(data)

    async def _exa_answer(
        self, base_url: str, api_key: str, query: str, timeout_sec: int
    ) -> str:
        data = await self._post_json_async(
            self._build_url(base_url, "/answer"),
            {"query": query},
            self._build_exa_headers(api_key),
            timeout_sec,
        )
        answer = self._extract_answer(data)
        if not answer:
            return ""
        sources = self._extract_sources(data)
        lines = ["[Exa Answer]", answer]
        if sources:
            lines.append("")
            lines.append("来源：")
            for idx, item in enumerate(sources[:5], 1):
                title = str(item.get("title") or item.get("name") or "无标题").strip()
                url = str(item.get("url") or item.get("link") or item.get("id") or "").strip()
                lines.append(f"[{idx}] {title} {url}".strip())
        return "\n".join(lines).strip()

    async def _exa_contents_merge(
        self,
        base_url: str,
        api_key: str,
        results: List[Dict[str, Any]],
        max_items: int,
        timeout_sec: int,
    ) -> List[Dict[str, Any]]:
        ids = [str(item.get("id")) for item in results if item.get("id")]
        urls = [str(item.get("url")) for item in results if item.get("url")]
        if not ids and not urls:
            return results

        payload: Dict[str, Any] = {}
        if ids:
            payload["ids"] = ids[:max_items]
        else:
            payload["urls"] = urls[:max_items]

        data = await self._post_json_async(
            self._build_url(base_url, "/contents"),
            payload,
            self._build_exa_headers(api_key),
            timeout_sec,
        )
        contents = self._extract_results(data)
        if not contents:
            return results

        index: Dict[str, Dict[str, Any]] = {}
        for item in contents:
            key = str(item.get("id") or item.get("url") or "").strip()
            if key:
                index[key] = item

        merged: List[Dict[str, Any]] = []
        for item in results:
            key = str(item.get("id") or item.get("url") or "").strip()
            if key and key in index:
                merged.append({**item, **index[key]})
            else:
                merged.append(item)
        return merged

    def _get_active_character_name(self) -> str:
        if not character_manager:
            return ""
        try:
            char = character_manager.get_active_character() or {}
            return str(char.get("name") or "").strip()
        except Exception:
            return ""

    def _get_active_persona(self) -> str:
        if not character_manager:
            return ""
        try:
            char = character_manager.get_active_character() or {}
            return str(char.get("prompt") or "").strip()
        except Exception:
            return ""

    def _trim_persona(self, text: str, limit: int = 1200) -> str:
        cleaned = str(text or "").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit].rstrip() + "..."

    def _clean_answer_text(self, text: str) -> str:
        cleaned = self._normalize_markdown_links(str(text or ""))
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _normalize_markdown_links(self, text: str) -> str:
        raw = str(text or "")
        if not raw:
            return ""

        def _replace(match: re.Match[str]) -> str:
            label = str(match.group(1) or "").strip()
            url = str(match.group(2) or "").strip()
            if not url:
                return label
            if label:
                return f"{label} {url}"
            return url

        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _replace, raw)

    def _is_generic_link_only_request(self, text: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        cleaned = raw
        for token in (
            "给我提供链接",
            "给我链接",
            "提供链接",
            "来源链接",
            "发我链接",
            "链接发我",
            "给我网址",
            "提供网址",
            "链接",
            "网址",
            "来源",
            "source",
            "sources",
            "url",
            "link",
            "给我",
            "发我",
            "提供",
            "一下",
            "下",
            "看下",
            "看看",
            "请",
        ):
            cleaned = cleaned.replace(token, " ")
        cleaned = re.sub(r"[\s，。,！!？?：:;；、】【\"'“”‘’()（）\-]+", "", cleaned)
        return len(cleaned) < 2

    def _infer_recent_topic_from_ctx(self, ctx: Optional[dict], current_query: str) -> str:
        runtime = ctx or {}
        brain = runtime.get("brain")
        memory = getattr(brain, "short_term_memory", None)
        if not memory:
            return ""
        try:
            items = list(memory)[-12:]
        except Exception:
            return ""

        current_text = str(current_query or "").strip()
        for item in reversed(items):
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").strip().lower() != "user":
                continue
            content = str(item.get("content") or "").strip()
            if not content or content == current_text:
                continue
            if self._is_generic_link_only_request(content):
                continue
            return content
        return ""

    def _resolve_search_query(self, query: str, ctx: Optional[dict]) -> str:
        raw = str(query or "").strip()
        if not raw:
            return ""
        if not self._is_link_request(raw, ctx):
            return raw
        if not self._is_generic_link_only_request(raw):
            return raw
        inferred = self._infer_recent_topic_from_ctx(ctx, raw)
        if inferred:
            logger.info(f"链接追问自动继承上一主题: {inferred}")
            return inferred
        return raw

    def _build_grok_system_prompt(
        self, settings: Dict[str, Any], query: str, show_links: bool = False
    ) -> str:
        need_numeric = self._needs_numeric(query)
        time_sensitive = self._is_time_sensitive(query)
        force_web_hint = self._to_bool(
            self._read_setting(settings, "grok_force_web_hint", True)
        )
        use_active_character = self._to_bool(
            self._read_setting(settings, "grok_use_active_character", True)
        )

        instructions = [
            "你是联网搜索助手，首要目标是基于当前可访问的网络信息给出准确答案。",
            "如果你具备联网或搜索能力，必须先检索再回答，不能只凭记忆作答。",
            "不要编造来源、日期、数字、链接或未确认的事实。",
            "如果没有查到可靠信息，就明确说没有查到，不要硬编。",
            "先给结论，再给简要依据。",
            "输出保持紧凑，避免多余空行和重复表述。",
        ]
        if time_sensitive:
            instructions.append("这是时效性问题，请尽量给出具体日期；若能确认时间也请写出。")
        if need_numeric:
            instructions.append("这是数值类查询，若能确认请给出具体数值、单位和对应日期；不能确认就直说未查到可靠实时数值。")
        if show_links:
            instructions.append("用户希望看到链接，请在来源部分尽量给出可访问 URL。")
        else:
            instructions.append("来源部分至少给出来源名称；有可靠 URL 时可以一并附上。")

        if use_active_character:
            persona_text = self._trim_persona(self._get_active_persona())
            character_name = self._get_active_character_name()
            if persona_text:
                instructions.extend(
                    [
                        "在最终表述阶段，你可以轻微借用当前激活角色的语气，但绝不能为了人设牺牲事实准确性。",
                        "不要沉浸式角色扮演，不要添加与检索无关的设定发挥，不要故意卖萌或拖长回答。",
                        f"当前角色名：{character_name or '未命名角色'}",
                        f"当前角色设定：\n{persona_text}",
                    ]
                )
        return "\n".join(instructions).strip()

    def _build_grok_user_prompt(self, query: str, show_links: bool = False) -> str:
        lines = [
            f"请查询并回答这个问题：{query}",
            "请先检索，再根据检索结果作答。",
            "回答格式尽量使用下面结构：",
            "结论：用 1 到 2 句话先说清答案。",
            "依据：列出 2 到 4 条关键依据，优先写来源名、关键事实、日期或数值。",
            "来源：列出你实际参考的来源；如果有链接可一并给出。",
        ]
        if self._needs_numeric(query):
            lines.append("如果没有查到可靠的实时数值，请明确写“未查到可靠实时数值”。")
        if self._is_time_sensitive(query):
            lines.append("请避免使用“今天/最新”这类相对时间，尽量改成具体日期。")
        if show_links:
            lines.append("用户明确需要链接，请尽量提供来源 URL。")
        return "\n".join(lines).strip()

    def _build_grok_chat_payload(
        self,
        *,
        query: str,
        model: str,
        settings: Dict[str, Any],
        show_links: bool = False,
    ) -> Dict[str, Any]:
        return {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": self._build_grok_system_prompt(
                        settings, query, show_links=show_links
                    ),
                },
                {
                    "role": "user",
                    "content": self._build_grok_user_prompt(query, show_links=show_links),
                },
            ],
            "stream": False,
            "temperature": 0.2,
        }

    def _build_grok_responses_payload(
        self,
        *,
        query: str,
        model: str,
        settings: Dict[str, Any],
        show_links: bool = False,
    ) -> Dict[str, Any]:
        prompt = (
            self._build_grok_system_prompt(settings, query, show_links=show_links)
            + "\n\n"
            + self._build_grok_user_prompt(query, show_links=show_links)
        )
        return {
            "model": model,
            "input": prompt,
            "stream": False,
        }

    def _maybe_parse_sse_json(self, text: str) -> Optional[Dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        if not raw.startswith("data:"):
            return None
        last_obj: Optional[Dict[str, Any]] = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if isinstance(obj, dict):
                last_obj = obj
        return last_obj

    def _extract_chat_message_text(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return ""
        for item in choices:
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                chunks: List[str] = []
                for part in content:
                    if isinstance(part, dict):
                        text = str(part.get("text") or part.get("content") or "").strip()
                        if text:
                            chunks.append(text)
                if chunks:
                    return "\n".join(chunks).strip()
        return ""

    def _extract_grok_output_text(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        direct = str(payload.get("output_text") or "").strip()
        if direct:
            return direct

        chunks: List[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = str(part.get("type") or "").strip().lower()
                    if part_type in {"output_text", "text"}:
                        text = str(part.get("text") or part.get("content") or "").strip()
                        if text:
                            chunks.append(text)
        return "\n".join(chunks).strip()

    def _normalize_possible_stream_payload(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        raw = payload.get("raw")
        if not isinstance(raw, str):
            return payload
        parsed = self._maybe_parse_sse_json(raw)
        return parsed if parsed is not None else payload

    async def _grok_search(
        self,
        *,
        query: str,
        settings: Dict[str, Any],
        timeout_sec: int,
        show_links: bool = False,
    ) -> str:
        base_url = self._resolve_grok_base_url(settings)
        api_key = self._resolve_grok_api_key(settings)
        model = str(
            self._read_setting(settings, "grok_model", "grok-4.20-reasoning")
        ).strip()

        if not api_key:
            raise RuntimeError(
                "未配置 Grok/xAI API Key。请填写插件设置中的 Grok API Key，或设置环境变量 XAI_API_KEY / GROK_API_KEY。"
            )

        errors: List[str] = []

        try:
            payload = self._build_grok_chat_payload(
                query=query,
                model=model,
                settings=settings,
                show_links=show_links,
            )
            data = await self._post_json_async(
                self._build_url(base_url, "/chat/completions"),
                payload,
                self._build_grok_headers(api_key),
                timeout_sec,
            )
            data = self._normalize_possible_stream_payload(data)
            answer = self._clean_answer_text(self._extract_chat_message_text(data))
            if answer:
                return (
                    f"[search_meta] provider=GrokChat; model={model}; query={query[:80]}\n"
                    f"{answer}"
                ).strip()
            errors.append(f"chat/completions 返回中没有可提取的正文 keys={list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__}")
        except Exception as exc:
            errors.append(f"chat/completions: {exc}")

        try:
            payload = self._build_grok_responses_payload(
                query=query,
                model=model,
                settings=settings,
                show_links=show_links,
            )
            data = await self._post_json_async(
                self._build_url(base_url, "/responses"),
                payload,
                self._build_grok_headers(api_key),
                timeout_sec,
            )
            data = self._normalize_possible_stream_payload(data)
            answer = self._clean_answer_text(self._extract_grok_output_text(data))
            if answer:
                return (
                    f"[search_meta] provider=GrokResponses; model={model}; query={query[:80]}\n"
                    f"{answer}"
                ).strip()
            errors.append(f"responses 返回中没有可提取的正文 keys={list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__}")
        except Exception as exc:
            errors.append(f"responses: {exc}")

        raise RuntimeError("；".join(errors) or "Grok 搜索失败")

    def _compact_text(self, text: str, limit: int = 220) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if len(cleaned) > limit:
            return cleaned[:limit] + "..."
        return cleaned

    def _short_date(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "T" in text:
            return text.split("T", 1)[0]
        return text

    def _extract_domain(self, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""
        try:
            host = urlparse(text).netloc
        except Exception:
            host = ""
        if not host:
            host = re.sub(r"^https?://", "", text).split("/")[0]
        return host

    def _strip_dates(self, text: str) -> str:
        t = str(text or "")
        t = re.sub(r"\b20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?\b", " ", t)
        return t

    def _extract_numbers(self, text: str, limit: int = 3) -> List[str]:
        if not text:
            return []
        cleaned = self._strip_dates(text)
        unit_pattern = "|".join(re.escape(u) for u in NUMERIC_UNITS)
        if unit_pattern:
            pattern = re.compile(
                r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+|\d+)(?:\s*("
                + unit_pattern
                + r"))?",
                flags=re.IGNORECASE,
            )
        else:
            pattern = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+|\d+)")
        seen: List[str] = []
        for match in pattern.finditer(cleaned):
            num = match.group(1)
            unit = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
            if not unit and re.fullmatch(r"\d{4}", num):
                year = int(num)
                if 1900 <= year <= 2100:
                    continue
            value = f"{num}{unit}".strip()
            if not value or value in seen:
                continue
            seen.append(value)
            if len(seen) >= limit:
                break
        return seen

    def _needs_numeric(self, query: str) -> bool:
        text = str(query or "")
        if any(k in text for k in NUMERIC_HINTS):
            return True
        lower = text.lower()
        return bool(re.search(r"\b(price|quote|rate|index|usd|cny|rmb)\b", lower))

    def _is_link_request(self, query: str, ctx: Optional[dict] = None) -> bool:
        text = str(query or "")
        if isinstance(ctx, dict):
            extra = str(ctx.get("user_text") or "").strip()
            if extra:
                text = f"{text} {extra}".strip()
        lower = text.lower()
        if "链接" in text or "网址" in text:
            return True
        return ("link" in lower) or ("url" in lower)

    def _format_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
        provider: str,
        show_links: bool = False,
    ) -> str:
        if not results:
            return f"未找到关于 '{query}' 的相关结果。"

        lines: List[str] = []
        display_max = 3
        need_nums = self._needs_numeric(query)
        any_nums = False
        has_links = False
        has_published = False

        for i, res in enumerate(results[:display_max], 1):
            title = str(res.get("title") or res.get("name") or "无标题").strip()
            snippet = self._compact_text(
                res.get("text")
                or res.get("snippet")
                or res.get("content")
                or res.get("summary")
                or "",
                limit=80,
            )
            url = str(res.get("url") or res.get("link") or res.get("id") or "").strip()
            published = self._short_date(
                str(res.get("publishedDate") or res.get("published_date") or "")
            )
            domain = self._extract_domain(url)
            if url:
                has_links = True
            if published:
                has_published = True

            prefix = f"[{i}] {title}"
            meta = " | ".join([x for x in (published, domain) if x])
            if meta:
                prefix += f"（{meta}）"
            lines.append(prefix)
            if snippet:
                lines.append(f"    摘要：{snippet}")
            if need_nums:
                nums = self._extract_numbers(f"{title} {snippet}")
                if nums:
                    any_nums = True
                    lines.append(f"    关键数值：{', '.join(nums)}")
            if show_links and url:
                lines.append(f"    链接：{url}")

        if need_nums and not any_nums:
            lines.append("未在摘要中发现具体数值。")

        meta_line = (
            f"[search_meta] provider={provider}; query={query[:80]}; results={min(len(results), display_max)}; "
            f"need_numeric={1 if need_nums else 0}; has_numbers={1 if any_nums else 0}; "
            f"has_links={1 if has_links else 0}; has_published={1 if has_published else 0}"
        )
        return "\n".join(
            [meta_line, f"关于 '{query}' 的搜索结果（{provider}）：", *lines]
        )

    async def _ddg_search(
        self, query: str, num_results: int, show_links: bool = False
    ) -> str:
        ddgs_cls = None
        import_error = None
        try:
            from ddgs import DDGS as ddgs_cls  # type: ignore
        except Exception as exc:
            import_error = exc
            try:
                from duckduckgo_search import DDGS as ddgs_cls  # type: ignore
            except Exception as exc2:
                return f"⚠️ Grok/Exa 不可用，DDG 也无法加载：{exc2 or import_error}"

        def _search_sync() -> List[Dict[str, Any]]:
            with ddgs_cls() as ddgs:
                return list(ddgs.text(query, region="cn-zh", max_results=num_results))

        try:
            results = await asyncio.to_thread(_search_sync)
        except Exception as exc:
            return f"搜索时发生网络错误或接口限制: {exc}"

        if not results:
            return f"未找到关于 '{query}' 的相关结果。"
        return self._format_results(query, results, provider="DDG", show_links=show_links)
