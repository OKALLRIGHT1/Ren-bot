from __future__ import annotations

from typing import Any, Dict, Optional

from .event import DisasterEvent


def _redact(text: str) -> str:
    raw = str(text or "")
    if "sk-" in raw:
        return "[REDACTED]"
    return raw


def format_text(event: DisasterEvent, extra: Optional[Dict[str, Any]] = None) -> str:
    extra = extra or {}
    kind = str(event.kind or "eew").strip() or "eew"
    if kind == "typhoon":
        header = f"台风预警（{event.display_name or '中央气象台'}）"
    elif kind == "weather":
        header = f"气象预警（{event.display_name or '中央气象台'}）"
    else:
        header = f"地震预警（{event.display_name or '中国地震预警网'}）"
    lines = [header]
    title = str(event.raw_title or "").strip()[:120]
    if title and kind in {"typhoon", "weather"}:
        lines.append(f"内容：{title}")
    place = str(event.place_name or "").strip()[:80]
    if place and kind == "eew":
        lines.append(f"地点：{place}")
    if kind == "eew" and event.magnitude is not None:
        lines.append(f"震级：M{event.magnitude:g}")
    if event.occurred_at:
        lines.append(f"时间：{event.occurred_at}")
    if kind == "eew" and event.depth_km is not None:
        lines.append(f"深度：{event.depth_km:g} km")
    color = str(extra.get("color") or event.color_level or "").strip()
    if color and kind in {"typhoon", "weather"}:
        lines.append(f"等级：{color}")
    label = str(extra.get("label") or "").strip()
    dist = extra.get("dist_km")
    if label and dist is not None:
        try:
            lines.append(f"关注：{label}（约 {float(dist):.0f} km）")
        except (TypeError, ValueError):
            lines.append(f"关注：{label}")
    elif label:
        lines.append(f"关注：{label}")
    if int(event.updates or 0) > 0:
        lines.append(f"报告：第 {int(event.updates)} 报")
    return "\n".join(lines)


def gateway_ctx(session_id: str) -> Dict[str, Any]:
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "session_id": str(session_id or "").strip(),
            "adapter": "napcat_qq",
        },
    }


async def send_alert(gateway: Any, sessions: Any, text: str) -> int:
    if gateway is None:
        raise RuntimeError("gateway_unavailable")
    available = getattr(gateway, "available", None)
    if callable(available) and not bool(available()):
        raise RuntimeError("gateway_unavailable")
    if isinstance(sessions, str):
        targets = [sessions]
    else:
        targets = [str(item or "").strip() for item in (sessions or [])]
    targets = [item for item in targets if item]
    if not targets:
        raise RuntimeError("owner_session_empty")
    sent = 0
    last_error: Optional[Exception] = None
    for session in targets:
        try:
            await gateway.send_text(text, gateway_ctx(session), emotion="neutral")
            sent += 1
        except Exception as exc:
            last_error = exc
            print(f"[DisasterWarning] send fail session={session}: {exc}")
    if sent == 0 and last_error is not None:
        raise last_error
    if sent == 0:
        raise RuntimeError("owner_session_empty")
    return sent
