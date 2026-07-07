from __future__ import annotations

import re
import time
from typing import Protocol

from .client import MoegirlApiError
from .models import MoegirlCandidate, MoegirlLookupResult


class _LookupClient(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[MoegirlCandidate]: ...
    async def opensearch(
        self, query: str, limit: int = 5
    ) -> list[MoegirlCandidate]: ...
    async def fetch_page_summary(self, title: str): ...


_LEADING_PATTERNS = (
    "请问",
    "请帮我查",
    "帮我查",
    "帮我看看",
    "给我介绍一下",
    "介绍一下",
    "介绍下",
    "我想知道",
    "想知道",
    "你知道",
    "关于",
)

_TRAILING_PATTERNS = (
    "是哪个作品的",
    "是哪部作品的",
    "是什么角色",
    "是什么梗",
    "到底是谁",
    "到底是什么",
    "是谁啊",
    "是什么啊",
    "是谁",
    "是什么",
    "是啥",
)


def normalize_lookup_query(query: str) -> str:
    normalized = query.strip().strip(" \t\r\n\"'“”‘’")
    normalized = re.sub(r"[？?！!。,.，、；;：:~～]+$", "", normalized)
    changed = True
    while changed and normalized:
        changed = False
        for prefix in _LEADING_PATTERNS:
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                normalized = normalized[len(prefix) :].strip()
                changed = True
        for suffix in _TRAILING_PATTERNS:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[: -len(suffix)].strip()
                changed = True
    normalized = normalized.strip(" \t\r\n\"'“”‘’")
    normalized = re.sub(r"[？?！!。,.，、；;：:~～]+$", "", normalized)
    return normalized or query.strip()


def _truncate_text(text: str, limit: int = 90) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


class MoegirlQueryService:
    def __init__(
        self,
        client: _LookupClient,
        prefer_exact_title: bool = True,
        cache_ttl_seconds: int = 0,
        enable_generator_search: bool = False,
    ):
        self.client = client
        self.prefer_exact_title = prefer_exact_title
        self.cache_ttl_seconds = cache_ttl_seconds
        self.enable_generator_search = enable_generator_search
        self._cache: dict[tuple[str, str, int], tuple[float, MoegirlLookupResult]] = {}

    def _read_cache(self, query: str, mode: str, max_candidates: int):
        if self.cache_ttl_seconds <= 0:
            return None
        key = (query, mode, max_candidates)
        cached = self._cache.get(key)
        if not cached:
            return None
        ts, result = cached
        if time.time() - ts > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return result

    def _write_cache(
        self, query: str, mode: str, max_candidates: int, result: MoegirlLookupResult
    ):
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[(query, mode, max_candidates)] = (time.time(), result)

    def _find_trusted_main_candidate(
        self, candidates: list[MoegirlCandidate], normalized_query: str
    ):
        for candidate in candidates:
            if candidate.title == normalized_query:
                return candidate
        if len(candidates) == 1:
            candidate = candidates[0]
            if candidate.title.startswith(normalized_query):
                return candidate
        return None

    async def _build_context_candidates(
        self,
        candidates: list[MoegirlCandidate],
        *,
        exclude_title: str | None = None,
        limit: int = 4,
    ) -> list[MoegirlCandidate]:
        contexts: list[MoegirlCandidate] = []
        for candidate in candidates:
            if exclude_title and candidate.title == exclude_title:
                continue
            description = _truncate_text(candidate.description)
            url = candidate.url
            if not description:
                try:
                    page = await self.client.fetch_page_summary(candidate.title)
                except (MoegirlApiError, TimeoutError):
                    page = None
                if page is not None:
                    description = _truncate_text(page.summary)
                    url = page.url or url
            contexts.append(
                MoegirlCandidate(
                    title=candidate.title, url=url, description=description
                )
            )
            if len(contexts) >= limit:
                break
        return contexts

    async def _lookup_candidates(
        self, query: str, max_candidates: int
    ) -> list[MoegirlCandidate]:
        if self.enable_generator_search:
            try:
                candidates = await self.client.search(query, limit=max_candidates)
            except (MoegirlApiError, TimeoutError):
                candidates = []
            if candidates:
                return candidates
        return await self.client.opensearch(query, limit=max_candidates)

    async def lookup(
        self, query: str, mode: str = "summary", max_candidates: int = 5
    ) -> MoegirlLookupResult:
        raw_query = query.strip()
        normalized_query = normalize_lookup_query(raw_query)
        cached = self._read_cache(raw_query, mode, max_candidates)
        if cached is not None:
            return cached

        candidates = await self._lookup_candidates(normalized_query, max_candidates)
        if not candidates:
            result = MoegirlLookupResult(
                status="not_found", message="未找到明显匹配词条"
            )
            self._write_cache(raw_query, mode, max_candidates, result)
            return result

        if mode == "candidates":
            contexts = await self._build_context_candidates(
                candidates, limit=max_candidates
            )
            result = MoegirlLookupResult(
                status="ambiguous", candidates=contexts, message="以下词条可能相关："
            )
            self._write_cache(raw_query, mode, max_candidates, result)
            return result

        trusted = (
            self._find_trusted_main_candidate(candidates, normalized_query)
            if self.prefer_exact_title
            else None
        )
        if trusted is not None:
            try:
                page = await self.client.fetch_page_summary(trusted.title)
            except (MoegirlApiError, TimeoutError):
                page = None
            if page is None:
                contexts = await self._build_context_candidates(
                    candidates, limit=max_candidates
                )
                result = MoegirlLookupResult(
                    status="ambiguous",
                    candidates=contexts,
                    message="未找到足够可信的主词条，以下词条可能相关：",
                )
                self._write_cache(raw_query, mode, max_candidates, result)
                return result
            related = await self._build_context_candidates(
                candidates,
                exclude_title=trusted.title,
                limit=max(0, min(max_candidates - 1, 4)),
            )
            result = MoegirlLookupResult(status="ok", page=page, candidates=related)
            self._write_cache(raw_query, mode, max_candidates, result)
            return result

        contexts = await self._build_context_candidates(
            candidates, limit=max_candidates
        )
        result = MoegirlLookupResult(
            status="ambiguous",
            candidates=contexts,
            message="未找到足够可信的主词条，以下词条可能相关：",
        )
        self._write_cache(raw_query, mode, max_candidates, result)
        return result
