import importlib.util
import sys
from pathlib import Path


PLUGIN_DIR = Path("plugins/qq_draw")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_qq_draw_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


def test_extracts_image_url_from_chat_completion_message_content():
    Plugin = load_plugin_class()
    plugin = Plugin()
    result = {
        "choices": [
            {
                "message": {
                    "content": "已生成：![image](https://example.test/generated.png)"
                }
            }
        ]
    }

    assert plugin._pick_image_url_from_result(result) == "https://example.test/generated.png"


def test_extracts_image_url_from_chat_completion_plain_text_url():
    Plugin = load_plugin_class()
    plugin = Plugin()
    result = {
        "choices": [
            {
                "message": {
                    "content": "https://cdn.example.test/generated-image"
                }
            }
        ]
    }

    assert plugin._pick_image_url_from_result(result) == "https://cdn.example.test/generated-image"


def test_extracts_image_url_from_chat_completion_content_part():
    Plugin = load_plugin_class()
    plugin = Plugin()
    result = {
        "choices": [
            {
                "message": {
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://img.example.test/generated.webp"
                            },
                        }
                    ]
                }
            }
        ]
    }

    assert plugin._pick_image_url_from_result(result) == "https://img.example.test/generated.webp"


def test_extracts_image_url_from_nested_data_object():
    Plugin = load_plugin_class()
    plugin = Plugin()
    result = {
        "status": "success",
        "data": {
            "image": {
                "url": "https://nested.example.test/image.png",
            }
        },
    }

    assert plugin._pick_image_url_from_result(result) == "https://nested.example.test/image.png"


def test_extracts_image_url_from_output_list():
    Plugin = load_plugin_class()
    plugin = Plugin()
    result = {
        "output": [
            "https://output.example.test/0.png",
            "https://output.example.test/1.png",
        ]
    }

    assert plugin._pick_image_url_from_result(result) == "https://output.example.test/1.png"


def test_failure_message_includes_result_summary_when_no_image():
    Plugin = load_plugin_class()
    plugin = Plugin()
    result = {
        "status": "completed",
        "data": {"task_id": "abc123"},
    }

    message = plugin._format_no_image_result(result)

    assert "返回摘要" in message
    assert "task_id" in message


def test_qq_draw_declares_command_only_capability():
    Plugin = load_plugin_class()
    plugin = Plugin()

    capabilities = plugin.get_capabilities()

    assert [cap.id for cap in capabilities] == ["qq_draw.generate_image_cmd"]
    assert capabilities[0].trigger_mode == "command_only"


def test_qq_draw_capability_matches_only_slash_command():
    Plugin = load_plugin_class()
    plugin = Plugin()
    capability = plugin.get_capabilities()[0]

    command_match = capability.match("/画图 一只猫", {})
    natural_match = capability.match("帮我画图，一只猫", {})

    assert command_match is not None
    assert command_match.plugin == "qq_draw"
    assert command_match.args == {"prompt": "一只猫"}
    assert natural_match is None


def test_qq_draw_capability_reports_missing_api_key():
    from services.capability_manager import ToolCapabilityManager

    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {"api_key": {"type": "secret", "default": ""}}
    plugin.reload_config()
    manager = ToolCapabilityManager.from_plugin_maps(direct_map={"qq_draw": plugin})

    result = manager.match("/画图 一只猫", {})

    assert result.selected is None
    assert result.reason == "unavailable"
    assert result.candidates[0].capability_id == "qq_draw.generate_image_cmd"
    assert result.candidates[0].unavailable_reason == "missing_secret: qq_draw.api_key"
