from types import SimpleNamespace

from hud_client import HudBridge, parse_runtime_status_payload


def test_hud_bridge_dispatches_log_and_control():
    calls = []
    ui = SimpleNamespace(
        append=lambda role, text: calls.append(("log", role, text)),
        set_status=lambda text: calls.append(("status", text)),
        request_show_hud=lambda: calls.append(("show",)),
        request_toggle_show_hide=lambda: calls.append(("toggle",)),
        request_hud_hotkey=lambda: calls.append(("hotkey",)),
    )
    bridge = HudBridge(ui)
    bridge.handle_event({"type": "log", "role": "assistant", "text": "你好"})
    bridge.handle_event({"type": "status", "text": "思考中…"})
    bridge.handle_event({"type": "hud_control", "action": "toggle"})
    assert ("log", "assistant", "你好") in calls
    assert ("status", "思考中…") in calls
    assert ("toggle",) in calls


def test_parse_runtime_status_payload_reads_components():
    parsed = parse_runtime_status_payload(
        {
            "ok": True,
            "data": {
                "overall": "degraded",
                "components": {
                    "tts": {
                        "state": "healthy",
                        "effective_state": "healthy",
                        "summary": "ok",
                    }
                },
            },
        }
    )
    assert parsed["overall"] == "degraded"
    assert "tts" in parsed["components"]
