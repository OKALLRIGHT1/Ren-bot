"""URL / local-path policy for chat media loading.

Identity trust (qq_owner) does NOT imply content trust:
remote and owner messages never load file://, bare local paths, or private IPs
via image metadata. Local desktop may load whitelisted paths only.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Set, Tuple
from urllib import parse


class MediaBlockReason(str, Enum):
    EMPTY = "empty"
    BLOCKED_SCHEME = "blocked_scheme"
    BLOCKED_FILE = "blocked_file"
    BLOCKED_PRIVATE_IP = "blocked_private_ip"
    BLOCKED_HOST = "blocked_host"
    TOO_LARGE = "too_large"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    PATH_ESCAPE = "path_escape"
    PATH_NOT_ALLOWED = "path_not_allowed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class MediaPolicy:
    allow_http: bool = True
    allow_file: bool = False
    allow_private_ip: bool = False
    max_bytes: int = 10 * 1024 * 1024
    max_redirects: int = 3
    timeout_sec: float = 12.0
    allowed_path_roots: Tuple[Path, ...] = ()


def policy_for_source(source: str = "remote", *, path_roots: Optional[Sequence[str]] = None) -> MediaPolicy:
    """source: remote | local | trusted_cache (alias of local with roots)."""
    kind = str(source or "remote").strip().lower()
    roots = tuple(Path(p).resolve() for p in (path_roots or []) if str(p).strip())
    if kind in {"local", "local_ui", "text_input", "voice", "gui", "trusted_cache"}:
        return MediaPolicy(
            allow_http=True,
            allow_file=True,
            allow_private_ip=False,
            allowed_path_roots=roots or _default_local_roots(),
        )
    # remote / qq / owner — content untrusted
    return MediaPolicy(
        allow_http=True,
        allow_file=False,
        allow_private_ip=False,
        allowed_path_roots=(),
    )


def _default_local_roots() -> Tuple[Path, ...]:
    cwd = Path.cwd().resolve()
    candidates = [
        cwd / "temp_audio",
        cwd / "audio_cache",
        cwd / "data" / "outbound" / "gateway_media",
        cwd / "data" / "qq_file_browser",
    ]
    return tuple(p for p in candidates if True)


def is_blocked_ip(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(ip_text).strip())
    except ValueError:
        return True
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # Cloud metadata common ranges already covered by is_link_local / private.
    return False


def resolve_host_ips(hostname: str) -> Set[str]:
    host = str(hostname or "").strip().strip("[]")
    if not host:
        return set()
    # Literal IP
    try:
        ipaddress.ip_address(host)
        return {host}
    except ValueError:
        pass
    ips: Set[str] = set()
    try:
        for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        ):
            if not sockaddr:
                continue
            ips.add(str(sockaddr[0]))
    except socket.gaierror:
        return set()
    return ips


def check_http_url(
    url: str, policy: MediaPolicy
) -> Tuple[bool, str, MediaBlockReason | str]:
    text = str(url or "").strip()
    if not text:
        return False, "", MediaBlockReason.EMPTY
    if not policy.allow_http:
        return False, "", MediaBlockReason.BLOCKED_SCHEME
    parsed = parse.urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False, "", MediaBlockReason.BLOCKED_SCHEME
    host = parsed.hostname or ""
    if not host:
        return False, "", MediaBlockReason.BLOCKED_HOST
    host_l = host.lower()
    if host_l in {"localhost", "metadata", "metadata.google.internal"}:
        if not policy.allow_private_ip:
            return False, "", MediaBlockReason.BLOCKED_PRIVATE_IP
    ips = resolve_host_ips(host)
    if not ips:
        return False, "", MediaBlockReason.BLOCKED_HOST
    if not policy.allow_private_ip:
        for ip in ips:
            if is_blocked_ip(ip):
                return False, "", MediaBlockReason.BLOCKED_PRIVATE_IP
    return True, text, ""


def check_local_path(
    path_value: str, policy: MediaPolicy
) -> Tuple[bool, str, MediaBlockReason | str]:
    if not policy.allow_file:
        return False, "", MediaBlockReason.BLOCKED_FILE
    raw = str(path_value or "").strip()
    if not raw:
        return False, "", MediaBlockReason.EMPTY
    try:
        path = Path(raw).expanduser().resolve()
    except Exception:
        return False, "", MediaBlockReason.PATH_ESCAPE
    if not path.exists() or not path.is_file():
        return False, "", MediaBlockReason.PATH_NOT_ALLOWED
    roots = policy.allowed_path_roots or _default_local_roots()
    if not roots:
        return False, "", MediaBlockReason.PATH_NOT_ALLOWED
    for root in roots:
        try:
            path.relative_to(root.resolve())
            return True, str(path), ""
        except ValueError:
            continue
    return False, "", MediaBlockReason.PATH_NOT_ALLOWED


def path_from_file_uri(uri: str) -> str:
    parsed = parse.urlparse(uri)
    path = parse.unquote(parsed.path or "")
    if path.startswith("/") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        # UNC or host-qualified file URI — reject via empty for remote policy
        return ""
    return path


def clamp_read_bytes(data: bytes, max_bytes: int) -> Tuple[bytes, bool]:
    if max_bytes <= 0:
        return data, False
    if len(data) > max_bytes:
        return data[:max_bytes], True
    return data, False
