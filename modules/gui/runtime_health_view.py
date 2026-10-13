from __future__ import annotations

from typing import Any, Dict, List


# 30s：空闲时健康状态基本不变，10s 的 HTTP 探测纯属浪费；
# 真正的断连由 WS 断开事件即时感知，不靠这个轮询。
RUNTIME_HEALTH_REFRESH_INTERVAL_MS = 30_000

OVERALL_PRESENTATIONS = {
    "healthy": ("运行健康", "#22C55E"),
    "degraded": ("运行需注意", "#F59E0B"),
    "offline": ("运行异常", "#EF4444"),
    "unknown": ("健康状态未知", "#94A3B8"),
}

STATE_LABELS = {
    "healthy": "健康",
    "degraded": "需注意",
    "offline": "离线",
    "reconnecting": "重连中",
    "cooldown": "冷却中",
    "disabled": "已禁用",
}

COMPONENT_LABELS = {
    "brain": "大脑",
    "live2d_ws": "桌面形象",
    "rust_activity": "活动感知",
    "qq_gateway": "QQ 网关",
    "tts": "语音合成",
    "asr": "语音识别",
    "plugin_manager": "插件管理器",
}

_STATE_PRIORITY = {
    "offline": 0,
    "degraded": 1,
    "reconnecting": 1,
    "cooldown": 1,
    "healthy": 2,
    "disabled": 3,
}


def overall_presentation(snapshot: Any) -> Dict[str, str]:
    state = "unknown"
    if isinstance(snapshot, dict):
        candidate = str(snapshot.get("overall") or "").strip().lower()
        if candidate in OVERALL_PRESENTATIONS:
            state = candidate
    label, color = OVERALL_PRESENTATIONS[state]
    return {"state": state, "label": label, "color": color}


def _derived_brain_record(snapshot: Dict[str, Any], components: Dict[str, Any]) -> Dict[str, Any] | None:
    existing = components.get("brain")
    if isinstance(existing, dict):
        return existing
    hud = components.get("hud_link")
    if isinstance(hud, dict):
        return hud
    overall = str(snapshot.get("overall") or "").strip().lower()
    if overall == "offline":
        return {
            "state": "offline",
            "effective_state": "offline",
            "summary": "大脑未连接",
        }
    if components or overall in {"healthy", "degraded"}:
        return {
            "state": "healthy",
            "effective_state": "healthy",
            "summary": "大脑进程在线",
        }
    return None


def component_rows(snapshot: Any) -> List[Dict[str, str]]:
    if not isinstance(snapshot, dict):
        return []
    components = snapshot.get("components")
    if not isinstance(components, dict):
        return []

    derived = dict(components)
    brain = _derived_brain_record(snapshot, derived)
    derived.pop("hud_link", None)
    if brain is not None:
        derived["brain"] = brain

    rows = []
    for component, record in derived.items():
        if not isinstance(record, dict):
            continue
        component_key = str(component)
        if component_key.startswith("model:"):
            continue
        state = str(
            record.get("effective_state") or record.get("state") or "unknown"
        ).strip().lower()
        component_label = COMPONENT_LABELS.get(component_key)
        rows.append(
            {
                "component": component_key,
                "component_label": component_label or component_key,
                "state": state,
                "state_label": STATE_LABELS.get(state, state or "未知"),
                "summary": str(record.get("summary") or ""),
                "updated_at": str(record.get("updated_at") or ""),
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            _STATE_PRIORITY.get(row["state"], 1),
            row["component_label"].casefold(),
        ),
    )
