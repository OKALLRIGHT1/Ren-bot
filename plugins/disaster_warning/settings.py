from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .watch import WatchRegion

DEFAULT_APP_ID = "97b68b51-ec96-42c3-80d7-83d2bff70d99"
DEFAULT_WS_URL = "wss://ws.fanstudio.tech/all"
DEFAULT_WS_BACKUP_URL = "wss://ws.fanstudio.hk/all"
FAN_APPLY_URL = "https://api.fanstudio.tech/dev-platform/"
DEFAULT_WOLFX_WS_URL = "wss://ws-api.wolfx.jp/all_eew"
WOLFX_DOCS_URL = "https://wolfx.jp/zh/docs/open-api"


def setting_value(raw: Any, default: Any) -> Any:
    if isinstance(raw, dict):
        if "default" in raw:
            return raw.get("default", default)
        if "value" in raw:
            return raw.get("value", default)
        return default
    if raw is None:
        return default
    return raw


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def parse_watch_regions(raw: Any) -> List[WatchRegion]:
    text = setting_value(raw, "[]")
    if isinstance(text, list):
        items = text
    else:
        blob = str(text or "").strip() or "[]"
        try:
            parsed = json.loads(blob)
        except Exception:
            print("[DisasterWarning] watch_regions_json 解析失败，按空列表处理")
            return []
        items = parsed if isinstance(parsed, list) else []
    regions: List[WatchRegion] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        region_id = str(item.get("id") or label or uuid.uuid4().hex[:8]).strip()
        keywords = item.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [part.strip() for part in keywords.replace("，", ",").split(",")]
        kinds = item.get("kinds") or ["eew", "typhoon", "weather"]
        if isinstance(kinds, str):
            kinds = [part.strip() for part in kinds.replace("，", ",").split(",")]
        lat = item.get("lat")
        lng = item.get("lng")
        try:
            lat_f = float(lat) if lat is not None and str(lat).strip() != "" else None
        except (TypeError, ValueError):
            lat_f = None
        try:
            lng_f = float(lng) if lng is not None and str(lng).strip() != "" else None
        except (TypeError, ValueError):
            lng_f = None
        regions.append(
            WatchRegion(
                id=region_id or uuid.uuid4().hex[:8],
                label=label or region_id,
                keywords=[str(k).strip() for k in keywords if str(k).strip()],
                lat=lat_f,
                lng=lng_f,
                radius_km=_as_float(item.get("radius_km"), 300.0),
                min_intensity=_as_float(item.get("min_intensity"), 2.0),
                kinds=[str(k).strip() for k in kinds if str(k).strip()]
                or ["eew"],
                min_color_level=str(item.get("min_color_level") or "黄色"),
            )
        )
    return regions


def regions_to_payload(regions: List[WatchRegion]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for region in regions:
        item: Dict[str, Any] = {
            "id": region.id,
            "label": region.label,
            "keywords": list(region.keywords),
            "radius_km": region.radius_km,
            "min_intensity": region.min_intensity,
            "kinds": list(region.kinds),
            "min_color_level": region.min_color_level,
        }
        if region.lat is not None and region.lng is not None:
            item["lat"] = region.lat
            item["lng"] = region.lng
        payload.append(item)
    return payload


def normalize_session(raw: Any, *, default_kind: str = "group") -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    kind_default = str(default_kind or "group").strip().lower()
    if kind_default not in {"group", "private"}:
        kind_default = "group"
    if ":" in text:
        kind, peer = text.split(":", 1)
        kind = kind.strip().lower()
        peer = "".join(ch for ch in peer.strip() if ch.isdigit())
        if kind in {"group", "private"} and peer:
            return f"{kind}:{peer}"
        return ""
    peer = "".join(ch for ch in text if ch.isdigit())
    if not peer:
        return ""
    return f"{kind_default}:{peer}"


def parse_session_list(raw: Any, *, default_kind: str = "group") -> List[str]:
    value = setting_value(raw, "")
    items: List[Any]
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "").strip()
        if not text:
            items = []
        elif text.startswith("["):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = []
            items = parsed if isinstance(parsed, list) else [text]
        else:
            for sep in ("，", ";", "；", "\n", ",", " "):
                text = text.replace(sep, ",")
            items = [part.strip() for part in text.split(",") if part.strip()]
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        session = normalize_session(item, default_kind=default_kind)
        if not session or session in seen:
            continue
        seen.add(session)
        out.append(session)
    return out


