import pytest

import modules.llm as llm
from modules.llm_types import ChatCompletionResult


class _RateLimitedError(RuntimeError):
    status_code = 429


class _ToolsNotSupported(RuntimeError):
    status_code = 400

    def __init__(self):
        super().__init__("tools is not supported")


def _openai_client(create):
    class FakeCompletions:
        def create(self, **kwargs):
            return create(kwargs)

    class FakeOpenAI:
        def __init__(self, *, api_key, base_url, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    return FakeOpenAI


def _text_response(text):
    class Message:
        content = text
        tool_calls = None

    class Choice:
        message = Message()
        finish_reason = "stop"

    class Response:
        choices = [Choice()]

    return Response()


def _tool_response(name, arguments, *, content=None):
    class Function:
        def __init__(self):
            self.name = name
            self.arguments = arguments

    class ToolCall:
        id = "call_1"
        type = "function"
        function = Function()

    class Message:
        def __init__(self):
            self.content = content
            self.tool_calls = [ToolCall()]

    class Choice:
        message = Message()
        finish_reason = "tool_calls"

    class Response:
        choices = [Choice()]

    return Response()


def test_resolve_model_config_keeps_explicit_model_url(monkeypatch):
    monkeypatch.setattr(
        llm,
        "PROVIDERS",
        {"pool": {"base_url": "http://127.0.0.1:8317/v1", "api_key": "pool-key"}},
    )
    resolved = llm._resolve_model_config(
        {
            "model": "deepseek-v4-flash",
            "provider": "pool",
            "base_url": "https://example.remote/v1",
            "api_key": "model-key",
        }
    )
    assert resolved["base_url"] == "https://example.remote/v1"
    assert resolved["api_key"] == "model-key"


def test_resolve_model_config_fills_blank_url_from_provider(monkeypatch):
    monkeypatch.setattr(
        llm,
        "PROVIDERS",
        {"pool": {"base_url": "http://127.0.0.1:8317/v1", "api_key": "pool-key"}},
    )
    resolved = llm._resolve_model_config(
        {"model": "gemma-4-31b", "provider": "pool"}
    )
    assert resolved["base_url"] == "http://127.0.0.1:8317/v1"
    assert resolved["api_key"] == "pool-key"


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    llm._MODEL_COOLDOWNS.clear()
    monkeypatch.setattr(
        llm,
        "MODELS",
        {"only": {"model": "one", "api_key": "x", "base_url": "only"}},
    )
    monkeypatch.setattr(llm, "_build_attempt_order", lambda config, key: ["openai"])
    yield
    llm._MODEL_COOLDOWNS.clear()


def test_chat_with_ai_still_returns_text(monkeypatch):
    monkeypatch.setattr(llm, "OpenAI", _openai_client(lambda kwargs: _text_response("hi")))
    reply = llm.chat_with_ai(
        [{"role": "user", "content": "hello"}],
        caller="test",
        model_keys_override=["only"],
    )
    assert reply == "hi"


def test_chat_complete_accepts_empty_content_when_tool_calls(monkeypatch):
    seen = {}

    def create(kwargs):
        seen.update(kwargs)
        return _tool_response("info_gateway", '{"city":"上海","capability":"weather_now"}')

    monkeypatch.setattr(llm, "OpenAI", _openai_client(create))
    tools = [{"type": "function", "function": {"name": "info_gateway", "parameters": {}}}]
    result = llm.chat_complete(
        [{"role": "user", "content": "天气"}],
        caller="test",
        model_keys_override=["only"],
        tools=tools,
    )
    assert isinstance(result, ChatCompletionResult)
    assert result.text == ""
    assert result.tool_calls[0].name == "info_gateway"
    assert result.tool_calls[0].arguments["city"] == "上海"
    assert seen.get("tools") == tools


def test_chat_complete_retries_without_tools_on_4xx(monkeypatch):
    calls = []

    def create(kwargs):
        calls.append(kwargs)
        if "tools" in kwargs:
            raise _ToolsNotSupported()
        return _text_response("[CMD: info_gateway | weather_now city=上海]")

    monkeypatch.setattr(llm, "OpenAI", _openai_client(create))
    result = llm.chat_complete(
        [{"role": "user", "content": "天气"}],
        caller="test",
        model_keys_override=["only"],
        tools=[{"type": "function", "function": {"name": "info_gateway"}}],
    )
    assert result.fc_skipped_reason == "tools_unsupported"
    assert "CMD:" in result.text
    assert result.tool_calls == ()
    assert "tools" in calls[0]
    assert "tools" not in calls[1]
    assert "only" not in llm._MODEL_COOLDOWNS


def test_chat_complete_does_not_retry_rate_limit_as_tools_unsupported(monkeypatch):
    def create(kwargs):
        raise _RateLimitedError("rate limit reached")

    monkeypatch.setattr(llm, "OpenAI", _openai_client(create))
    result = llm.chat_complete(
        [{"role": "user", "content": "天气"}],
        caller="test",
        model_keys_override=["only"],
        tools=[{"type": "function", "function": {"name": "info_gateway"}}],
    )
    assert result.text.startswith("❌")
    assert "only" in llm._MODEL_COOLDOWNS


def _async_openai_client(create):
    class FakeCompletions:
        async def create(self, **kwargs):
            return create(kwargs)

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key, base_url, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        async def close(self):
            return None

    return FakeAsyncOpenAI


@pytest.mark.asyncio
async def test_analyze_image_uses_vision_router_chain(monkeypatch):
    calls = []

    def create(kwargs):
        calls.append(kwargs["model"])
        return _text_response(f"seen-{kwargs['model']}")

    monkeypatch.setattr(llm, "AsyncOpenAI", _async_openai_client(create))
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "vl-a": {
                "model": "vision-a",
                "api_key": "x",
                "base_url": "a",
                "purposes": ["vision"],
            },
            "vl-b": {
                "model": "vision-b",
                "api_key": "x",
                "base_url": "b",
                "purposes": ["vision"],
            },
        },
    )
    monkeypatch.setattr(llm, "LLM_ROUTER", {"vision": ["vl-b", "vl-a"]})
    monkeypatch.setattr(llm, "SENSOR_VISION_MODEL", "vl-a")

    result = await llm.analyze_image("abc", prompt="看图", caller="qq_image_describe")
    assert result == "seen-vision-b"
    assert calls == ["vision-b"]


