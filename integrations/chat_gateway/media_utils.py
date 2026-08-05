from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional
from urllib import error, request

from integrations.chat_gateway.media_policy import (
    MediaPolicy,
    check_http_url,
    check_local_path,
    clamp_read_bytes,
    path_from_file_uri,
    policy_for_source,
)

_logger = logging.getLogger(__name__)


def _coerce_base64(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("data:image/") and "," in text:
        return text.split(",", 1)[1].strip()
    if text.startswith("base64://"):
        return text.split("://", 1)[1].strip()
    return text


def _read_bytes(path_value: str, max_bytes: int) -> bytes:
    from pathlib import Path

    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return b""
    data = path.read_bytes()
    clipped, too_large = clamp_read_bytes(data, max_bytes)
    if too_large:
        _logger.warning("media path too large: reason=too_large path=%s", path_value)
        return b""
    return clipped


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _fetch_http(url: str, policy: MediaPolicy) -> bytes:
    """Fetch with limited redirects; re-check each hop against policy."""
    opener = request.build_opener(_NoRedirect)
    current = url
    for _ in range(max(0, int(policy.max_redirects)) + 1):
        ok, _, reason = check_http_url(current, policy)
        if not ok:
            _logger.warning(
                "media http blocked: reason=%s url=%s", reason, current[:200]
            )
            return b""
        req = request.Request(
            current,
            headers={"User-Agent": "Live2D-Suzu-Media/1.0"},
            method="GET",
        )
        try:
            with opener.open(req, timeout=policy.timeout_sec) as resp:
                chunks: list[bytes] = []
                total = 0
                while True:
                    piece = resp.read(64 * 1024)
                    if not piece:
                        break
                    total += len(piece)
                    if total > policy.max_bytes:
                        _logger.warning(
                            "media http too large: reason=too_large url=%s",
                            current[:200],
                        )
                        return b""
                    chunks.append(piece)
                return b"".join(chunks)
        except error.HTTPError as exc:
            if 300 <= int(getattr(exc, "code", 0) or 0) < 400:
                location = ""
                try:
                    location = str(exc.headers.get("Location") or "")
                except Exception:
                    location = ""
                if not location:
                    return b""
                current = request.urljoin(current, location)
                continue
            _logger.warning("media http error: code=%s url=%s", exc.code, current[:200])
            return b""
        except Exception as exc:
            _logger.warning("media http fetch failed: %s url=%s", exc, current[:200])
            return b""
    _logger.warning("media too many redirects: reason=too_many_redirects url=%s", url[:200])
    return b""


def load_image_base64(
    image_meta: Dict[str, Any],
    timeout: float = 12.0,
    *,
    source: str = "remote",
    policy: Optional[MediaPolicy] = None,
) -> str:
    """Load image bytes as base64.

    source defaults to ``remote`` (strict). Pass ``local`` only for desktop-originated
    loads that may use whitelisted filesystem paths.
    """
    meta = image_meta if isinstance(image_meta, dict) else {}
    active = policy or policy_for_source(source)
    if timeout and timeout > 0:
        active = MediaPolicy(
            allow_http=active.allow_http,
            allow_file=active.allow_file,
            allow_private_ip=active.allow_private_ip,
            max_bytes=active.max_bytes,
            max_redirects=active.max_redirects,
            timeout_sec=float(timeout),
            allowed_path_roots=active.allowed_path_roots,
        )

    for key in ("base64", "image_base64", "data"):
        value = meta.get(key)
        if isinstance(value, str):
            normalized = _coerce_base64(value)
            if normalized:
                # rough size guard on decoded payload
                try:
                    raw = base64.b64decode(normalized, validate=False)
                except Exception:
                    return normalized
                if len(raw) > active.max_bytes:
                    _logger.warning("media base64 too large: reason=too_large")
                    return ""
                return normalized

    url = str(meta.get("url") or meta.get("src") or "").strip()
    if url:
        if url.startswith("data:image/"):
            return _coerce_base64(url)
        if url.startswith("http://") or url.startswith("https://"):
            raw = _fetch_http(url, active)
            if raw:
                return base64.b64encode(raw).decode("ascii")
            return ""
        if url.startswith("file://"):
            path_value = path_from_file_uri(url)
            ok, resolved, reason = check_local_path(path_value, active)
            if not ok:
                _logger.warning(
                    "media file uri blocked: reason=%s", reason
                )
                return ""
            raw = _read_bytes(resolved, active.max_bytes)
            if raw:
                return base64.b64encode(raw).decode("ascii")
            return ""

    file_value = str(meta.get("file") or meta.get("path") or "").strip()
    if file_value:
        normalized = _coerce_base64(file_value)
        if normalized != file_value and normalized:
            return normalized
        if file_value.startswith("file://"):
            file_value = path_from_file_uri(file_value)
        ok, resolved, reason = check_local_path(file_value, active)
        if not ok:
            _logger.warning("media local path blocked: reason=%s", reason)
            return ""
        raw = _read_bytes(resolved, active.max_bytes)
        if raw:
            return base64.b64encode(raw).decode("ascii")

    return ""
