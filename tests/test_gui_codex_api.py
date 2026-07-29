from __future__ import annotations

from typing import Any, Dict

from services.gui_api.codex_service import CodexGuiService


class FakeRuntime:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {
            "codex_mode_enabled": True,
            "codex_last_path": "D:/proj",
            "codex_allow_write": False,
            "codex_allow_exec": False,
            "codex_autorun": False,
            "codex_last_task_id": "t1",
        }

    def load(self) -> Dict[str, Any]:
        return dict(self.data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.data.update(patch or {})
        return dict(self.data)


def test_get_and_save_codex_settings():
    runtime = FakeRuntime()
    service = CodexGuiService(load_runtime=runtime.load, update_runtime=runtime.update)
    got = service.get_settings()
    assert got["ok"] is True
    assert got["data"]["codex_mode_enabled"] is True
    saved = service.save_settings(
        {
            "codex_mode_enabled": False,
            "codex_allow_write": True,
            "codex_last_path": "D:/work",
        }
    )
    assert saved["ok"] is True
    assert runtime.data["codex_mode_enabled"] is False
    assert runtime.data["codex_allow_write"] is True
    assert runtime.data["codex_last_path"] == "D:/work"