@pytest.mark.asyncio
async def test_analyze_image_prefers_chat_model_with_vision(monkeypatch):
    calls = []

    def create(kwargs):
        calls.append(kwargs["model"])
        return _text_response("from-chat")

    monkeypatch.setattr(llm, "AsyncOpenAI", _async_openai_client(create))
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "chat-vl": {
                "model": "chat-vision",
                "api_key": "x",
                "base_url": "c",
                "purposes": ["chat", "vision"],
            },
            "vl-only": {
                "model": "vision-only",
                "api_key": "x",
                "base_url": "v",
                "purposes": ["vision"],
            },
        },
    )
    monkeypatch.setattr(
        llm,
        "LLM_ROUTER",
        {"default": ["chat-vl"], "vision": ["vl-only"]},
    )

    result = await llm.analyze_image("abc")
    assert result == "from-chat"
    assert calls == ["chat-vision"]


@pytest.mark.asyncio
async def test_analyze_image_skips_chat_models_in_vision_route(monkeypatch):
    calls = []

    def create(kwargs):
        calls.append(kwargs["model"])
        return _text_response("ok")

    monkeypatch.setattr(llm, "AsyncOpenAI", _async_openai_client(create))
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "chat-a": {
                "model": "chat-only",
                "api_key": "x",
                "base_url": "c",
                "purposes": ["chat"],
            },
            "vl-a": {
                "model": "vision-a",
                "api_key": "x",
                "base_url": "a",
                "purposes": ["vision"],
            },
        },
    )
    monkeypatch.setattr(llm, "LLM_ROUTER", {"vision": ["chat-a", "vl-a"]})

    result = await llm.analyze_image("abc")
    assert result == "ok"
    assert calls == ["vision-a"]


@pytest.mark.asyncio
async def test_analyze_image_falls_back_to_sensor_vision_model(monkeypatch):
    calls = []

    def create(kwargs):
        calls.append(kwargs["model"])
        return _text_response("ok")

    monkeypatch.setattr(llm, "AsyncOpenAI", _async_openai_client(create))
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "vl-legacy": {
                "model": "legacy-v",
                "api_key": "x",
                "base_url": "legacy",
            }
        },
    )
    monkeypatch.setattr(llm, "LLM_ROUTER", {"vision": []})
    monkeypatch.setattr(llm, "SENSOR_VISION_MODEL", "vl-legacy")

    result = await llm.analyze_image("abc")
    assert result == "ok"
    assert calls == ["legacy-v"]


@pytest.mark.asyncio
async def test_analyze_image_reports_missing_vision_route(monkeypatch):
    monkeypatch.setattr(llm, "MODELS", {})
    monkeypatch.setattr(llm, "LLM_ROUTER", {"vision": []})
    monkeypatch.setattr(llm, "SENSOR_VISION_MODEL", "gone")
    result = await llm.analyze_image("abc")
    assert "请在任务路由里配置「识图」模型" in result


@pytest.mark.asyncio
async def test_analyze_image_falls_through_failed_primary(monkeypatch):
    calls = []

    def create(kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "vision-a":
            raise RuntimeError("primary down")
        return _text_response("backup ok")

    monkeypatch.setattr(llm, "AsyncOpenAI", _async_openai_client(create))
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "vl-a": {
                "model": "vision-a",
                "api_key": "x",
                "base_url": "a",
                "purposes": ["vision"],
            },
            "vl-b": {
                "model": "vision-b",
                "api_key": "x",
                "base_url": "b",
                "purposes": ["vision"],
            },
        },
    )
    monkeypatch.setattr(llm, "LLM_ROUTER", {"vision": ["vl-a", "vl-b"]})

    result = await llm.analyze_image("abc")
    assert result == "backup ok"
    assert calls == ["vision-a", "vision-b"]
