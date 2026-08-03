from types import SimpleNamespace

import pytest

import modules.llm as llm_module
from modules.plugin_manager import PluginManager
from modules.plugin_model_gateway import PluginModelGateway


@pytest.mark.asyncio
async def test_gateway_uses_explicit_model_queue_in_order():
    calls = []

    def fake_chat(messages, **kwargs):
        calls.append(kwargs)
        kwargs["call_metadata"]["model_key"] = "search-b"
        return "selected-model-result"

    gateway = PluginModelGateway(
        catalog_getter=lambda: {
            "search-a": {"model": "upstream-a", "purposes": ["web_search"]},
            "search-b": {"model": "upstream-b", "purposes": ["web_search"]},
        },
        router_getter=lambda: {"web_search": ["search-b"]},
        chat_callable=fake_chat,
    )

    result = await gateway.invoke_text(
        [{"role": "user", "content": "query"}],
        selected_ids=["search-a", "search-b"],
        required_purpose="web_search",
        task_type="web_search",
        caller="search_web",
    )

    assert result.ok is True
    assert result.text == "selected-model-result"
    assert result.model_id == "search-b"
    assert calls[0]["model_keys_override"] == ["search-a", "search-b"]


@pytest.mark.asyncio
async def test_gateway_rejects_manual_model_with_wrong_purpose():
    gateway = PluginModelGateway(
        catalog_getter=lambda: {
            "chat-a": {"model": "upstream", "purposes": ["chat"]}
        },
        router_getter=lambda: {},
        chat_callable=lambda *_args, **_kwargs: "must-not-run",
    )

    result = await gateway.invoke_text(
        [],
        selected_ids=["chat-a"],
        required_purpose="web_search",
        task_type="web_search",
        caller="search_web",
    )

    assert result.ok is False
    assert result.error_code == "model_purpose_mismatch"
    assert result.model_id == "chat-a"


@pytest.mark.asyncio
async def test_gateway_empty_selection_uses_task_route():
    calls = []

    def fake_chat(messages, **kwargs):
        calls.append(kwargs)
        return "route-result"

    gateway = PluginModelGateway(
        catalog_getter=lambda: {"route-a": {"model": "upstream"}},
        router_getter=lambda: {"summary": ["route-a"]},
        chat_callable=fake_chat,
    )

    result = await gateway.invoke_text(
        [],
        selected_ids=[],
        required_purpose="summary",
        task_type="summary",
        caller="plugin_summary",
    )

    assert result.ok is True
    assert calls[0]["model_keys_override"] == ["route-a"]


def test_plugin_manager_injects_shared_model_gateway():
    manager = PluginManager(plugin_dir="plugins")

    runtime = manager._build_runtime_context({"source": "local"})
    delegated = manager._build_delegate_runtime_context({"source": "local"})

    assert runtime["model_gateway"] is manager.model_gateway
    assert delegated["model_gateway"] is manager.model_gateway


def test_chat_with_ai_accepts_explicit_model_override(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            message = SimpleNamespace(content="manual-result")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(
        llm_module,
        "MODELS",
        {
            "route-a": {"api_key": "route", "base_url": "http://route", "model": "route"},
            "manual-a": {"api_key": "manual", "base_url": "http://manual", "model": "manual"},
        },
    )
    monkeypatch.setattr(llm_module, "LLM_ROUTER", {"default": ["route-a"]})
    monkeypatch.setattr(llm_module, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm_module, "_build_attempt_order", lambda *_args: ["openai"])
    monkeypatch.setattr(llm_module, "record_success", lambda *_args: None)
    monkeypatch.setattr(llm_module, "record_task_model_success", lambda *_args: None)
    monkeypatch.setattr(llm_module, "_record_metric", lambda *_args: None)
    monkeypatch.setattr(llm_module, "_trace_log", lambda *_args: None)

    metadata = {}
    result = llm_module.chat_with_ai(
        [{"role": "user", "content": "hello"}],
        model_keys_override=["manual-a"],
        call_metadata=metadata,
    )

    assert result == "manual-result"
    assert calls[0]["model"] == "manual"
    assert metadata["model_key"] == "manual-a"
    assert metadata["transport"] == "openai"
