from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional


SYSTEM_WINDOW_KEYWORDS = (
    "锁屏",
    "windows 默认锁屏界面",
    "live2d agent",
    "登录",
    "閿佸睆",
    "windows 榛樿閿佸睆鐣岄潰",
    "鐧诲綍",
)

_TITLE_NOISE_RE = re.compile(r"[\s|;\-\[\]()（）【】<>《》·•]+")


@dataclass(frozen=True)
class SensorEventGuardResult:
    allowed: bool
    clean_title: str
    display_app: str
    reason: str = ""


@dataclass(frozen=True)
class SensorFocusRevalidateResult:
    ok: bool
    active_title: str = ""
    reason: str = ""


def clean_sensor_title(text: str) -> str:
    if not text:
        return ""
    return "".join(ch for ch in str(text) if ch.isprintable())


def _normalize_focus_token(text: str) -> str:
    cleaned = clean_sensor_title(text).strip().lower()
    if not cleaned:
        return ""
    cleaned = _TITLE_NOISE_RE.sub("", cleaned)
    for suffix in (".exe", ".app"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned


def titles_soft_match(event_title: str, active_title: str, *, app_name: str = "") -> bool:
    """Loose title/app match so minor title jitter does not drop valid focus."""
    event_raw = clean_sensor_title(event_title).strip()
    active_raw = clean_sensor_title(active_title).strip()
    app_raw = clean_sensor_title(app_name).strip()

    event_key = _normalize_focus_token(event_raw)
    active_key = _normalize_focus_token(active_raw)
    app_key = _normalize_focus_token(app_raw)

    if not active_key:
        # Capture layer could not resolve foreground title; do not hard-block.
        return True
    if not event_key and not app_key:
        return True
    if event_key and active_key:
        if event_key == active_key:
            return True
        if event_key in active_key or active_key in event_key:
            return True
        # Shared long fragment helps with "page - App" style titles.
        min_len = min(len(event_key), len(active_key))
        if min_len >= 8:
            shorter, longer = (
                (event_key, active_key)
                if len(event_key) <= len(active_key)
                else (active_key, event_key)
            )
            chunk = max(6, min(12, len(shorter) // 2))
            for idx in range(0, len(shorter) - chunk + 1):
                if shorter[idx : idx + chunk] in longer:
                    return True
    if app_key and active_key:
        if app_key == active_key or app_key in active_key or active_key in app_key:
            return True
    return False


def revalidate_focus_for_sensor(
    *,
    event_title: str,
    app_name: str = "",
    active_title_getter: Optional[Callable[[], str]] = None,
    alternate_titles: Optional[list[str]] = None,
) -> SensorFocusRevalidateResult:
    getter = active_title_getter
    if getter is None:
        try:
            from modules.vision.capture import get_active_window_title as _get_title
        except Exception:
            return SensorFocusRevalidateResult(ok=True, reason="active_title_unavailable")
        getter = _get_title

    try:
        active_title = clean_sensor_title(str(getter() or "")).strip()
    except Exception:
        return SensorFocusRevalidateResult(ok=True, reason="active_title_unavailable")

    candidates = [active_title]
    for extra in alternate_titles or []:
        cleaned = clean_sensor_title(str(extra or "")).strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    for candidate in candidates:
        if titles_soft_match(event_title, candidate, app_name=app_name):
            return SensorFocusRevalidateResult(
                ok=True,
                active_title=candidate,
                reason="matched",
            )
    return SensorFocusRevalidateResult(
        ok=False,
        active_title=active_title,
        reason="focus_mismatch",
    )


def check_sensor_event_guard(
    *,
    window_title: str,
    category: str,
    app_name: str,
    last_reply_time: float,
    min_reply_interval_sec: float,
    now: Callable[[], float] = time.time,
) -> SensorEventGuardResult:
    clean_title = clean_sensor_title(window_title)
    if not clean_title.strip():
        clean_title = str(category or "")

    display_app = app_name or clean_title
    lowered_title = clean_title.lower()
    if any(bad in lowered_title for bad in SYSTEM_WINDOW_KEYWORDS):
        return SensorEventGuardResult(
            allowed=False,
            clean_title=clean_title,
            display_app=display_app,
            reason="system_window",
        )

    if now() - float(last_reply_time or 0.0) < float(min_reply_interval_sec or 0.0):
        return SensorEventGuardResult(
            allowed=False,
            clean_title=clean_title,
            display_app=display_app,
            reason="reply_cooldown",
        )

    return SensorEventGuardResult(
        allowed=True,
        clean_title=clean_title,
        display_app=display_app,
    )
