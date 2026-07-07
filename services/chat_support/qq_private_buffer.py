from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


QQ_REMOTE_SOURCES = {"qq_gateway", "napcat_qq"}
DEFAULT_COMMAND_PREFIXES = ("/", "!", "！", "#")


@dataclass(slots=True)
class QqPrivateBufferItem:
    message_id: str
    text: str
    images: List[Dict[str, Any]] = field(default_factory=list)
    reply: Dict[str, Any] = field(default_factory=dict)
    components: List[Dict[str, Any]] = field(default_factory=list)
    forward_contexts: List[str] = field(default_factory=list)
    sender_name: str = ""
    created_at: float = 0.0


@dataclass(slots=True)
class QqPrivateBufferResult:
    text: str
    items: List[QqPrivateBufferItem] = field(default_factory=list)
    bypassed: bool = False


@dataclass
class _BufferState:
    seq: int = 0
    items: List[QqPrivateBufferItem] = field(default_factory=list)
    deadline: float = 0.0
    force_flush: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class QqPrivateMessageBuffer:
    def __init__(
        self,
        *,
        enabled: bool = True,
        debounce_sec: float = 3.2,
        short_debounce_sec: float = 2.2,
        max_typing_wait_sec: float = 12.0,
        max_items: int = 12,
        max_text_chars: int = 2400,
        command_prefixes: Optional[List[str]] = None,
        enable_reply_context: bool = True,
        now_fn=time.monotonic,
    ):
        self.enabled = bool(enabled)
        self.debounce_sec = max(0.1, float(debounce_sec))
        self.short_debounce_sec = max(0.1, float(short_debounce_sec))
        self.max_typing_wait_sec = max(0.5, float(max_typing_wait_sec))
        self.max_items = max(1, int(max_items))
        self.max_text_chars = max(100, int(max_text_chars))
        self.command_prefixes = tuple(command_prefixes or DEFAULT_COMMAND_PREFIXES)
        self.enable_reply_context = bool(enable_reply_context)
        self.now_fn = now_fn
        self._states: Dict[str, _BufferState] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _notify_state(state: _BufferState) -> None:
        state.changed.set()
        state.changed = asyncio.Event()

    def _session_id(self, ctx: Optional[Dict[str, Any]]) -> str:
        channel_meta = (ctx or {}).get("channel_meta") or {}
        return str(channel_meta.get("session_id") or "").strip()

    def _channel_meta(self, ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(ctx, dict):
            return {}
        meta = ctx.setdefault("channel_meta", {})
        if not isinstance(meta, dict):
            meta = {}
            ctx["channel_meta"] = meta
        return meta

    def _is_command(self, text: str) -> bool:
        clean = str(text or "").strip()
        return bool(clean) and clean.startswith(self.command_prefixes)

    def _is_bufferable(self, text: str, ctx: Optional[Dict[str, Any]]) -> tuple[bool, str]:
        if not self.enabled or not isinstance(ctx, dict):
            return False, ""
        source = str(ctx.get("source") or "").strip().lower()
        if source not in QQ_REMOTE_SOURCES:
            return False, ""
        if bool(ctx.get("codex_mode", False)):
            return False, ""
        meta = ctx.get("channel_meta") or {}
        if not isinstance(meta, dict):
            return False, ""
        session_id = str(meta.get("session_id") or "").strip()
        message_type = str(meta.get("message_type") or "private").strip().lower()
        if message_type != "private" or not session_id.startswith("private:"):
            return False, ""
        if bool(meta.get("has_file")):
            return False, ""
        clean = str(text or "").strip()
        images = meta.get("images") if isinstance(meta.get("images"), list) else []
        if not clean and not images:
            return False, ""
        if len(clean) > self.max_text_chars:
            return False, ""
        return True, session_id

    def _delay_for(self, item: QqPrivateBufferItem, pending_count: int) -> float:
        clean = item.text.strip()
        if pending_count >= 2:
            return self.short_debounce_sec
        if len(clean) <= 8:
            return self.debounce_sec
        if clean.endswith(("?", "？", "!", "！", "。", ".", "…", "~", "～")):
            return self.short_debounce_sec
        return self.debounce_sec

    def _make_item(self, text: str, ctx: Dict[str, Any]) -> QqPrivateBufferItem:
        meta = self._channel_meta(ctx)
        images = meta.get("images") if isinstance(meta.get("images"), list) else []
        components = meta.get("components") if isinstance(meta.get("components"), list) else []
        reply = meta.get("reply") if isinstance(meta.get("reply"), dict) else {}
        forward_contexts = (
            meta.get("forward_contexts")
            if isinstance(meta.get("forward_contexts"), list)
            else []
        )
        return QqPrivateBufferItem(
            message_id=str(meta.get("message_id") or "").strip(),
            text=str(text or "").strip(),
            images=[dict(item) for item in images if isinstance(item, dict)],
            reply=dict(reply or {}),
            components=[dict(item) for item in components if isinstance(item, dict)],
            forward_contexts=[
                str(item).strip() for item in forward_contexts if str(item).strip()
            ],
            sender_name=str(meta.get("sender_name") or meta.get("user_id") or "").strip(),
            created_at=self.now_fn(),
        )

    @staticmethod
    def _escape_attr(text: str) -> str:
        return (
            str(text or "")
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _format_reply_context(self, item: QqPrivateBufferItem) -> str:
        if not self.enable_reply_context or not item.reply:
            return ""
        quoted_text = str(
            item.reply.get("text")
            or item.reply.get("content")
            or item.reply.get("raw_message")
            or ""
        ).strip()
        if not quoted_text:
            return ""
        sender = str(
            item.reply.get("sender_name")
            or item.reply.get("nickname")
            or item.reply.get("user_id")
            or "unknown"
        ).strip()
        return (
            f'<quoted_message sender="{self._escape_attr(sender)}">'
            f"{quoted_text}</quoted_message>"
        )

    def _merged_text(self, items: List[QqPrivateBufferItem]) -> str:
        parts: List[str] = []
        seen_quotes = set()
        seen_forwards = set()
        for item in items:
            quote = self._format_reply_context(item)
            if quote and quote not in seen_quotes:
                seen_quotes.add(quote)
                parts.append(quote)
            for forward_context in item.forward_contexts:
                if forward_context and forward_context not in seen_forwards:
                    seen_forwards.add(forward_context)
                    parts.append(forward_context)
            if item.text.strip():
                parts.append(item.text.strip())
        return "\n".join(parts).strip()

    def _merge_images(self, items: List[QqPrivateBufferItem]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            for image in item.images:
                key = str(image.get("url") or image.get("file") or image.get("name") or image)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(image))
        return merged

    def _apply_context(self, ctx: Dict[str, Any], items: List[QqPrivateBufferItem]) -> None:
        meta = self._channel_meta(ctx)
        texts = [item.text.strip() for item in items if item.text.strip()]
        ctx["qq_buffered_messages"] = texts
        ctx["qq_buffered_count"] = len(items)
        meta["qq_buffered_message_ids"] = [item.message_id for item in items if item.message_id]
        images = self._merge_images(items)
        meta["images"] = images
        meta["has_image"] = bool(images)
        meta["image_count"] = len(images)
        if items:
            meta["message_id"] = items[-1].message_id or meta.get("message_id")
            meta["components"] = [part for item in items for part in item.components]

    async def wait(
        self, text: str, ctx: Optional[Dict[str, Any]]
    ) -> Optional[QqPrivateBufferResult]:
        if self._is_command(text):
            session_id = self._session_id(ctx)
            if session_id:
                await self.flush_session(session_id)
            return QqPrivateBufferResult(text=str(text or "").strip(), bypassed=True)

        ok, session_id = self._is_bufferable(text, ctx)
        if not ok or not isinstance(ctx, dict):
            return QqPrivateBufferResult(text=str(text or "").strip(), bypassed=True)

        item = self._make_item(text, ctx)
        async with self._lock:
            state = self._states.setdefault(session_id, _BufferState())
            state.items.append(item)
            state.items = state.items[-self.max_items :]
            state.seq += 1
            seq = state.seq
            state.deadline = self.now_fn() + self._delay_for(item, len(state.items))
            self._notify_state(state)

        return await self._wait_until_ready(session_id, state, seq, ctx)

    async def _wait_until_ready(
        self,
        session_id: str,
        state: _BufferState,
        seq: int,
        ctx: Dict[str, Any],
    ) -> Optional[QqPrivateBufferResult]:
        while True:
            async with self._lock:
                latest = self._states.get(session_id)
                if latest is not state:
                    return None
                if state.seq != seq and not state.force_flush:
                    return None
                if not state.items:
                    self._states.pop(session_id, None)
                    return QqPrivateBufferResult(text="")
                timeout = max(0.0, state.deadline - self.now_fn())
                event = state.changed
                should_flush = state.force_flush or timeout <= 0
                if should_flush:
                    items = list(state.items)
                    self._states.pop(session_id, None)
                    self._apply_context(ctx, items)
                    return QqPrivateBufferResult(text=self._merged_text(items), items=items)
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                continue

    async def flush_session(self, session_id: str) -> bool:
        async with self._lock:
            state = self._states.get(str(session_id or "").strip())
            if state is None:
                return False
            state.force_flush = True
            self._notify_state(state)
            return True

    async def handle_recall(self, session_id: str, message_id: str) -> int:
        msg_id = str(message_id or "").strip()
        if not msg_id:
            return 0
        async with self._lock:
            state = self._states.get(str(session_id or "").strip())
            if state is None:
                return 0
            before = len(state.items)
            state.items = [item for item in state.items if item.message_id != msg_id]
            removed = before - len(state.items)
            if removed:
                if not state.items:
                    state.force_flush = True
                self._notify_state(state)
            return removed

    async def handle_typing(self, session_id: str, *, is_typing: bool) -> bool:
        async with self._lock:
            state = self._states.get(str(session_id or "").strip())
            if state is None:
                return False
            now = self.now_fn()
            if is_typing:
                state.deadline = max(state.deadline, now + self.max_typing_wait_sec)
            else:
                state.deadline = min(state.deadline, now + self.short_debounce_sec)
            self._notify_state(state)
            return True
