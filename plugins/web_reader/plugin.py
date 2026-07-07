import ipaddress
import re
import socket
from html import unescape
from typing import Any, Dict, Optional
from urllib import error, request
from urllib.parse import urljoin, urlparse

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors
from services.capability_manager import ToolCapability, ToolCapabilityMatch

logger = get_logger()


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Plugin:
    type = "delegate"
    _MAX_RESPONSE_BYTES = 2 * 1024 * 1024
    _MAX_REDIRECTS = 4
    _BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}

    _NOISE_BLOCK_PATTERNS = [
        r"<header[\s\S]*?</header>",
        r"<footer[\s\S]*?</footer>",
        r"<nav[\s\S]*?</nav>",
        r"<aside[\s\S]*?</aside>",
        r"<form[\s\S]*?</form>",
        r"<button[\s\S]*?</button>",
        r"<svg[\s\S]*?</svg>",
    ]

    def get_capabilities(self):
        return [
            ToolCapability(
                id="web_reader.read_url",
                plugin="web_reader",
                trigger_mode="natural",
                match=self._match_read_url,
                description="Read and summarize a public webpage URL.",
                examples=["帮我解析链接 https://example.com/article"],
            )
        ]

    def _match_read_url(
        self, text: str, ctx: Dict[str, Any]
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        url = self._normalize_url(raw)
        if not url:
            return None
        lowered = raw.lower()
        intent_hints = (
            "解析链接",
            "读网页",
            "网页内容",
            "打开链接",
            "看看链接",
            "总结网页",
            "解析网页",
            "read this url",
            "open this link",
            "summarize this page",
        )
        if not any(hint in lowered for hint in intent_hints):
            return None
        return ToolCapabilityMatch(
            capability_id="web_reader.read_url",
            plugin="web_reader",
            score=0.9,
            args={"url": url},
            raw_text=raw,
            reason="web_reader_url_intent",
        )

    @handle_plugin_errors("网页解析")
    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        if not bool((ctx or {}).get("delegate_mode", False)):
            return "web_reader 现在仅允许通过副脑委托执行。"
        url = self._normalize_url(args)
        if not url:
            return "请提供要解析的网页链接。"

        settings = getattr(self, "settings", {}) or {}
        timeout_sec = self._to_int(settings.get("request_timeout_sec", 12), 12, 3, 60)
        max_chars = self._to_int(settings.get("max_chars", 2400), 2400, 400, 8000)

        logger.info(f"正在解析网页: {url}")
        html = await self._fetch_text(url, timeout_sec)
        title = self._extract_title(html)
        body = self._extract_body_text(html, max_chars)
        domain = urlparse(url).netloc or "unknown"
        has_body = bool(body)
        text_len = len(body)

        lines = [
            f"[web_meta] domain={domain}; has_title={1 if title else 0}; has_body={1 if has_body else 0}; text_length={text_len}",
            f"网页解析结果：{url}",
            f"标题：{title or '（未提取到标题）'}",
            f"域名：{domain}",
        ]
        if body:
            lines.append("正文摘要：")
            lines.append(body)
        else:
            lines.append("未提取到可靠正文，可能是动态网页、反爬或页面内容过少。")
        return "\n".join(lines)

    def _normalize_url(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        m = re.search(r"https?://[^\s]+", raw, flags=re.IGNORECASE)
        if m:
            raw = m.group(0)
        if raw.startswith("www."):
            raw = "https://" + raw
        if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
            return ""
        return raw.rstrip(".,;，。)）")

    def _validate_public_url(self, url: str) -> None:
        parsed = urlparse(str(url or ""))
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("只允许解析 http/https 网页")
        host = str(parsed.hostname or "").strip().lower()
        if not host:
            raise ValueError("网页链接缺少主机名")
        if host in self._BLOCKED_HOSTS or host.endswith(".local"):
            raise ValueError("不允许解析本机或局域网地址")

        if self._is_unsafe_ip(host):
            raise ValueError("不允许解析本机、内网或保留地址")

        try:
            infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"域名解析失败: {host}") from exc

        for info in infos:
            addr = str((info[4] or [""])[0])
            if self._is_unsafe_ip(addr):
                raise ValueError("不允许解析会指向本机、内网或保留地址的网页")

    def _is_unsafe_ip(self, value: str) -> bool:
        try:
            ip = ipaddress.ip_address(str(value or "").strip("[]"))
        except ValueError:
            return False
        return any(
            (
                ip.is_loopback,
                ip.is_private,
                ip.is_link_local,
                ip.is_multicast,
                ip.is_reserved,
                ip.is_unspecified,
            )
        )

    def _to_int(self, value: Any, default: int, min_val: int, max_val: int) -> int:
        if isinstance(value, dict):
            value = value.get("default", default)
        try:
            num = int(value)
        except Exception:
            num = default
        return max(min_val, min(max_val, num))

    async def _fetch_text(self, url: str, timeout_sec: int) -> str:
        return await __import__("asyncio").to_thread(
            self._fetch_text_sync, url, timeout_sec
        )

    def _fetch_text_sync(self, url: str, timeout_sec: int) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        current_url = url
        opener = request.build_opener(_NoRedirectHandler)
        for _ in range(self._MAX_REDIRECTS + 1):
            self._validate_public_url(current_url)
            req = request.Request(current_url, headers=headers, method="GET")
            try:
                with opener.open(req, timeout=float(timeout_sec)) as resp:
                    final_url = resp.geturl() or current_url
                    self._validate_public_url(final_url)
                    content_length = resp.headers.get("Content-Length")
                    if content_length:
                        try:
                            content_length_value = int(content_length)
                        except ValueError:
                            content_length_value = 0
                        if content_length_value > self._MAX_RESPONSE_BYTES:
                            raise ValueError("网页内容过大，已拒绝解析")
                    raw = resp.read(self._MAX_RESPONSE_BYTES + 1)
                    if len(raw) > self._MAX_RESPONSE_BYTES:
                        raise ValueError("网页内容过大，已拒绝解析")
                    charset = resp.headers.get_content_charset() or "utf-8"
                    break
            except error.HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        raise RuntimeError(f"HTTP {exc.code}: 缺少重定向地址")
                    current_url = urljoin(current_url, location)
                    continue
                body = (
                    exc.read().decode("utf-8", errors="replace")
                    if hasattr(exc, "read")
                    else ""
                )
                raise RuntimeError(f"HTTP {exc.code}: {body[:160]}")
            except ValueError:
                raise
            except Exception as exc:
                raise RuntimeError(str(exc))
        else:
            raise ValueError("网页重定向次数过多")

        try:
            return raw.decode(charset, errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")

    def _extract_title(self, html: str) -> str:
        m = re.search(
            r"<title[^>]*>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL
        )
        if not m:
            return ""
        return self._clean_text(m.group(1), 200)

    def _extract_body_text(self, html: str, max_chars: int) -> str:
        raw = str(html or "")
        raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<noscript[\s\S]*?</noscript>", " ", raw, flags=re.IGNORECASE)
        for pattern in self._NOISE_BLOCK_PATTERNS:
            raw = re.sub(pattern, " ", raw, flags=re.IGNORECASE)

        candidates = []
        for pattern in [
            r"<article[^>]*>([\s\S]*?)</article>",
            r"<main[^>]*>([\s\S]*?)</main>",
            r"<(?:div|section)[^>]*(?:content|article|post|entry|main)[^>]*>([\s\S]*?)</(?:div|section)>",
        ]:
            for m in re.finditer(pattern, raw, flags=re.IGNORECASE):
                candidates.append(m.group(1))

        best = ""
        best_len = 0
        if candidates:
            for item in candidates:
                cleaned = self._html_to_text(item)
                if len(cleaned) > best_len:
                    best = cleaned
                    best_len = len(cleaned)
        else:
            best = self._html_to_text(raw)

        best = self._trim_repeated_noise(best)
        return self._clean_text(best, max_chars)

    def _html_to_text(self, html: str) -> str:
        raw = str(html or "")
        raw = re.sub(
            r"<(?:br|p|div|section|article|li|h[1-6])\b[^>]*>",
            "\n",
            raw,
            flags=re.IGNORECASE,
        )
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = unescape(raw)
        raw = re.sub(r"[ \t\x0b\f\r]+", " ", raw)
        raw = re.sub(r"\n{2,}", "\n", raw)
        return raw.strip()

    def _trim_repeated_noise(self, text: str) -> str:
        lines = []
        seen = set()
        for line in re.split(r"\n+", str(text or "")):
            cleaned = re.sub(r"\s+", " ", line).strip()
            if len(cleaned) < 8:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            lines.append(cleaned)
        return "\n".join(lines)

    def _clean_text(self, text: str, limit: int) -> str:
        raw = unescape(str(text or ""))
        raw = re.sub(r"\s+", " ", raw).strip()
        if len(raw) > limit:
            raw = raw[: limit - 3].rstrip() + "..."
        return raw
