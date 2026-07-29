from integrations.gui_http import GuiHttpServer
from modules.plugin_manager import PluginManager


def test_gui_masks_common_sensitive_field_names() -> None:
    masked = GuiHttpServer._mask_secrets(
        {
            "password": "pw-value",
            "client-secret": "secret-value",
            "access_key": "access-value",
            "api-key": "api-value",
        }
    )

    assert masked["password"] == GuiHttpServer.SECRET_MASK
    assert masked["client-secret"] == GuiHttpServer.SECRET_MASK
    assert masked["access_key"] == GuiHttpServer.SECRET_MASK
    assert masked["api-key"] == GuiHttpServer.SECRET_MASK


def test_gui_masks_secret_setting_default_without_exposing_schema() -> None:
    masked = GuiHttpServer._mask_secrets(
        {
            "settings": {
                "grok_api_key": {
                    "type": "secret",
                    "label": "Grok API Key",
                    "default": "secret-value",
                },
                "credential": {
                    "type": "secret",
                    "label": "Credential",
                    "default": "schema-secret-value",
                },
            }
        }
    )

    setting = masked["settings"]["grok_api_key"]
    assert setting["type"] == "secret"
    assert setting["label"] == "Grok API Key"
    assert setting["default"] == GuiHttpServer.SECRET_MASK
    assert masked["settings"]["credential"]["default"] == GuiHttpServer.SECRET_MASK


def test_gui_restore_masked_secret_uses_real_secret_mask() -> None:
    restored = GuiHttpServer._restore_masked_secrets(
        {"password": GuiHttpServer.SECRET_MASK},
        {"password": "existing-password"},
    )

    assert restored["password"] == "existing-password"


def test_gui_restore_masked_secret_setting_default() -> None:
    restored = GuiHttpServer._restore_masked_secrets(
        {
            "settings": {
                "grok_api_key": {
                    "type": "secret",
                    "default": GuiHttpServer.SECRET_MASK,
                },
                "credential": {
                    "type": "secret",
                    "default": GuiHttpServer.SECRET_MASK,
                },
            }
        },
        {
            "settings": {
                "grok_api_key": {
                    "type": "secret",
                    "default": "existing-secret",
                },
                "credential": {
                    "type": "secret",
                    "default": "existing-schema-secret",
                },
            }
        },
    )

    assert restored["settings"]["grok_api_key"]["default"] == "existing-secret"
    assert (
        restored["settings"]["credential"]["default"]
        == "existing-schema-secret"
    )


def test_secret_setting_detects_schema_type_even_when_key_name_is_plain() -> None:
    manager = PluginManager()

    assert manager._is_secret_setting(
        "credential",
        {"type": "secret", "default": "secret-value"},
    )


def test_secret_setting_normalizes_dash_and_underscore_names() -> None:
    manager = PluginManager()

    assert manager._is_secret_setting("api-key", {"default": "value"})
    assert manager._is_secret_setting("api_key", {"default": "value"})
