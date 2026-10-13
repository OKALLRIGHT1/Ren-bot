"""Prompt snapshot observability utilities.

Provides request-scoped context, section splitting, usage extraction,
and atomic JSONL writing.  Records **only lengths, section names, and
metrics** — never raw prompt content.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# request_id
# ---------------------------------------------------------------------------

def make_request_id() -> str:
    """Generate a per-turn id with a pid prefix for cross-process correlation."""
    return f"p{os.getpid()}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# RequestContext — per-turn mutable state
# ---------------------------------------------------------------------------

@dataclass
class RequestContext:
    """Mutable per-request state carried through the chat pipeline.

    ``next_call_seq`` is safe under asyncio's single-thread model — no lock
    needed for cooperative increments inside the event loop.
    """

    request_id: str
    _llm_call_count: int = 0
    _usage_records: list = field(default_factory=list)

    def next_call_seq(self) -> int:
        """Increment and return the call sequence number for the next LLM call."""
        self._llm_call_count += 1
        return self._llm_call_count

    @property
    def llm_call_count(self) -> int:
        return self._llm_call_count

    def record_usage(self, usage: dict) -> None:
        """Record a single LLM call's token usage dict."""
        self._usage_records.append(usage)

    @property
    def total_usage(self) -> dict:
        """Aggregate token counts across all LLM calls in this turn."""
        if not self._usage_records:
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "available": False,
            }
        pt = sum(u.get("prompt_tokens") or 0 for u in self._usage_records)
        ct = sum(u.get("completion_tokens") or 0 for u in self._usage_records)
        return {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "available": True,
        }


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

KNOWN_SECTION_HEADERS: frozenset = frozenset({
    "【时间】", "【时间规则】", "【角色人格】", "【输出格式规范】",
    "【对话边界】", "【回复风格】", "【工具调用规则】", "【工具描述】",
    "【调用模式】", "【用户画像】", "【角色补充档案】", "【待办/承诺】",
    "【知识库】", "【近期事件】", "【跨通道近史】", "【活跃会话】",
    "【中期记忆】", "【长期记忆】", "【工具记录】",
    "【本轮】", "【本轮内心·勿照念】", "【说法参考·可忽略】",
    "【当前Live2D情绪状态】", "【内在状态】", "【自我认知提示】",
})

# Match 【...】 at chunk start; cap header interior at 80 chars to prevent
# pathological content from blowing up the regex.
_HEADER_RE = re.compile(r'^(【[^】]{1,80}】)')


def split_sections(system_text: str) -> Dict[str, Dict[str, int]]:
    """Split a system prompt into known sections by ``\\n\\n`` boundaries.

    Returns a dict mapping known section header → ``{char_count, line_count}``.
    Unrecognised chunks (no header, or header not in the whitelist) are merged
    into ``__unclassified__`` with only aggregate lengths — no raw text.
    """
    chunks = (system_text or "").split("\n\n")
    sections: Dict[str, Dict[str, int]] = {}
    unclassified_chars = 0
    unclassified_lines = 0

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _HEADER_RE.match(chunk)
        if m:
            header = m.group(1)
            if header in KNOWN_SECTION_HEADERS:
                sections[header] = {
                    "char_count": len(chunk),
                    "line_count": chunk.count("\n") + 1,
                }
                continue
        # No header, or header not whitelisted → unclassified aggregate only
        unclassified_chars += len(chunk)
        unclassified_lines += chunk.count("\n") + 1

    if unclassified_chars > 0:
        sections["__unclassified__"] = {
            "char_count": unclassified_chars,
            "line_count": unclassified_lines,
        }
    return sections


# ---------------------------------------------------------------------------
# Usage extraction — unified across transports
# ---------------------------------------------------------------------------

def extract_usage(
    response: Any,
    transport: str = "",
    raw_json: Optional[dict] = None,
) -> dict:
    """Extract token usage from OpenAI / Gemini / streaming responses.

    Returns ``{prompt_tokens, completion_tokens, total_tokens}`` where each
    value is ``None`` when the provider didn't include it.
    """
    try:
        if transport == "gemini_native" and raw_json:
            um = raw_json.get("usageMetadata", {})
            return {
                "prompt_tokens": um.get("promptTokenCount"),
                "completion_tokens": um.get("candidatesTokenCount"),
                "total_tokens": um.get("totalTokenCount"),
            }
        # OpenAI Chat Completion (sync or final streaming chunk)
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage:
            return {
                "prompt_tokens": (
                    getattr(usage, "prompt_tokens", None)
                    if not isinstance(usage, dict)
                    else usage.get("prompt_tokens")
                ),
                "completion_tokens": (
                    getattr(usage, "completion_tokens", None)
                    if not isinstance(usage, dict)
                    else usage.get("completion_tokens")
                ),
                "total_tokens": (
                    getattr(usage, "total_tokens", None)
                    if not isinstance(usage, dict)
                    else usage.get("total_tokens")
                ),
            }
    except Exception:
        pass
    return {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


# ---------------------------------------------------------------------------
# JSONL snapshot writer
# ---------------------------------------------------------------------------

_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_PATH: Optional[str] = None


def _get_snapshot_path() -> str:
    global _SNAPSHOT_PATH
    if _SNAPSHOT_PATH is None:
        from pathlib import Path
        p = Path("data") / "prompt_snapshots.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_PATH = str(p)
    return _SNAPSHOT_PATH


def reset_snapshot_path() -> None:
    """Reset the cached path (used by tests that monkeypatch cwd)."""
    global _SNAPSHOT_PATH
    _SNAPSHOT_PATH = None


def set_snapshot_path(path: str) -> None:
    """Override the snapshot file path (used by tests)."""
    global _SNAPSHOT_PATH
    _SNAPSHOT_PATH = path


def write_snapshot(snapshot: dict) -> None:
    """Append one JSONL line atomically.  Failures only log a warning.

    Uses a process-level ``threading.Lock`` and a single ``write`` call to
    guarantee no interleaved lines under concurrent access.
    """
    try:
        line = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        with _SNAPSHOT_LOCK:
            path = _get_snapshot_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        print(f"[PromptSnapshot] write failed (ignored): {e}")


def read_snapshots(path: str) -> List[dict]:
    """Read all snapshot records from a JSONL file (test utility)."""
    records: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except FileNotFoundError:
        pass
    return records
