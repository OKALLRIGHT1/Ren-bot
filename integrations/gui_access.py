from __future__ import annotations

import secrets

from modules.runtime_settings import load_runtime_settings, update_runtime_settings


GUI_ACCESS_TOKEN_KEY = "gui_access_token"


def get_or_create_gui_access_token() -> str:
    settings = load_runtime_settings()
    token = str(settings.get(GUI_ACCESS_TOKEN_KEY) or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(32)
    update_runtime_settings({GUI_ACCESS_TOKEN_KEY: token})
    return token
