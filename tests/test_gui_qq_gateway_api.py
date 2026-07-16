from __future__ import annotations

from typing import Any, Dict

from services.gui_api.qq_gateway_service import QqGatewayGuiService


class FakeRuntime:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {
            "napcat_enabled": True,
            "napcat_webhook_host": "127.0.0.1",
            "napcat_webhook_port": 8080,
            "napcat_access_token": "secret-access",
            "napcat_api_token": "secret-api",
            "napcat_owner_user_ids": ["10001"],
            "napcat_allow_group": True,
        }

    def load(self) -> Dict[str, Any]:
        return dict(self.data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self.data.update(patch or {})
        return dict(self.data)


def test_get_gateway_masks_tokens():
    runtime = FakeRuntime()
    service = QqGatewayGuiService(load_runtime=runtime.load, update_runtime=runtime.update)
    result = service.get_settings()
    assert result["ok"] is True
    data = result["data"]
    assert data["napcat_enabled"] is True
    assert data["has_access_token"] is True
    assert data["has_api_token"] is True
    assert "secret-access" not in str(data)
    assert "secret-api" not in str(data)


def test_save_gateway_keeps_masked_tokens():
    runtime = FakeRuntime()
    service = QqGatewayGuiService(load_runtime=runtime.load, update_runtime=runtime.update)
    saved = service.save_settings(
        {
            "napcat_enabled": False,
            "napcat_webhook_port": 9090,
            "napcat_access_token": "********",
            "napcat_api_token": {"action": "replace", "value": "new-api"},
            "napcat_owner_user_ids": "10001,10002",
        }
    )
    assert saved["ok"] is True
    assert runtime.data["napcat_enabled"] is False
    assert runtime.data["napcat_webhook_port"] == 9090
    assert runtime.data["napcat_access_token"] == "secret-access"
    assert runtime.data["napcat_api_token"] == "new-api"
    assert runtime.data["napcat_owner_user_ids"] == ["10001", "10002"]
