"""Compatibility shim. The product GUI lives in this repo."""

from __future__ import annotations

from typing import Any, Dict

import boot


class HudBridge:
    def __init__(self, ui: Any) -> None:
        self.ui = ui

    def handle_event(self, event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        if etype == "log":
            if hasattr(self.ui, "append"):
                self.ui.append(event.get("role"), event.get("text"))
        elif etype == "status":
            if hasattr(self.ui, "set_status"):
                self.ui.set_status(event.get("text"))
        elif etype == "hud_control":
            action = str(event.get("action") or "").strip().lower()
            if action == "toggle" and hasattr(self.ui, "request_toggle_show_hide"):
                self.ui.request_toggle_show_hide()
            elif action == "show" and hasattr(self.ui, "request_show_hud"):
                self.ui.request_show_hud()
            elif action == "hotkey" and hasattr(self.ui, "request_hud_hotkey"):
                self.ui.request_hud_hotkey()


def parse_runtime_status_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"overall": "unknown", "components": {}}
    data = payload.get("data") if payload.get("ok") else payload
    if not isinstance(data, dict):
        return {"overall": "unknown", "components": {}}
    return {
        "overall": data.get("overall", "unknown"),
        "components": data.get("components", {}),
    }


def main() -> int:
    return int(boot.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())

