import asyncio
import json
import mimetypes
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from config import DEFAULT_PERSONA
from core.logger import get_logger
from modules.llm import chat_with_ai
from plugins.plugin_utils import handle_plugin_errors

logger = get_logger()


class Plugin:
    type = "direct"
    aliases = ["点歌", "qq点歌", "QQ点歌", "来首歌", "放首歌", "播首歌", "听歌"]
    _PROVIDER_LABELS = {"qqmusic": "QQ音乐", "netease": "网易云", "kugou": "酷狗"}
    _QUERY_PREFIXES = (
        "点歌",
        "qq点歌",
        "QQ点歌",
        "来首歌",
        "来首",
        "放首歌",
        "放首",
        "播首歌",
        "播首",
        "听歌",
        "给我点歌",
        "给我来首歌",
        "给我来首",
        "给我放首歌",
        "给我播首歌",
        "帮我点歌",
        "帮我来首歌",
    )

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        lowered = raw.lower()
        key_lower = str(key or "").strip().lower()
        if key_lower and lowered.startswith(key_lower):
            return True
        if "点歌" in raw:
            return True
        return any(raw.startswith(prefix) for prefix in ("来首歌", "放首歌", "播首歌", "听歌"))

    @handle_plugin_errors("QQ点歌")
    async def run(self, args: str, ctx: Dict[str, Any]):
        settings = getattr(self, "settings", {}) or {}
        query = self._extract_query(args)
        if not query or query in {"help", "帮助", "/help"}:
            return self._help_text()

        base_url = self._setting_text(settings, "base_url", "https://api.example.com")
        api_key = self._resolve_api_key(settings)
        session_cookie = self._resolve_session_cookie(settings)
        if not api_key and not session_cookie:
            return "还没有配置 QQ 点歌接口的 API Key 或 Session Cookie。"

        quality = self._setting_text(settings, "default_quality", "MP3_320")
        page_size = self._setting_int(settings, "search_page_size", 8, 1, 20)
        providers = self._provider_order(settings)
        timeout_sec = self._request_timeout_sec(settings)

        self._cleanup_cache(settings)

        search_provider = ""
        search_api_root = ""
        candidates: List[Dict[str, Any]] = []
        last_search_error = ""
        html_only = True
        auth_required = False
        request_failed = False

        search_attempts = [
            (provider, api_root)
            for provider in providers
            for api_root in self._api_root_candidates(base_url)
        ]
        search_results = await asyncio.gather(
            *[
                self._search_song_payload(
                    api_root=api_root,
                    provider=provider,
                    query=query,
                    page_size=page_size,
                    api_key=api_key,
                    session_cookie=session_cookie,
                    timeout_sec=timeout_sec,
                )
                for provider, api_root in search_attempts
            ],
            return_exceptions=False,
        )
        for provider, api_root, search_payload, err_text in search_results:
            if err_text:
                request_failed = True
                html_only = False
                if "401" in err_text or "authentication required" in err_text.lower():
                    auth_required = True
                logger.info(
                    f"QQ点歌搜索请求异常 provider={provider} api_root={api_root} query={query} error={err_text}"
                )
                last_search_error = err_text
                continue

            if not self._is_html_payload(search_payload):
                html_only = False

            search_error = self._extract_api_error(search_payload)
            if search_error:
                logger.info(
                    f"QQ点歌搜索失败 provider={provider} api_root={api_root} query={query} error={search_error}"
                )
                last_search_error = search_error
                continue

            candidates = self._extract_song_candidates(search_payload)
            if candidates:
                search_provider = provider
                search_api_root = api_root
                break

            logger.info(
                "QQ点歌搜索返回空候选: "
                f"provider={provider} "
                f"api_root={api_root} "
                f"code={search_payload.get('code') if isinstance(search_payload, dict) else ''} "
                f"message={search_payload.get('message') if isinstance(search_payload, dict) else ''} "
                f"keys={list(search_payload.keys())[:8] if isinstance(search_payload, dict) else type(search_payload).__name__}"
            )
            if isinstance(search_payload, dict) and isinstance(search_payload.get("raw"), str):
                raw_preview = str(search_payload.get("raw") or "")[:400].replace("\r", " ").replace("\n", " ")
                logger.info(
                    f"QQ点歌原始响应预览 provider={provider} api_root={api_root} raw={raw_preview}"
                )

        if not candidates:
            if auth_required:
                return "音乐网关要求鉴权。请在 QQ点歌 插件里填写有效的 API Key，或改填网页登录态的 Session Cookie。"
            if request_failed and last_search_error:
                return f"搜歌失败：{self._friendly_error_text(last_search_error)}"
            if html_only:
                return "当前配置的音乐网关地址返回的是前端页面，不是 API 接口。请把 base_url 改成真实 API 地址，或让我继续帮你适配。"
            if last_search_error:
                return f"搜歌失败：{self._friendly_error_text(last_search_error)}"
            return f"没有找到和“{query}”接近的歌曲。"

        song = self._pick_best_song(query, candidates)
        if not song:
            return f"没有找到和“{query}”接近的歌曲。"

        song_id = self._song_id(song)
        if not song_id:
            return "找到了候选歌曲，但接口返回里没有可用的 song id。"

        try:
            direct_payload = await self._get_json_async(
                self._build_song_url_request(
                    search_api_root or self._api_root_candidates(base_url)[0],
                    search_provider or providers[0],
                    song_id,
                    quality,
                ),
                api_key,
                session_cookie,
                timeout_sec,
            )
        except Exception as exc:
            err_text = str(exc or "").strip()
            if "401" in err_text or "authentication required" in err_text.lower():
                return "音乐网关要求鉴权。请在 QQ点歌 插件里填写有效的 API Key，或改填网页登录态的 Session Cookie。"
            return f"歌曲已找到，但取直链请求失败：{self._friendly_error_text(err_text)}"

        direct_error = self._extract_api_error(direct_payload)
        if direct_error:
            return f"歌曲已找到，但取直链失败：{self._friendly_error_text(direct_error)}"

        media_url = self._extract_media_url(direct_payload)
        if not media_url:
            return "歌曲已找到，但接口没有返回可下载的音频直链。"

        title = self._song_title(song) or query
        artist = self._song_artist(song)
        voice_path = await self._download_audio_async(
            media_url,
            title=title,
            artist=artist,
            song_id=song_id,
            timeout_sec=timeout_sec,
            settings=settings,
        )
        if not voice_path:
            return "歌曲直链拿到了，但下载音频失败了。"

        info = f"{title}" + (f" - {artist}" if artist else "")
        provider_label = self._provider_label(search_provider or providers[0])
        source = str((ctx or {}).get("source") or "").strip().lower()
        post_send_text = ""
        should_comment = self._setting_bool(settings, "comment_after_send", True)
        defer_comment = self._setting_bool(settings, "comment_defer_until_voice", True)
        if should_comment and source in {"qq_gateway", "napcat_qq"} and defer_comment:
            self._schedule_deferred_song_comment(
                ctx=ctx,
                settings=settings,
                api_root=search_api_root or self._api_root_candidates(base_url)[0],
                provider=search_provider or providers[0],
                song=song,
                song_id=song_id,
                title=title,
                artist=artist,
                api_key=api_key,
                session_cookie=session_cookie,
                timeout_sec=timeout_sec,
            )
        elif should_comment:
            post_send_text = await self._build_song_comment_async(
                settings=settings,
                api_root=search_api_root or self._api_root_candidates(base_url)[0],
                provider=search_provider or providers[0],
                song=song,
                song_id=song_id,
                title=title,
                artist=artist,
                api_key=api_key,
                session_cookie=session_cookie,
                timeout_sec=timeout_sec,
            )

        if source in {"qq_gateway", "napcat_qq"}:
            return {
                "__type__": "gateway_voice",
                "voice_path": voice_path,
                "success_text": f"🎵 已经通过{provider_label}给你点了《{info}》。",
                "fallback_text": f"《{info}》已经从{provider_label}找到了，但回发 QQ 语音失败了。",
                "post_send_text": post_send_text,
            }
        return f"已通过{provider_label}找到《{info}》，音频缓存路径：{voice_path}"

    def _help_text(self) -> str:
        return (
            "QQ点歌\n"
            "用法示例：\n"
            "- 点歌 晴天\n"
            "- 给我点歌 周杰伦 晴天\n"
            "- 来首歌 打上花火"
        )

    def _extract_query(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        for prefix in self._QUERY_PREFIXES:
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        raw = re.sub(r"^[，,\-—\s]+", "", raw)
        raw = re.sub(r"^(给我|帮我|我想听|想听|来一首|来个)\s*", "", raw)
        return raw.strip(" ，。！？?,")

    def _resolve_api_key(self, settings: Dict[str, Any]) -> str:
        key = self._setting_text(settings, "api_key", "")
        if key:
            return key
        for env_name in ("QQMUSIC_API_KEY", "MUSIC_GATEWAY_API_KEY", "API_KEY"):
            value = str(os.getenv(env_name, "")).strip()
            if value:
                return value
        return ""

    def _resolve_session_cookie(self, settings: Dict[str, Any]) -> str:
        cookie = self._setting_text(settings, "session_cookie", "")
        if cookie:
            return cookie
        for env_name in ("QQMUSIC_SESSION_COOKIE", "MUSIC_GATEWAY_SESSION_COOKIE"):
            value = str(os.getenv(env_name, "")).strip()
            if value:
                return value
        return ""

    def _setting_text(self, settings: Dict[str, Any], key: str, default: str) -> str:
        value = settings.get(key, default)
        if isinstance(value, dict):
            value = value.get("value", value.get("default", default))
        text = str(value or "").strip()
        return text or default

    def _setting_int(self, settings: Dict[str, Any], key: str, default: int, min_val: int, max_val: int) -> int:
        value = settings.get(key, default)
        if isinstance(value, dict):
            value = value.get("value", value.get("default", default))
        try:
            num = int(value)
        except Exception:
            num = default
        return max(min_val, min(max_val, num))

    def _setting_bool(self, settings: Dict[str, Any], key: str, default: bool) -> bool:
        value = settings.get(key, default)
        if isinstance(value, dict):
            value = value.get("value", value.get("default", default))
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _request_timeout_sec(self, settings: Dict[str, Any]) -> int:
        timeout = self._setting_int(settings, "request_timeout_sec", 12, 5, 120)
        outer_timeout = getattr(self, "timeout_sec", 0) or 0
        try:
            outer = int(float(outer_timeout))
        except Exception:
            outer = 0
        if outer > 0:
            timeout = min(timeout, max(5, outer // 3))
        return timeout

    def _provider_order(self, settings: Dict[str, Any]) -> List[str]:
        raw = self._setting_text(settings, "provider_order", "qqmusic,netease")
        fallback_to_netease = self._setting_bool(settings, "fallback_to_netease", True)
        parts = re.split(r"[\s,，、]+", raw)
        out: List[str] = []
        seen = set()
        for item in parts:
            provider = str(item or "").strip().lower()
            if provider not in {"qqmusic", "netease", "kugou"} or provider in seen:
                continue
            seen.add(provider)
            out.append(provider)
        if not out:
            out = ["qqmusic"]
        if fallback_to_netease and "qqmusic" in out and "netease" not in out:
            out.append("netease")
        return out

    def _provider_label(self, provider: str) -> str:
        return self._PROVIDER_LABELS.get(str(provider or "").strip().lower(), str(provider or "音乐源"))

    def _build_search_url(self, api_root: str, provider: str, query: str, page_size: int) -> str:
        q = parse.quote(query)
        provider_part = parse.quote(str(provider or "qqmusic").strip())
        return f"{api_root}/{provider_part}/search/songs?q={q}&page=1&page_size={page_size}"

    def _build_song_url_request(self, api_root: str, provider: str, song_id: str, quality: str) -> str:
        q = parse.quote(str(quality or "MP3_320").strip())
        sid = parse.quote(str(song_id or "").strip())
        provider_part = parse.quote(str(provider or "qqmusic").strip())
        return f"{api_root}/{provider_part}/songs/{sid}/url?quality={q}"

    def _build_song_detail_url(self, api_root: str, provider: str, song_id: str) -> str:
        sid = parse.quote(str(song_id or "").strip())
        provider_part = parse.quote(str(provider or "qqmusic").strip())
        return f"{api_root}/{provider_part}/songs/{sid}"

    def _build_song_lyric_url(self, api_root: str, provider: str, song_id: str) -> str:
        sid = parse.quote(str(song_id or "").strip())
        provider_part = parse.quote(str(provider or "qqmusic").strip())
        return f"{api_root}/{provider_part}/songs/{sid}/lyric"

    async def _search_song_payload(
        self,
        *,
        api_root: str,
        provider: str,
        query: str,
        page_size: int,
        api_key: str,
        session_cookie: str,
        timeout_sec: int,
    ):
        try:
            payload = await self._get_json_async(
                self._build_search_url(api_root, provider, query, page_size),
                api_key,
                session_cookie,
                timeout_sec,
            )
            return provider, api_root, payload, ""
        except Exception as exc:
            return provider, api_root, None, str(exc or "").strip()

    def _api_root_candidates(self, base_url: str) -> List[str]:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            return []
        candidates: List[str] = []
        if base.endswith("/api/proxy"):
            candidates.append(base)
        elif base.endswith("/api/v1"):
            candidates.append(base)
        elif base.endswith("/v1"):
            candidates.append(base)
            root = base[: -len("/v1")].rstrip("/")
            if root and not root.endswith("/api"):
                candidates.append(f"{root}/api/proxy")
                candidates.append(f"{root}/api/v1")
        else:
            candidates.append(f"{base}/api/proxy")
            candidates.append(f"{base}/v1")
            if not base.endswith("/api"):
                candidates.append(f"{base}/api/v1")
        out: List[str] = []
        seen = set()
        for item in candidates:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _auth_headers(self, api_key: str, session_cookie: str = "") -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "live2d-llm/qq-music-plugin",
        }
        if api_key:
            headers["x-api-key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
        if session_cookie:
            headers["Cookie"] = session_cookie
        return headers

    def _get_json(self, url: str, api_key: str, session_cookie: str, timeout_sec: int) -> Any:
        req = request.Request(url, headers=self._auth_headers(api_key, session_cookie), method="GET")
        try:
            with request.urlopen(req, timeout=float(timeout_sec)) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}")
        except Exception as exc:
            raise RuntimeError(str(exc))
        try:
            return json.loads(body)
        except Exception:
            parsed = self._parse_json_like_text(body)
            if parsed is not None:
                return parsed
            return {"raw": body}

    def _parse_json_like_text(self, text: str) -> Optional[Any]:
        raw = str(text or "").strip().lstrip("\ufeff")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            pass
        match = re.search(r"(\{.*\}|\[.*\])", raw, flags=re.DOTALL)
        if not match:
            return None
        candidate = str(match.group(1) or "").strip()
        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except Exception:
            return None

    async def _get_json_async(self, url: str, api_key: str, session_cookie: str, timeout_sec: int) -> Any:
        import asyncio

        return await asyncio.to_thread(self._get_json, url, api_key, session_cookie, timeout_sec)

    async def _build_song_comment_async(
        self,
        *,
        settings: Dict[str, Any],
        api_root: str,
        provider: str,
        song: Dict[str, Any],
        song_id: str,
        title: str,
        artist: str,
        api_key: str,
        session_cookie: str,
        timeout_sec: int,
    ) -> str:
        detail_payload: Any = {}
        lyric_payload: Any = {}

        try:
            detail_payload = await self._get_json_async(
                self._build_song_detail_url(api_root, provider, song_id),
                api_key,
                session_cookie,
                timeout_sec,
            )
        except Exception as exc:
            logger.info(f"QQ点歌详情获取失败 song_id={song_id} error={exc}")

        try:
            lyric_payload = await self._get_json_async(
                self._build_song_lyric_url(api_root, provider, song_id),
                api_key,
                session_cookie,
                timeout_sec,
            )
        except Exception as exc:
            logger.info(f"QQ点歌歌词获取失败 song_id={song_id} error={exc}")

        detail = self._extract_song_detail(detail_payload, song)
        lyric_excerpt = self._extract_lyric_excerpt(
            lyric_payload,
            max_lines=self._setting_int(settings, "comment_lyric_lines", 2, 1, 4),
        )
        summary = self._build_song_summary(detail, title=title, artist=artist, provider=provider)
        fallback = self._build_song_comment_fallback(
            title=title,
            artist=artist,
            summary=summary,
            lyric_excerpt=lyric_excerpt,
        )
        if not fallback and not summary and not lyric_excerpt:
            return ""

        if not self._setting_bool(settings, "comment_use_llm", True):
            return fallback

        llm_timeout_sec = self._setting_int(settings, "comment_timeout_sec", 30, 1, 60)
        try:
            import asyncio

            reply = await asyncio.wait_for(
                self._generate_song_comment_with_llm(
                    title=title,
                    artist=artist,
                    provider=provider,
                    summary=summary,
                    lyric_excerpt=lyric_excerpt,
                    timeout_sec=llm_timeout_sec,
                ),
                timeout=float(llm_timeout_sec),
            )
            return reply or fallback
        except Exception as exc:
            logger.info(f"QQ点歌点评生成失败 song_id={song_id} error={exc}")
            return fallback

    def _schedule_deferred_song_comment(
        self,
        *,
        ctx: Dict[str, Any],
        settings: Dict[str, Any],
        api_root: str,
        provider: str,
        song: Dict[str, Any],
        song_id: str,
        title: str,
        artist: str,
        api_key: str,
        session_cookie: str,
        timeout_sec: int,
    ) -> None:
        import asyncio

        chat_service = (ctx or {}).get("chat_service")
        if chat_service is None or not hasattr(chat_service, "_send_gateway_reply"):
            return
        delay_sec = float(
            self._setting_int(settings, "comment_dispatch_delay_sec", 2, 0, 15)
        )
        task_ctx = dict(ctx or {})

        async def _runner() -> None:
            try:
                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)
                comment = await self._build_song_comment_async(
                    settings=settings,
                    api_root=api_root,
                    provider=provider,
                    song=song,
                    song_id=song_id,
                    title=title,
                    artist=artist,
                    api_key=api_key,
                    session_cookie=session_cookie,
                    timeout_sec=timeout_sec,
                )
                comment = self._clean_comment_text(comment)
                if not comment:
                    return
                await chat_service._send_gateway_reply(
                    comment, task_ctx, emotion="neutral"
                )
            except Exception as exc:
                logger.info(f"QQ点歌延迟点评发送失败 song_id={song_id} error={exc}")

        try:
            asyncio.create_task(_runner())
        except Exception as exc:
            logger.info(f"QQ点歌延迟点评任务创建失败 song_id={song_id} error={exc}")

    async def _generate_song_comment_with_llm(
        self,
        *,
        title: str,
        artist: str,
        provider: str,
        summary: str,
        lyric_excerpt: str,
        timeout_sec: int,
    ) -> str:
        import asyncio

        system_prompt = f"""{self._current_persona_prompt()}

你刚刚为用户点了一首歌。请根据提供的歌曲资料，用当前角色口吻说一句很短的听感点评。
要求：
1. 只输出最终回复，不要解释，不要分点，不要 Markdown。
2. 最多两句，尽量控制在 45 个字以内。
3. 评价必须基于给定资料，不能编造评论区内容、创作背景或用户喜好。
4. 不要重复“已点歌”“已发送语音”之类的话。
5. 不要输出空行。"""

        user_prompt = (
            f"歌曲：{title or '未知'}\n"
            f"歌手：{artist or '未知'}\n"
            f"来源：{self._provider_label(provider)}\n"
            f"歌曲资料：{summary or '无'}\n"
            f"歌词摘录：{lyric_excerpt or '无'}\n"
            "现在请给一句自然、克制、准确的评价。"
        )
        reply = await asyncio.to_thread(
            chat_with_ai,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "default",
            "qq_music_comment",
            "",
            float(timeout_sec),
        )
        return self._clean_comment_text(reply)

    def _current_persona_prompt(self) -> str:
        prompt = DEFAULT_PERSONA
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if isinstance(active_char, dict):
                prompt = active_char.get("prompt", DEFAULT_PERSONA)
        except Exception:
            pass
        return str(prompt or DEFAULT_PERSONA).strip()

    def _extract_song_candidates(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                items = data.get("items")
                if isinstance(items, list):
                    return [item for item in items if self._is_song_item(item)]
        if isinstance(payload, list):
            return [item for item in payload if self._is_song_item(item)]
        if not isinstance(payload, dict):
            return []

        direct_lists: List[List[Dict[str, Any]]] = []

        def _walk(node: Any) -> None:
            if isinstance(node, list):
                items = [item for item in node if self._is_song_item(item)]
                if items:
                    direct_lists.append(items)
                for item in node:
                    _walk(item)
                return
            if isinstance(node, dict):
                for value in node.values():
                    _walk(value)

        _walk(payload)
        if direct_lists:
            direct_lists.sort(key=len, reverse=True)
            return direct_lists[0]
        return []

    def _is_html_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        raw = payload.get("raw")
        if not isinstance(raw, str):
            return False
        text = raw.lstrip().lower()
        return text.startswith("<!doctype html") or text.startswith("<html")

    def _is_song_item(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        return bool(
            self._song_id(item)
            or self._song_title(item)
            or item.get("songmid")
            or item.get("songname")
        )

    def _song_id(self, item: Dict[str, Any]) -> str:
        for key in ("songmid", "mid", "id", "song_id", "musicId", "songId"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _song_title(self, item: Dict[str, Any]) -> str:
        for key in ("title", "name", "songname", "song_name"):
            value = item.get(key)
            if value:
                return str(value).strip()
        return ""

    def _song_artist(self, item: Dict[str, Any]) -> str:
        artist = item.get("artist") or item.get("singer") or item.get("artists")
        if isinstance(artist, str):
            return artist.strip()
        if isinstance(artist, dict):
            for key in ("name", "title"):
                value = artist.get(key)
                if value:
                    return str(value).strip()
        if isinstance(artist, list):
            names: List[str] = []
            for part in artist:
                if isinstance(part, str) and part.strip():
                    names.append(part.strip())
                elif isinstance(part, dict):
                    name = str(part.get("name") or part.get("title") or "").strip()
                    if name:
                        names.append(name)
            return " / ".join(names)
        return ""

    def _song_album(self, item: Dict[str, Any]) -> str:
        album = item.get("album") or item.get("albumName") or item.get("album_name")
        if isinstance(album, str):
            return album.strip()
        if isinstance(album, dict):
            for key in ("name", "title"):
                value = album.get(key)
                if value:
                    return str(value).strip()
        return ""

    def _song_duration_text(self, item: Dict[str, Any]) -> str:
        raw = item.get("duration") or item.get("duration_ms") or item.get("interval") or item.get("dt") or item.get("length")
        try:
            value = int(raw)
        except Exception:
            return ""
        if value <= 0:
            return ""
        if value > 10000:
            value = value // 1000
        minutes = value // 60
        seconds = value % 60
        if minutes <= 0 and seconds <= 0:
            return ""
        return f"{minutes}:{seconds:02d}"

    def _extract_api_error(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        code = payload.get("code")
        if code in (None, "", 0, "0", 200, "200"):
            return ""
        try:
            code_num = int(str(code).strip())
        except Exception:
            code_num = None
        if code_num is not None and 200 <= code_num < 300:
            return ""
        message = str(payload.get("message") or payload.get("msg") or "").strip()
        if payload.get("data") not in (None, "") and message.lower() in {"success", "ok"}:
            return ""
        return message or f"code={code}"

    def _pick_best_song(self, query: str, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        q = self._norm(query)
        if not q:
            return candidates[0] if candidates else None

        def _score(item: Dict[str, Any]) -> int:
            title = self._norm(self._song_title(item))
            artist = self._norm(self._song_artist(item))
            merged = f"{title} {artist}".strip()
            score = 0
            if q == title:
                score += 120
            if q in title:
                score += 80
            if q in merged:
                score += 50
            for token in [x for x in re.split(r"\s+", q) if x]:
                if token in title:
                    score += 20
                elif token in merged:
                    score += 8
            if title:
                score -= min(10, abs(len(title) - len(q)))
            return score

        ranked = sorted(candidates, key=_score, reverse=True)
        return ranked[0] if ranked else None

    def _norm(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip().lower())

    def _extract_media_url(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip() if payload.startswith("http") else ""
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                direct = data.get("url")
                if isinstance(direct, str) and direct.startswith(("http://", "https://")):
                    return direct.strip()

        found: List[str] = []

        def _walk(node: Any) -> None:
            if isinstance(node, str):
                if node.startswith(("http://", "https://")):
                    found.append(node.strip())
                return
            if isinstance(node, list):
                for item in node:
                    _walk(item)
                return
            if isinstance(node, dict):
                preferred = node.get("url") or node.get("play_url") or node.get("playUrl") or node.get("src") or node.get("link") or node.get("purl")
                if isinstance(preferred, str) and preferred.startswith(("http://", "https://")):
                    found.append(preferred.strip())
                    return
                for value in node.values():
                    _walk(value)

        _walk(payload)
        return found[0] if found else ""

    async def _download_audio_async(
        self,
        media_url: str,
        *,
        title: str,
        artist: str,
        song_id: str,
        timeout_sec: int,
        settings: Dict[str, Any],
    ) -> str:
        import asyncio

        return await asyncio.to_thread(
            self._download_audio,
            media_url,
            title,
            artist,
            song_id,
            timeout_sec,
            settings,
        )

    def _download_audio(
        self,
        media_url: str,
        title: str,
        artist: str,
        song_id: str,
        timeout_sec: int,
        settings: Dict[str, Any],
    ) -> str:
        cache_dir = self._cache_dir(settings)
        os.makedirs(cache_dir, exist_ok=True)
        ext = self._guess_ext(media_url, "")
        filename = self._safe_filename(f"{title}-{artist}-{song_id}") + ext
        dest = os.path.join(cache_dir, filename)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return dest

        req = request.Request(
            media_url,
            headers={"User-Agent": "live2d-llm/qq-music-plugin", "Accept": "*/*"},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=float(timeout_sec)) as resp:
                content_type = str(getattr(resp, "headers", {}).get("Content-Type") or "")
                final_ext = self._guess_ext(media_url, content_type)
                if final_ext != ext:
                    filename = self._safe_filename(f"{title}-{artist}-{song_id}") + final_ext
                    dest = os.path.join(cache_dir, filename)
                raw = resp.read()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"音频下载失败 HTTP {exc.code}: {body[:200]}")
        except Exception as exc:
            raise RuntimeError(f"音频下载失败: {exc}")

        if not raw:
            raise RuntimeError("音频下载失败：返回为空")
        with open(dest, "wb") as f:
            f.write(raw)
        return dest

    def _cache_dir(self, settings: Dict[str, Any]) -> str:
        subdir = self._setting_text(settings, "cache_subdir", "audio_cache/qq_music")
        normalized = subdir.replace("/", os.sep).replace("\\", os.sep).strip()
        if normalized in {"data/cache", f"data{os.sep}cache"}:
            normalized = os.path.join("audio_cache", "qq_music")
        if os.path.isabs(normalized):
            return os.path.abspath(normalized)
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
        )
        return os.path.abspath(os.path.join(project_root, normalized))

    def _cleanup_cache(self, settings: Dict[str, Any]) -> None:
        cache_dir = self._cache_dir(settings)
        if not os.path.isdir(cache_dir):
            return
        ttl_hours = self._setting_int(settings, "cache_ttl_hours", 24, 1, 168)
        expire_before = time.time() - ttl_hours * 3600
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                if os.path.getmtime(path) < expire_before:
                    os.remove(path)
            except Exception:
                continue

    def _guess_ext(self, media_url: str, content_type: str) -> str:
        path = parse.urlparse(str(media_url or "")).path
        ext = os.path.splitext(path)[1].lower()
        if ext in {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac", ".amr", ".silk"}:
            return ext
        mime = str(content_type or "").split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(mime) or ""
        return guessed or ".mp3"

    def _safe_filename(self, text: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]+', "_", str(text or "").strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned[:80] or "qq_music"

    def _friendly_error_text(self, text: str) -> str:
        raw = str(text or "").strip()
        lowered = raw.lower()
        if "10061" in lowered or "actively refused" in lowered or "积极拒绝" in raw:
            return "连接被拒绝。请检查当前网络、代理设置，或确认音乐网关是否可访问。"
        if "timed out" in lowered or "timeout" in lowered or "超时" in raw:
            return "请求超时。请稍后重试。"
        if "name or service not known" in lowered or "nodename nor servname provided" in lowered:
            return "域名解析失败。请检查网关地址是否正确。"
        return raw[:220]

    def _extract_song_detail(self, payload: Any, fallback_song: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
        return fallback_song if isinstance(fallback_song, dict) else {}

    def _build_song_summary(self, item: Dict[str, Any], *, title: str, artist: str, provider: str) -> str:
        if not isinstance(item, dict):
            item = {}
        parts: List[str] = []
        final_title = self._song_title(item) or title
        final_artist = self._song_artist(item) or artist
        album = self._song_album(item)
        duration = self._song_duration_text(item)
        if final_title:
            parts.append(f"标题《{final_title}》")
        if final_artist:
            parts.append(f"歌手 {final_artist}")
        if album:
            parts.append(f"专辑《{album}》")
        if duration:
            parts.append(f"时长 {duration}")
        parts.append(f"来源 {self._provider_label(provider)}")
        return "，".join(parts)

    def _extract_lyric_excerpt(self, payload: Any, *, max_lines: int = 2) -> str:
        raw = ""
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("lyric", "lrc", "text", "lyrics", "content"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        raw = value
                        break
            if not raw:
                for key in ("lyric", "lrc", "text", "lyrics", "content"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        raw = value
                        break
        elif isinstance(payload, str):
            raw = payload

        lines: List[str] = []
        for line in str(raw or "").splitlines():
            clean = re.sub(r"\[[^\]]+\]", "", line).strip()
            clean = re.sub(r"<[^>]+>", "", clean).strip()
            if not clean or len(clean) < 2:
                continue
            if clean.startswith(("作词", "作曲", "编曲", "词:", "曲:")):
                continue
            lines.append(clean)
            if len(lines) >= max_lines:
                break
        return " / ".join(lines)

    def _clean_comment_text(self, text: Any) -> str:
        clean = str(text or "").replace("\r", "\n")
        lines = [line.strip() for line in clean.split("\n") if line.strip()]
        clean = " ".join(lines)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:120]

    def _build_song_comment_fallback(self, *, title: str, artist: str, summary: str, lyric_excerpt: str) -> str:
        if lyric_excerpt:
            return self._clean_comment_text(f"《{title}》里“{lyric_excerpt}”这一段很抓人，气质立得住。")
        if summary:
            artist_part = f"{artist}的" if artist else ""
            return self._clean_comment_text(f"《{title}》整体信息很完整，{artist_part}这首歌的氛围感不错。")
        return ""
