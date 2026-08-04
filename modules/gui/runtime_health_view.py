from __future__ import annotations

from typing import Any, Dict, List


RUNTIME_HEALTH_REFRESH_INTERVAL_MS = 10_000

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
    "live2d_ws": "Live2D 连接",
    "rust_activity": "Rust 活动感知",
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


def component_rows(snapshot: Any) -> List[Dict[str, str]]:
    if not isinstance(snapshot, dict):
        return []
    components = snapshot.get("components")
    if not isinstance(components, dict):
        return []

    rows = []
    for component, record in components.items():
        if not isinstance(record, dict):
            continue
        state = str(
            record.get("effective_state") or record.get("state") or "unknown"
        ).strip().lower()
        component_key = str(component)
        component_label = COMPONENT_LABELS.get(component_key)
        if component_label is None and component_key.startswith("model:"):
            component_label = f"模型 · {component_key.partition(':')[2]}"
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
