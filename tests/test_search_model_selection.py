import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from modules.plugin_model_gateway import PluginModelCallResult


PLUGIN_DIR = Path("plugins/search")


def load_plugin_class():
    spec = importlib.util.spec_from_file_location(
        "test_search_model_selection_plugin", PLUGIN_DIR / "plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Plugin


class FakeGateway:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def invoke_text(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.result


def test_search_config_selects_models_from_main_catalog():
    config = json.loads((PLUGIN_DIR / "config.json").read_text(encoding="utf-8"))
    setting = config["settings"]["model_queue"]

    assert config["type"] == "delegate"
    assert setting["type"] == "model_queue"
    assert setting["purpose"] == ["web_search"]


def test_search_uses_selected_main_model_before_legacy_provider(monkeypatch):
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {
        "model_queue": {"type": "model_queue", "default": ["search-a"]},
        "fallback_ddg": {"type": "bool", "default": False},
    }
    gateway = FakeGateway(
        PluginModelCallResult(
            ok=True,
            text="fresh search result",
            model_id="search-a",
        )
    )

    async def legacy_must_not_run(**_kwargs):
        raise AssertionError("legacy provider should not run")

    monkeypatch.setattr(plugin, "_grok_search", legacy_must_not_run)

    result = asyncio.run(
        plugin.run(
            "latest news",
            {"delegate_mode": True, "model_gateway": gateway},
        )
    )

    assert "fresh search result" in result
    assert "model=search-a" in result
    assert gateway.calls[0][1]["selected_ids"] == ["search-a"]
    assert gateway.calls[0][1]["required_purpose"] == "web_search"


def test_search_empty_main_route_keeps_legacy_compatibility(monkeypatch):
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {
        "model_queue": {"type": "model_queue", "default": []},
        "provider": {"type": "text", "default": "grok"},
        "fallback_ddg": {"type": "bool", "default": False},
    }
    gateway = FakeGateway(
        PluginModelCallResult(
            ok=False,
            error_code="model_route_empty",
            error_message="route empty",
        )
    )

    async def legacy_search(**_kwargs):
        return "legacy result"

    monkeypatch.setattr(plugin, "_grok_search", legacy_search)

    result = asyncio.run(
        plugin.run(
            "latest news",
            {"delegate_mode": True, "model_gateway": gateway},
        )
    )

    assert result == "legacy result"


def test_search_gui_check_reports_configured_main_route_failure(monkeypatch):
    Plugin = load_plugin_class()
    plugin = Plugin()
    plugin.settings = {
        "model_queue": {"type": "model_queue", "default": []},
        "provider": {"type": "text", "default": "grok"},
        "grok_api_key": {"type": "secret", "default": "legacy-key"},
    }

    async def failed_main_route(**_kwargs):
        raise RuntimeError("route model unavailable")

    monkeypatch.setattr(plugin, "_search_with_main_models", failed_main_route)

    result = asyncio.run(plugin.gui_check_endpoints())

    assert result == "❌ 主程序联网搜索模型不可用：route model unavailable"
