"""App control plugin — currently restart-only.

Triggers local/remote slash commands that return __type__=app_restart.
Not a general application launcher (see open_app for that).
"""

from typing import Any, Dict


class Plugin:
    type = "direct"
    aliases = ["/重启", "/远程重启", "/restart"]

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip().lower()
        key_text = str(key or "").strip().lower()
        return bool(key_text and raw == key_text)

    async def run(self, args: str, ctx: Dict[str, Any]):
        return {
            "__type__": "app_restart",
            "message": "收到，正在重启主程序。",
            "delay_sec": 1.0,
        }
