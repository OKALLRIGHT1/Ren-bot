from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable


ALLOWED_MEDIA_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp3",
        "audio/ogg",
    }
)
DEFAULT_TTL_SECONDS = 120
DEFAULT_MAX_BYTES = 32 * 1024 * 1024


class MediaTicketError(ValueError):
    pass


@dataclass(frozen=True)
class MediaTicketEntry:
    path: Path
    media_type: str
    size: int
    expires_at: float
    used: bool = False


@dataclass(frozen=True)
class OpenedMedia:
    path: Path
    media_type: str
    size: int


class GuiMediaRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        allowed_types: Iterable[str] | None = None,
    ):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_bytes = max(1, int(max_bytes))
        self.allowed_types = frozenset(
            str(item).strip().lower()
            for item in (allowed_types or ALLOWED_MEDIA_TYPES)
            if str(item).strip()
        )
        self._entries: dict[str, MediaTicketEntry] = {}
        self._lock = Lock()

    def register(self, path: Path | str, *, media_type: str) -> str:
        media_type = str(media_type or "").strip().lower()
        if media_type not in self.allowed_types:
            raise MediaTicketError(f"不支持的媒体类型: {media_type}")
        candidate = Path(path).expanduser().resolve()
        if not candidate.exists() or not candidate.is_file():
            raise MediaTicketError("媒体路径无效")
        size = int(candidate.stat().st_size)
        if size <= 0:
            raise MediaTicketError("媒体文件为空")
        if size > self.max_bytes:
            raise MediaTicketError("媒体文件过大")
        ticket = secrets.token_urlsafe(32)
        entry = MediaTicketEntry(
            path=candidate,
            media_type=media_type,
            size=size,
            expires_at=time.time() + self.ttl_seconds,
            used=False,
        )
        with self._lock:
            self._purge_locked()
            self._entries[ticket] = entry
        return ticket

    def consume(self, ticket: str) -> OpenedMedia:
        key = str(ticket or "").strip()
        if not key:
            raise MediaTicketError("票据无效")
        with self._lock:
            self._purge_locked()
            entry = self._entries.get(key)
            if entry is None:
                raise MediaTicketError("票据不存在")
            if entry.used:
                raise MediaTicketError("票据已使用")
            if entry.expires_at <= time.time():
                self._entries.pop(key, None)
                raise MediaTicketError("票据过期")
            if not entry.path.exists() or not entry.path.is_file():
                self._entries.pop(key, None)
                raise MediaTicketError("媒体文件不可用")
            self._entries[key] = MediaTicketEntry(
                path=entry.path,
                media_type=entry.media_type,
                size=entry.size,
                expires_at=entry.expires_at,
                used=True,
            )
            return OpenedMedia(
                path=entry.path,
                media_type=entry.media_type,
                size=entry.size,
            )

    def purge(self) -> None:
        with self._lock:
            self._purge_locked()

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [
            ticket
            for ticket, entry in self._entries.items()
            if entry.expires_at <= now
        ]
        for ticket in expired:
            self._entries.pop(ticket, None)


def guess_audio_media_type(path: Path | str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in {".wav"}:
        return "audio/wav"
    if suffix in {".mp3"}:
        return "audio/mpeg"
    if suffix in {".ogg", ".oga"}:
        return "audio/ogg"
    return None
