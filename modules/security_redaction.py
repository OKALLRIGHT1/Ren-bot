import re
from typing import Any

_SENSITIVE_KEY_TOKENS = (
    "api_key",
    "token",
    "secret",
    "password",
    "access_key",
    "authorization",
    "bearer",
    "client_secret",
)


_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?KEY|TOKEN|SECRET|PASSWORD)"
    r"[A-Z0-9_-]*)\b(\s*[:=]\s*)([^\s,;&]+)",
    flags=re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._~+/=-]{12,})", re.IGNORECASE)
_COMMON_KEY_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|sk-proj-[A-Za-z0-9_-]{12,}|"
    r"sk-or-v1-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})\b"
)


def normalize_sensitive_key(key: str) -> str:
    return str(key or "").strip().lower().replace("-", "_")


def is_sensitive_key(key: str) -> bool:
    value = normalize_sensitive_key(key)
    if not value:
        return False
    if any(token in value for token in _SENSITIVE_KEY_TOKENS):
        return True
    return value.endswith("_token") or value.endswith("_api_key")


def is_secret_setting(key: str, setting_info: Any = None) -> bool:
    if isinstance(setting_info, dict):
        setting_type = str(setting_info.get("type") or "").strip().lower()
        if setting_type in {"secret", "password"}:
            return True
    return is_sensitive_key(key)


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", text)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    return _COMMON_KEY_RE.sub("[REDACTED_KEY]", text)
