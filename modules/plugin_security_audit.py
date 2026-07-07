from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List


RISK_FLAGS_BY_TRIGGER: Dict[str, List[str]] = {
    "_claw_email": ["mail"],
    "agently_mail": ["mail"],
    "app_control": ["process_control"],
    "code_agent": ["process_control", "code_or_skill"],
    "code_executor": ["process_control"],
    "info_gateway": ["paid_api"],
    "mcp_tools": ["code_or_skill"],
    "qq_draw": ["paid_api"],
    "qq_file_browser": ["file_access"],
    "qq_music": ["paid_api"],
    "qq_screenshot": ["file_access"],
    "search_web": ["paid_api"],
    "skill_runtime": ["code_or_skill"],
    "user_files": ["file_access"],
}

HIGH_RISK_FLAGS = {
    "code_or_skill",
    "file_access",
    "mail",
    "paid_api",
    "process_control",
}


def _sorted_unique(values: Iterable[str]) -> List[str]:
    return sorted(dict.fromkeys(str(value) for value in values if str(value)))


def build_plugin_security_matrix(
    plugin_configs: Dict[str, dict],
    normalize_access_control: Callable[[Any], Dict[str, bool]],
) -> List[dict]:
    rows: List[dict] = []
    for trigger_key, config in sorted((plugin_configs or {}).items()):
        if not isinstance(config, dict):
            continue
        trigger = str(config.get("trigger") or trigger_key or "").strip()
        if not trigger:
            continue
        access = normalize_access_control(config.get("access_control"))
        risk_flags = _sorted_unique(RISK_FLAGS_BY_TRIGGER.get(trigger, []))
        rows.append(
            {
                "trigger": trigger,
                "name": str(config.get("name") or trigger),
                "type": str(config.get("type") or "react"),
                "allow_local": bool(access.get("allow_local")),
                "allow_remote_qq": bool(access.get("allow_remote_qq")),
                "allow_qq_owner": bool(access.get("allow_qq_owner")),
                "allow_qq_others": bool(access.get("allow_qq_others")),
                "allow_group_without_at": bool(access.get("allow_group_without_at")),
                "risk_flags": risk_flags,
            }
        )
    return rows


def summarize_plugin_security_matrix(matrix: List[dict]) -> dict:
    owner_remote_high_risk: List[str] = []
    other_qq: List[str] = []
    group_without_at: List[str] = []
    for row in matrix or []:
        trigger = str(row.get("trigger") or "").strip()
        if not trigger or not bool(row.get("allow_remote_qq")):
            continue
        risk_flags = set(row.get("risk_flags") or [])
        if bool(row.get("allow_qq_owner")) and bool(risk_flags & HIGH_RISK_FLAGS):
            owner_remote_high_risk.append(trigger)
        if bool(row.get("allow_qq_others")):
            other_qq.append(trigger)
        if bool(row.get("allow_group_without_at")):
            group_without_at.append(trigger)
    return {
        "owner_remote_high_risk_plugins": _sorted_unique(owner_remote_high_risk),
        "other_qq_plugins": _sorted_unique(other_qq),
        "group_without_at_plugins": _sorted_unique(group_without_at),
    }
