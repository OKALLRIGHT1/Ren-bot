"""Where expression learning is allowed, and how aggressive it is.

Using saved patterns is separate. These settings only control new learning.
"""

from __future__ import annotations

import os
import re
from typing import Any

QQ_SOURCES = {"qq_gateway", "napcat_qq"}

INTENSITY_PRESETS = {
    "low": {"batch": 20, "daily_cap": 3, "min_quality": 7.0, "enable_new": False},
    "medium": {"batch": 10, "daily_cap": 8, "min_quality": 5.5, "enable_new": True},
    "high": {"batch": 6, "daily_cap": 16, "min_quality": 5.0, "enable_new": True},
}

DEFAULT_POLICY = {
    "enabled": False,
    "local": True,
    "intensity": "low",
    "accounts": [],
    "groups": [],
    "batch": 20,
    "daily_cap": 3,
    "min_quality": 7.0,
    "enable_new": False,
}


def parse_id_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,，;；]+", str(value or ""))
    found: list[str] = []
    for item in raw_items:
        digits = re.sub(r"\D", "", str(item or ""))
        if digits and digits not in found:
            found.append(digits)
    return found


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def normalize_expression_learning_policy(runtime: dict[str, Any] | None) -> dict[str, Any]:
    raw = runtime if isinstance(runtime, dict) else {}
    if "expression_learning_enabled" in raw:
        enabled = _as_bool(raw.get("expression_learning_enabled"), False)
    else:
        env_value = os.environ.get("MEMORY_CORE_EXPRESSION_LEARNING_ENABLED")
        enabled = _as_bool(env_value, False) if env_value is not None else False
    local = _as_bool(raw.get("expression_learning_local_enabled"), True)
    intensity = str(raw.get("expression_learning_intensity") or "low").strip().lower()
    if intensity not in INTENSITY_PRESETS:
        intensity = "low"
    preset = INTENSITY_PRESETS[intensity]
    return {
        "enabled": enabled,
        "local": local,
        "intensity": intensity,
        "accounts": parse_id_list(raw.get("expression_learning_qq_accounts")),
        "groups": parse_id_list(raw.get("expression_learning_qq_groups")),
        "batch": int(preset["batch"]),
        "daily_cap": int(preset["daily_cap"]),
        "min_quality": float(preset["min_quality"]),
        "enable_new": bool(preset["enable_new"]),
    }


def expression_source_allowed(meta: dict[str, Any] | None, policy: dict[str, Any]) -> bool:
    """Local chat is its own switch. QQ learns only listed accounts or groups, and only the owner."""
    if not policy.get("enabled"):
        return False
    data = meta if isinstance(meta, dict) else {}
    source = str(data.get("source") or "").strip().lower()
    if source not in QQ_SOURCES:
        return bool(policy.get("local"))
    if not bool(data.get("is_owner")):
        return False
    group_id = re.sub(r"\D", "", str(data.get("group_id") or ""))
    message_type = str(data.get("message_type") or "").strip().lower()
    user_id = re.sub(r"\D", "", str(data.get("user_id") or ""))
    is_group = message_type == "group" or bool(group_id)
    if is_group:
        return bool(group_id) and group_id in set(policy.get("groups") or [])
    return bool(user_id) and user_id in set(policy.get("accounts") or [])
