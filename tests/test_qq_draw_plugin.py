import importlib.util
import json
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


def test_build_request_url_dedupes_v1_when_base_has_v1():
    Plugin = load_plugin_class()
    plugin = Plugin()
    url = plugin._build_request_url(
        provider={
            "base_url": "https://x666.me/v1",
            "endpoint_path": "/v1/images/generations",
        }
    )
    assert url == "https://x666.me/v1/images/generations"
    url2 = plugin._build_request_url(
        provider={
            "base_url": "https://api.tomori.de5.net",
            "endpoint_path": "/v1/images/generations",
        }
    )
    assert url2 == "https://api.tomori.de5.net/v1/images/generations"


def test_default_http_headers_include_browser_ua():
    Plugin = load_plugin_class()
    plugin = Plugin()
    headers = plugin._default_http_headers("sk-test")
    assert headers["Authorization"] == "Bearer sk-test"
    assert "Mozilla" in headers["User-Agent"]


def test_build_request_body_passes_all_reference_images():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.reload_config()
    body = plugin._build_request_body(
        "改成蓝色",
        image_base64_list=["aaa", "bbb", "ccc"],
        provider={
            "model_name": "gpt-image-2",
            "include_chat_image_part": True,
            "input_image_field": "image",
            "input_image_format": "base64",
        },
    )
    assert body["image"] == ["aaa", "bbb", "ccc"]
    assert body["images"] == ["aaa", "bbb", "ccc"]
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert [part["image_url"]["url"] for part in content[1:]] == ["aaa", "bbb", "ccc"]


def test_build_request_body_keeps_single_image_compat():
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.reload_config()
    body = plugin._build_request_body(
        "改成红色",
        image_base64="only-one",
        provider={
            "model_name": "gpt-image-2",
            "include_chat_image_part": False,
            "input_image_field": "image",
            "input_image_format": "base64",
        },
    )
    assert body["image"] == "only-one"
    assert "images" not in body


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


def test_qq_draw_capability_reports_missing_image_models(monkeypatch):
    from services.capability_manager import ToolCapabilityManager

    Plugin = load_plugin_class()
    plugin = Plugin()
    monkeypatch.setattr(plugin, "_build_provider_queue", lambda **kwargs: [])
    manager = ToolCapabilityManager.from_plugin_maps(direct_map={"qq_draw": plugin})

    result = manager.match("/画图 一只猫", {})

    assert result.selected is None
    assert result.reason == "unavailable"
    assert result.candidates[0].capability_id == "qq_draw.generate_image_cmd"
    assert "no_image_models" in result.candidates[0].unavailable_reason


def test_queue_uses_plugin_selected_models(monkeypatch):
    Plugin = load_plugin_class()
    plugin = Plugin()
    catalog = {
        "chat-only": {
            "base_url": "http://chat.example/v1",
            "api_key": "chat-key",
            "model": "gpt-chat",
            "purposes": ["chat"],
        },
        "draw-a": {
            "base_url": "http://draw-a.example/v1",
            "api_key": "draw-a-key",
            "model": "image-a",
            "purposes": ["画图"],
        },
        "draw-b": {
            "base_url": "http://draw-b.example/v1",
            "api_key": "draw-b-key",
            "model": "image-b",
            "purposes": ["image_gen"],
        },
    }
    monkeypatch.setattr(plugin, "_load_model_catalog", lambda: catalog)
    # Plugin selection overrides empty/global route.
    plugin.settings = {
        "model_queue": {"type": "model_queue", "default": ["draw-b"]},
    }
    plugin.reload_config()

    providers = plugin._build_provider_queue()
    names = [str(item.get("name")) for item in providers]

    assert names == ["draw-b"]
    assert providers[0]["endpoint_path"] == "/v1/images/generations"
    assert providers[0]["api_key"] == "draw-b-key"


def test_queue_empty_when_plugin_and_route_not_configured(monkeypatch):
    Plugin = load_plugin_class()
    plugin = Plugin()
    catalog = {
        "draw-a": {
            "base_url": "http://draw-a.example/v1",
            "api_key": "draw-a-key",
            "model": "image-a",
            "purposes": ["image_gen"],
        }
    }
    monkeypatch.setattr(plugin, "_load_model_catalog", lambda: catalog)
    monkeypatch.setattr(plugin, "_load_router", lambda: {"image_gen": []})
    plugin.settings = {"model_queue": {"type": "model_queue", "default": []}}
    plugin.reload_config()
    assert plugin._build_provider_queue() == []


def test_queue_falls_back_across_image_models(monkeypatch):
    import asyncio

    Plugin = load_plugin_class()
    plugin = Plugin()
    monkeypatch.setattr(
        plugin,
        "_build_provider_queue",
        lambda **kwargs: [
            {
                "name": "draw-a",
                "base_url": "http://a.example",
                "endpoint_path": "/v1/images/generations",
                "model_name": "image-a",
                "api_key": "a-key",
            },
            {
                "name": "draw-b",
                "base_url": "http://b.example",
                "endpoint_path": "/v1/images/generations",
                "model_name": "image-b",
                "api_key": "b-key",
            },
        ],
    )
    plugin.reload_config()

    calls = []

    async def fake_call(prompt, image_base64="", image_base64_list=None, provider=None):
        name = str((provider or {}).get("name") or "")
        calls.append(name)
        if name == "draw-a":
            raise RuntimeError("HTTP 503: primary down")
        return {
            "data": [
                {
                    "b64_json": "aGVsbG8=",  # "hello"
                }
            ]
        }

    monkeypatch.setattr(plugin, "_call_image_api_with_provider", fake_call)

    result, provider, image_bytes = asyncio.run(plugin._call_image_api("一只猫"))

    assert calls == ["draw-a", "draw-b"]
    assert provider.get("name") == "draw-b"
    assert image_bytes == b"hello"
    assert result["data"][0]["b64_json"] == "aGVsbG8="


def test_call_image_api_requires_configured_image_models(monkeypatch):
    import asyncio

    Plugin = load_plugin_class()
    plugin = Plugin()
    monkeypatch.setattr(plugin, "_build_provider_queue", lambda **kwargs: [])
    try:
        asyncio.run(plugin._call_image_api("一只猫"))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "模型与路由" in str(exc)
