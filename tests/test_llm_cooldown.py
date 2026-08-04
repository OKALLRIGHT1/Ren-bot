import pytest

import modules.llm as llm
from services.runtime_health import RuntimeHealthCenter


class RateLimitedError(RuntimeError):
    status_code = 429

    def __init__(self, reset_seconds=120):
        super().__init__("rate limit reached")
        self.body = {
            "error": {
                "code": "model_cooldown",
                "reset_seconds": reset_seconds,
            }
        }


@pytest.fixture(autouse=True)
def clear_cooldowns(monkeypatch):
    llm._MODEL_COOLDOWNS.clear()
    health = RuntimeHealthCenter(clock=lambda: 1000.0)
    monkeypatch.setattr(llm, "_RUNTIME_HEALTH", health)
    yield health
    llm._MODEL_COOLDOWNS.clear()


def test_rate_limit_delay_uses_structured_reset_and_caps_at_fifteen_minutes():
    assert llm._rate_limit_delay(RateLimitedError(120)) == 120.0
    assert llm._rate_limit_delay(RateLimitedError(5000)) == 900.0
    assert llm._rate_limit_delay(RuntimeError("rate limit")) == 60.0
    assert llm._rate_limit_delay(RuntimeError("gemini_native HTTP 429")) == 60.0
    assert llm._rate_limit_delay(RuntimeError("socket closed")) is None


def test_sync_skips_cooled_model_and_uses_backup(monkeypatch, clear_cooldowns):
    now = {"value": 1000.0}
    calls = []

    class FakeResponse:
        class Choice:
            class Message:
                content = "backup reply"

            message = Message()

        choices = [Choice()]

    class FakeCompletions:
        def __init__(self, model_key):
            self.model_key = model_key

        def create(self, **kwargs):
            calls.append(self.model_key)
            return FakeResponse()

    class FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = type(
                "Chat",
                (),
                {"completions": FakeCompletions(base_url)},
            )()

    monkeypatch.setattr(llm.time, "time", lambda: now["value"])
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "primary": {
                "model": "one",
                "api_key": "x",
                "base_url": "primary",
            },
            "backup": {
                "model": "two",
                "api_key": "x",
                "base_url": "backup",
            },
        },
    )
    monkeypatch.setattr(
        llm,
        "_build_attempt_order",
        lambda config, key: ["openai"],
    )
    llm._start_model_cooldown(
        "primary",
        RateLimitedError(120),
        now=now["value"],
    )

    reply = llm.chat_with_ai(
        [{"role": "user", "content": "hello"}],
        caller="test",
        model_keys_override=["primary", "backup"],
    )

    assert reply == "backup reply"
    assert calls == ["backup"]
    component = clear_cooldowns.snapshot(now=1000.0)["components"]["model:primary"]
    assert component["state"] == "cooldown"
    assert "rate limit reached" not in str(component)


@pytest.mark.asyncio
async def test_stream_observes_same_sync_cooldown_and_recovers_after_expiry(
    monkeypatch,
):
    now = {"value": 2000.0}
    monkeypatch.setattr(llm.time, "time", lambda: now["value"])
    monkeypatch.setattr(llm, "LLM_ROUTER", {"default": ["shared"]})
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "shared": {
                "model": "one",
                "api_key": "x",
                "base_url": "shared",
            }
        },
    )
    llm._set_model_cooldown(
        "shared",
        until=2005.0,
        reason="rate_limit",
    )

    chunks = [
        chunk
        async for chunk in llm.chat_with_ai_stream([], caller="test")
    ]
    assert chunks == ["（所有模型连接失败，请检查网络或 Key）"]

    now["value"] = 2006.0
    assert llm._model_cooldown_remaining("shared", now=now["value"]) == 0.0
    assert "shared" not in llm._MODEL_COOLDOWNS


def test_success_clears_existing_model_cooldown():
    llm._set_model_cooldown(
        "backup",
        until=4000.0,
        reason="rate_limit",
    )
    llm._clear_model_cooldown("backup", summary="模型调用恢复")
    assert "backup" not in llm._MODEL_COOLDOWNS


@pytest.mark.asyncio
async def test_partial_stream_rate_limit_still_starts_cooldown(monkeypatch):
    now = {"value": 5000.0}

    class PartialStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "yielded"):
                self.yielded = True
                delta = type("Delta", (), {"content": "partial"})()
                choice = type("Choice", (), {"delta": delta})()
                return type("Chunk", (), {"choices": [choice]})()
            raise RateLimitedError(90)

    class FakeCompletions:
        async def create(self, **kwargs):
            return PartialStream()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(llm.time, "time", lambda: now["value"])
    monkeypatch.setattr(llm, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(llm, "LLM_ROUTER", {"default": ["partial"]})
    monkeypatch.setattr(
        llm,
        "MODELS",
        {
            "partial": {
                "model": "one",
                "api_key": "x",
                "base_url": "partial",
            }
        },
    )
    monkeypatch.setattr(
        llm,
        "_build_attempt_order",
        lambda config, key: ["openai"],
    )

    chunks = [
        chunk
        async for chunk in llm.chat_with_ai_stream([], caller="test")
    ]

    assert chunks == ["partial"]
    assert llm._model_cooldown_remaining("partial", now=5000.0) == 90.0
