import asyncio
import json
import os
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
from urllib import error, request

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

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
    "几",
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
        base_url = str(self._read_setting(settings, "base_url", "")).strip()
        remote_base_url = str(
            self._read_setting(settings, "remote_base_url", "")
        ).strip()
        local_base_url = str(self._read_setting(settings, "local_base_url", "")).strip()
        api_key = str(self._read_setting(settings, "api_key", "")).strip()
        if not api_key:
            api_key = str(os.getenv("EXA_API_KEY", "")).strip()

        candidates = []
        for item in [local_base_url, remote_base_url, base_url]:
            norm = str(item or "").strip()
            if norm and norm not in candidates:
                candidates.append(norm)
        if not candidates:
            return "⚠️ 当前没有配置任何 Exa 接口地址。"

        lines = []
        headers = self._build_headers(api_key)
        for current_base_url in candidates:
            url = self._build_url(current_base_url, "/search")
            try:
                payload = {"query": "hello", "numResults": 1}
                await self._post_json_async(url, payload, headers, 5)
                lines.append(f"✅ {current_base_url} 可用")
            except Exception as exc:
                lines.append(f"❌ {current_base_url} 不可用：{exc}")
        return "\n".join(lines)

    @handle_plugin_errors("联网搜索")
    async def run(self, args, ctx):
        if not bool((ctx or {}).get("delegate_mode", False)):
            return "search_web 现在仅允许通过副脑委托执行。"
        query = str(args or "").strip()
        if not query:
            logger.warning("搜索词为空")
            return "❌ 搜索词不能为空。"

        settings = getattr(self, "settings", {}) or {}
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
        api_key = str(self._read_setting(settings, "api_key", "")).strip()
        if not api_key:
            api_key = str(os.getenv("EXA_API_KEY", "")).strip()

        num_results = self._to_int(
            self._read_setting(settings, "num_results", 5), 5, 1, 10
        )
        use_answer = self._to_bool(self._read_setting(settings, "use_answer", True))
        use_contents = self._to_bool(
            self._read_setting(settings, "use_contents", False)
        )
        contents_max = self._to_int(
            self._read_setting(settings, "contents_max", 3), 3, 1, 10
        )
        fallback_ddg = self._to_bool(self._read_setting(settings, "fallback_ddg", True))
        timeout_sec = self._to_int(
            self._read_setting(settings, "request_timeout_sec", 12), 12, 3, 60
        )
        link_request = self._is_link_request(query, ctx)

        logger.info(f"正在搜索: {query}")

        candidate_base_urls = []
        if local_base_url and prefer_local_first:
            candidate_base_urls.append(local_base_url)
        if remote_base_url:
            candidate_base_urls.append(remote_base_url)
        if base_url:
            candidate_base_urls.append(base_url)
        if local_base_url and not prefer_local_first:
            candidate_base_urls.append(local_base_url)

        dedup = []
        seen = set()
        for item in candidate_base_urls:
            norm = str(item or "").strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            dedup.append(norm)
        candidate_base_urls = dedup

        if candidate_base_urls:
            try:
                last_exc = None
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
                                show_links=link_request,
                            )
                    except Exception as single_exc:
                        last_exc = single_exc
                        logger.warning(
                            f"Exa 接口失败 ({current_base_url}): {single_exc}"
                        )
                        continue
                if last_exc:
                    raise last_exc

                if fallback_ddg:
                    return await self._ddg_search(
                        query, num_results, show_links=link_request
                    )
                return f"未找到关于 '{query}' 的相关结果。"
            except Exception as exc:
                logger.warning(f"Exa 搜索失败: {exc}")
                if fallback_ddg:
                    return await self._ddg_search(
                        query, num_results, show_links=link_request
                    )
                return f"搜索失败: {exc}"

        return await self._ddg_search(query, num_results, show_links=link_request)

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

    def _build_headers(self, api_key: str) -> Dict[str, str]:
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
            raise RuntimeError(f"HTTP {exc.code}: {body[:200]}")
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

    async def _exa_search(
        self,
        base_url: str,
        api_key: str,
        query: str,
        num_results: int,
        timeout_sec: int,
    ) -> List[Dict[str, Any]]:
        url = self._build_url(base_url, "/search")
        payload = {"query": query, "numResults": num_results}
        headers = self._build_headers(api_key)
        data = await self._post_json_async(url, payload, headers, timeout_sec)
        return self._extract_results(data)

    async def _exa_answer(
        self, base_url: str, api_key: str, query: str, timeout_sec: int
    ) -> str:
        url = self._build_url(base_url, "/answer")
        payload = {"query": query}
        headers = self._build_headers(api_key)
        data = await self._post_json_async(url, payload, headers, timeout_sec)
        answer = self._extract_answer(data)
        if not answer:
            return ""
        sources = self._extract_sources(data)
        lines = ["【Exa Answer】", answer]
        if sources:
            lines.append("\n来源：")
            for idx, item in enumerate(sources[:5], 1):
                title = str(item.get("title") or item.get("name") or "无标题")
                url = str(item.get("url") or item.get("link") or item.get("id") or "")
                lines.append(f"[{idx}] {title} {url}".strip())
        return "\n".join(lines)

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

        url = self._build_url(base_url, "/contents")
        headers = self._build_headers(api_key)
        data = await self._post_json_async(url, payload, headers, timeout_sec)
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
                enriched = {**item, **index[key]}
                merged.append(enriched)
            else:
                merged.append(item)
        return merged

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
        unit_pattern = "|".join([re.escape(u) for u in NUMERIC_UNITS])
        if unit_pattern:
            pattern = re.compile(
                r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+|\d+)(?:\s*("
                + unit_pattern
                + r"))?",
                flags=re.IGNORECASE,
            )
        else:
            pattern = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+|\d+)")
        seen = []
        for m in pattern.finditer(cleaned):
            num = m.group(1)
            unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
            if not unit:
                if re.fullmatch(r"\d{4}", num):
                    yr = int(num)
                    if 1900 <= yr <= 2100:
                        continue
            val = f"{num}{unit}".strip()
            if not val or val in seen:
                continue
            seen.append(val)
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

        lines = []
        display_max = 3
        need_nums = self._needs_numeric(query)
        any_nums = False
        has_links = False
        has_published = False
        for i, res in enumerate(results[:display_max], 1):
            title = str(res.get("title") or res.get("name") or "无标题")
            snippet = self._compact_text(
                res.get("text")
                or res.get("snippet")
                or res.get("content")
                or res.get("summary")
                or "",
                limit=80,
            )
            url = str(res.get("url") or res.get("link") or res.get("id") or "")
            published = self._short_date(
                res.get("publishedDate") or res.get("published_date") or ""
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
        DDGS = None
        import_error = None
        try:
            from ddgs import DDGS as DDGS  # type: ignore
        except Exception as exc:
            import_error = exc
            try:
                from duckduckgo_search import DDGS as DDGS  # type: ignore
            except Exception as exc2:
                return f"⚠️ Exa 不可用，DDG 也无法加载：{exc2 or import_error}"

        def _search_sync() -> List[Dict[str, Any]]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, region="cn-zh", max_results=num_results))

        try:
            results = await asyncio.to_thread(_search_sync)
        except Exception as exc:
            return f"搜索时发生网络错误或接口限制: {exc}"

        if not results:
            return f"未找到关于 '{query}' 的相关结果。"
        return self._format_results(
            query, results, provider="DDG", show_links=show_links
        )
