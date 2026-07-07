from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


SYSTEM_WINDOW_KEYWORDS = (
    "锁屏",
    "windows 默认锁屏界面",
    "live2d agent",
    "登录",
    "閿佸睆",
    "windows 榛樿閿佸睆鐣岄潰",
    "鐧诲綍",
)


@dataclass(frozen=True)
class SensorEventGuardResult:
    allowed: bool
    clean_title: str
    display_app: str
    reason: str = ""


def clean_sensor_title(text: str) -> str:
    if not text:
        return ""
    return "".join(ch for ch in str(text) if ch.isprintable())


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