def clamp_ui_host(host: str) -> str:
    text = str(host or "").strip() or "127.0.0.1"
    if text in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return text


@dataclass
class PluginSettings:
    enabled: bool = False
    owner_session: str = ""
    group_sessions: List[str] = field(default_factory=list)
    regions: List[WatchRegion] = field(default_factory=list)
    min_magnitude: float = 4.0
    major_override_mag: float = 7.0
    silence_sec: int = 60
    fanstudio_app_id: str = DEFAULT_APP_ID
    fanstudio_api_key: str = ""
    ws_url: str = DEFAULT_WS_URL
    ws_backup_url: str = DEFAULT_WS_BACKUP_URL
    wolfx_eew_enabled: bool = False
    wolfx_ws_url: str = DEFAULT_WOLFX_WS_URL
    wolfx_ws_backup_url: str = ""
    alert_poll_enabled: bool = False
    alert_poll_sec: int = 300
    state_path: str = "data/disaster_warning_state.json"
    status_ui_enabled: bool = True
    status_ui_host: str = "127.0.0.1"
    status_ui_port: int = 8765

    @classmethod
    def from_raw(cls, raw: Dict[str, Any] | None) -> "PluginSettings":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            enabled=_as_bool(setting_value(data.get("enabled"), False), False),
            owner_session=normalize_session(
                setting_value(data.get("owner_session"), ""),
                default_kind="private",
            ),
            group_sessions=parse_session_list(
                data.get("group_sessions"), default_kind="group"
            ),
            regions=parse_watch_regions(data.get("watch_regions_json")),
            min_magnitude=_as_float(
                setting_value(data.get("min_magnitude"), 4.0), 4.0
            ),
            major_override_mag=_as_float(
                setting_value(data.get("major_override_mag"), 7.0), 7.0
            ),
            silence_sec=max(
                0,
                _as_int(setting_value(data.get("silence_sec"), 60), 60),
            ),
            fanstudio_app_id=str(
                setting_value(data.get("fanstudio_app_id"), DEFAULT_APP_ID)
                or DEFAULT_APP_ID
            ).strip(),
            fanstudio_api_key=str(
                setting_value(data.get("fanstudio_api_key"), "") or ""
            ).strip(),
            ws_url=str(
                setting_value(data.get("ws_url"), DEFAULT_WS_URL) or DEFAULT_WS_URL
            ).strip(),
            ws_backup_url=str(
                setting_value(data.get("ws_backup_url"), DEFAULT_WS_BACKUP_URL)
                or DEFAULT_WS_BACKUP_URL
            ).strip(),
            wolfx_eew_enabled=_as_bool(
                setting_value(data.get("wolfx_eew_enabled"), False), False
            ),
            wolfx_ws_url=str(
                setting_value(data.get("wolfx_ws_url"), DEFAULT_WOLFX_WS_URL)
                or DEFAULT_WOLFX_WS_URL
            ).strip(),
            wolfx_ws_backup_url=str(
                setting_value(data.get("wolfx_ws_backup_url"), "") or ""
            ).strip(),
            alert_poll_enabled=_as_bool(
                setting_value(data.get("alert_poll_enabled"), False), False
            ),
            alert_poll_sec=max(
                30,
                min(3600, _as_int(setting_value(data.get("alert_poll_sec"), 300), 300)),
            ),
            state_path=str(
                setting_value(
                    data.get("state_path"), "data/disaster_warning_state.json"
                )
                or "data/disaster_warning_state.json"
            ).strip(),
            status_ui_enabled=_as_bool(
                setting_value(data.get("status_ui_enabled"), True), True
            ),
            status_ui_host=clamp_ui_host(
                str(setting_value(data.get("status_ui_host"), "127.0.0.1") or "127.0.0.1")
            ),
            status_ui_port=max(
                1024,
                min(65535, _as_int(setting_value(data.get("status_ui_port"), 8765), 8765)),
            ),
        )

    def push_sessions(self) -> List[str]:
        sessions: List[str] = []
        seen: set[str] = set()
        for item in [self.owner_session, *self.group_sessions]:
            session = normalize_session(
                item, default_kind="private" if item == self.owner_session else "group"
            )
            if not session or session in seen:
                continue
            seen.add(session)
            sessions.append(session)
        return sessions
